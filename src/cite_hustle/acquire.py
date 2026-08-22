"""Shared acquisition service functions.

No click imports here: this module is a service layer used by both the
`resolve-fallbacks` CLI command and (later) the single-DOI `get` command.
All user-facing output stays in the CLI.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from selenium.common.exceptions import WebDriverException

from cite_hustle.collectors.fallback_resolvers import RESOLVERS, ResolverError
from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename, download_pdf
from cite_hustle.collectors.institutional import InstitutionalDownloader, SessionExpired
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
    remaining articles (abort_reason="session_expired"). A WebDriverException
    counts as an error for the current article and triggers one driver
    rebuild attempt before the loop continues; if the rebuild itself fails,
    the batch aborts (abort_reason="webdriver").
    """
    counts = {
        "downloaded": 0,
        "no_match": 0,
        "error": 0,
        "aborted": False,
        "abort_reason": None,
    }

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
            counts["error"] += 1
            counts["aborted"] = True
            counts["abort_reason"] = "session_expired"
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
                counts["abort_reason"] = "webdriver"
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


def _crossref_year(msg: dict) -> Optional[int]:
    """Publication year from a CrossRef message, or None if it has none."""
    for key in ("issued", "published-print", "published-online"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def fetch_crossref_article(doi: str) -> Optional[dict]:
    """Fetch one article's metadata from CrossRef by DOI.

    Returns the article dict, or None if CrossRef does not know the DOI (404)
    or the record has no usable title or publication year. Other HTTP errors
    propagate.
    """
    from cite_hustle.collectors.metadata import MetadataCollector

    params = {"mailto": settings.crossref_email} if settings.crossref_email else None
    response = httpx.get(
        f"https://api.crossref.org/works/{doi}",
        params=params,
        timeout=30.0,
        headers={"User-Agent": "cite-hustle/0.1"},
        follow_redirects=True,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()

    msg = response.json()["message"]
    year = _crossref_year(msg)
    if year is None:
        return None

    title = MetadataCollector.clean_title(" ".join(msg.get("title") or []))
    if not title:
        return None

    return {
        "doi": doi,
        "title": title,
        "authors": "; ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip() for a in msg.get("author", [])
        ),
        "year": year,
        "journal_issn": (msg.get("ISSN") or [None])[0],
        "journal_name": (msg.get("container-title") or [""])[0],
        "publisher": msg.get("publisher", ""),
    }


def _default_downloader_factory():
    """Build the real EZproxy downloader with a live webdriver."""
    downloader = InstitutionalDownloader(
        storage_dir=settings.pdf_storage_dir,
        profile_dir=settings.chrome_profile_dir,
        ezproxy_prefix=settings.ezproxy_prefix,
        headless=False,
    )
    downloader.setup_webdriver()
    return downloader


def acquire_one(
    repo,
    doi: str,
    downloader_factory=None,
    use_institutional: bool = True,
    run_verify: bool = True,
) -> dict:
    """Get one paper end-to-end: metadata -> OA fallbacks -> EZproxy -> verify.

    Unlike the batch commands this ignores the pdf_candidates memo: a human
    asked for this specific paper, so every source is tried fresh. Returns
    {doi, status, source, path, verify_status, detail} with status one of
    already_have / downloaded / metadata_not_found / no_source / session_expired.
    """
    out = {
        "doi": doi,
        "status": None,
        "source": None,
        "path": None,
        "verify_status": None,
        "detail": None,
    }

    article = repo.get_article_by_doi(doi)
    if article is None:
        fetched = fetch_crossref_article(doi)
        if fetched is None:
            out["status"] = "metadata_not_found"
            return out
        repo.insert_article(**fetched)
        article = repo.get_article_by_doi(doi)

    existing = repo.get_pdf_file_by_doi(doi)
    if existing:
        out.update(
            status="already_have",
            source=existing["source"],
            path=existing["pdf_file_path"],
            verify_status=existing["verify_status"],
        )
        return out

    source_order = ["oa", "nber", "arxiv"]
    resolvers = {
        name: RESOLVERS[name](threshold=settings.similarity_threshold) for name in source_order
    }
    with httpx.Client(
        timeout=30.0, headers={"User-Agent": "cite-hustle/0.1"}, follow_redirects=True
    ) as client:
        won = try_sources_for_article(repo, article, source_order, resolvers, client)

    if not won and use_institutional:
        factory = downloader_factory or _default_downloader_factory
        try:
            downloader = factory()
        except Exception as exc:
            out["status"] = "no_source"
            out["detail"] = f"institutional browser setup failed: {exc}"[:200]
            return out
        try:
            counts = run_institutional_batch(repo, downloader, pd.DataFrame([article]), delay=0)
        finally:
            downloader.quit()

        if counts["abort_reason"] == "session_expired":
            out["status"] = "session_expired"
            out["detail"] = "EZproxy session expired"
            return out
        if counts["downloaded"]:
            won = "ezproxy"
        elif counts["abort_reason"]:
            out["detail"] = f"institutional aborted: {counts['abort_reason']}"
        elif counts["error"]:
            out["detail"] = "institutional attempt failed"

    if not won:
        out["status"] = "no_source"
        return out

    row = repo.get_pdf_file_by_doi(doi)
    out.update(
        status="downloaded",
        source=won,
        path=row["pdf_file_path"] if row else None,
        verify_status=row["verify_status"] if row else None,
    )

    if run_verify:
        try:
            from cite_hustle.verifier import PDFVerifier

            pending = repo.get_pdfs_pending_verification()
            pending = pending[pending["doi"] == doi]
            if not pending.empty:
                verifier = PDFVerifier(
                    repo=repo,
                    quarantine_dir=settings.quarantine_dir,
                    model=settings.pdf_verifier_model,
                    gray_low=settings.verify_gray_zone_low,
                    gray_high=settings.verify_gray_zone_high,
                    use_llm=bool(os.environ.get("OLLAMA_API_KEY")),
                )
                verifier.verify_batch(pending)
                row = repo.get_pdf_file_by_doi(doi)
                if row:
                    out["path"] = row["pdf_file_path"]
                    out["verify_status"] = row["verify_status"]
                else:
                    # The verifier deletes the pdf_files row only when it
                    # quarantines a mismatched PDF.
                    out["path"] = None
                    out["verify_status"] = "mismatch"
        except Exception as exc:
            out["detail"] = f"verification failed: {exc}"[:200]

    return out
