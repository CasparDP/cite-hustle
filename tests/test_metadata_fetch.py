"""CrossRef cursor pagination: last page without next-cursor, and 429 backoff."""

import httpx

from cite_hustle.collectors import metadata
from cite_hustle.collectors.metadata import MetadataCollector


def _page(items, cursor=None):
    message = {"items": items}
    if cursor:
        message["next-cursor"] = cursor
    return httpx.Response(200, json={"message": message})


def test_last_page_without_next_cursor_returns_all_items(repo, tmp_path):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        if request.url.params["cursor"] == "*":
            return _page([{"DOI": "10.1/a"}, {"DOI": "10.1/b"}], cursor="c2")
        # CrossRef omits next-cursor on the final page; crossref-commons crashed here.
        return _page([])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    collector = MetadataCollector(repo, cache_dir=tmp_path, http_client=client)

    articles = collector.fetch_articles_by_issn(2026, "0000-0000")

    assert [a["DOI"] for a in articles] == ["10.1/a", "10.1/b"]
    assert calls[0]["filter"] == "issn:0000-0000,from-pub-date:2026-01-01,until-pub-date:2026-12-31"
    assert (tmp_path / "cache_0000-0000_2026.json").exists()


def test_429_is_retried_after_backoff(repo, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(metadata.time, "sleep", sleeps.append)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}),
            _page([{"DOI": "10.1/a"}]),
        ]
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))
    collector = MetadataCollector(repo, cache_dir=tmp_path, http_client=client)

    articles = collector.fetch_articles_by_issn(2026, "0000-0000")

    assert [a["DOI"] for a in articles] == ["10.1/a"]
    assert sleeps == [3.0]


def test_persistent_failure_logs_and_skips_cache(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.time, "sleep", lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    collector = MetadataCollector(repo, cache_dir=tmp_path, http_client=client)

    assert collector.fetch_articles_by_issn(2026, "0000-0000") == []
    assert not (tmp_path / "cache_0000-0000_2026.json").exists()
    logged = repo.conn.execute(
        "SELECT status FROM processing_log WHERE stage = 'metadata_fetch'"
    ).fetchall()
    assert logged == [("failed",)]
