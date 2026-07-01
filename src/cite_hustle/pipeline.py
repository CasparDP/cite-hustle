"""Pipeline orchestration: profiles, preflight guards, and run reports.

The `cite-hustle pipeline` command chains the existing subcommands in-process
(via Click's ctx.invoke) so all stages share one write connection. Each stage
is recorded in pipeline_runs; a failed stage is logged and the pipeline moves
on (except collect, whose output later stages depend on).
"""

from __future__ import annotations

import fcntl
import json
from datetime import datetime
from pathlib import Path

import click

from cite_hustle.database.repository import ArticleRepository

# Stage order per profile. Names map to CLI subcommands in cli/commands.py.
PROFILES = {
    "monthly": [
        "collect",
        "scrape",
        "enrich",
        "download",
        "fallbacks",
        "verify",
        "ingest",
        "index",
        "fts",
    ],
    "incremental": ["scrape", "download", "fallbacks", "verify", "ingest", "index", "fts"],
}

# Stages whose failure aborts the run (later stages depend on their output)
ABORT_ON_FAILURE = {"collect"}

LOCK_PATH = Path.home() / ".cache" / "cite-hustle" / "pipeline.lock"


def preflight_guards(db_path: Path) -> None:
    """Refuse to open the DB read-write in states that risk corruption.

    Called before the write connection is opened. Raises ClickException.
    """
    conflicted = list(db_path.parent.glob("*conflicted copy*"))
    if conflicted:
        raise click.ClickException(
            "Dropbox conflicted copy of the database found:\n  "
            + "\n  ".join(str(p) for p in conflicted)
            + "\nThe single-writer discipline was violated. Resolve the conflict "
            "(keep one copy, delete the other) before running the pipeline."
        )

    wal = Path(str(db_path) + ".wal")
    if wal.exists():
        raise click.ClickException(
            f"Write-ahead log present: {wal}\n"
            "Either another machine is mid-write (wait for Dropbox to settle) or "
            "a previous writer crashed. If you are sure no other machine is "
            "writing, run 'poetry run cite-hustle status' once on the machine "
            "that crashed to let DuckDB recover, then retry."
        )


def acquire_pipeline_lock():
    """Non-blocking exclusive lockfile (NOT on Dropbox). Returns the handle.

    Raises ClickException if another pipeline run holds the lock.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise click.ClickException(
            "Another pipeline run is already in progress (lock held at "
            f"{LOCK_PATH}). Wait for it to finish or stop it first."
        )
    return handle


def write_run_report(repo: ArticleRepository, reports_dir: Path, run_id: str) -> Path:
    """Render the run's stages and end-state statistics to markdown."""
    stages = repo.get_pipeline_run_stages(run_id)
    stats = repo.get_statistics()

    lines = [f"# Pipeline run {run_id}\n\n", "## Stages\n\n"]
    lines.append("| Stage | Status | Started | Finished | Detail |\n")
    lines.append("|---|---|---|---|---|\n")
    for s in stages:
        detail = (s["detail"] or "").replace("|", "\\|")
        lines.append(
            f"| {s['stage']} | {s['status']} | {s['started_at']} | "
            f"{s['finished_at'] or ''} | {detail} |\n"
        )

    lines.append("\n## Attention needed\n\n")
    attention = repo.get_run_attention_items(run_id)
    if attention:
        for item in attention:
            lines.append(
                f"- `{item['doi']}` — {item['stage']}: {item['status']} "
                f"({item['error_message'] or 'no detail'})\n"
            )
    else:
        lines.append("_Nothing quarantined or flagged in this run._\n")

    lines.append("\n## Database state\n\n")
    for key in (
        "total_articles",
        "ssrn_scraped",
        "pdfs_by_source",
        "pdfs_by_verify_status",
        "pdfs_quarantined",
        "wiki_ingested",
    ):
        lines.append(f"- **{key}**: {stats.get(key)}\n")

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"run-{run_id}.md"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def make_run_id(profile: str) -> str:
    return datetime.now().strftime("%Y-%m-%dT%H%M") + f"-{profile}"


def stage_detail(**kwargs) -> str:
    """JSON stats blob for pipeline_runs.detail."""
    return json.dumps(kwargs, default=str)
