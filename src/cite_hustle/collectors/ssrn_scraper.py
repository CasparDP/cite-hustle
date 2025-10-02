"""SSRN web scraper for finding papers and extracting abstracts"""
import time
from pathlib import Path
from typing import Optional, Dict, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from rapidfuzz import fuzz
from tqdm import tqdm

from cite_hustle.config import settings
from cite_hustle.database.repository import ArticleRepository


class SSRNScraper:
    """Scrapes SSRN to find papers and extract abstracts"""
    
    def __init__(self, repo: ArticleRepository, 
                 crawl_delay: int = 5,
                 similarity_threshold: int = 85,
                 headless: bool = True,
                 html_storage_dir: Optional[Path] = None):
        """
        Initialize SSRN scraper
        
        Args:
            repo: Article repository for database access
            crawl_delay: Seconds to wait between requests
            similarity_threshold: Minimum fuzzy match score (0-100)
            headless: Run browser in headless mode
            html_storage_dir: Directory to save HTML pages
        """
        self.repo = repo
        self.crawl_delay = crawl_delay
        self.similarity_threshold = similarity_threshold
        self.headless = headless
        self.html_storage_dir = html_storage_dir or settings.html_storage_dir
        self.html_storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.driver = None
        self.cookies_accepted = False
    
    def setup_webdriver(self):
        """Set up Selenium WebDriver with appropriate options"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument("--headless")
        
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Add user agent to look more like a real browser
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        self.driver = webdriver.Chrome(options=chrome_options)
        return self.driver
    
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
            print("✓ Accepted cookies")
        except TimeoutException:
            # No cookie banner or already accepted
            self.cookies_accepted = True
    
    def search_ssrn(self, title: str, timeout: int = 10) -> bool:
        """
        Search for a title on SSRN
        
        Args:
            title: Article title to search for
            timeout: Max seconds to wait for page elements
            
        Returns:
            True if search succeeded, False otherwise
        """
        ssrn_url = "https://www.ssrn.com/index.cfm/en/"
        
        try:
            # Navigate to SSRN homepage
            self.driver.get(ssrn_url)
            
            # Accept cookies on first search
            self.accept_cookies(timeout)
            
            # Wait for and fill search box
            search_box = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.ID, "txtKeywords"))
            )
            search_box.clear()
            search_box.send_keys(title)
            
            # Click search button
            search_button = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#searchForm1 > div.big-search > div"))
            )
            search_button.click()
            
            # Wait for results to load
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#maincontent > div > div:nth-child(2) > div"))
            )
            
            return True
            
        except Exception as e:
            print(f"✗ Error searching SSRN for '{title}': {e}")
            return False
    
    def extract_best_result(self, db_title: str, 
                           max_results: int = 8,
                           timeout: int = 10) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        """
        Extract the best matching result from SSRN search results
        
        Args:
            db_title: Original title from database
            max_results: Maximum number of results to consider
            timeout: Timeout for page loads
            
        Returns:
            Tuple of (ssrn_url, abstract, match_score) or (None, error_message, None)
        """
        try:
            # Locate all result titles
            result_elements = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h3[data-component='Typography'] a"))
            )
            
            # Extract titles and compute similarity scores
            results = []
            for index, element in enumerate(result_elements[:max_results]):
                title = element.text.strip()
                similarity = fuzz.partial_ratio(db_title.lower(), title.lower())
                results.append((similarity, index, title, element))
            
            if not results:
                return None, "No search results found", None
            
            # Sort by similarity (descending), with tie-breaker on index (ascending)
            results.sort(key=lambda x: (-x[0], x[1]))
            
            # Log all matches
            for similarity, _, title, _ in results:
                print(f"  Result: {title[:60]}... (similarity: {similarity})")
            
            # Get best match
            best_similarity, _, best_title, best_element = results[0]
            
            # Check if match is good enough
            if best_similarity < self.similarity_threshold:
                message = f"No match above threshold {self.similarity_threshold}. Best: {best_similarity}"
                print(f"  ⚠️  {message}")
                return None, message, best_similarity
            
            print(f"  ✓ Selected: {best_title[:60]}... (score: {best_similarity})")
            
            # Click the best match
            best_element.click()
            
            # Wait for paper page to load
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.abstract-text"))
            )
            
            # Extract abstract
            abstract_div = self.driver.find_element(By.CSS_SELECTOR, "div.abstract-text")
            paragraphs = abstract_div.find_elements(By.TAG_NAME, "p")
            abstract = " ".join(p.text for p in paragraphs if p.text.strip())
            
            # Get current URL (the paper page)
            ssrn_url = self.driver.current_url
            
            return ssrn_url, abstract, best_similarity
            
        except TimeoutException:
            return None, "Timeout waiting for search results", None
        except NoSuchElementException as e:
            return None, f"Element not found: {e}", None
        except Exception as e:
            return None, f"Error extracting result: {e}", None
    
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
            
            return str(filepath)
        except Exception as e:
            print(f"⚠️  Failed to save HTML for {doi}: {e}")
            return None
    
    def scrape_article(self, doi: str, title: str) -> Dict:
        """
        Scrape SSRN for a single article
        
        Args:
            doi: Article DOI
            title: Article title to search for
            
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
            # Search SSRN
            if not self.search_ssrn(title):
                result['error_message'] = "Failed to search SSRN"
                return result
            
            # Extract best result
            ssrn_url, abstract, match_score = self.extract_best_result(title)
            
            if ssrn_url:
                # Success - save HTML and results
                html_content = self.driver.page_source
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
            result['error_message'] = f"Unexpected error: {e}"
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
                
                # Respect crawl delay
                if idx < len(articles_df) - 1:  # Don't delay after last item
                    time.sleep(self.crawl_delay)
            
        finally:
            # Clean up
            if self.driver:
                self.driver.quit()
        
        return stats
