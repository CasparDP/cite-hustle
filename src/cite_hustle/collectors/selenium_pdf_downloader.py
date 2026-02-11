"""Selenium-based PDF downloader for SSRN papers

This module provides a browser-based PDF downloader that can bypass SSRN's
Cloudflare protection by using undetected-chromedriver to avoid detection.
"""
import time
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, List
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from tqdm import tqdm


class SeleniumPDFDownloader:
    """Download PDFs from SSRN using browser automation to bypass Cloudflare"""

    def __init__(self,
                 storage_dir: Path,
                 delay: int = 3,
                 headless: bool = True,
                 download_timeout: int = 60,
                 page_timeout: int = 30):
        """
        Initialize Selenium PDF downloader

        Args:
            storage_dir: Directory to save PDFs
            delay: Delay between downloads in seconds
            headless: Run browser in headless mode
            download_timeout: Max seconds to wait for PDF download
            page_timeout: Max seconds to wait for page elements
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.headless = headless
        self.download_timeout = download_timeout
        self.page_timeout = page_timeout

        # Setup downloads directory - we'll use a temp folder then move files
        self.temp_download_dir = self.storage_dir / "temp_downloads"
        self.temp_download_dir.mkdir(parents=True, exist_ok=True)

        self.driver = None
        self.cookies_accepted = False

    def setup_webdriver(self):
        """Set up undetected-chromedriver with download preferences."""
        chrome_options = uc.ChromeOptions()

        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")

        # Configure downloads
        chrome_options.add_experimental_option("prefs", {
            "download.default_directory": str(self.temp_download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,  # Don't open PDFs in browser
            "plugins.plugins_disabled": ["Chrome PDF Viewer"],
        })

        self.driver = uc.Chrome(
            options=chrome_options,
            headless=self.headless,
            version_main=self._detect_chrome_major_version(),
        )
        print("  ✓ undetected-chromedriver started for PDF downloads")

        return self.driver

    @staticmethod
    def _detect_chrome_major_version():
        """Return the major version of the locally installed Chrome/Chromium."""
        import subprocess, re
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

    def accept_cookies(self, timeout: int = 10):
        """Accept cookies if banner appears"""
        if self.cookies_accepted:
            return

        try:
            cookie_button = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            cookie_button.click()
            self.cookies_accepted = True
            print("  ✓ Accepted cookies")
        except TimeoutException:
            # No cookie banner or already accepted
            self.cookies_accepted = True

    def handle_cloudflare_challenge(self, timeout: int = 30):
        """Handle Cloudflare 'Verify you are human' checkbox if present

        Args:
            timeout: Max seconds to wait for challenge to complete

        Returns:
            True if challenge was handled or not present, False if failed
        """
        try:
            # Wait a moment for Cloudflare to load
            time.sleep(2)

            # Check if we're on a Cloudflare challenge page
            page_source = self.driver.page_source.lower()
            if 'cloudflare' not in page_source and 'just a moment' not in page_source:
                # No Cloudflare challenge detected
                return True

            print("  → Cloudflare challenge detected, attempting to solve...")

            # Try to find and click the verification checkbox
            # Cloudflare often uses an iframe for the checkbox
            try:
                # Look for Cloudflare iframe
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")

                for iframe in iframes:
                    try:
                        # Switch to iframe
                        self.driver.switch_to.frame(iframe)

                        # Look for the checkbox using various selectors
                        checkbox_selectors = [
                            "input[type='checkbox']",
                            "#challenge-stage input",
                            ".ctp-checkbox-label",
                            "label",
                        ]

                        for selector in checkbox_selectors:
                            try:
                                checkbox = WebDriverWait(self.driver, 3).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                                )
                                checkbox.click()
                                print("  ✓ Clicked Cloudflare verification checkbox")

                                # Switch back to main content
                                self.driver.switch_to.default_content()

                                # Wait for challenge to complete
                                time.sleep(5)

                                # Check if we're past the challenge
                                wait_start = time.time()
                                while time.time() - wait_start < timeout:
                                    current_source = self.driver.page_source.lower()
                                    if 'cloudflare' not in current_source or 'just a moment' not in current_source:
                                        print("  ✓ Cloudflare challenge passed")
                                        return True
                                    time.sleep(2)

                                break
                            except (TimeoutException, NoSuchElementException):
                                continue

                        # Switch back to main content before trying next iframe
                        self.driver.switch_to.default_content()

                    except Exception as e:
                        # Switch back to main content if error occurred
                        self.driver.switch_to.default_content()
                        continue

                # If we get here, try waiting for Cloudflare to complete automatically
                print("  → Waiting for Cloudflare to complete automatically...")
                wait_start = time.time()
                while time.time() - wait_start < timeout:
                    current_source = self.driver.page_source.lower()
                    if 'cloudflare' not in current_source or 'just a moment' not in current_source:
                        print("  ✓ Cloudflare challenge passed automatically")
                        return True
                    time.sleep(2)

                print("  ⚠️  Cloudflare challenge timeout")
                return False

            except Exception as e:
                print(f"  ⚠️  Error handling Cloudflare challenge: {type(e).__name__}")
                self.driver.switch_to.default_content()
                return False

        except Exception as e:
            print(f"  ⚠️  Unexpected error in Cloudflare handler: {type(e).__name__}")
            return False

    def find_pdf_download_link(self, ssrn_url: str) -> Optional[str]:
        """
        Navigate to SSRN paper page and find the PDF download link

        Args:
            ssrn_url: SSRN paper URL

        Returns:
            Direct PDF download URL or None if not found
        """
        try:
            print(f"  → Navigating to: {ssrn_url}")
            self.driver.get(ssrn_url)

            # Handle Cloudflare challenge if present
            if not self.handle_cloudflare_challenge():
                print(f"  ✗ Failed to pass Cloudflare challenge")
                return None

            # Accept cookies on first visit
            self.accept_cookies(self.page_timeout)

            # Wait for page to load
            time.sleep(2)

            # Look for download links using various selectors
            download_selectors = [
                # Common SSRN download link patterns
                "a[href*='Delivery.cfm']",
                "a[href*='download']",
                "a[href*='pdf']",  # Fixed: CSS syntax instead of XPath
                ".download-button",
                ".btn-download",
                "button[data-action='download']",
                # More general patterns
                "a[href*='.pdf']",
                "a[title*='Download']",
                "a[aria-label*='Download']"
            ]

            for selector in download_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        for element in elements:
                            href = element.get_attribute('href')
                            if href and ('pdf' in href.lower() or 'delivery.cfm' in href.lower()):
                                print(f"  ✓ Found download link: {href}")
                                return href
                except:
                    continue

            # If no direct download link found, try to find any clickable download elements
            download_text_patterns = [
                "download",
                "pdf",
                "full text",
                "view pdf"
            ]

            for pattern in download_text_patterns:
                try:
                    elements = self.driver.find_elements(By.XPATH, f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}')]")
                    if elements:
                        element = elements[0]
                        href = element.get_attribute('href')
                        if href:
                            print(f"  ✓ Found download link by text '{pattern}': {href}")
                            return href
                except:
                    continue

            print(f"  ✗ No download link found on {ssrn_url}")
            return None

        except Exception as e:
            print(f"  ✗ Error finding download link: {type(e).__name__}: {str(e)}")
            return None

    def download_pdf_via_click(self, ssrn_url: str) -> Optional[str]:
        """
        Download PDF by clicking download button/link on SSRN page

        Args:
            ssrn_url: SSRN paper URL

        Returns:
            Downloaded filename or None if failed
        """
        try:
            print(f"  → Navigating to: {ssrn_url}")
            self.driver.get(ssrn_url)

            # Handle Cloudflare challenge if present
            if not self.handle_cloudflare_challenge():
                print(f"  ✗ Failed to pass Cloudflare challenge")
                return None

            # Accept cookies
            self.accept_cookies(self.page_timeout)
            time.sleep(2)

            # Clear temp download directory
            for file in self.temp_download_dir.glob("*"):
                if file.is_file():
                    file.unlink()

            # Look for clickable download elements
            download_selectors = [
                "a[href*='Delivery.cfm']",
                "a[href*='download']",
                ".download-button",
                ".btn-download",
                "button[data-action='download']"
            ]

            download_element = None
            for selector in download_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        download_element = elements[0]
                        break
                except:
                    continue

            if not download_element:
                # Try by text content
                download_text_patterns = ["download", "pdf", "full text"]
                for pattern in download_text_patterns:
                    try:
                        elements = self.driver.find_elements(By.XPATH, f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern}')]")
                        if elements:
                            download_element = elements[0]
                            break
                    except:
                        continue

            if not download_element:
                print(f"  ✗ No clickable download element found")
                return None

            print(f"  → Clicking download element...")
            download_element.click()

            # Wait for download to complete
            print(f"  → Waiting for download to complete (max {self.download_timeout}s)...")
            start_time = time.time()

            while time.time() - start_time < self.download_timeout:
                # Check for downloaded files
                pdf_files = list(self.temp_download_dir.glob("*.pdf"))
                if pdf_files:
                    # Found a PDF file
                    downloaded_file = pdf_files[0]
                    print(f"  ✓ Download completed: {downloaded_file.name}")
                    return str(downloaded_file)

                # Check for partial downloads (Chrome creates .crdownload files)
                partial_files = list(self.temp_download_dir.glob("*.crdownload"))
                if partial_files:
                    print(f"  → Download in progress...")

                time.sleep(1)

            print(f"  ✗ Download timeout after {self.download_timeout}s")
            return None

        except Exception as e:
            print(f"  ✗ Error downloading via click: {type(e).__name__}: {str(e)}")
            return None

    def download_pdf(self, ssrn_url: str, doi: str) -> Optional[Path]:
        """
        Download a PDF from SSRN using browser automation

        Args:
            ssrn_url: SSRN paper URL
            doi: DOI for filename

        Returns:
            Path to downloaded file or None if failed
        """
        if not ssrn_url:
            print(f"  ✗ No SSRN URL provided for {doi}")
            return None

        # Create safe filename from DOI
        safe_filename = doi.replace('/', '_').replace('\\', '_')
        final_filepath = self.storage_dir / f"{safe_filename}.pdf"

        # Skip if already exists
        if final_filepath.exists():
            print(f"✓ Already exists: {safe_filename}.pdf")
            return final_filepath

        try:
            # Try clicking download on the page
            temp_file = self.download_pdf_via_click(ssrn_url)

            if temp_file and Path(temp_file).exists():
                # Move file to final location
                shutil.move(temp_file, final_filepath)
                print(f"✓ Downloaded: {safe_filename}.pdf")

                # Respect rate limiting
                time.sleep(self.delay)

                return final_filepath
            else:
                print(f"✗ Failed to download PDF for {doi}")
                return None

        except Exception as e:
            print(f"✗ Error downloading {doi}: {type(e).__name__}: {str(e)}")
            return None

    def download_batch(self, pdf_list: List[Dict], show_progress: bool = True) -> List[Dict]:
        """
        Download multiple PDFs using browser automation

        Args:
            pdf_list: List of dicts with 'doi' and 'ssrn_url' keys
            show_progress: Show overall progress bar

        Returns:
            List of results with success/failure info
        """
        results = []

        # Setup webdriver
        self.setup_webdriver()

        try:
            iterator = tqdm(pdf_list, desc="Downloading PDFs (Selenium)") if show_progress else pdf_list

            for item in iterator:
                doi = item['doi']
                ssrn_url = item.get('ssrn_url')

                if show_progress:
                    tqdm.write(f"\nDownloading: {doi}")
                else:
                    print(f"\nDownloading: {doi}")

                filepath = self.download_pdf(ssrn_url, doi)

                results.append({
                    'doi': doi,
                    'ssrn_url': ssrn_url,
                    'filepath': str(filepath) if filepath else None,
                    'success': filepath is not None
                })

        finally:
            # Clean up
            if self.driver:
                self.driver.quit()

            # Clean up temp directory
            try:
                if self.temp_download_dir.exists():
                    shutil.rmtree(self.temp_download_dir)
            except:
                pass

        # Summary
        successful = sum(1 for r in results if r['success'])
        print(f"\n✓ Downloaded {successful}/{len(pdf_list)} PDFs using Selenium")

        if successful < len(pdf_list):
            failed = len(pdf_list) - successful
            print(f"⚠️  {failed} PDFs failed to download")
            print("   This may be due to:")
            print("   - Papers not available for download")
            print("   - Changed SSRN page structure")
            print("   - Network issues or timeouts")

        return results

    def __del__(self):
        """Cleanup when object is destroyed"""
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
            except:
                pass
