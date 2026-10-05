"""Duplicate-DOI rule (duplicates.py) and the merge in the repository and CLI."""

import json

from click.testing import CliRunner

from cite_hustle import duplicates as dup
from conftest import add_article

JT = "Returns to Buying Winners and Selling Losers"


def rec(
    doi,
    title=JT,
    authors="Narasimhan Jegadeesh; Sheridan Titman",
    year=1993,
    page="65-91",
    cites=0,
    issn="0022-1082",
):
    return dup.Record(doi, title, authors, year, issn, page, cites)


def test_jstor_and_publisher_doi_merge_onto_the_publisher_doi():
    wiley = rec(
        "10.1111/j.1540-6261.1993.tb04702.x", authors="NARASIMHAN JEGADEESH; SHERIDAN TITMAN"
    )
    jstor = rec("10.2307/2328882", page="65", cites=10**6)  # JSTOR loses even with more cites
    assert dup.pair_decision(wiley, jstor) == "merge"
    scan = dup.find_duplicates([[wiley, jstor]], {})
    assert [(g.keep, g.drop) for g in scan.groups] == [(wiley.doi, [jstor.doi])]


def test_jstor_start_page_may_be_off_by_two_but_not_within_one_prefix():
    a, b = rec("10.1111/x", page="528-543"), rec("10.2307/1", page="529")
    assert dup.pair_decision(a, b) == "merge"
    assert dup.pair_decision(a, rec("10.2307/2", page="531")) == "no:pages"
    assert dup.pair_decision(a, rec("10.1111/y", page="529")) == "no:pages"


def test_corrigendum_and_authors_reply_are_not_merged():
    # White (1982) and its corrigendum: same title and author, different start page
    paper = rec(
        "10.2307/1912526",
        "Maximum Likelihood Estimation of Misspecified Models",
        "Halbert White",
        1982,
        "1",
    )
    corr = rec("10.2307/1912004", paper.title, "Halbert White", 1983, "513")
    assert dup.pair_decision(paper, corr) == "no:pages"
    # AUD 1999: two DOI formats of the paper (p. 45) plus the authors' reply (p. 79)
    authors = "Donald F. Arnold; Richard A. Bernardi; Presha E. Neidermeyer"
    title = "The Effect of Independence on Decisions concerning Additional Audit Work"
    s45 = rec("10.2308/aud.1999.18.s-1.45", title, authors, 1999, "45-67", 9)
    sup45 = rec("10.2308/aud.1999.18.supplement.45", title, authors, 1999, "45-67", 23)
    s79 = rec("10.2308/aud.1999.18.s-1.79", title, authors, 1999, "79-83")
    scan = dup.find_duplicates([[s45, sup45, s79]], {})
    assert [(g.keep, g.drop) for g in scan.groups] == [(sup45.doi, [s45.doi])]


def test_author_typos_tolerated_but_different_authors_refused():
    a = rec("10.1111/a", authors="TIM S. CAMPBEL; WILLIAM A. KRACAW")
    b = rec("10.2307/b", authors="Tim S. Campbell; William A. Kracaw")
    assert dup.authors_compatible(a.authors, b.authors)
    assert dup.authors_compatible(
        "SASSON BAE‐YOSEF; ODED H. SARIG", "Sasson Bar-Yosef; Oded H. Sarig"
    )
    assert dup.authors_compatible("K Rogg", "Kirk L. Rogg; David B. Schmidt")  # first-author only
    # Same title, different papers (1980 vs 1987)
    x = rec(
        "10.1016/1",
        "The information content of security prices",
        "William Beaver; Richard Lambert; Dale Morse",
        1980,
        "3",
    )
    y = rec(
        "10.1016/2", x.title, "William H. Beaver; Richard A. Lambert; Stephen G. Ryan", 1987, "3"
    )
    assert dup.pair_decision(x, y) == "no:authors"
    assert dup.authors_compatible("Unknown", "A Smith") is None


