"""Shared acquisition service functions.

No click imports here: this module is a service layer used by both the
`resolve-fallbacks` CLI command and (later) the single-DOI `get` command.
All user-facing output stays in the CLI.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd
from selenium.common.exceptions import WebDriverException

from cite_hustle.collectors.fallback_resolvers import ResolverError
from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename, download_pdf
from cite_hustle.collectors.institutional import SessionExpired
from cite_hustle.collectors.publisher_pdf import build_ezproxy_url
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


def run_institutional_batch(
    repo,
    downloader,
    articles: pd.DataFrame,
    delay: int,
    already_checked: set = frozenset(),
) -> dict:
    """Run the EZproxy institutional resolver over a batch of articles.

    Records a pdf_candidates row (source "ezproxy") for every article
    attempted, and on success upserts pdf_files + logs processing. A
    SessionExpired abort stops the loop immediately without touching the
    remaining articles. A WebDriverException counts as an error for the
    current article and triggers one driver rebuild attempt before the loop
    continues; if the rebuild itself fails, the batch aborts.
    """
    counts = {"downloaded": 0, "no_match": 0, "error": 0, "aborted": False}

    for _, row in articles.iterrows():
        article = row.to_dict()
        doi = article["doi"]
        if (doi, "ezproxy") in already_checked:
            continue

        try:
            result = downloader.acquire(article)
        except SessionExpired:
            repo.record_pdf_candidate(
                doi, "ezproxy", status="error", error_message="session_expired"
            )
            repo.log_processing(doi, "resolve_institutional", "failed")
            counts["aborted"] = True
            break
        except WebDriverException as exc:
            repo.record_pdf_candidate(
                doi, "ezproxy", status="error", error_message=f"webdriver: {exc}"[:200]
            )
            counts["error"] += 1
            try:
                downloader.quit()
                downloader.setup_webdriver()
            except Exception:
                counts["aborted"] = True
                break
            time.sleep(delay)
            continue

        status = result["status"]
        if status == "downloaded":
            repo.record_pdf_candidate(
                doi,
                "ezproxy",
                candidate_url=result["pdf_url"],
                pdf_url=result["pdf_url"],
                match_score=100.0,
                status="downloaded",
            )
            repo.upsert_pdf_file(
                doi=doi,
                source="ezproxy",
                source_url=build_ezproxy_url(settings.ezproxy_prefix, doi),
                pdf_url=result["pdf_url"],
                pdf_file_path=result["filepath"],
                match_score=100.0,
            )
            repo.log_processing(doi, "resolve_institutional", "success")
            counts["downloaded"] += 1
        elif status == "no_pdf_link":
            repo.record_pdf_candidate(
                doi, "ezproxy", status="no_match", error_message="no_pdf_link"
            )
            counts["no_match"] += 1
        else:  # nav_error / not_a_pdf
            repo.record_pdf_candidate(
                doi,
                "ezproxy",
                status="error",
                error_message=f"{status}: {result.get('error')}",
            )
            counts["error"] += 1

        time.sleep(delay)

    return counts
