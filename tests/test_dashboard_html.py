"""HTML dashboard: data query and self-contained rendering."""

import json
import re

from cite_hustle.dashboard_html import build_payload, render
from conftest import add_article


def test_dashboard_flags_front_matter_and_renders_offline(repo, tmp_path):
    add_article(repo, "10.1/real", title="A Real Study of Audit Fees</script><b>", year=2025)
    for i in range(3):
        add_article(repo, f"10.1/mast{i}", title="American Finance Association", year=2020 + i)
    add_article(repo, "10.1111/joar.v63.4", title="Issue Information", year=2025)
    repo.upsert_abstract("10.1/real", "Some abstract text.")

    payload = build_payload(repo, tmp_path / "x.duckdb")
    rows = {(r["year"], r["n"], r["n_suspect"], r["n_abstract"]) for r in payload["journal_years"]}
    assert (2025, 2, 1, 1) in rows  # the real study plus the issue-level DOI
    assert sum(r["n_suspect"] for r in payload["journal_years"]) == 4

    page = render(payload)
    # No external assets: no script/stylesheet/font loads
    assert not re.search(r"<script[^>]+src=|<link[^>]+href=|@import|url\(http", page)
    blob = re.search(r'<script id="payload" type="application/json">(.*?)</script>', page, re.S)
    assert json.loads(blob.group(1))["recent_articles"]  # title with </script> did not break out
