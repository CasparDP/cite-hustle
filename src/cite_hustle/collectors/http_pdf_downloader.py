"""Plain HTTP PDF downloader for fallback sources (NBER, arXiv, OA links).

These hosts do not Cloudflare-block, so no browser is needed. SSRN downloads
still go through SeleniumPDFDownloader.
"""

from pathlib import Path
from typing import Optional, Tuple

import httpx

USER_AGENT = "cite-hustle/0.1 (academic research tool)"
MAX_BYTES = 100 * 1024 * 1024  # sanity cap


def doi_slug_filename(doi: str) -> str:
    """PDF filename convention shared with the SSRN downloader."""
    return doi.replace("/", "_") + ".pdf"


def download_pdf(url: str, dest_path: Path, timeout_s: float = 60.0) -> Tuple[bool, Optional[str]]:
    """Stream a PDF to dest_path; validates the %PDF- magic bytes.

    Returns (success, error_message). Writes to a .part file first so an
    interrupted download never leaves a truncated .pdf behind.
    """
    tmp_path = dest_path.with_suffix(".part")
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            if response.is_error:
                return False, f"http_{response.status_code}"

            total = 0
            with open(tmp_path, "wb") as fh:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        fh.close()
                        tmp_path.unlink()
                        return False, "file_too_large"
                    fh.write(chunk)
    except httpx.RequestError as exc:
        return False, f"request_error: {exc}"
    finally:
        # Never leave a partial file behind on any failure path
        if tmp_path.exists() and not _looks_like_pdf(tmp_path):
            tmp_path.unlink()

    if not tmp_path.exists():
        return False, "not_a_pdf"

    tmp_path.rename(dest_path)
    return True, None


def _looks_like_pdf(path: Path) -> bool:
    """Same magic-bytes check as SeleniumPDFDownloader._looks_like_pdf."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False
