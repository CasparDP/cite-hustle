"""Return path from completed pdfgrabba downloads into cite-hustle DuckDB."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cite_hustle.collectors.http_pdf_downloader import _looks_like_pdf
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.pdfgrabba_export import (
    PdfgrabbaExportError,
    load_pdfgrabba_manifest,
    normalize_doi,
    unique_normalized_dois,
)

IMPORTABLE_STATUSES = {"downloaded", "skipped"}


class PdfgrabbaImportError(ValueError):
    """Raised when a pdfgrabba return import cannot proceed safely."""


@dataclass(frozen=True)
class ImportSummary:
    """Counts from inspecting and optionally importing a manifest."""

    manifest_entries: int
    terminal_entries: int
    ignored_status: int
    already_present: int
    empty_doi: int
    unresolved_doi: int
    missing_file: int
    invalid_pdf: int
    ready: int
    selected: int
    imported: int
    dry_run: bool


@dataclass(frozen=True)
class _ReadyImport:
    doi: str
    source_url: str | None
    pdf_path: Path


def import_from_pdfgrabba(
    repo: ArticleRepository,
    manifest_path: str | Path,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> ImportSummary:
    """Import completed pdfgrabba files as pending cite-hustle PDFs.

    The manifest itself is read-only. Existing ``pdf_files`` rows always win,
    and every candidate is validated before the first database mutation.
    """
    path = Path(manifest_path).expanduser()
    try:
        manifest = load_pdfgrabba_manifest(path)
        unique_normalized_dois(manifest)
    except PdfgrabbaExportError as exc:
        raise PdfgrabbaImportError(str(exc)) from exc

    terminal_entries = 0
    ignored_status = 0
    already_present = 0
    empty_doi = 0
    unresolved_doi = 0
    missing_file = 0
    invalid_pdf = 0
    ready: list[_ReadyImport] = []

    terminal = []
    for entry in manifest:
        if entry.get("status") not in IMPORTABLE_STATUSES:
            ignored_status += 1
            continue
        terminal_entries += 1
        doi = normalize_doi(entry.get("doi"))
        if not doi:
            empty_doi += 1
            continue
        terminal.append((doi, entry))

    for normalized_doi, entry in sorted(terminal, key=lambda item: item[0]):
        target = entry.get("target_filename")
        pdf_path = _safe_target_path(path.parent, target)
        if pdf_path is None:
            missing_file += 1
            continue

        try:
            article_doi = repo.resolve_article_doi(normalized_doi)
        except ValueError as exc:
            raise PdfgrabbaImportError(str(exc)) from exc
        if article_doi is None:
            unresolved_doi += 1
            continue
        if repo.get_pdf_file_by_doi(article_doi) is not None:
            already_present += 1
            continue
        if not pdf_path.is_file():
            missing_file += 1
            continue
        if not _looks_like_pdf(pdf_path):
            invalid_pdf += 1
            continue

        source_url = entry.get("url")
        ready.append(
            _ReadyImport(
                doi=article_doi,
                source_url=source_url if isinstance(source_url, str) and source_url else None,
                pdf_path=pdf_path,
            )
        )

    selected = ready[:limit] if limit is not None else ready
    imported = 0
    if not dry_run:
        for candidate in selected:
            if repo.insert_pdf_file_if_absent(
                doi=candidate.doi,
                source="pdfgrabba",
                source_url=candidate.source_url,
                pdf_url=None,
                pdf_file_path=str(candidate.pdf_path),
            ):
                imported += 1

    return ImportSummary(
        manifest_entries=len(manifest),
        terminal_entries=terminal_entries,
        ignored_status=ignored_status,
        already_present=already_present,
        empty_doi=empty_doi,
        unresolved_doi=unresolved_doi,
        missing_file=missing_file,
        invalid_pdf=invalid_pdf,
        ready=len(ready),
        selected=len(selected),
        imported=imported,
        dry_run=dry_run,
    )


def _safe_target_path(output_dir: Path, target: object) -> Path | None:
    if not isinstance(target, str) or not target.strip():
        return None
    target_path = Path(target)
    if target_path.is_absolute() or target_path.name != target:
        raise PdfgrabbaImportError(f"Unsafe pdfgrabba target_filename: {target!r}")
    return output_dir / target_path
