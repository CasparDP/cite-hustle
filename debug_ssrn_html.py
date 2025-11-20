#!/usr/bin/env python3
"""
Debug script to inspect SSRN's actual HTML structure.
Run with: poetry run python debug_ssrn_html.py
"""

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from pathlib import Path

# Test title
test_title = "Real Earnings Management"

print(f"Testing SSRN search with: {test_title}")
print("=" * 80)

# Setup Chrome with visible browser
chrome_options = Options()
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument(
    "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

driver = webdriver.Chrome(options=chrome_options)

try:
    # Navigate to SSRN
    print("\n1. Navigating to SSRN...")
    driver.get("https://www.ssrn.com/ssrn/")
    time.sleep(2)

    # Accept cookies if present
    try:
        cookie_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        )
        cookie_button.click()
        print("✓ Accepted cookies")
        time.sleep(1)
    except:
        print("✓ No cookie banner or already accepted")

    # Find and fill search box
    print("\n2. Finding search box...")
    search_box = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "term"))
    )
    print(f"✓ Found search box: {search_box.tag_name}")

    search_box.clear()
    search_box.send_keys(test_title)
    print(f"✓ Entered search term: {test_title}")

    # Click search
    print("\n3. Clicking search button...")
    search_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, ".search-input-wrapper button[type='Submit']"))
    )
    search_button.click()
    print("✓ Clicked search button")

    # Wait for results page to load
    print("\n4. Waiting for results to load...")
    time.sleep(3)

    print(f"\n5. Current URL: {driver.current_url}")
    print(f"   Page title: {driver.title}")

    # Save the full HTML
    html_path = Path("debug_ssrn_search_results.html")
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    print(f"\n✓ Saved full HTML to: {html_path.absolute()}")

    # Try multiple selector strategies to find results
    print("\n6. Testing different selectors to find paper results...")

    selectors_to_test = [
        ("h3[data-component='Typography'] a", "Original selector"),
        ("h3 a", "Any h3 with link"),
        ("a[href*='abstract_id']", "Links with abstract_id"),
        ("a[href*='papers.cfm']", "Links to papers"),
        (".title a", "Class 'title' with link"),
        ("div.box-container", "Box containers"),
        ("article", "Article tags"),
        ("div[class*='result']", "Divs with 'result' in class"),
        ("div[class*='paper']", "Divs with 'paper' in class"),
        ("h1, h2, h3, h4", "All headers"),
    ]

    for selector, description in selectors_to_test:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            print(f"\n   [{selector}] - {description}")
            print(f"   Found: {len(elements)} elements")

            if elements:
                for i, elem in enumerate(elements[:3]):  # Show first 3
                    try:
                        text = elem.text.strip()[:100] if elem.text else "(no text)"
                        href = elem.get_attribute('href') if elem.tag_name == 'a' else "(not a link)"
                        print(f"     [{i}] Text: {text}")
                        if href != "(not a link)":
                            print(f"         URL: {href}")
                    except:
                        print(f"     [{i}] (could not extract info)")
        except Exception as e:
            print(f"\n   [{selector}] - {description}")
            print(f"   Error: {e}")

    # Check page source for key text
    print("\n7. Checking page source for indicators...")
    page_source = driver.page_source.lower()

    indicators = [
        ("results found", "Results counter"),
        ("no results", "No results message"),
        ("abstract", "Paper abstracts"),
        ("download", "Download links"),
        ("author", "Author information"),
        ("captcha", "CAPTCHA present"),
        ("rate limit", "Rate limiting"),
        ("too many requests", "Rate limit message"),
    ]

    for text, description in indicators:
        if text in page_source:
            print(f"   ✓ Found: '{text}' ({description})")
        else:
            print(f"   ✗ Not found: '{text}' ({description})")

    # Extract main content area
    print("\n8. Extracting main content area...")
    try:
        main_content = driver.find_element(By.ID, "maincontent")
        print(f"   ✓ Found #maincontent")
        print(f"   Content preview (first 500 chars):")
        print(f"   {main_content.text[:500]}")
    except:
        print("   ✗ Could not find #maincontent")

    # Take screenshot
    screenshot_path = Path("debug_ssrn_search_results.png")
    driver.save_screenshot(str(screenshot_path))
    print(f"\n✓ Saved screenshot to: {screenshot_path.absolute()}")

    print("\n" + "=" * 80)
    print("NEXT STEPS:")
    print("1. Open debug_ssrn_search_results.html in a browser")
    print("2. Right-click on a paper title → Inspect Element")
    print("3. Look at the HTML structure")
    print("4. Share the HTML structure with me")
    print("=" * 80)

    input("\nPress Enter to close browser...")

finally:
    driver.quit()

print("\nDone! Check the files:")
print(f"  - debug_ssrn_search_results.html")
print(f"  - debug_ssrn_search_results.png")
