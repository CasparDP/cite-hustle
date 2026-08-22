"""Repository methods for per-DOI acquisition."""

from datetime import datetime, timedelta

from conftest import add_article


def test_get_article_by_doi_returns_row(repo):
    add_article(repo, "10.1111/test.1")
    row = repo.get_article_by_doi("10.1111/test.1")
    assert row["doi"] == "10.1111/test.1"
    assert row["title"] == "Some Paper Title"
    assert row["year"] == 2024


def test_get_article_by_doi_missing_returns_none(repo):
    assert repo.get_article_by_doi("10.9999/nope") is None


def test_recheck_windows_distinguish_error_from_no_match(repo):
    add_article(repo, "10.1/a")
    add_article(repo, "10.1/b")
    repo.record_pdf_candidate("10.1/a", "oa", status="no_match")
    repo.record_pdf_candidate("10.1/b", "oa", status="error", error_message="http_503")

    long_cutoff = datetime.now() - timedelta(days=90)
    # error rows use a cutoff in the future -> nothing is "recent" for errors
    future = datetime.now() + timedelta(days=1)

    suppressed = repo.get_recent_candidate_checks(long_cutoff, future)
    assert ("10.1/a", "oa") in suppressed  # no_match still memoized
    assert ("10.1/b", "oa") not in suppressed  # error eligible for retry

    both_recent = repo.get_recent_candidate_checks(long_cutoff, long_cutoff)
    assert ("10.1/b", "oa") in both_recent
