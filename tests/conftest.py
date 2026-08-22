"""Shared fixtures: a fresh DuckDB-backed repository per test."""

import pytest

from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


@pytest.fixture()
def repo(tmp_path):
    db = DatabaseManager(tmp_path / "test.duckdb")
    db.connect()
    db.initialize_schema()
    yield ArticleRepository(db)
    db.close()


def add_article(
    repo,
    doi,
    title="Some Paper Title",
    authors="Alice Smith; Bob Jones",
    year=2024,
):
    repo.insert_article(doi, title, authors, year, "0000-0000", "Test Journal", "TestPub")
