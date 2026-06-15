"""Selenium-based PDF downloader for SSRN papers.

SSRN sits behind Cloudflare. The download flow that actually works is:

1. Run a *visible* (non-headless) Chrome via undetected-chromedriver. Headless
   Chrome is reliably flagged by Cloudflare's Turnstile and never gets past the
   "Just a moment..." interstitial, so the paper page never loads.
2. Wait for the Turnstile interstitial to clear (a real browser passes it
   automatically in a few seconds).
3. Accept the cookie banner once, otherwise its overlay intercepts clicks.
4. Click the "Download This Paper" button with a JavaScript click. A plain
   ``element.click()`` is intercepted by the sticky header/cookie overlay, and
   navigating straight to the ``Delivery.cfm`` URL is bounced back to the
   abstract page (SSRN checks the referer), so neither alternative works.

Papers whose full text was never posted show a disabled "Not Available for
Download" button; those are reported as ``unavailable`` so they are skipped on
later runs instead of being retried forever.
"""

import time
import random
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Callable
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from tqdm import tqdm


class SeleniumPDFDownloader:
    """Download PDFs from SSRN using a real browser session."""

    def __init__(
        self,
        storage_dir: Path,
        delay: int = 3,
        headless: bool = False,
        download_timeout: int = 60,
        page_timeout: int = 30,
        restart_every: int = 40,
    ):
        """
        Args:
            storage_dir: Directory to save PDFs
            delay: Base delay between downloads in seconds (jittered)
            headless: Run browser headless. NOTE: headless is blocked by SSRN's
                Cloudflare and is kept only for debugging; leave it False.
            download_timeout: Max seconds to wait for a PDF download to finish
            page_timeout: Max seconds to wait for page elements
            restart_every: Recreate the browser after this many papers to keep
                long unattended runs stable (0 disables).
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.headless = headless
        self.download_timeout = download_timeout
        self.page_timeout = page_timeout
        self.restart_every = restart_every

        # Downloads land in a temp folder, then get renamed to <doi>.pdf
        self.temp_download_dir = self.storage_dir / "temp_downloads"
        self.temp_download_dir.mkdir(parents=True, exist_ok=True)

        self.driver = None
        self.cookies_accepted = False

    # ── Browser lifecycle ──────────────────────────────────────────────────

    def setup_webdriver(self):
        """Set up undetected-chromedriver with download preferences."""
        chrome_options = uc.ChromeOptions()
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1400,1000")

        chrome_options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.temp_download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,  # download, don't render
            },
        )

        # Only pin version_main when we actually detected it. Passing None makes
        # undetected-chromedriver grab "latest", which can mismatch the installed
        # Chrome and fail to start.
        kwargs = {"options": chrome_options, "headless": self.headless}
        chrome_major = self._detect_chrome_major_version()
        if chrome_major is not None:
            kwargs["version_main"] = chrome_major

        self.driver = uc.Chrome(**kwargs)
        self.cookies_accepted = False
        if self.headless:
            print("  ⚠️  Headless mode is blocked by SSRN's Cloudflare; use visible mode.")
        print("  ✓ undetected-chromedriver started for PDF downloads")
        return self.driver

    def quit(self):
        """Close the browser if open."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    @staticmethod
    def _detect_chrome_major_version() -> Optional[int]:
        """Return the major version of the locally installed Chrome/Chromium."""
        import subprocess
        import re

        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
        ]
        for path in candidates:
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, text=True
                )
                m = re.search(r"(\d+)\.", out)
                if m:
                    return int(m.group(1))
            except Exception:
                continue
        return None

    # ── Page handling ──────────────────────────────────────────────────────

    def wait_for_cloudflare(self, timeout: int = 40) -> bool:
        """Wait until the Cloudflare 'Just a moment...' interstitial clears.

        Returns True if the real page loaded, False if still challenged.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                if "just a moment" not in self.driver.page_source.lower():
                    return True
            except WebDriverException:
                pass
        return False

    def accept_cookies(self, timeout: int = 8):
        """Accept the OneTrust cookie banner once (its overlay blocks clicks)."""
        if self.cookies_accepted:
            return
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            ).click()
            print("  ✓ Accepted cookies")
            time.sleep(1)
        except TimeoutException:
            pass  # no banner this session
        self.cookies_accepted = True

    def _find_download_button(self):
        """Return (element, available) for the SSRN download control.

        - (element, True): an enabled "Download This Paper" button.
        - (None, False): the paper has no posted full text (the page shows a
          disabled "Not Available for Download" button).
        - (None, None): no recognizable download control (treat as a failure
          worth retrying).
        """
        # An enabled download button links to Delivery.cfm and is not disabled.
        for el in self.driver.find_elements(By.CSS_SELECTOR, "a[href*='Delivery.cfm']"):
            cls = (el.get_attribute("class") or "").lower()
            if "disabled" not in cls and "no-availab" not in cls:
                return el, True

        # No enabled button: is the paper explicitly flagged as unavailable?
        if "not available for download" in self.driver.page_source.lower():
            return None, False
        if self.driver.find_elements(
            By.CSS_SELECTOR, "a[class*='no-availab'], a[class*='btn-disabled']"
        ):
            return None, False

        return None, None

    # ── Single download ──────────────────────────────────────────────────────

    def download_pdf(self, ssrn_url: str, doi: str) -> Dict:
        """Download one paper. Returns a result dict with a ``status`` field:
        ``downloaded`` | ``skipped`` | ``unavailable`` | ``failed``.
        """
        result = {
            "doi": doi,
            "ssrn_url": ssrn_url,
            "filepath": None,
            "success": False,
            "status": "failed",
            "error": None,
        }

        if not ssrn_url:
            result["error"] = "No SSRN URL"
            return result

        safe_filename = doi.replace("/", "_").replace("\\", "_")
        final_filepath = self.storage_dir / f"{safe_filename}.pdf"
        if final_filepath.exists():
            print(f"✓ Already exists: {safe_filename}.pdf")
            result.update(success=True, status="skipped", filepath=str(final_filepath))
            return result

        try:
            print(f"  → {ssrn_url}")
            self.driver.get(ssrn_url)

            if not self.wait_for_cloudflare():
                result["error"] = "Cloudflare challenge did not clear"
                return result

            self.accept_cookies()
            time.sleep(1)

            button, available = self._find_download_button()
            if available is False:
                print("  – Not available for download")
                result["status"] = "unavailable"
                result["error"] = "Not available for download"
                return result
            if button is None:
                result["error"] = "No download button found"
                return result

            # Clear stale temp files so we can detect the new download
            for f in self.temp_download_dir.glob("*"):
                if f.is_file():
                    f.unlink()

            handles_before = len(self.driver.window_handles)
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", button)

            temp_file = self._wait_for_download(handles_before)
            if not temp_file:
                result["error"] = "Download did not complete"
                return result

            if not self._looks_like_pdf(temp_file):
                result["error"] = "Downloaded file is not a valid PDF"
                Path(temp_file).unlink(missing_ok=True)
                return result

            shutil.move(temp_file, final_filepath)
            print(f"✓ Downloaded: {safe_filename}.pdf")
            result.update(success=True, status="downloaded", filepath=str(final_filepath))
            return result

        except WebDriverException as e:
            # Surface so the batch loop can restart the browser
            result["error"] = f"{type(e).__name__}: {e}"
            raise
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            return result

    def _wait_for_download(self, handles_before: int) -> Optional[str]:
        """Wait for a completed .pdf to appear in the temp dir."""
        deadline = time.time() + self.download_timeout
        while time.time() < deadline:
            time.sleep(2)
            # Some papers open the PDF in a new tab before downloading
            if len(self.driver.window_handles) > handles_before:
                self.driver.switch_to.window(self.driver.window_handles[-1])
            if list(self.temp_download_dir.glob("*.crdownload")):
                continue  # still downloading
            pdfs = list(self.temp_download_dir.glob("*.pdf"))
            if pdfs:
                return str(pdfs[0])
        return None

    @staticmethod
    def _looks_like_pdf(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(5) == b"%PDF-"
        except OSError:
            return False

    # ── Batch ──────────────────────────────────────────────────────────────

    def download_batch(
        self,
        pdf_list: List[Dict],
        show_progress: bool = True,
        on_result: Optional[Callable[[Dict], None]] = None,
    ) -> List[Dict]:
        """Download multiple PDFs in one browser session.

        Args:
            pdf_list: dicts with ``doi`` and ``ssrn_url`` keys
            show_progress: show a tqdm progress bar
            on_result: optional callback invoked after each paper with its
                result dict. Use this to persist progress incrementally so an
                interrupted overnight run loses at most one paper.
        """
        results = []
        self.setup_webdriver()

        try:
            iterator = tqdm(pdf_list, desc="Downloading PDFs") if show_progress else pdf_list
            since_restart = 0

            for item in pdf_list if not show_progress else iterator:
                doi = item["doi"]
                ssrn_url = item.get("ssrn_url")
                (tqdm.write if show_progress else print)(f"\nDownloading: {doi}")

                try:
                    result = self.download_pdf(ssrn_url, doi)
                except WebDriverException as e:
                    # Browser died; rebuild it and record this one as failed
                    print(f"  ⚠️  Browser error ({type(e).__name__}); restarting browser")
                    self.quit()
                    self.setup_webdriver()
                    since_restart = 0
                    result = {
                        "doi": doi,
                        "ssrn_url": ssrn_url,
                        "filepath": None,
                        "success": False,
                        "status": "failed",
                        "error": str(e),
                    }

                results.append(result)
                if on_result:
                    on_result(result)

                # Polite, jittered delay between papers
                if self.delay > 0:
                    time.sleep(random.uniform(self.delay * 0.6, self.delay * 1.4))

                # Periodically recycle the browser on long runs
                since_restart += 1
                if self.restart_every and since_restart >= self.restart_every:
                    print(f"  ↻ Recycling browser after {since_restart} papers")
                    self.quit()
                    self.setup_webdriver()
                    since_restart = 0
        finally:
            self.quit()
            try:
                if self.temp_download_dir.exists():
                    shutil.rmtree(self.temp_download_dir)
            except Exception:
                pass

        self._print_summary(results)
        return results

    @staticmethod
    def _print_summary(results: List[Dict]):
        by_status = {}
        for r in results:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        downloaded = by_status.get("downloaded", 0)
        skipped = by_status.get("skipped", 0)
        unavailable = by_status.get("unavailable", 0)
        failed = by_status.get("failed", 0)
        print(
            f"\n✓ {downloaded} downloaded, {skipped} already present, "
            f"{unavailable} not available, {failed} failed (of {len(results)})"
        )

    def __del__(self):
        self.quit()
