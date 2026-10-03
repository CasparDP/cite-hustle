"""Abstract sources: CrossRef cache backfill, Semantic Scholar batch, NBER landing page."""

import json

import httpx

from cite_hustle.collectors import abstract_sources as ab
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.collectors.journals import Journal
from conftest import add_article

LONG = "We study how auditors respond to new disclosure rules. " * 4


def _abstract(repo, doi):
    row = repo.get_ssrn_page_by_doi(doi)
    return row["abstract"] if row else None


def test_clean_abstract_strips_jats_and_label():
    raw = f"<jats:title>Abstract</jats:title><jats:p>{LONG}&amp; more</jats:p>"
    assert ab.clean_abstract(raw) == (LONG + "& more").strip()
    assert ab.clean_abstract("<jats:p>Too short.</jats:p>") is None


def test_collect_stores_crossref_abstract(repo, tmp_path):
    item = {
        "DOI": "10.1/new",
        "type": "journal-article",
        "title": ["A Real Paper"],
        "issued": {"date-parts": [[2026]]},
        "abstract": f"<jats:p>{LONG}</jats:p>",
    }
    (tmp_path / "cache_0000-0000_2026.json").write_text(json.dumps([item]))
    collector = MetadataCollector(repo, cache_dir=tmp_path)

    collector.collect_for_journal(Journal("J", "0000-0000", "accounting", "P"), [2026], False)

    assert _abstract(repo, "10.1/new") == LONG.strip()
    # An abstract alone must not count as an SSRN search.
    assert list(repo.get_pending_ssrn_scrapes()["doi"]) == ["10.1/new"]


def test_crossref_cache_backfill_fills_only_missing(repo, tmp_path):
    add_article(repo, "10.1/Missing")
    add_article(repo, "10.1/has")
    repo.upsert_abstract("10.1/has", "Existing abstract stays.")
    items = [
        {"DOI": "10.1/missing", "abstract": f"<jats:p>{LONG}</jats:p>"},
        {"DOI": "10.1/has", "abstract": f"<jats:p>{LONG}</jats:p>"},
    ]
    (tmp_path / "cache_0000-0000_2024.json").write_text(json.dumps(items))

    stats = ab.backfill_from_crossref_cache(repo, tmp_path)

    assert stats["updated"] == 1
    assert _abstract(repo, "10.1/Missing") == LONG.strip()
    assert _abstract(repo, "10.1/has") == "Existing abstract stays."


def test_s2_batch_maps_abstracts_and_logs_no_match(repo):
    add_article(repo, "10.1/a")
    add_article(repo, "10.1/b")

    def handler(request):
        assert json.loads(request.content)["ids"] == ["DOI:10.1/a", "DOI:10.1/b"]
        return httpx.Response(200, json=[{"abstract": LONG}, None])

    source = ab.SemanticScholarSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = ab.enrich_from_s2(repo, [{"doi": "10.1/a"}, {"doi": "10.1/b"}], source, delay_s=0)

    assert (stats["updated"], stats["no_match"]) == (1, 1)
    assert _abstract(repo, "10.1/a") == LONG.strip()
    # Both attempts are recent, so neither is offered to S2 again.
    again = repo.get_articles_missing_abstract(skip_stage="abstract_s2")
    assert again.empty


def test_nber_uses_full_landing_page_abstract(repo):
    add_article(
        repo,
        "10.1/n",
        title="Earnings Management and Price Informativeness",
        authors="Zhiguo He; Wei Xiong",
    )

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "Unrelated Paper on Banks", "url": "/papers/w1"},
                        {
                            "title": "Earnings Management and Price Informativeness",
                            "url": "/papers/w35178",
                            "authors": ['<a href="/people/zhiguo_he">Zhiguo He</a>'],
                            "abstract": "truncated",
                        },
                    ]
                },
            )
        assert request.url.path == "/papers/w35178"
        page = f'<div class="page-header__intro-inner"><p>{LONG}</p></div>'
        return httpx.Response(200, text=page)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    articles = repo.get_articles_missing_abstract().to_dict("records")
    stats = ab.enrich_from_nber(repo, articles, client=client, delay_s=0)

    assert stats["updated"] == 1
    assert _abstract(repo, "10.1/n") == LONG.strip()


def test_nber_rejects_short_title_and_author_mismatches(repo):
    search = {
        "results": [
            # Same title, different authors: a different paper.
            {"title": "Robots and Workers", "url": "/papers/w1", "authors": ["Jane Doe"]},
            # Right author, but the article title is only a substring.
            {
                "title": "Robots and Workers: Evidence on Crime",
                "url": "/papers/w2",
                "authors": ["Alice Smith"],
            },
        ]
    }

    def handler(request):
        assert request.url.path.endswith("/search"), "no landing page should be fetched"
        return httpx.Response(200, json=search)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = ab.NBERAbstractSource()
    assert source.fetch(client, "Robots and Workers", "Alice Smith; Bob Jones") is None


def test_s2_length_mismatch_never_misaligns(repo):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[{"abstract": LONG}]))
    )
    source = ab.SemanticScholarSource(client=client)
    stats = ab.enrich_from_s2(repo, [{"doi": "10.1/a"}, {"doi": "10.1/b"}], source, delay_s=0)
    assert stats["failed"] == 2 and stats["updated"] == 0
