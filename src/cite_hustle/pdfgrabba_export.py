"""One-way export of terminal Elsevier residuals to a pdfgrabba manifest."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename
from cite_hustle.wiki.bridge import make_bib_key

DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
IMPORT_NOTE = "Imported from cite-hustle terminal Elsevier residuals"


class PdfgrabbaExportError(ValueError):
    """Raised when a manifest cannot be merged safely."""


@dataclass(frozen=True)
class ExportSummary:
    """Counts from a manifest merge."""

    eligible: int
    existing: int
    already_present: int
    added: int
    final: int
    created: bool
    dry_run: bool


def normalize_doi(value: object) -> str:
    """Return the case-folded bare DOI used for deduplication."""
    if value is None:
        return ""
    normalized = str(value).strip()
    previous = None
    while normalized != previous:
        previous = normalized
        normalized = DOI_PREFIX.sub("", normalized).strip()
    return normalized.lower()


def export_to_pdfgrabba(
    manifest_path: str | Path,
    residuals: Iterable[Mapping[str, object]],
    *,
    create: bool = False,
    dry_run: bool = False,
) -> ExportSummary:
    """Merge residual rows into a pdfgrabba JSON list.

    Existing entries are never modified. The destination is replaced atomically
    only when at least one entry is appended, or when ``create`` initializes a
    missing manifest. A dry run performs no filesystem writes.
    """
    path = Path(manifest_path).expanduser()
    if not path.parent.is_dir():
        raise PdfgrabbaExportError(f"Manifest parent directory does not exist: {path.parent}")

    existed = path.exists()
    if existed:
        manifest = load_pdfgrabba_manifest(path)
    elif create:
        manifest = []
    else:
        raise PdfgrabbaExportError(
            f"Manifest does not exist: {path} (pass --create to initialize it)"
        )

    existing_dois = unique_normalized_dois(manifest)
    taken_keys = {
        entry.get("bib_key")
        for entry in manifest
        if isinstance(entry.get("bib_key"), str) and entry.get("bib_key")
    }

    rows_by_doi: dict[str, Mapping[str, object]] = {}
    for row in residuals:
        normalized = normalize_doi(row.get("doi"))
        if not normalized:
            raise PdfgrabbaExportError("Eligible cite-hustle row has an empty DOI")
        if normalized in rows_by_doi:
            raise PdfgrabbaExportError(f"Ambiguous duplicate DOI in cite-hustle rows: {normalized}")
        rows_by_doi[normalized] = row

    appended = []
    already_present = 0
    for doi in sorted(rows_by_doi):
        if doi in existing_dois:
            already_present += 1
            continue
        row = rows_by_doi[doi]
        year = int(row.get("year") or 0)
        title = str(row.get("title") or "")
        authors_text = str(row.get("authors") or "")
        bib_key = _make_available_bib_key(authors_text, year, title, taken_keys)
        appended.append(
            {
                "bib_key": bib_key,
                "doi": doi,
                "url": f"https://doi.org/{doi}",
                "title": title,
                "authors": [author.strip() for author in authors_text.split(";") if author.strip()],
                "journal": str(row.get("journal_name") or ""),
                "journal_abbrev": "",
                "year": year,
                "target_filename": doi_slug_filename(doi),
                "status": "pending",
                "notes": IMPORT_NOTE,
            }
        )

    merged = [*manifest, *appended]
    should_write = not dry_run and (not existed or bool(appended))
    if should_write:
        _atomic_write_json(path, merged, mode_source=path if existed else None)

    return ExportSummary(
        eligible=len(rows_by_doi),
        existing=len(manifest),
        already_present=already_present,
        added=len(appended),
        final=len(merged),
        created=not existed,
        dry_run=dry_run,
    )


def load_pdfgrabba_manifest(path: Path) -> list[dict]:
    """Load and structurally validate a pdfgrabba JSON manifest."""
    if not path.is_file():
        raise PdfgrabbaExportError(f"Manifest is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PdfgrabbaExportError(f"Cannot read valid JSON manifest {path}: {exc}") from exc
    if not isinstance(manifest, list):
        raise PdfgrabbaExportError("Manifest JSON root must be a list")
    if any(not isinstance(entry, dict) for entry in manifest):
        raise PdfgrabbaExportError("Every manifest entry must be a JSON object")
    return manifest


def unique_normalized_dois(manifest: list[dict]) -> set[str]:
    """Return canonical manifest DOIs, rejecting ambiguous duplicates."""
    seen: set[str] = set()
    for index, entry in enumerate(manifest):
        doi = normalize_doi(entry.get("doi"))
        if not doi:
            continue
        if doi in seen:
            raise PdfgrabbaExportError(
                f"Ambiguous duplicate normalized DOI in manifest at entry {index}: {doi}"
            )
        seen.add(doi)
    return seen


def _make_available_bib_key(authors: str, year: int, title: str, taken: set[str]) -> str:
    before = set(taken)
    key = make_bib_key(authors, year, title, taken)
    if key not in before:
        taken.add(key)
        return key

    # make_bib_key uses b-z suffixes. Retain its stable base if an unusually
    # large collision family exhausts those suffixes.
    suffix = 2
    while f"{key}{suffix}" in taken:
        suffix += 1
    key = f"{key}{suffix}"
    taken.add(key)
    return key


def _atomic_write_json(path: Path, manifest: list[dict], mode_source: Path | None) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if mode_source is not None:
            os.chmod(temp_path, stat.S_IMODE(mode_source.stat().st_mode))
        os.replace(temp_path, path)
        temp_path = None
    except OSError as exc:
        raise PdfgrabbaExportError(f"Could not atomically replace manifest {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
