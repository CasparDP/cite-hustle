"""Citation year rule, year refresh on re-collect, front-matter filter and cleanup."""

from click.testing import CliRunner

from cite_hustle.collectors.journals import Journal
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.front_matter import is_front_matter
from conftest import add_article


def _item(doi, title, **dates):
    item = {"DOI": doi, "type": "journal-article", "title": [title]}
    for key, year in dates.items():
        item[key.replace("_", "-")] = {"date-parts": [[year, 1]]}
    return item


def test_citation_year_prefers_print_over_online_first():
    year = MetadataCollector.citation_year
    assert year(_item("d", "t", issued=2025, published_print=2026)) == 2026
    issue = {
        "issued": {"date-parts": [[2024]]},
        "journal-issue": {"published-print": {"date-parts": [[2025]]}},
    }
    assert year(issue) == 2025
    assert year(_item("d", "t", issued=2025)) == 2025  # online only: forthcoming


def test_recollect_moves_article_to_its_print_year(repo, tmp_path):
    journal = Journal("J", "0000-0000", "finance", "P")
    collector = MetadataCollector(repo, cache_dir=tmp_path)
    online_only = _item("10.1/x", "A Real Paper", issued=2025)
    repo.bulk_insert_articles(collector.transform_articles([online_only], journal))
    assert repo.get_article_by_doi("10.1/x")["year"] == 2025
    # A referencing row: DuckDB rejected the year update while year was indexed
    repo.insert_ssrn_page("10.1/x", "https://ssrn.com/abstract=1", None, "x.html", "abs", 95)

    in_issue = _item("10.1/x", "A Real Paper", issued=2025, published_print=2026)
    repo.bulk_insert_articles(collector.transform_articles([in_issue], journal))
    assert repo.get_article_by_doi("10.1/x")["year"] == 2026


def test_front_matter_rule_is_whole_title_and_keeps_scholarly_items():
    for title in [
        "No Title Available",
        "MISCELLANEA",
        "Report of the Treasurer",
        "Call for Papers—Special Issue of Management Science: Marketing",
        "Journal of Political Economy",
        "Minutes of the Annual Meeting Boston, MA",
        "Editorial Statement—Finance",
        "Sherwin Rosen Prize",
        "Errata.",
    ]:
        assert is_front_matter(title), title
    for title in [
        "Discussion",
        "Comment",
        "Reply",
        "Correspondence",
        "Introduction",
        "Notes on the Theory of Auditing",
        "The Journal of Economic Perspectives and the Marketplace of Ideas",
        "Calls for Papers and the Market for Ideas",
        "Earnings Management",
    ]:
        assert not is_front_matter(title), title
    assert is_front_matter("Some Title", "10.1111/joar.v63.4")
    assert is_front_matter("Some Title", "10.1111/care.2016.33.issue-1")
    assert not is_front_matter("Some Title", "10.1111/1475-679X.12345")


def test_cleanup_dry_run_then_apply_keeps_pdfs_and_backs_up(repo, tmp_path):
    from cite_hustle.cli import commands

    add_article(repo, "10.1/real", title="Audit Fees and Firm Value")
    add_article(repo, "10.1/mast", title="American Finance Association")
    add_article(repo, "10.1/pdfmast", title="Forthcoming Papers")
    repo.upsert_abstract("10.1/mast", "x" * 120)
    repo.log_processing("10.1/mast", "scrape_ssrn", "no_match")
    repo.upsert_pdf_file(
        doi="10.1/pdfmast", source="oa", source_url=None, pdf_url=None, pdf_file_path="/tmp/x.pdf"
    )

    obj = {"repo": repo, "db": repo.db}
    run = lambda *args: CliRunner().invoke(commands.cleanup_non_articles, list(args), obj=obj)

    dry = run()
    assert dry.exit_code == 0, dry.output
    assert "2 (1 deletable)" in dry.output and "kept (has PDF/wiki" in dry.output
    assert repo.get_article_by_doi("10.1/mast") is not None

    applied = run("--apply", "--backup-dir", str(tmp_path / "bk"))
    assert applied.exit_code == 0, applied.output
    assert repo.get_article_by_doi("10.1/mast") is None
    assert repo.get_ssrn_page_by_doi("10.1/mast") is None
    assert repo.get_article_by_doi("10.1/pdfmast") is not None  # has a PDF: kept
    assert repo.get_article_by_doi("10.1/real") is not None
    assert list((tmp_path / "bk").glob("*/test.duckdb"))


def test_delete_articles_refuses_rows_with_pdfs(repo):
    add_article(repo, "10.1/a", title="Forthcoming Papers")
    repo.upsert_pdf_file(
        doi="10.1/a", source="oa", source_url=None, pdf_url=None, pdf_file_path="/tmp/a.pdf"
    )
    assert repo.delete_articles(["10.1/a"])["articles"] == 0
    assert repo.get_article_by_doi("10.1/a") is not None


def test_open_ended_patterns_do_not_swallow_research_titles():
    for title in [
        "Committee on Banking Theory and Practice",
        "Minutes of Monetary Policy Meetings and Asset Prices",
        "Index to the Literature on Corporate Governance",
    ]:
        assert not is_front_matter(title), title
    for title in [
        "Committee on the Status of Women in the Economics Profession",
        "Minutes of the 2014 Annual Membership Meeting",
        "Index to Journal of Management",
    ]:
        assert is_front_matter(title), title
