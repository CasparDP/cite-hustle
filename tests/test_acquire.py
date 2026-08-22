"""Shared acquisition service functions."""

from unittest.mock import patch

from conftest import add_article
from selenium.common.exceptions import WebDriverException

from cite_hustle import acquire
from cite_hustle.collectors.fallback_resolvers import Candidate, ResolverError
from cite_hustle.collectors.institutional import SessionExpired


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


class FakeInstDownloader:
    def __init__(self, results):
        self.results = results  # doi -> result dict or Exception

    def acquire(self, article):
        r = self.results[article["doi"]]
        if isinstance(r, Exception):
            raise r
        return r


def _inst_result(doi, status, **kw):
    return {
        "doi": doi,
        "status": status,
        "pdf_url": kw.get("pdf_url"),
        "filepath": kw.get("filepath"),
        "error": kw.get("error"),
    }


def test_institutional_batch_records_and_counts(repo, tmp_path):
    for doi in ("10.1/a", "10.1/b"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    fake = FakeInstDownloader(
        {
            "10.1/a": _inst_result(
                "10.1/a", "downloaded", pdf_url="https://p/x.pdf", filepath=str(tmp_path / "a.pdf")
            ),
            "10.1/b": _inst_result("10.1/b", "no_pdf_link", error="paywall"),
        }
    )
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert counts["downloaded"] == 1 and counts["no_match"] == 1 and not counts["aborted"]
    assert (
        repo.conn.execute("SELECT source FROM pdf_files WHERE doi='10.1/a'").fetchone()[0]
        == "ezproxy"
    )


def test_institutional_batch_aborts_on_session_expired(repo, tmp_path):
    for doi in ("10.1/c", "10.1/d"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    first = articles.iloc[0]["doi"]
    fake = FakeInstDownloader({d: SessionExpired("login") for d in articles["doi"]})
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert counts["aborted"] is True
    rows = repo.conn.execute(
        "SELECT doi, status, error_message FROM pdf_candidates WHERE source='ezproxy'"
    ).fetchall()
    assert rows == [(first, "error", "session_expired")]  # only the first article touched


class FakeWebDriverErrorOnceDownloader:
    """Fake whose acquire() raises WebDriverException once, then succeeds."""

    def __init__(self, results):
        self.results = results  # doi -> result dict, or "raise" sentinel
        self.quit_calls = 0
        self.setup_calls = 0
        self._raised = False

    def acquire(self, article):
        r = self.results[article["doi"]]
        if r == "raise" and not self._raised:
            self._raised = True
            raise WebDriverException("boom")
        return r

    def quit(self):
        self.quit_calls += 1

    def setup_webdriver(self):
        self.setup_calls += 1


def test_institutional_batch_rebuilds_driver_on_webdriver_exception(repo, tmp_path):
    for doi in ("10.1/e", "10.1/f"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    second_doi = articles.iloc[1]["doi"]
    fake = FakeWebDriverErrorOnceDownloader(
        {
            articles.iloc[0]["doi"]: "raise",
            second_doi: _inst_result(second_doi, "no_pdf_link", error="paywall"),
        }
    )
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert not counts["aborted"]
    assert counts["error"] == 1 and counts["no_match"] == 1
    assert fake.quit_calls == 1 and fake.setup_calls == 1