def test_unknown_authors_merge_only_across_prefixes_with_pages():
    a = rec("10.5465/amr.1", authors="Unknown", page="195-209")
    assert dup.pair_decision(a, rec("10.2307/2", page="195")) == "merge"
    assert dup.pair_decision(a, rec("10.5465/amr.3", page="195-209")) == "no:authors-empty"


def test_reply_like_titles_merge_only_across_prefixes():
    for title in [
        "Discussion",
        "Weekend Effects on Stock Returns: A Comment",
        "Reply",
        "Minimax Play at Wimbledon: Comment",
        "Capsule Commentary",
        "A Comment on Bank Funding Risks",
    ]:
        assert dup.is_reply_like(title), title
    for title in [
        "Market Response to Earnings Surprises",
        "Performance improvement efforts in response to negative feedback",
    ]:
        assert not dup.is_reply_like(title), title
    a, b = rec("10.1111/a", "Discussion", "Stephen A. Ross"), rec(
        "10.2307/b", "Discussion", "Stephen A. Ross"
    )
    assert dup.pair_decision(a, b) == "merge"
    assert (
        dup.pair_decision(a, rec("10.1111/c", "Discussion", "Stephen A. Ross")) == "no:reply-like"
    )


def test_year_gap_and_missing_pages():
    assert dup.pair_decision(rec("10.1111/a", year=1993), rec("10.2307/b", year=1995)) == "no:year"
    assert dup.pair_decision(rec("10.1111/a", page=None), rec("10.2307/b")) == "held:no-pages"
    scan = dup.find_duplicates([[rec("10.1111/a", page=None), rec("10.2307/b")]], {})
    assert not scan.groups and scan.held_no_pages == [("10.1111/a", "10.2307/b")]


def test_kept_doi_prefers_current_prefix_then_citations():
    old = rec("10.1162/qjec.1", issn="0033-5533", cites=117)
    new = rec("10.1093/qje/1", issn="0033-5533", cites=52)
    recent = [rec(f"10.1093/qje/{i}", f"Paper {i}", issn="0033-5533", year=2026) for i in range(3)]
    current = dup.current_prefixes([old, new] + recent)
    assert current["0033-5533"] == "10.1093"
    assert dup.find_duplicates([[old, new]], current).groups[0].keep == new.doi
    assert dup.find_duplicates([[old, new]], {}).groups[0].keep == old.doi  # citations


def test_same_title_groups_normalize_markup_and_case():
    groups = dup.same_title_groups(
        [rec("a", "The <i>Noise</i> Trader"), rec("b", "THE NOISE TRADER."), rec("c", "Other")]
    )
    assert [[r.doi for r in g] for g in groups] == [["a", "b"]]
    # Wiley's Unicode hyphen and curly apostrophe, JSTOR's ASCII ones
    assert dup.normalize_title("Asset\u2010pricing Puzzles") == dup.normalize_title(
        "Asset-Pricing Puzzles"
    )
    assert dup.normalize_title("Analysts\u2019 Forecasts") == dup.normalize_title(
        "Analysts' Forecasts"
    )
    assert dup.normalize_title("Étude sur l\u2019économie") == "etude sur leconomie"


def test_load_crossref_pages_reads_cache(tmp_path):
    items = [{"DOI": "10.2307/ABC", "page": "65", "is-referenced-by-count": 7}, {"DOI": "10.1/x"}]
    (tmp_path / "cache_0022-1082_1993.json").write_text(json.dumps(items))
    (tmp_path / "cache_broken_1993.json").write_text("{not json")
    assert dup.load_crossref_pages(tmp_path, ["10.2307/abc"]) == {"10.2307/abc": ("65", 7)}


def _pair(repo, keep="10.1/keep", drop="10.2307/drop", title=JT):
    add_article(repo, keep, title=title, authors="A Smith; B Jones", year=1993)
    add_article(repo, drop, title=title, authors="A. Smith; B. Jones", year=1993)


