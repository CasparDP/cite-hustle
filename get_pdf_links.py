import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import duckdb
from rapidfuzz import fuzz


# Set up Selenium WebDriver with headless mode
def setup_webdriver():
    chrome_options = Options()
   # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=chrome_options)
    return driver

# Search for a title on SSRN
def search_ssrn(driver, title):
    ssrn_url = "https://www.ssrn.com/index.cfm/en/"
    timeout = 10

    try:
        # Navigate to SSRN homepage
        driver.get(ssrn_url)

        # Accept cookies if prompted
        try:
            cookie_button = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            cookie_button.click()
            print("Accepted cookies.")
        except Exception:
            print("No cookie banner found or already accepted.")

        # Wait for the search box and fill it
        search_box = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "txtKeywords"))
        )
        search_box.clear()
        search_box.send_keys(title)

        # Click the search button
        search_button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#searchForm1 > div.big-search > div"))
        )
        search_button.click()
        print("Clicked the search button.")

        # Wait for results to load
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#maincontent > div > div:nth-child(2) > div"))
        )

        return driver.page_source  # Return the HTML content of the results page
    except Exception as e:
        print(f"Error during SSRN search for '{title}': {e}")
        return None


# Extract the first result's link and abstract

def extract_best_result(driver, db_title, similarity_threshold=85, max_results=8):
    timeout = 10
    try:
        # Locate all result titles
        result_elements = WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h3[data-component='Typography'] a"))
        )

        # Extract titles and compute similarity scores
        results = []
        for index, element in enumerate(result_elements[:max_results]):  # Limit to the first `max_results` results
            title = element.text.strip()
            similarity = fuzz.partial_ratio(db_title.lower(), title.lower())
            results.append((similarity, index, title, element))  # Include index for tie-breaking

        # Sort results by similarity (descending), with tie-breaker on index (ascending)
        results.sort(key=lambda x: (-x[0], x[1]))

        # Log and select the best match
        for similarity, _, title, _ in results:
            print(f"Result title: {title}, Similarity: {similarity}")

        best_similarity, _, best_title, best_element = results[0]
        if best_similarity < similarity_threshold:
            print(f"No match found with similarity above {similarity_threshold}. Best similarity: {best_similarity}")
            return None, "No sufficiently similar result found"

        print(f"Selected title: {best_title}, Similarity: {best_similarity}")

        # Click the best matching result
        best_element.click()

        # Wait for the paper page to load
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.abstract-text"))
        )

        # Extract the abstract
        abstract_div = driver.find_element(By.CSS_SELECTOR, "div.abstract-text")
        paragraphs = abstract_div.find_elements(By.TAG_NAME, "p")
        abstract = " ".join(p.text for p in paragraphs if p.text.strip())  # Concatenate text from all <p> tags
        return driver.current_url, abstract

    except Exception as e:
        print(f"Error extracting the result: {e}")
        return None, "Error during extraction"
    
# Check which DOIs are already processed
def fetch_processed_dois(db_name=None):
    if db_name is None:
        import os
        db_name = os.path.expandvars("$HOME/Dropbox/Github Data/cite-hustle/DB/articles.duckdb")
    conn = duckdb.connect(db_name)
    processed_dois = conn.execute("SELECT DISTINCT DOI FROM ssrn_links WHERE Abstract IS NOT NULL").fetchall()
    conn.close()
    return {doi[0] for doi in processed_dois}  # Return a set of DOIs for faster lookup

# Fetch titles and DOIs from the DuckDB database
def fetch_titles_and_dois(db_name=None):
    if db_name is None:
        import os
        db_name = os.path.expandvars("$HOME/Dropbox/Github Data/cite-hustle/DB/articles.duckdb")
    conn = duckdb.connect(db_name)
    # Fetch all titles and DOIs from the DuckDB database filter out duplicates and where ssrn_link is null
    articles = conn.execute("SELECT DISTINCT DOI, Title FROM articles_meta").fetchall()
    conn.close()
    return articles


# Process titles and save results to DuckDB
def process_titles(db_name=None, crawl_delay=5):
    if db_name is None:
        import os
        db_name = os.path.expandvars("$HOME/Dropbox/Github Data/cite-hustle/DB/articles.duckdb")
    # Fetch all titles and DOIs
    articles = fetch_titles_and_dois(db_name)

    # Fetch already processed DOIs
    processed_dois = fetch_processed_dois(db_name)

    driver = setup_webdriver()

    # Create or connect to the DuckDB table
    conn = duckdb.connect(db_name)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ssrn_links (
            DOI TEXT PRIMARY KEY,
            Title TEXT,
            SSRN_Link TEXT,
            Abstract TEXT
        )
    """)
    conn.close()

    for doi, db_title in articles:
        if doi in processed_dois:
            print(f"Skipping already processed DOI: {doi}")
            continue

        print(f"Searching SSRN for: {db_title} (DOI: {doi})")
        search_page = search_ssrn(driver, db_title)
        if search_page:
            ssrn_link, abstract = extract_best_result(driver, db_title)
            
            # Check if we got valid results before attempting database operations
            if ssrn_link is not None:
                print(f"Found result: Link: {ssrn_link}, Abstract: {abstract[:50]}...")

                conn = duckdb.connect(db_name)
                conn.execute(
                    "INSERT OR REPLACE INTO ssrn_links (DOI, Title, SSRN_Link, Abstract) VALUES (?, ?, ?, ?)",
                    (doi, db_title, ssrn_link, abstract)
                )
                conn.close()
            else:
                print(f"No valid SSRN match found for: {db_title}")
                
                # Insert a record with NULL for SSRN_Link to mark as processed
                conn = duckdb.connect(db_name)
                conn.execute(
                    "INSERT OR REPLACE INTO ssrn_links (DOI, Title, SSRN_Link, Abstract) VALUES (?, ?, NULL, ?)",
                    (doi, db_title, abstract)  # abstract will contain error message
                )
                conn.close()

        print(f"Delaying for {crawl_delay} seconds...")
        time.sleep(crawl_delay)

    driver.quit()
    print("All articles processed.")


# Run the function
if __name__ == "__main__":
    process_titles()