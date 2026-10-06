"""OpenAlex enrichment: 50 DOIs per request, stop on a rate-limit streak, skip answered DOIs."""

from click.testing import CliRunner

from cite_hustle.collectors import abstract_sources
from cite_hustle.collectors.fallback_resolvers import ResolverError
from cite_hustle.collectors.openalex_enricher import OpenAlexEnricher
from conftest import add_article

ABSTRACT = (
    "We study how audit fees respond to litigation risk using a large panel of public firms "
    "and find that auditors price expected legal exposure in their engagement fees."
)


class FakeSource:
    BATCH_SIZE = 50

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def fetch(self, dois):
        self.calls.append(list(dois))
        if self.fail_with:
            raise ResolverError(self.fail_with)
        found = {d: None for d in dois}
        for d in dois:
            if d.endswith("0"):
                found[d] = ABSTRACT
            elif d.endswith("1"):
                found[d] = "Jane Roe is a Professor of Accounting at Example University."
        return found


def _articles(repo, n):
    for i in range(n):
        add_article(repo, f"10.1/{i}", title="Audit Fees and Litigation Risk")
    return repo.get_articles_missing_abstract().to_dict("records")


def test_batches_of_50_and_no_biographies(repo):
    source = FakeSource()
    rows = _articles(repo, 120) + [{"doi": "", "title": "x"}]
    stats = OpenAlexEnricher(
        repo, source=source, delay_s=0, log=lambda m: None
    ).enrich_missing_abstracts(rows)
    assert [len(c) for c in source.calls] == [50, 50, 20]
    assert stats["updated"] == 12 and stats["no_abstract"] == 108 and stats["invalid_doi"] == 1
    assert repo.get_ssrn_page_by_doi("10.1/10")["abstract"] == ABSTRACT
    assert repo.get_ssrn_page_by_doi("10.1/11") is None  # a biography is not an abstract


def test_rate_limit_streak_stops_without_logging(repo):
    source = FakeSource(fail_with="http_429 after 5 attempts")
    messages = []
    rows = _articles(repo, 260)
    stats = OpenAlexEnricher(
        repo, source=source, delay_s=0, log=messages.append
    ).enrich_missing_abstracts(rows)
    assert len(source.calls) == 3 and stats.get("stopped")
    assert any("rate-limited 3 batches in a row" in m for m in messages)
    logged = repo.conn.execute(
        "SELECT COUNT(*) FROM processing_log WHERE stage = 'enrich_openalex'"
    ).fetchone()[0]
    assert logged == 0  # retried next run


def test_cli_skips_dois_openalex_already_answered(repo, monkeypatch):
    from cite_hustle.cli import commands

    source = FakeSource()
    monkeypatch.setattr(abstract_sources, "OpenAlexBatchSource", lambda: source)
    _articles(repo, 3)

    def run():
        return CliRunner().invoke(
            commands.enrich_openalex,
            ["--delay", "0", "--skip-fts-rebuild"],
            obj={"repo": repo, "db": repo.db},
        )

    first = run()
    assert first.exit_code == 0, first.output
    assert "Updated: 1" in first.output
    assert "No articles missing abstracts" in run().output  # the rest got no_match
