"""Dropbox-synced request queue.

Lets any machine (including read-only ones) queue a DOI for acquisition by
the runner laptop. The queue is a JSON-lines file synced via Dropbox; the
runner drains it as the first stage of its pipeline.
"""

import json
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional

from cite_hustle.config import settings


def queue_path() -> Path:
    """Path to the requests queue file (no directory creation as a side effect)."""
    return settings.dropbox_base / "requests.jsonl"


def read_requests() -> list[dict]:
    """Read all queued requests. Returns [] if the queue file doesn't exist."""
    path = queue_path()
    if not path.exists():
        return []
    with open(path, "r") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_requests(entries: list[dict]) -> None:
    """Rewrite the queue file atomically (write to .tmp, then os.replace)."""
    path = queue_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    os.replace(tmp_path, path)


def append_request(doi: str, note: Optional[str] = None) -> bool:
    """Queue a DOI for acquisition. Returns False if it's already queued."""
    doi = doi.strip().lower()
    entries = read_requests()
    if any(e["doi"].strip().lower() == doi for e in entries):
        return False
    entries.append(
        {
            "doi": doi,
            "requested_at": datetime.now().isoformat(),
            "machine": platform.node(),
            "note": note,
            "attempts": 0,
        }
    )
    write_requests(entries)
    return True