def test_merge_moves_better_rows_fills_abstract_and_deletes_drop(repo):
    _pair(repo)
    # Keep: searched, no match, no abstract. Drop: SSRN URL found, no abstract.
    repo.insert_ssrn_page("10.1/keep", None, None, None, None, None, "No search results found")
    repo.insert_ssrn_page("10.2307/drop", "https://ssrn.com/abstract=1", None, "d.html", None, 95)
    # A third member with only an enrichment abstract fills the missing abstract
    add_article(repo, "10.1/third", title=JT, authors="A Smith", year=1993)
    repo.upsert_abstract("10.1/third", "x" * 120)
    repo.upsert_pdf_file(
        doi="10.2307/drop", source="oa", source_url=None, pdf_url=None, pdf_file_path="/tmp/d.pdf"
    )
    repo.record_pdf_candidate("10.1/keep", "oa", status="no_match")
    repo.record_pdf_candidate("10.2307/drop", "oa", status="downloaded")
    repo.record_pdf_candidate("10.2307/drop", "nber", status="no_match")
    repo.log_processing("10.2307/drop", "scrape_ssrn", "success")

    counts = repo.merge_duplicate_articles([("10.1/keep", ["10.2307/drop", "10.1/third"])])

    assert counts["articles_deleted"] == 2 and counts["groups"] == 1
    assert counts["ssrn_rows_taken_from_dropped"] == 1 and counts["abstracts_filled"] == 1
    assert counts["pdf_files_moved"] == 1 and counts["pdf_candidates_moved"] == 1
    assert counts["processing_log_repointed"] == 1
    assert repo.get_article_by_doi("10.2307/drop") is None
    page = repo.get_ssrn_page_by_doi("10.1/keep")
    assert page["ssrn_url"] == "https://ssrn.com/abstract=1" and page["abstract"] == "x" * 120
    assert repo.get_pdf_file_by_doi("10.1/keep")["source"] == "oa"
    cands = dict(
        repo.conn.execute(
            "SELECT source, status FROM pdf_candidates WHERE doi = '10.1/keep'"
        ).fetchall()
    )
    assert cands == {"oa": "no_match", "nber": "no_match"}  # kept DOI's row wins a clash
    for table in ("ssrn_pages", "pdf_files", "pdf_candidates", "processing_log"):
        left = repo.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE doi IN ('10.2307/drop', '10.1/third')"
        ).fetchone()[0]
        assert left == 0, table
    assert (
        repo.conn.execute("SELECT COUNT(*) FROM processing_log WHERE doi = '10.1/keep'").fetchone()[
            0
        ]
        == 1
    )
    assert repo.find_duplicate_keys() == {}


def test_merge_skips_groups_whose_dropped_doi_has_a_wiki_page(repo):
    _pair(repo)
    repo.upsert_wiki_page("10.2307/drop", "smith1993returns")
    repo.insert_ssrn_page("10.1/keep", "https://ssrn.com/abstract=9", None, "k.html", None, 95)
    counts = repo.merge_duplicate_articles([("10.1/keep", ["10.2307/drop"])])
    assert counts["skipped_wiki"] == ["10.1/keep"] and counts["articles_deleted"] == 0
    assert repo.get_article_by_doi("10.2307/drop") is not None
    assert repo.get_ssrn_page_by_doi("10.1/keep")["ssrn_url"]  # skipped group left untouched


def test_cli_dry_run_then_apply(repo, tmp_path):
    from cite_hustle.cli import commands

    _pair(repo)
    add_article(repo, "10.1/reply", title=JT, authors="A Smith; B Jones", year=1993)
    cache = tmp_path / "cache"
    cache.mkdir()
    pages = [
        {"DOI": "10.1/keep", "page": "65-91"},
        {"DOI": "10.2307/drop", "page": "65"},
        {"DOI": "10.1/reply", "page": "99-100"},
    ]
    (cache / "cache_0000-0000_1993.json").write_text(json.dumps(pages))
    obj = {"repo": repo, "db": repo.db}

    def run(*args):
        return CliRunner().invoke(
            commands.merge_duplicates,
            ["--cache-dir", str(cache), "--report", str(tmp_path / "r.csv"), *args],
            obj=obj,
        )

    dry = run()
    assert dry.exit_code == 0, dry.output
    assert "merge groups: 1 (1 articles to remove)" in dry.output
    assert repo.get_article_by_doi("10.2307/drop") is not None

    applied = run("--apply", "--backup-dir", str(tmp_path / "bk"))
    assert applied.exit_code == 0, applied.output
    assert repo.get_article_by_doi("10.2307/drop") is None
    assert repo.get_article_by_doi("10.1/reply") is not None  # different start page
    assert list((tmp_path / "bk").glob("*/test.duckdb"))


