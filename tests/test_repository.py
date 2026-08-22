"""Repository methods for per-DOI acquisition."""

from conftest import add_article


def test_get_article_by_doi_returns_row(repo):
    add_article(repo, "10.1111/test.1")
    row = repo.get_article_by_doi("10.1111/test.1")
    assert row["doi"] == "10.1111/test.1"
    assert row["title"] == "Some Paper Title"
    assert row["year"] == 2024


def test_get_article_by_doi_missing_returns_none(repo):
    assert repo.get_article_by_doi("10.9999/nope") is None
