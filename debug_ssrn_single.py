#!/usr/bin/env python3
"""
Quick debug script to test SSRN scraping for a single paper.
Run with: poetry run python debug_ssrn_single.py
"""

from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.ssrn_scraper import SSRNScraper

# Test with the paper that's failing
test_title = "Thirty Years of Change: The Evolution of Classified Boards"

print(f"Testing SSRN scraper with title: {test_title}")
print(f"Running in NON-HEADLESS mode so you can see what's happening...")
print("=" * 80)

# Setup
db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Create scraper with visible browser and more timeout
scraper = SSRNScraper(
    repo=repo,
    crawl_delay=8,
    similarity_threshold=85,
    headless=False,  # Show browser!
    max_retries=1,
    backoff_factor=2.0
)

# Setup webdriver
print("\nSetting up Chrome browser (you should see it open)...")
scraper.setup_webdriver()

try:
    print("\nAttempting to search SSRN...")
    success, error = scraper.search_ssrn(test_title, timeout=15)
    
    if success:
        print("\n✓ Search succeeded!")
        print("\nNow attempting to extract results...")
        
        ssrn_url, abstract, match_score = scraper.extract_best_result(test_title, timeout=15)
        
        if ssrn_url:
            print(f"\n✓ SUCCESS!")
            print(f"  SSRN URL: {ssrn_url}")
            print(f"  Match score: {match_score}")
            print(f"  Abstract (first 200 chars): {abstract[:200] if abstract else 'None'}...")
        else:
            print(f"\n✗ Failed to extract results: {abstract}")
    else:
        print(f"\n✗ Search failed: {error}")
        print("\nCheck the browser window to see what page SSRN is showing.")
        print("Common issues:")
        print("  - CAPTCHA challenge")
        print("  - Rate limiting block")
        print("  - Changed page structure")
        
        # Save current page for debugging
        if scraper.driver:
            print(f"\nCurrent URL: {scraper.driver.current_url}")
            print(f"Page title: {scraper.driver.title}")
            
            # Save the HTML
            html_path = scraper.html_storage_dir / "debug_failed_page.html"
            with open(html_path, 'w') as f:
                f.write(scraper.driver.page_source)
            print(f"Saved page HTML to: {html_path}")
            print("You can open this file in a browser to inspect it.")
            
    input("\nPress Enter to close browser and exit...")
    
finally:
    if scraper.driver:
        scraper.driver.quit()
    db.close()

print("\nDone!")