def test_crossref_alias_proves_a_pair_without_pages_and_target_is_kept():
    wiley = rec("10.1111/j.1540-6261.1997.tb02750.x", page="2051-2072", cites=1)
    jstor = rec("10.2307/2329473", page=None, cites=10**6)
    assert dup.pair_decision(wiley, jstor) == "held:no-pages"
    jstor.alias_of = wiley.doi.lower()
    assert dup.pair_decision(wiley, jstor) == "merge"
    assert dup.find_duplicates([[jstor, wiley]], {}).groups[0].keep == wiley.doi
    # Two aliases of the same work merge with each other and with the target
    jstor.alias_of = wiley.doi.lower()
    old = rec("10.1111/j.1540-6261.1997.tb02750_1.x", page=None, cites=5)
    old.alias_of = wiley.doi.lower()
    assert dup.pair_decision(jstor, old) == "merge"
    scan = dup.find_duplicates([[jstor, old, wiley]], {})
    assert not scan.conflicted and scan.groups[0].keep == wiley.doi
    # An alias pointing elsewhere proves nothing about this pair
    jstor.alias_of = "10.1111/other"
    assert dup.pair_decision(wiley, jstor) == "held:no-pages"


def test_resolve_aliases_follows_redirects_backs_off_and_caches(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        assert request.method == "GET"
        doi = request.url.path.split("/works/", 1)[1]
        calls.append(doi)
        if doi == "10.2307/alias":
            return httpx.Response(301, headers={"Location": "/works/10.1111/TARGET"})
        if doi == "10.1257/old":  # chain: old -> mid -> new
            return httpx.Response(301, headers={"Location": "/works/10.1257/mid"})
        if doi == "10.1257/mid":
            return httpx.Response(301, headers={"Location": "/works/10.1257/new"})
        if doi == "10.1/busy" and calls.count(doi) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if doi == "10.1/busy":
            return httpx.Response(200)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        dup.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    got = dup.resolve_aliases(
        ["10.2307/alias", "10.1/busy", "10.1/gone", "10.1257/old"], tmp_path, delay=0
    )
    assert got == {
        "10.2307/alias": "10.1111/target",
        "10.1/busy": "",
        "10.1/gone": "",
        "10.1257/old": "10.1257/new",  # followed to the end of the chain
    }
    n = len(calls)
    assert dup.resolve_aliases(["10.2307/ALIAS"], tmp_path, delay=0) == {
        "10.2307/alias": "10.1111/target"
    }
    assert len(calls) == n  # answered from crossref_aliases.json


def test_cli_merges_alias_without_cached_page(repo, tmp_path):
    from cite_hustle.cli import commands

    _pair(repo)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "cache_0000-0000_1993.json").write_text(
        json.dumps([{"DOI": "10.1/keep", "page": "65"}])
    )
    (cache / dup.ALIAS_CACHE_NAME).write_text(json.dumps({"10.2307/drop": "10.1/keep"}))
    result = CliRunner().invoke(
        commands.merge_duplicates,
        [
            "--cache-dir",
            str(cache),
            "--report",
            str(tmp_path / "r.csv"),
            "--apply",
            "--backup-dir",
            str(tmp_path / "bk"),
        ],
        obj={"repo": repo, "db": repo.db},
    )
    assert result.exit_code == 0, result.output
    assert "CrossRef aliases among checked DOIs: 1" in result.output
    assert repo.get_article_by_doi("10.2307/drop") is None
