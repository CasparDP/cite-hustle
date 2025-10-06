# SSRN Scraper Debugging Guide

## Problem Summary

After scraping 5-10 entries successfully, the scraper was failing with an empty error message:
```
Error searching SSRN for 'Peer evaluations in diverse teams...': Message: 
```

## Root Cause

The issue was **poor error propagation** in the `search_ssrn()` method. When an exception occurred:

1. The exception was caught and printed to console
2. But only `False` was returned (no error message)
3. The calling function then set a generic "Failed to search SSRN" message
4. The actual exception details were lost

**Secondary issue**: SSRN was likely rate-limiting requests after 5-10 searches because the scraper was:
- Navigating to SSRN homepage
- Performing a search
- Clicking results
- Loading paper pages

This is ~4 HTTP requests per paper, which quickly hits rate limits.

## What Was Fixed

### 1. Better Error Handling

**Before:**
```python
def search_ssrn(self, title: str, timeout: int = 10) -> bool:
    try:
        # ... search logic ...
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False  # Error message lost!
```

**After:**
```python
def search_ssrn(self, title: str, timeout: int = 10) -> Tuple[bool, Optional[str]]:
    try:
        # ... search logic ...
        return True, None
    except TimeoutException as e:
        error_msg = f"Timeout waiting for page elements: {str(e)}"
        print(f"✗ Error: {error_msg}")
        return False, error_msg
    except WebDriverException as e:
        error_msg = f"WebDriver error: {str(e)}"
        return False, error_msg
    except Exception as e:
        error_msg = f"Unexpected error: {type(e).__name__}: {str(e)}"
        return False, error_msg
```

Now you'll see the **actual error** including:
- Exception type (`TimeoutException`, `WebDriverException`, etc.)
- Full error message
- Where exactly it failed

### 2. Exponential Backoff Retry

Added retry logic with exponential backoff to handle temporary rate limiting:

```python
def scrape_article(self, doi: str, title: str, retry_count: int = 0) -> Dict:
    # ... search SSRN ...
    
    if not search_success:
        # Retry with exponential backoff
        if retry_count < self.max_retries:
            wait_time = self.crawl_delay * (self.backoff_factor ** retry_count)
            print(f"⏳ Retry {retry_count + 1}/{self.max_retries} after {wait_time}s...")
            time.sleep(wait_time)
            return self.scrape_article(doi, title, retry_count + 1)
```

**Default settings:**
- `max_retries = 3`
- `backoff_factor = 2.0`
- `crawl_delay = 5` seconds

**Retry sequence:**
- 1st retry: Wait 5 seconds (5 * 2^0)
- 2nd retry: Wait 10 seconds (5 * 2^1)
- 3rd retry: Wait 20 seconds (5 * 2^2)

### 3. Enhanced Error Messages

All errors now include:
- Exception type name
- Full exception message  
- Context about what failed

## How to Debug Further

### 1. Run with Visual Browser (No Headless)

See exactly what's happening:

```bash
poetry run cite-hustle scrape --no-headless --limit 5
```

This will show the Chrome browser so you can see:
- If CAPTCHA appears
- If rate limiting messages appear
- Where the scraper gets stuck

### 2. Check the Database Logs

All errors are saved to the `processing_log` table:

```python
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository

db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Get failed scrapes with error messages
failed = db.con.execute("""
    SELECT doi, error_message, processed_at
    FROM processing_log
    WHERE stage = 'scrape_ssrn' 
    AND status = 'failed'
    ORDER BY processed_at DESC
    LIMIT 20
""").fetchdf()

print(failed)
```

### 3. Increase Crawl Delay

If you're still getting rate limited, increase the delay between requests:

```bash
poetry run cite-hustle scrape --delay 10 --limit 10
```

Or edit `.env`:
```bash
CITE_HUSTLE_CRAWL_DELAY=10
```

### 4. Monitor Network Requests

Add logging to see all HTTP requests:

```python
# In ssrn_scraper.py, add this to setup_webdriver():
chrome_options.add_argument("--enable-logging")
chrome_options.add_argument("--v=1")
```

