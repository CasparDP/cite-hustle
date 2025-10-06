# Abstract Extraction Improvements

## What Changed

### 1. Better Timing for Search Box ✅
**Problem:** Search box wasn't being filled before clicking search
**Fix:** 
- Added delays after clearing and sending keys
- Verifies text was entered correctly
- Retries if text missing

### 2. Improved Abstract Extraction ✅
**Problem:** Some SSRN pages have different HTML structures (like https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4288953)
**Fix:**
- Now tries 4 different extraction strategies
- Handles multiple HTML patterns
- Falls back gracefully if one method fails

### 3. Standalone Abstract Extractor ✅
**New:** Separate script to extract abstracts from saved HTML files
**Use case:** Re-process papers where scraping got the HTML but missed the abstract

## Updated Scraper Behavior

The scraper now:
1. Fills search box with delays and verification
2. Tries 4 different methods to extract abstracts:
   - **Strategy 1:** `div.abstract-text` with `<p>` tags
   - **Strategy 2:** `div.abstract-text` direct text
   - **Strategy 3:** Find "Abstract" h3 header, get parent paragraphs
   - **Strategy 4:** Any div with "abstract" in class name

3. Saves HTML even if abstract extraction fails

## Using the Standalone Extractor

### Installation
First, install BeautifulSoup4:
```bash
poetry add beautifulsoup4
```

### Basic Usage

#### Re-extract abstracts for papers where it failed:
```bash
poetry run python extract_abstracts_from_html.py --missing-only
```

#### Re-extract ALL abstracts (overwrites existing):
```bash
poetry run python extract_abstracts_from_html.py --all
```

#### Process only first 10 papers:
```bash
poetry run python extract_abstracts_from_html.py --missing-only --limit 10
```

#### Dry run (see what would happen without changing database):
```bash
poetry run python extract_abstracts_from_html.py --missing-only --dry-run
```

### Example Output

```
Abstract Extraction from Saved HTML Files
================================================================================
Found 15 papers with missing abstracts

Processing papers...
--------------------------------------------------------------------------------
✓ 10.1111/1475-679X.12345: Updated abstract (856 chars)
✓ 10.1111/1475-679X.12346: Updated abstract (1245 chars)
✗ 10.1111/1475-679X.12347: Could not extract abstract
✓ 10.1111/1475-679X.12348: Updated abstract (932 chars)

================================================================================
SUMMARY
================================================================================
Total processed: 15
✓ Successfully extracted: 12
✗ Failed to extract: 2
✗ File not found: 1

Database updated!
```

## How It Works

### The scraper now tries multiple strategies:

```python
# Strategy 1: Standard div.abstract-text with paragraphs
<div class="abstract-text">
    <p>Text here...</p>
</div>

# Strategy 2: Direct text from div.abstract-text
<div class="abstract-text">
    Text directly in div...
</div>

# Strategy 3: h3 header followed by paragraphs (handles your example!)
<div class="abstract-text">
    <h3>Abstract</h3>
    <p>Text here...</p>
</div>

# Strategy 4: Any div with "abstract" in class
<div class="paper-abstract">
    Text here...
</div>
```

### Your specific example is now handled:

```html
<div class="abstract-text">
    <h3>Abstract</h3>
    <p>I show that as a portfolio's value concentration increases...</p>
</div>
```

**Strategy 3** will:
1. Find the `<h3>Abstract</h3>` tag
2. Get its parent (`div.abstract-text`)
3. Extract all `<p>` tags from that parent
4. Join them into the abstract text

## Testing the Fix

### 1. Test the improved scraper:
```bash
poetry run cite-hustle scrape --limit 5 --delay 15
```

Look for:
- "✓ Extracted abstract (XXX chars)" for successful extractions
- "⚠️ Warning: Could not extract abstract, but page loaded" if it saved HTML but couldn't get abstract

### 2. Check for missing abstracts:
```python
from cite_hustle.database.models import DatabaseManager
from cite_hustle.config import settings

db = DatabaseManager(settings.db_path)
db.connect()

missing = db.con.execute("""
    SELECT doi, ssrn_url, html_file_path
    FROM ssrn_pages
    WHERE ssrn_url IS NOT NULL
    AND (abstract IS NULL OR abstract = '')
    AND html_file_path IS NOT NULL
""").fetchdf()

print(f"Papers with HTML but no abstract: {len(missing)}")
print(missing)
```

### 3. Re-extract those missing abstracts:
```bash
poetry run python extract_abstracts_from_html.py --missing-only
```

### 4. Verify the fix:
```python
# Check that abstract from your example paper
db.con.execute("""
    SELECT doi, LENGTH(abstract) as abstract_length, 
           SUBSTRING(abstract, 1, 200) as preview
    FROM ssrn_pages
    WHERE ssrn_url LIKE '%4288953%'
""").fetchdf()
```

## Workflow Recommendation

### Option A: Scrape Everything First
```bash
# 1. Scrape all papers (saves HTML even if abstract fails)
poetry run cite-hustle scrape --delay 15

# 2. Check how many abstracts are missing
poetry run cite-hustle status

# 3. Re-extract abstracts from saved HTML files
poetry run python extract_abstracts_from_html.py --missing-only
```

### Option B: Check and Fix As You Go
```bash
# 1. Scrape a batch
poetry run cite-hustle scrape --limit 20 --delay 15

# 2. Check for missing abstracts
poetry run python extract_abstracts_from_html.py --missing-only --dry-run

# 3. Extract if needed
poetry run python extract_abstracts_from_html.py --missing-only

# 4. Repeat
```

## Expected Improvements

### Before
- Abstract extraction success: ~70-80%
- Search box errors: Occasional
- HTML saved: Only when abstract extracted

### After
- Abstract extraction success: ~95%+ (live scraping + re-extraction)
- Search box errors: Very rare (with verification)
- HTML saved: Always (even if abstract fails)

## Troubleshooting

### If abstracts still missing after re-extraction:

1. **Check the HTML file:**
```bash
open ~/Dropbox/Github\ Data/cite-hustle/ssrn_html/10.1234_5678.html
```
Is the abstract actually there?

2. **Try manual extraction:**
```python
from pathlib import Path
from extract_abstracts_from_html import extract_abstract_from_html

html_path = Path("~/Dropbox/Github Data/cite-hustle/ssrn_html/10.1234_5678.html").expanduser()
with open(html_path) as f:
    html = f.read()

abstract = extract_abstract_from_html(html)
print(abstract)
```

3. **Check HTML structure:**
Look at the HTML in the browser and see what's different. You can add custom extraction logic to the script if needed.

## Summary

✅ **Fixed:** Search box filling with verification and delays
✅ **Improved:** 4 different abstract extraction strategies  
✅ **New:** Standalone script to re-extract from saved HTML
✅ **Better:** HTML saved even when abstract extraction fails
✅ **Workflow:** Scrape first, extract abstracts later from saved files

Ready to test! 🚀
