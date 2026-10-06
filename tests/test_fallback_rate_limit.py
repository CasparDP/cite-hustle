"""resolve-fallbacks: rate-limit streak stops the run, batches skip checked articles."""

import httpx
from click.testing import CliRunner

from cite_hustle.collectors import fallback_resolvers as fr
from conftest import add_article


def _client(responses):
    it = iter(responses)
    return httpx.Client(transport=httpx.MockTransport(lambda request: next(it)))


def test_exhausted_429_raises_rate_limited_and_counts_streak(monkeypatch):
    monkeypatch.setattr(fr.time, "sleep", lambda s: None)
    resolver = fr.NBERResolver()
    client = _client([httpx.Response(429)] * resolver.MAX_RETRIES)
    try:
        resolver._get(client, "https://example.org")
        raise AssertionError("expected ResolverError")
    except fr.ResolverError as exc:
        assert str(exc) == "rate_limited"
    assert resolver.rate_limited_streak == 1
    resolver._get(_client([httpx.Response(429), httpx.Response(200)]), "https://example.org")
    assert resolver.rate_limited_streak == 0  # any real answer resets it


def test_openalex_lookup_sends_api_key(monkeypatch):
    monkeypatch.setattr(fr.settings, "openalex_api_key", "KEY")
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fr.OAResolver().resolve(client, {"doi": "10.1/x"}) is None
    assert seen.get("api_key") == "KEY"


class FakeResolver:
    calls = []

    def __init__(self, threshold=90.0, outcome=None):
        self.outcome = outcome
        self.rate_limited_streak = 0

    def resolve(self, client, article):
        FakeResolver.calls.append(article["doi"])
        if self.outcome == "rate_limited":
            self.rate_limited_streak += 1
            raise fr.ResolverError("rate_limited")
        return None


def _run(repo, monkeypatch, outcome, *args):
    from cite_hustle.cli import commands

    FakeResolver.calls = []
    monkeypatch.setitem(fr.RESOLVERS, "oa", lambda threshold: FakeResolver(threshold, outcome))
    return CliRunner().invoke(
        commands.resolve_fallbacks, ["--sources", "oa", "--delay", "0", *args], obj={"repo": repo}
    )


def test_batch_skips_recently_checked_articles(repo, monkeypatch):
    for i, year in enumerate([2026, 2025, 2024]):
        add_article(repo, f"10.1/{i}", title=f"Paper {i}", year=year)
    repo.record_pdf_candidate("10.1/0", "oa", status="no_match")  # checked: skipped
    result = _run(repo, monkeypatch, None, "--limit", "1")
    assert result.exit_code == 0, result.output
    assert FakeResolver.calls == ["10.1/1"]
    assert "1 of 2 pending" in result.output


def test_rate_limit_streak_stops_the_run(repo, monkeypatch):
    for i in range(6):
        add_article(repo, f"10.1/{i}", title=f"Paper {i}")
    result = _run(repo, monkeypatch, "rate_limited", "--max-rate-limited", "3")
    assert result.exit_code == 0, result.output
    assert len(FakeResolver.calls) == 3
    assert "Stopped: oa rate-limited 3 articles in a row" in result.output
