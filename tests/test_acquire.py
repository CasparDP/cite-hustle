"""Shared acquisition service functions."""

from unittest.mock import patch

from conftest import add_article

from cite_hustle import acquire
from cite_hustle.collectors.fallback_resolvers import Candidate, ResolverError


class FakeResolver:
    def __init__(self, outcome):
        self.outcome = outcome

    def resolve(self, client, article):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_try_sources_first_hit_wins_and_records(repo, tmp_path):
    add_article(repo, "10.1/x")
    article = repo.get_article_by_doi("10.1/x")
    cand = Candidate(
        source="nber", candidate_url="http://n/1", pdf_url="http://n/1.pdf", match_score=95.0
    )
    resolvers = {"oa": FakeResolver(None), "nber": FakeResolver(cand)}
    with patch.object(acquire, "download_pdf", return_value=(True, None)):
        won = acquire.try_sources_for_article(
            repo, article, ["oa", "nber"], resolvers, client=None, pdf_dir=tmp_path
        )
    assert won == "nber"
    row = repo.conn.execute(
        "SELECT source, verify_status FROM pdf_files WHERE doi = '10.1/x'"
    ).fetchone()
    assert row == ("nber", "pending")
    statuses = dict(
        repo.conn.execute("SELECT source, status FROM pdf_candidates WHERE doi='10.1/x'").fetchall()
    )
    assert statuses == {"oa": "no_match", "nber": "downloaded"}


def test_try_sources_resolver_error_recorded_and_continues(repo, tmp_path):
    add_article(repo, "10.1/y")
    article = repo.get_article_by_doi("10.1/y")
    resolvers = {"oa": FakeResolver(ResolverError("http_503"))}
    won = acquire.try_sources_for_article(
        repo, article, ["oa"], resolvers, client=None, pdf_dir=tmp_path
    )
    assert won is None
    status = repo.conn.execute(
        "SELECT status FROM pdf_candidates WHERE doi='10.1/y' AND source='oa'"
    ).fetchone()[0]
    assert status == "error"
