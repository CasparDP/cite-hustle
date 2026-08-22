"""EZproxy institutional PDF acquisition via a persistent Chrome profile.

Unlike the fallback resolvers (plain httpx), publisher PDFs behind EUR's
EZproxy must be fetched inside the authenticated browser: the persistent
profile at settings.chrome_profile_dir holds the EZproxy/SSO cookies created
by the one-time `cite-hustle login`.
"""

import shutil
import time
from pathlib import Path
from typing import Dict, Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException

from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename
from cite_hustle.collectors.publisher_pdf import (
    build_ezproxy_url,
    extract_pdf_url,
    is_login_page,
)
from cite_hustle.collectors.selenium_pdf_downloader import SeleniumPDFDownloader

# Suffixes Chrome uses while a download is still in flight
IN_PROGRESS_SUFFIXES = (".crdownload", ".part")


class SessionExpired(Exception):
    """The EZproxy/ERNA login page appeared: run `cite-hustle login`."""


class InstitutionalDownloader:
    def __init__(
        self,
        storage_dir: Path,
        profile_dir: Path,
        ezproxy_prefix: str,
        headless: bool = False,
        page_timeout: int = 45,
        download_timeout: int = 90,
    ):
        self.storage_dir = Path(storage_dir)
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.ezproxy_prefix = ezproxy_prefix
        self.headless = headless
        self.page_timeout = page_timeout
        self.download_timeout = download_timeout
        self.temp_download_dir = self.storage_dir / "temp_downloads_inst"
        self.temp_download_dir.mkdir(parents=True, exist_ok=True)
        self.driver = None

    # ── Browser lifecycle ──────────────────────────────────────────────────

    def setup_webdriver(self):
        chrome_options = uc.ChromeOptions()
        chrome_options.add_argument(f"--user-data-dir={self.profile_dir}")
        chrome_options.add_argument("--window-size=1400,1000")
        chrome_options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.temp_download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
            },
        )
        kwargs = {"options": chrome_options, "headless": self.headless}
        major = SeleniumPDFDownloader._detect_chrome_major_version()
        if major is not None:
            kwargs["version_main"] = major
        self.driver = uc.Chrome(**kwargs)
        self.driver.set_page_load_timeout(self.page_timeout)
        return self.driver

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ── Single article ─────────────────────────────────────────────────────

    def acquire(self, article: Dict) -> Dict:
        """Try to fetch one article's publisher PDF through EZproxy."""
        doi = article["doi"]
        result = {"doi": doi, "status": None, "pdf_url": None, "filepath": None, "error": None}
        url = build_ezproxy_url(self.ezproxy_prefix, doi)
        try:
            self.driver.get(url)
        except WebDriverException as exc:
            result.update(status="nav_error", error=str(exc)[:200])
            return result
        time.sleep(3)  # let EZproxy redirects and the landing page settle

        html, current = self.driver.page_source, self.driver.current_url
        if is_login_page(html, current):
            raise SessionExpired(current)

        pdf_url = extract_pdf_url(html, current)
        if not pdf_url:
            result.update(status="no_pdf_link", error=current[:200])
            return result

        result["pdf_url"] = pdf_url
        try:
            filepath = self._download_via_browser(pdf_url, doi)
        except RuntimeError as exc:
            result.update(status="not_a_pdf", error=str(exc)[:200])
            return result
        result.update(status="downloaded", filepath=str(filepath))
        return result

    # ── Download handling ──────────────────────────────────────────────────

    def _download_via_browser(self, pdf_url: str, doi: str) -> Path:
        """Fetch the PDF inside the authenticated browser and file it by DOI.

        The profile cookies authorize the request and
        ``plugins.always_open_pdf_externally`` makes Chrome download rather
        than render it. Raises RuntimeError with a short reason on failure.
        """
        for stale in self.temp_download_dir.glob("*"):
            if stale.is_file():
                stale.unlink()

        try:
            self.driver.get(pdf_url)
        except TimeoutException:
            pass  # page-load timeout fires on downloads; Chrome keeps going

        temp_file = self._wait_for_download()
        if temp_file is None:
            raise RuntimeError("download_timeout")

        if not SeleniumPDFDownloader._looks_like_pdf(str(temp_file)):
            temp_file.unlink(missing_ok=True)
            raise RuntimeError("not_a_pdf")

        final_path = self.storage_dir / doi_slug_filename(doi)
        shutil.move(str(temp_file), str(final_path))
        return final_path

    def _wait_for_download(self) -> Optional[Path]:
        """Wait for a finished, size-stable file in the temp download dir.

        Publisher PDFs do not reliably land with a ``.pdf`` extension, so any
        file counts as long as no in-progress sibling remains and its size is
        unchanged across two consecutive polls. Dotfiles are ignored: the temp
        dir sits under Dropbox, where a stray ``.DS_Store`` would otherwise
        look like a finished, size-stable download.
        """
        deadline = time.time() + self.download_timeout
        last_size = None
        while time.time() < deadline:
            time.sleep(1)
            files = [
                f
                for f in self.temp_download_dir.glob("*")
                if f.is_file() and not f.name.startswith(".")
            ]
            if any(f.suffix in IN_PROGRESS_SUFFIXES for f in files):
                continue  # still downloading
            if not files:
                last_size = None
                continue
            candidate = max(files, key=lambda f: f.stat().st_mtime)
            size = candidate.stat().st_size
            if size and size == last_size:
                return candidate
            last_size = size
        return None
