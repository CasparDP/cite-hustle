#!/usr/bin/env python3
"""
Quick test of the updated scraper with better logging.
Run with: poetry run python test_scraper_fix.py
"""

from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.ssrn_scraper import SSRNScraper

# Test with a simple title
test_title = "Real Earnings Management"
test_doi = "test/123"

print(f"Testing updated scraper with: {test_title}")
print("=" * 80)

# Setup
db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Create scraper with visible browser for first test
print("\nCreating scraper (visible browser)...")
scraper = SSRNScraper(
    repo=repo,
    crawl_delay=10,
    similarity_threshold=85,
    headless=False,  # Show browser for debugging
    max_retries=1
)

# Setup webdriver
print("Setting up Chrome...")
scraper.setup_webdriver()

try:
    print("\nTesting search and URL extraction...")
    print("-" * 80)
    
    success, error, results = scraper.search_ssrn_and_extract_urls(test_title, timeout=15)
    
    if success:
        print(f"\n✓ Search succeeded!")
        print(f"  Found {len(results)} results")
        
        if results:
            print("\n  First 5 results:")
            for i, (url, title, snippet) in enumerate(results[:5]):
                print(f"    [{i}] {title[:70]}...")
                print(f"        URL: {url}")
            
            print("\nNow testing best result extraction...")
            print("-" * 80)
            
            ssrn_url, abstract, match_score = scraper.extract_best_result(test_title, results, max_results=8)
            
            if ssrn_url:
                print(f"\n✓ Successfully found best match!")
                print(f"  Match score: {match_score}")
                print(f"  URL: {ssrn_url}")
                print(f"  Abstract length: {len(abstract) if abstract else 0} chars")
                if abstract:
                    print(f"  Abstract preview: {abstract[:200]}...")
            else:
                print(f"\n✗ Failed to extract best result: {abstract}")
        else:
            print("\n✗ No results found in search")
    else:
        print(f"\n✗ Search failed: {error}")
    
finally:
    input("\nPress Enter to close browser and exit...")
    if scraper.driver:
        scraper.driver.quit()
    db.close()

print("\nDone!")
