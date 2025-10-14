"""SSRN web scraper for finding papers and extracting abstracts"""
import time
import os
import random
from pathlib import Path
from typing import Optional, Dict, Tuple, List
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from rapidfuzz import fuzz
from tqdm import tqdm
from selenium_stealth import stealth

from cite_hustle.config import settings
from cite_hustle.database.repository import ArticleRepository


# Pool of realistic user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class SSRNScraper:
    """Scrapes SSRN to find papers and extracting abstracts using direct URLs"""
    
    def __init__(self, repo: ArticleRepository, 
                 crawl_delay: int = 35,
                 similarity_threshold: int = 85,
                 length_similarity_weight: float = 0.3,
                 headless: bool = True,
                 html_storage_dir: Optional[Path] = None,
                 max_retries: int = 3,
                 backoff_factor: float = 2.0):
        """
        Initialize SSRN scraper
        
        Args:
            repo: Article repository for database access
            crawl_delay: Seconds to wait between requests
            similarity_threshold: Minimum combined match score (0-100)
            length_similarity_weight: Weight for length similarity (0-1), default 0.3
            headless: Run browser in headless mode
            html_storage_dir: Directory to save HTML pages
            max_retries: Maximum number of retry attempts
            backoff_factor: Exponential backoff multiplier
        """
        self.repo = repo
        self.crawl_delay = crawl_delay
        self.similarity_threshold = similarity_threshold
        self.length_similarity_weight = length_similarity_weight
        self.headless = headless
        self.html_storage_dir = html_storage_dir or settings.html_storage_dir
        self.html_storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        
        self.driver: Optional[webdriver.Chrome] = None
        self.cookies_accepted = False
    
    def _get_driver(self) -> webdriver.Chrome:
        """Return initialized WebDriver or raise if not set."""
        if self.driver is None:
            raise RuntimeError("WebDriver not initialized. Call setup_webdriver() first.")
        return self.driver
    
    def _convert_to_portable_path(self, absolute_path: str) -> str:
        """
        Convert absolute path to portable HOME-based path.
        
        Args:
            absolute_path: Absolute file path like /Users/casparm2/Dropbox/...
            
        Returns:
            Portable path like $HOME/Dropbox/...
        """
        path = Path(absolute_path)
        
        # Find if path starts with any /Users/username pattern
        parts = path.parts
        if len(parts) >= 3 and parts[0] == '/' and parts[1] == 'Users':
            # Replace /Users/username with $HOME
            relative_parts = parts[3:]  # Skip /, Users, username
            portable_path = '$HOME/' + '/'.join(relative_parts)
            return portable_path
        
        # If it doesn't match expected pattern, return as-is
        return absolute_path
    
    def setup_webdriver(self):
        """Set up Selenium WebDriver with comprehensive anti-detection options"""
        chrome_options = Options()
        
        # Randomly select user agent for this session
        user_agent = random.choice(USER_AGENTS)
        
        if self.headless:
            chrome_options.add_argument("--headless=new")  # use Chrome's new headless mode
        
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        # Randomize window size slightly to avoid fingerprinting
        width = random.randint(1920, 2560)
        height = random.randint(1080, 1440)
        chrome_options.add_argument(f"--window-size={width},{height}")
        
        # Add randomized user agent
        chrome_options.add_argument(f"user-agent={user_agent}")
        
        # Critical: Disable automation flags that websites can detect
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Additional stealth options
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        
        # Initialize driver
        self.driver = webdriver.Chrome(options=chrome_options)

        # Apply stealth mode to mask automation signals
        try:
            stealth(
                self.driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
            print(f"  ✓ Stealth mode enabled with User-Agent: {user_agent[:50]}...")
        except Exception as e:
            # Proceed without stealth if unavailable
            print(f"  ℹ️  Could not apply selenium-stealth: {type(e).__name__}: {e}")
        
        # Execute CDP commands to further hide automation
        try:
            self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": user_agent
            })
            # Remove webdriver property
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception as e:
            print(f"  ℹ️  Could not execute CDP commands: {type(e).__name__}: {e}")
        
        return self.driver
    
    def accept_cookies(self, timeout: int = 10):
        """Accept cookies if banner appears"""
        if self.cookies_accepted:
            return
        
        try:
            drv = self._get_driver()
            cookie_button = WebDriverWait(drv, timeout).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            cookie_button.click()
            self.cookies_accepted = True
            print("✓ Accepted cookies")
        except TimeoutException:
            # No cookie banner or already accepted
            self.cookies_accepted = True
    
    def _calculate_combined_similarity(self, db_title: str, result_title: str) -> float:
        """
        Calculate combined similarity score considering both fuzzy match and length
        
        Args:
            db_title: Original title from database
            result_title: Title from search result
            
        Returns:
            Combined similarity score (0-100)
        """
        # Fuzzy match score
        fuzzy_score = fuzz.partial_ratio(db_title.lower(), result_title.lower())
        
        # Length similarity score
        db_words = len(db_title.split())
        result_words = len(result_title.split())
        
        if db_words == 0 or result_words == 0:
            length_score = 0
        else:
            # Calculate word count ratio (closer to 1.0 is better)
            word_ratio = min(db_words, result_words) / max(db_words, result_words)
            length_score = word_ratio * 100
        
        # Combined score: weighted average
        # Default: 70% fuzzy match + 30% length similarity
        combined_score = (
            (1 - self.length_similarity_weight) * fuzzy_score +
            self.length_similarity_weight * length_score
        )
        
        return combined_score
    
    def search_ssrn_and_extract_urls(self, title: str, timeout: int = 10) -> Tuple[bool, Optional[str], List[Tuple[str, str, str]]]:
        """
        Search for a title on SSRN and extract URLs directly from search results
        
        Args:
            title: Article title to search for
            timeout: Max seconds to wait for page elements
            
        Returns:
            Tuple of (success, error_message, results_list)
            results_list contains: [(url, title, abstract_snippet), ...]
        """
        ssrn_url = "https://www.ssrn.com/index.cfm/en/"
        
        try:
            # Navigate to SSRN homepage
            print(f"  → Navigating to SSRN homepage...")
            drv = self._get_driver()
            drv.get(ssrn_url)
            
            # Accept cookies on first search
            self.accept_cookies(timeout)
            
            # Wait for and fill search box
            print(f"  → Waiting for search box...")
            search_box = WebDriverWait(drv, timeout).until(
                EC.presence_of_element_located((By.ID, "txtKeywords"))
            )
            print(f"  → Filling search box...")
            search_box.clear()
            time.sleep(0.5)  # Wait for clear to complete
            search_box.send_keys(title)
            time.sleep(1)  # Wait for text to be fully entered
            
            # Verify text was entered
            entered_text = search_box.get_attribute('value')
            if not entered_text or len(entered_text) < 5:
                preview = (entered_text or "")[:50]
                print(f"  ⚠️  Warning: Search box may not have been filled properly (got: '{preview}...')")
                # Try again
                search_box.clear()
                time.sleep(0.5)
                search_box.send_keys(title)
                time.sleep(1)
            
            # Click search button
            print(f"  → Clicking search button...")
            search_button = WebDriverWait(drv, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#searchForm1 > div.big-search > div"))
            )
            search_button.click()
            
            # Wait for results to load - wait for actual paper titles to appear
            print(f"  → Waiting for search results...")
            WebDriverWait(drv, timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h3[data-component='Typography'] a"))
            )
            
            # Also wait a moment for all elements to fully render
            time.sleep(1)
            
            # Extract paper information directly from search results
            print(f"  → Extracting paper URLs from search results...")
            results = []
            
            # Re-fetch elements to avoid stale element reference issues
            result_elements = drv.find_elements(By.CSS_SELECTOR, "h3[data-component='Typography'] a")
            print(f"  ✓ Found {len(result_elements)} result elements")
            
            for idx, element in enumerate(result_elements):
                try:
                    paper_title = element.text.strip()
                    paper_url = element.get_attribute('href')
                    
                    if paper_url and paper_title:
                        # We'll get the full abstract from the paper page later
                        results.append((paper_url, paper_title, ""))
                    else:
                        print(f"  ⚠️  Result {idx}: Missing title or URL (title={bool(paper_title)}, url={bool(paper_url)})")
                except Exception as e:
                    # Skip individual result if there's an error
                    print(f"  ⚠️  Error extracting result {idx}: {type(e).__name__}: {str(e)}")
                    continue
            
            print(f"  ✓ Extracted {len(results)} valid results")
            return True, None, results
            
        except TimeoutException as e:
            # Save screenshot for debugging
            screenshot_path = self._save_error_screenshot(title)
            current_url = self.driver.current_url if self.driver else "unknown"
            page_title = self.driver.title if self.driver else "unknown"
            
            error_msg = (
                f"Timeout waiting for page elements. "
                f"Current URL: {current_url}, Page title: '{page_title}'. "
                f"Screenshot: {screenshot_path}. "
                f"Details: {str(e) if str(e) else 'No details available'}"
            )
            print(f"✗ Error searching SSRN for '{title}': {error_msg}")
            return False, error_msg, []
        except WebDriverException as e:
            error_msg = f"WebDriver error: {str(e)}"
            print(f"✗ Error searching SSRN for '{title}': {error_msg}")
            return False, error_msg, []
        except Exception as e:
            error_msg = f"Unexpected error: {type(e).__name__}: {str(e)}"
            print(f"✗ Error searching SSRN for '{title}': {error_msg}")
            return False, error_msg, []
    
    def extract_best_result(self, db_title: str, 
                           results: List[Tuple[str, str, str]],
                           max_results: int = 8) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
        """
        Find the best matching result using combined similarity scoring
        
        Args:
            db_title: Original title from database
            results: List of (url, title, abstract_snippet) tuples from search
            max_results: Maximum number of results to consider
            
        Returns:
            Tuple of (ssrn_url, abstract, match_score, html_content) or (None, error_message, None, None)
        """
        if not results:
            return None, "No search results found", None, None
        
        # Calculate combined similarity scores for each result
        scored_results = []
        for idx, (url, title, snippet) in enumerate(results[:max_results]):
            similarity = self._calculate_combined_similarity(db_title, title)
            scored_results.append((similarity, idx, title, url, snippet))
        
        # Sort by similarity (descending), with tie-breaker on index (ascending)
        scored_results.sort(key=lambda x: (-x[0], x[1]))
        
        # Log all matches
        print(f"  Results with combined similarity scores:")
        for similarity, idx, title, url, _ in scored_results:
            # Also show individual components for debugging
            fuzzy = fuzz.partial_ratio(db_title.lower(), title.lower())
            db_words = len(db_title.split())
            result_words = len(title.split())
            word_ratio = min(db_words, result_words) / max(db_words, result_words) if max(db_words, result_words) > 0 else 0
            
            print(f"    [{idx}] Score: {similarity:.1f} (fuzzy: {fuzzy}, length: {word_ratio:.2f}, words: {result_words}/{db_words})")
            print(f"        Title: {title[:80]}...")
        
        # Get best match
        best_similarity, _, best_title, best_url, _ = scored_results[0]
        
        # Check if match is good enough
        if best_similarity < self.similarity_threshold:
            message = f"No match above threshold {self.similarity_threshold}. Best: {best_similarity:.1f}"
            print(f"  ⚠️  {message}")
            return None, message, int(best_similarity), None
        
        print(f"  ✓ Selected: {best_title[:60]}... (score: {best_similarity:.1f})")
        print(f"  ✓ URL: {best_url}")
        
        # Now navigate directly to the paper page to get full abstract
        try:
            print(f"  → Navigating to paper page...")
            drv = self._get_driver()
            drv.get(best_url)
            
            # Wait for page to load
            time.sleep(2)
            
            # Try multiple methods to extract abstract
            abstract = self._extract_abstract_from_page()
            
            if not abstract:
                print(f"  ⚠️  Warning: Could not extract abstract, but page loaded")
            else:
                print(f"  ✓ Extracted abstract ({len(abstract)} chars)")
            
            # Capture the HTML content from the paper page
            html_content = drv.page_source
            
            return best_url, abstract, int(best_similarity), html_content
            
        except Exception as e:
            error_msg = f"Error extracting abstract from paper page: {type(e).__name__}: {str(e)}"
            print(f"  ⚠️  {error_msg}")
            # Return URL anyway, even if abstract extraction failed, but no HTML
            return best_url, None, int(best_similarity), None
    
    def _extract_abstract_from_page(self) -> Optional[str]:
        """
        Extract abstract from SSRN paper page using multiple strategies
        
        Returns:
            Abstract text or None if not found
        """
        strategies = [
            # Strategy 1: div.abstract-text with paragraphs
            lambda: self._extract_by_selector("div.abstract-text", include_header=False),
            # Strategy 2: div.abstract-text direct text
            lambda: self._extract_by_selector("div.abstract-text", direct_text=True),
            # Strategy 3: Look for Abstract header then next paragraph
            lambda: self._extract_after_header("Abstract"),
            # Strategy 4: Any div containing "Abstract" header
            lambda: self._extract_from_abstract_div(),
        ]
        
        for idx, strategy in enumerate(strategies, 1):
            try:
                abstract = strategy()
                if abstract and len(abstract) > 50:  # Minimum reasonable abstract length
                    return abstract
            except Exception as e:
                # Strategy failed, try next one
                continue
        
        return None
    
    def _extract_by_selector(self, selector: str, include_header: bool = False, direct_text: bool = False) -> Optional[str]:
        """
        Extract abstract using CSS selector
        
        Args:
            selector: CSS selector for abstract container
            include_header: Whether to include h3 header text
            direct_text: Get direct text instead of paragraphs
            
        Returns:
            Abstract text or None
        """
        try:
            drv = self._get_driver()
            abstract_div = drv.find_element(By.CSS_SELECTOR, selector)
            
            if direct_text:
                # Get all text directly
                return abstract_div.text.strip().replace('Abstract\n', '').strip()
            else:
                # Extract paragraphs
                paragraphs = abstract_div.find_elements(By.TAG_NAME, "p")
                if paragraphs:
                    abstract = " ".join(p.text.strip() for p in paragraphs if p.text.strip())
                    return abstract if abstract else None
                
        except:
            pass
        
        return None
    
    def _extract_after_header(self, header_text: str) -> Optional[str]:
        """
        Find Abstract header and extract following paragraphs
        
        Args:
            header_text: Header text to search for (e.g., "Abstract")
            
        Returns:
            Abstract text or None
        """
        try:
            # Find all h3 elements
            drv = self._get_driver()
            headers = drv.find_elements(By.TAG_NAME, "h3")
            
            for header in headers:
                if header_text.lower() in header.text.lower():
                    # Found the Abstract header, get parent and extract paragraphs
                    parent = header.find_element(By.XPATH, "./..")
                    paragraphs = parent.find_elements(By.TAG_NAME, "p")
                    
                    if paragraphs:
                        abstract = " ".join(p.text.strip() for p in paragraphs if p.text.strip())
                        return abstract if abstract else None
        except:
            pass
        
        return None
    
    def _extract_from_abstract_div(self) -> Optional[str]:
        """
        Find any div containing Abstract and extract text
        
        Returns:
            Abstract text or None
        """
        try:
            # Find all divs
            drv = self._get_driver()
            divs = drv.find_elements(By.TAG_NAME, "div")
            
            for div in divs:
                # Check if this div contains "Abstract" header
                class_attr = div.get_attribute("class") or ""
                if "abstract" in class_attr.lower():
                    text = div.text.strip()
                    # Remove "Abstract" header if present
                    text = text.replace('Abstract\n', '').replace('Abstract', '').strip()
                    if len(text) > 50:
                        return text
        except:
            pass
        
        return None
    
    def _save_error_screenshot(self, title: str) -> Optional[str]:
        """
        Save screenshot when an error occurs
        
        Args:
            title: Article title (used for filename)
            
        Returns:
            Path to saved screenshot or None if failed
        """
        try:
            drv = self.driver
            if not drv:
                return None
                
            # Create safe filename from title
            safe_filename = title[:50].replace('/', '_').replace('\\', '_').replace(' ', '_')
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            filepath = self.html_storage_dir / f"ERROR_{safe_filename}_{timestamp}.png"
            
            drv.save_screenshot(str(filepath))
            return str(filepath)
        except Exception as e:
            print(f"  ⚠️  Failed to save screenshot: {e}")
            return None
    
    def save_html(self, doi: str, html_content: str) -> Optional[str]:
        """
        Save HTML content to file
        
        Args:
            doi: Article DOI (used for filename)
            html_content: HTML to save
            
        Returns:
            Path to saved file or None if failed
        """
        try:
            # Create safe filename from DOI
            safe_filename = doi.replace('/', '_').replace('\\', '_')
            filepath = self.html_storage_dir / f"{safe_filename}.html"
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            print(f"  ✓ Saved HTML to: {filepath}")
            # Return portable path for database storage
            return self._convert_to_portable_path(str(filepath))
        except Exception as e:
            print(f"⚠️  Failed to save HTML for {doi}: {e}")
            return None
    
    def scrape_article(self, doi: str, title: str, retry_count: int = 0) -> Dict:
        """
        Scrape SSRN for a single article with exponential backoff retry logic
        
        Args:
            doi: Article DOI
            title: Article title to search for
            retry_count: Current retry attempt (for internal use)
            
        Returns:
            Dictionary with scraping results
        """
        result = {
            'doi': doi,
            'ssrn_url': None,
            'abstract': None,
            'html_file_path': None,
            'match_score': None,
            'error_message': None,
            'success': False
        }
        
        try:
            # Search SSRN and extract URLs from results page
            search_success, search_error, results = self.search_ssrn_and_extract_urls(title)
            
            if not search_success:
                # Check if we should retry
                if retry_count < self.max_retries and search_error:
                    wait_time = self.crawl_delay * (self.backoff_factor ** retry_count)
                    print(f"  ⏳ Retry {retry_count + 1}/{self.max_retries} after {wait_time}s...")
                    time.sleep(wait_time)
                    
                    # Retry the scrape
                    return self.scrape_article(doi, title, retry_count + 1)
                
                result['error_message'] = search_error or "Failed to search SSRN"
                return result
            
            # Find best matching result using combined similarity
            ssrn_url, abstract, match_score, html_content = self.extract_best_result(title, results)
            
            if ssrn_url:
                # Success - save HTML and results
                html_path = None
                if html_content:
                    html_path = self.save_html(doi, html_content)
                
                result.update({
                    'ssrn_url': ssrn_url,
                    'abstract': abstract,
                    'html_file_path': html_path,
                    'match_score': match_score,
                    'success': True
                })
            else:
                # No match or error
                result['error_message'] = abstract  # abstract contains error message when ssrn_url is None
                result['match_score'] = match_score
            
            return result
            
        except Exception as e:
            error_msg = f"Unexpected error: {type(e).__name__}: {str(e)}"
            result['error_message'] = error_msg
            print(f"✗ {error_msg}")
            return result
    
    def scrape_articles(self, articles_df, show_progress: bool = True) -> Dict:
        """
        Scrape multiple articles from SSRN
        
        Args:
            articles_df: DataFrame with 'doi' and 'title' columns
            show_progress: Show progress bar
            
        Returns:
            Dictionary with statistics
        """
        stats = {
            'total': len(articles_df),
            'success': 0,
            'failed': 0,
            'no_match': 0
        }
        
        # Setup webdriver
        self.setup_webdriver()
        
        try:
            iterator = tqdm(articles_df.iterrows(), total=len(articles_df), 
                          desc="Scraping SSRN") if show_progress else articles_df.iterrows()
            
            for idx, row in iterator:
                doi = row['doi']
                title = row['title']
                
                if show_progress:
                    tqdm.write(f"\n{idx + 1}/{len(articles_df)}: {title[:60]}...")
                else:
                    print(f"\n{idx + 1}/{len(articles_df)}: {title[:60]}...")
                
                # Scrape article
                result = self.scrape_article(doi, title)
                
                # Save to database
                self.repo.insert_ssrn_page(
                    doi=result['doi'],
                    ssrn_url=result['ssrn_url'],
                    html_content=None,  # Don't store HTML in DB (too large)
                    html_file_path=result['html_file_path'],
                    abstract=result['abstract'],
                    match_score=result['match_score'],
                    error_message=result['error_message']
                )
                
                # Log processing
                if result['success']:
                    stats['success'] += 1
                    self.repo.log_processing(doi, 'scrape_ssrn', 'success')
                elif result['match_score'] is not None and result['match_score'] < self.similarity_threshold:
                    stats['no_match'] += 1
                    self.repo.log_processing(doi, 'scrape_ssrn', 'no_match', result['error_message'])
                else:
                    stats['failed'] += 1
                    self.repo.log_processing(doi, 'scrape_ssrn', 'failed', result['error_message'])
                
                # Respect crawl delay (only between successful/normal operations)
                if idx < len(articles_df) - 1:  # Don't delay after last item
                    time.sleep(self.crawl_delay)
            
        finally:
            # Clean up
            if self.driver:
                self.driver.quit()
        
        return stats
