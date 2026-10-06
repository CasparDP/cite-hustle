"""Abstract-vs-PDF check, junk abstracts, and the verify/repair commands."""

import json

from click.testing import CliRunner

from cite_hustle import abstract_check as ac
from conftest import add_article

ABSTRACT = (
    "We propose a model in which arbitrageurs act strategically in markets with entry "
    "costs. In a repeated game, arbitrageurs choose to specialize in some markets, which "
    "leads to the highest combined profits. We present evidence from the options market."
)
OTHER = (
    "This paper studies the spillovers of monetary policy and the mitigating role of "
    "foreign exchange interventions by combining deviations from a daily policy rule with "
    "high-frequency monetary policy shocks and daily exchange rate responses worldwide."
)
PAGE_TEXT = (
    "Download This PaperOpen PDF in BrowserAdd Paper to My LibraryShare:PermalinkUsing these "
    "links will ensure access to this page indefinitelyCopy URLCopy DOISupply Chain Shocks "
    "71 PagesPosted: 4 Jan 2024See all articles by Philip G. BergerDate Written: September "
    "13, 2025We examine the role of reporting quality in firms' responses to a supply chain "
    "shock. Our setting is the 1999 Taiwan earthquake, which disrupted supply chains for "
    "certain manufacturers.Keywords: reporting quality, supply chains"
)


def test_overlap_ignores_spaces_and_separates_wrong_abstracts():
    pdf_without_spaces = "Cover sheet Working Paper " + ABSTRACT.replace(" ", "")
    assert ac.overlap(ABSTRACT, pdf_without_spaces) == 1.0
    assert ac.overlap(OTHER, "Title page " + ABSTRACT) < ac.MATCH_THRESHOLD
    assert ac.overlap("Too short to judge.", ABSTRACT) is None
    assert ac.overlap(ABSTRACT, None) is None


def test_junk_detection_and_cut_out():
    assert ac.is_ssrn_page_text(PAGE_TEXT) and ac.is_junk(PAGE_TEXT)
    assert ac.is_placeholder(
        "An abstract is not available for this content so a preview has been provided."
    )
    assert not ac.is_junk(ABSTRACT)
    cut = ac.cut_abstract_from_page_text(PAGE_TEXT)
    assert cut.startswith("We examine the role of reporting quality")
    assert cut.endswith("certain manufacturers.")


def test_repository_never_stores_junk(repo):
    add_article(repo, "10.1/a")
    repo.upsert_abstract("10.1/a", "Your use of the JSTOR archive indicates your acceptance of")
    assert repo.get_ssrn_page_by_doi("10.1/a") is None
    repo.insert_ssrn_page("10.1/a", "https://ssrn.com/abstract=1", None, "a.html", PAGE_TEXT, 95)
    assert repo.get_ssrn_page_by_doi("10.1/a")["abstract"].startswith("We examine the role")


def _verified_pdf(repo, doi, abstract):
    add_article(repo, doi)
    repo.upsert_abstract(doi, abstract)
    repo.upsert_pdf_file(
        doi=doi,
        source="oa",
        source_url=None,
        pdf_url=None,
        pdf_file_path=f"/tmp/{doi.replace('/', '_')}.pdf",
    )
    repo.conn.execute("UPDATE pdf_files SET verify_status = 'match' WHERE doi = ?", [doi])


def test_verify_abstracts_flags_mismatch_and_rechecks_only_changes(repo, tmp_path, monkeypatch):
    from cite_hustle.cli import commands
    from cite_hustle.verifier import PDFVerifier

    _verified_pdf(repo, "10.1/ok", ABSTRACT)
    _verified_pdf(repo, "10.1/wrong", OTHER)  # its PDF is the ABSTRACT paper
    monkeypatch.setattr(PDFVerifier, "extract_head_text", staticmethod(lambda p, pages=2: ABSTRACT))

    def run(*args):
        return CliRunner().invoke(
            commands.verify_abstracts,
            ["--report", str(tmp_path / "r.csv"), *args],
            obj={"repo": repo},
        )

    first = run()
    assert first.exit_code == 0, first.output
    assert "match 1, mismatch 1" in first.output
    assert "10.1/wrong" in (tmp_path / "r.csv").read_text()
    assert "Checking 0 abstracts" in run().output  # nothing changed since
    repo.set_abstract("10.1/wrong", ABSTRACT)  # a corrected abstract is checked again
    again = run()
    assert "Checking 1 abstracts" in again.output and "✓ No flagged abstracts" in again.output


def test_repair_abstracts_prefers_crossref_then_cut_then_clear(repo, tmp_path):
    from cite_hustle.cli import commands

    for doi in ("10.1/cr", "10.1/cut", "10.1/clear"):
        add_article(repo, doi)
    # Stored as they were before the guards existed
    repo.conn.execute(
        "INSERT INTO ssrn_pages (doi, abstract) VALUES (?, ?), (?, ?), (?, ?)",
        [
            "10.1/cr",
            PAGE_TEXT,
            "10.1/cut",
            PAGE_TEXT,
            "10.1/clear",
            "An abstract is not available for this content so a preview has been provided.",
        ],
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "cache_0000-0000_2024.json").write_text(
        json.dumps([{"DOI": "10.1/CR", "abstract": "<jats:p>" + OTHER + "</jats:p>"}])
    )

    def run(*args):
        return CliRunner().invoke(
            commands.repair_abstracts,
            ["--cache-dir", str(cache), *args],
            obj={"repo": repo, "db": repo.db},
        )

    dry = run()
    assert dry.exit_code == 0, dry.output
    assert "clear 1, crossref 1, cut_from_page_text 1" in dry.output
    assert repo.get_ssrn_page_by_doi("10.1/cut")["abstract"] == PAGE_TEXT

    applied = run("--apply", "--backup-dir", str(tmp_path / "bk"))
    assert applied.exit_code == 0, applied.output
    assert repo.get_ssrn_page_by_doi("10.1/cr")["abstract"] == OTHER
    assert repo.get_ssrn_page_by_doi("10.1/cut")["abstract"].startswith("We examine")
    assert repo.get_ssrn_page_by_doi("10.1/clear")["abstract"] is None
    assert repo.get_junk_abstracts().empty
    assert list((tmp_path / "bk").glob("*/test.duckdb"))


def test_cleanup_and_merge_remove_abstract_checks(repo):
    for doi in ("10.1/keep", "10.2307/drop", "10.1/gone"):
        add_article(repo, doi, title="Same Title")
        repo.upsert_abstract(doi, ABSTRACT)
        repo.record_abstract_check(doi, "match", 1.0, "pdf")
    repo.delete_articles(["10.1/gone"])
    repo.merge_duplicate_articles([("10.1/keep", ["10.2307/drop"])])
    assert repo.conn.execute("SELECT COUNT(*) FROM abstract_checks").fetchone()[0] == 0