### 5. Check SSRN HTML Files

Inspect saved HTML to see if SSRN is blocking:

```bash
# HTML files are saved to:
ls -lh ~/Dropbox/Github\ Data/cite-hustle/ssrn_html/

# Open one in browser to check content
open ~/Dropbox/Github\ Data/cite-hustle/ssrn_html/10.1111_1475-679X.12345.html
```

Look for:
- CAPTCHA challenges
- "Too many requests" messages
- Access denied pages

## Better Approaches to Consider

### Option 1: Use Direct SSRN URLs

Instead of searching, construct direct URLs if you have SSRN IDs:
```python
ssrn_url = f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={ssrn_id}"
```

**Pros:**
- Only 1 request per paper (vs 4+ for search)
- Much less likely to hit rate limits
- Faster

**Cons:**
- Need SSRN IDs upfront

### Option 2: Use SSRN API (if available)

Check if SSRN has an official API:
- Lower rate limits
- More reliable
- Officially supported

### Option 3: Batch Processing with Longer Delays

Current: 5 seconds between each paper

Better approach for large batches:
```bash
# Process in smaller batches with longer delays
poetry run cite-hustle scrape --limit 10 --delay 30
# Wait an hour
poetry run cite-hustle scrape --limit 10 --delay 30
```

### Option 4: Use Title Matching on Pre-Downloaded SSRN Data

If you can get SSRN paper listings in bulk:
1. Download SSRN metadata in bulk
2. Use fuzzy matching locally
3. Only scrape the matched papers

## Monitoring Scraping Health

```bash
# Check how many succeeded vs failed
poetry run cite-hustle status

# Or query directly:
sqlite3 ~/Dropbox/Github\ Data/cite-hustle/DB/articles.duckdb
```

```sql
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN ssrn_url IS NOT NULL THEN 1 ELSE 0 END) as successful,
    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as failed
FROM ssrn_pages;
```

## Expected Error Types

With the new error handling, you should now see specific errors like:

1. **Rate Limiting:**
   ```
   WebDriver error: Message: timeout
   ```
   or
   ```
   Timeout waiting for page elements: Message: ...
   ```

2. **CAPTCHA:**
   ```
   Element not found: Unable to locate element: {"method":"css selector","selector":"#txtKeywords"}
   ```

3. **Network Issues:**
   ```
   WebDriverException: chrome not reachable
   ```

4. **Search No Results:**
   ```
   No search results found
   ```

## Current Configuration

Default parameters in `SSRNScraper.__init__()`:
- `crawl_delay = 5` seconds
- `similarity_threshold = 85` (out of 100)
- `max_retries = 3`
- `backoff_factor = 2.0`
- `headless = True`

Override via CLI:
```bash
poetry run cite-hustle scrape --delay 10 --threshold 90 --no-headless
```

## Next Steps

1. **Run a test batch** with the updated code:
   ```bash
   poetry run cite-hustle scrape --limit 20 --delay 8
   ```

2. **Check the error messages** in the database:
   ```python
   # See what actual errors you're getting now
   failed_logs = repo.get_failed_processing('scrape_ssrn')
   for log in failed_logs:
       print(f"{log['doi']}: {log['error_message']}")
   ```

3. **Adjust based on errors:**
   - If timeouts → increase delay
   - If CAPTCHA → consider direct URLs
   - If "no results" → lower similarity threshold

4. **Monitor the pattern:**
   - Does it still fail after 5-10 entries?
   - What's the exact error now?
   - Can you identify the rate limit threshold?

## Questions to Answer

After running with the updated code, check:

1. **What are the actual error messages now?**
   - Open the processing_log table
   - Look at error_message column

2. **Does the retry logic help?**
   - Are papers succeeding on 2nd or 3rd retry?

3. **Is there a pattern to failures?**
   - Always after N requests?
   - Specific times of day?
   - Certain journals/papers?

4. **What does the SSRN HTML show?**
   - Open saved HTML files
   - Check for rate limit messages

Share the **actual error messages** you now see and we can refine the approach further!
