"""Shared acquisition service functions."""

from unittest.mock import patch

import httpx
from conftest import add_article
from selenium.common.exceptions import WebDriverException

from cite_hustle import acquire
from cite_hustle import requests_queue as rq
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

    def quit(self):
        pass


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
    assert counts["abort_reason"] == "session_expired"
    assert counts["error"] == 1
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


class FakeWebDriverRebuildFailsDownloader:
    """Fake whose acquire() always raises WebDriverException and whose
    setup_webdriver() (the rebuild attempt) also fails."""

    def __init__(self, results):
        self.results = results  # doi -> "raise" sentinel

    def acquire(self, article):
        assert self.results[article["doi"]] == "raise"
        raise WebDriverException("boom")

    def quit(self):
        pass

    def setup_webdriver(self):
        raise RuntimeError("chromedriver missing")


def test_institutional_batch_aborts_on_webdriver_rebuild_failure(repo, tmp_path):
    for doi in ("10.1/g", "10.1/h"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    fake = FakeWebDriverRebuildFailsDownloader({d: "raise" for d in articles["doi"]})
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert counts["aborted"] is True
    assert counts["abort_reason"] == "webdriver"
    assert counts["error"] == 1  # only the first article's failed acquire() counted


def test_acquire_one_already_have(repo, tmp_path):
    add_article(repo, "10.1/have")
    repo.upsert_pdf_file("10.1/have", "oa", None, "http://x.pdf", str(tmp_path / "h.pdf"))
    out = acquire.acquire_one(repo, "10.1/have", use_institutional=False, run_verify=False)
    assert out["status"] == "already_have"
    assert out["source"] == "oa"


def test_acquire_one_normalizes_doi_case_and_whitespace(repo, tmp_path, monkeypatch):
    add_article(repo, "10.1/case")
    repo.upsert_pdf_file("10.1/case", "oa", None, "http://x.pdf", str(tmp_path / "h.pdf"))

    def fail_if_called(doi):
        raise AssertionError("fetch_crossref_article should not be called")

    monkeypatch.setattr(acquire, "fetch_crossref_article", fail_if_called)
    out = acquire.acquire_one(repo, " 10.1/CASE ", use_institutional=False, run_verify=False)
    assert out["status"] == "already_have"
    assert out["doi"] == "10.1/case"


def test_acquire_one_unknown_doi_fetches_metadata(repo, monkeypatch):
    fetched = {
        "doi": "10.1/new",
        "title": "T",
        "authors": "A B",
        "year": 2024,
        "journal_issn": "1234-5678",
        "journal_name": "J",
        "publisher": "P",
    }
    monkeypatch.setattr(acquire, "fetch_crossref_article", lambda doi: fetched)
    monkeypatch.setattr(acquire, "try_sources_for_article", lambda *a, **k: None)
    out = acquire.acquire_one(repo, "10.1/new", use_institutional=False, run_verify=False)
    assert out["status"] == "no_source"
    assert repo.get_article_by_doi("10.1/new")["title"] == "T"


def test_acquire_one_metadata_not_found(repo, monkeypatch):
    monkeypatch.setattr(acquire, "fetch_crossref_article", lambda doi: None)
    out = acquire.acquire_one(repo, "10.1/ghost", use_institutional=False, run_verify=False)
    assert out["status"] == "metadata_not_found"


def test_acquire_one_session_expired(repo, monkeypatch):
    add_article(repo, "10.1/expired")
    monkeypatch.setattr(acquire, "try_sources_for_article", lambda *a, **k: None)
    fake = FakeInstDownloader({"10.1/expired": SessionExpired("login")})
    out = acquire.acquire_one(
        repo, "10.1/expired", downloader_factory=lambda: fake, run_verify=False
    )
    assert out["status"] == "session_expired"


class FakeQuarantiningVerifier:
    """Stands in for PDFVerifier: verify_batch quarantines (drops the row)."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def verify_batch(self, rows):
        for doi in rows["doi"]:
            self.kwargs["repo"].delete_pdf_file(doi)
        return {"match": 0, "mismatch": len(rows), "uncertain": 0, "unreadable": 0}


def test_acquire_one_reports_mismatch_when_verifier_quarantines(repo, tmp_path, monkeypatch):
    add_article(repo, "10.1/bad")
    monkeypatch.setattr(acquire.settings, "dropbox_base", tmp_path)

    def fake_try_sources(repo_, article, *a, **k):
        repo_.upsert_pdf_file(
            article["doi"], "oa", None, "http://x/y.pdf", str(tmp_path / "bad.pdf")
        )
        return "oa"

    monkeypatch.setattr(acquire, "try_sources_for_article", fake_try_sources)
    monkeypatch.setattr("cite_hustle.verifier.PDFVerifier", FakeQuarantiningVerifier)

    out = acquire.acquire_one(repo, "10.1/bad", use_institutional=False, run_verify=True)
    assert out["status"] == "downloaded"
    assert out["verify_status"] == "mismatch"
    assert out["path"] is None
    assert out["detail"] is None  # the verification step did not error out


def test_acquire_one_webdriver_abort_is_browser_error(repo, monkeypatch):
    add_article(repo, "10.1/browserdead")
    monkeypatch.setattr(acquire, "try_sources_for_article", lambda *a, **k: None)
    fake = FakeWebDriverRebuildFailsDownloader({"10.1/browserdead": "raise"})
    out = acquire.acquire_one(
        repo, "10.1/browserdead", downloader_factory=lambda: fake, run_verify=False
    )
    assert out["status"] == "browser_error"
    assert "webdriver" in out["detail"]


def test_acquire_one_factory_raises_webdriver_exception_is_browser_error(repo, monkeypatch):
    add_article(repo, "10.1/nofactory")
    monkeypatch.setattr(acquire, "try_sources_for_article", lambda *a, **k: None)

    def bad_factory():
        raise WebDriverException("chromedriver mismatch")

    out = acquire.acquire_one(
        repo, "10.1/nofactory", downloader_factory=bad_factory, run_verify=False
    )
    assert out["status"] == "browser_error"
    assert "chromedriver mismatch" in out["detail"]


def _crossref_response(status, payload):
    """A canned httpx.Response as acquire.httpx.get would return it."""
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "https://api.crossref.org/works/10.1/x")
    )


def test_fetch_crossref_article_404_returns_none(monkeypatch):
    monkeypatch.setattr(acquire.httpx, "get", lambda *a, **k: _crossref_response(404, {}))
    assert acquire.fetch_crossref_article("10.1/ghost") is None


def test_fetch_crossref_article_maps_fields(monkeypatch):
    payload = {
        "message": {
            "title": ["Earnings <i>Management</i> &amp; Audit Quality"],
            "author": [
                {"given": "Alice", "family": "Smith"},
                {"given": "Bob", "family": "Jones"},
            ],
            "issued": {"date-parts": [[2024, 5, 1]]},
            "ISSN": ["1475-679X", "0021-8456"],
            "container-title": ["Journal of Accounting Research", "JAR"],
            "publisher": "Wiley",
        }
    }
    monkeypatch.setattr(acquire.httpx, "get", lambda *a, **k: _crossref_response(200, payload))
    assert acquire.fetch_crossref_article("10.1/x") == {
        "doi": "10.1/x",
        "title": "Earnings Management & Audit Quality",
        "authors": "Alice Smith; Bob Jones",
        "year": 2024,
        "journal_issn": "1475-679X",
        "journal_name": "Journal of Accounting Research",
        "publisher": "Wiley",
    }


def test_fetch_crossref_article_null_date_parts_returns_none(monkeypatch):
    payload = {"message": {"title": ["T"], "issued": {"date-parts": [[None]]}}}
    monkeypatch.setattr(acquire.httpx, "get", lambda *a, **k: _crossref_response(200, payload))
    assert acquire.fetch_crossref_article("10.1/x") is None


def test_fetch_crossref_article_missing_title_returns_none(monkeypatch):
    payload = {"message": {"issued": {"date-parts": [[2024]]}}, "publisher": "P"}
    monkeypatch.setattr(acquire.httpx, "get", lambda *a, **k: _crossref_response(200, payload))
    assert acquire.fetch_crossref_article("10.1/x") is None


def test_drain_requests_drops_resolved_keeps_failed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")
    rq.append_request("10.1/ok")
    rq.append_request("10.1/miss")

    def fake_acquire(repo_, doi):
        status = "downloaded" if doi == "10.1/ok" else "no_source"
        return {
            "doi": doi,
            "status": status,
            "source": None,
            "path": None,
            "verify_status": None,
            "detail": None,
        }

    counts = acquire.drain_requests(repo, acquire_fn=fake_acquire)
    assert counts["resolved"] == 1 and counts["kept"] == 1
    remaining = rq.read_requests()
    assert [e["doi"] for e in remaining] == ["10.1/miss"]
    assert remaining[0]["attempts"] == 1


def _drain_result(doi, status):
    return {
        "doi": doi,
        "status": status,
        "source": None,
        "path": None,
        "verify_status": None,
        "detail": None,
    }


def test_drain_requests_session_expired_leaves_queue_untouched(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")
    rq.append_request("10.1/a")
    rq.append_request("10.1/b")
    rq.append_request("10.1/c")
    before = (tmp_path / "requests.jsonl").read_text()

    def fake_acquire(repo_, doi):
        status = "no_source" if doi == "10.1/a" else "session_expired"
        return _drain_result(doi, status)

    counts = acquire.drain_requests(repo, acquire_fn=fake_acquire)
    assert counts == {
        "resolved": 0,
        "kept": 1,
        "dropped": 0,
        "session_expired": True,
        "halted_reason": "session_expired",
    }

    remaining = rq.read_requests()
    assert [e["doi"] for e in remaining] == ["10.1/a", "10.1/b", "10.1/c"]
    assert remaining[0]["attempts"] == 1  # processed before the expiry, incremented
    assert remaining[1]["attempts"] == 0  # the session_expired entry itself: untouched
    assert remaining[2]["attempts"] == 0  # never reached: untouched

    after = (tmp_path / "requests.jsonl").read_text()
    after_from_b = "\n".join(after.splitlines()[1:])
    before_from_b = "\n".join(before.splitlines()[1:])
    assert after_from_b == before_from_b  # b and c rewritten byte-identical to the original


def test_drain_requests_browser_error_leaves_queue_untouched(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")
    rq.append_request("10.1/a")
    rq.append_request("10.1/b")
    before = (tmp_path / "requests.jsonl").read_bytes()

    def fake_acquire(repo_, doi):
        return _drain_result(doi, "browser_error")

    counts = acquire.drain_requests(repo, acquire_fn=fake_acquire)
    assert counts == {
        "resolved": 0,
        "kept": 0,
        "dropped": 0,
        "session_expired": False,
        "halted_reason": "browser_error",
    }

    after = (tmp_path / "requests.jsonl").read_bytes()
    assert after == before  # queue untouched, no attempts bumped

    remaining = rq.read_requests()
    assert [e["doi"] for e in remaining] == ["10.1/a", "10.1/b"]
    assert all(e["attempts"] == 0 for e in remaining)


def test_drain_requests_gives_up_after_3_attempts(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")
    rq.append_request("10.1/stuck")
    entries = rq.read_requests()
    entries[0]["attempts"] = 2
    rq.write_requests(entries)

    counts = acquire.drain_requests(
        repo, acquire_fn=lambda repo_, doi: _drain_result(doi, "no_source")
    )
    assert counts == {
        "resolved": 0,
        "kept": 0,
        "dropped": 1,
        "session_expired": False,
        "halted_reason": None,
    }
    assert rq.read_requests() == []
    logged = repo.conn.execute(
        "SELECT stage, status, error_message FROM processing_log WHERE doi = '10.1/stuck'"
    ).fetchall()
    assert logged == [("request", "failed", "gave_up_after_3")]
