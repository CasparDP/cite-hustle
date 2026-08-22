"""Shared acquisition service functions.

No click imports here: this module is a service layer used by both the
`resolve-fallbacks` CLI command and (later) the single-DOI `get` command.
All user-facing output stays in the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cite_hustle.collectors.fallback_resolvers import ResolverError
from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename, download_pdf
from cite_hustle.config import settings


def try_sources_for_article(
    repo,
    article: dict,
    source_order: list[str],
    resolvers: dict,
    client,
    already_checked: set = frozenset(),
    pdf_dir: Optional[Path] = None,
) -> Optional[str]:
    """Try each source in order for one article; first hit wins.

    Records a pdf_candidates row for every source attempted (error / no_match /
    downloaded), and on success upserts pdf_files + logs processing. Returns
    the winning source name, or None if no source produced a PDF.
    """
    if pdf_dir is None:
        pdf_dir = settings.pdf_storage_dir

    doi = article["doi"]

    for name in source_order:
        if (doi, name) in already_checked:
            continue

        try:
            candidate = resolvers[name].resolve(client, article)
        except ResolverError as exc:
            repo.record_pdf_candidate(doi, name, status="error", error_message=str(exc))
            continue

        if candidate is None:
            repo.record_pdf_candidate(doi, name, status="no_match")
            continue

        dest = pdf_dir / doi_slug_filename(doi)
        success, error = download_pdf(candidate.pdf_url, dest)
        if not success:
            repo.record_pdf_candidate(
                doi,
                name,
                candidate_url=candidate.candidate_url,
                pdf_url=candidate.pdf_url,
                match_score=candidate.match_score,
                status="error",
                error_message=error,
            )
            continue

        repo.record_pdf_candidate(
            doi,
            name,
            candidate_url=candidate.candidate_url,
            pdf_url=candidate.pdf_url,
            match_score=candidate.match_score,
            status="downloaded",
        )
        repo.upsert_pdf_file(
            doi=doi,
            source=name,
            source_url=candidate.candidate_url,
            pdf_url=candidate.pdf_url,
            pdf_file_path=str(dest),
            match_score=candidate.match_score,
        )
        repo.log_processing(doi, "resolve_fallback", "success", None)
        return name

    return None
