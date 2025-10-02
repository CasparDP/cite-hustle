# Filtering Non-Article Content

## Problem

CrossRef returns not just research articles, but also:
- ❌ Front Matter / Back Matter
- ❌ Covers
- ❌ Book Reviews
- ❌ Editorials
- ❌ Errata / Corrigenda
- ❌ Table of Contents
- ❌ Issue Information

These items shouldn't be scraped from SSRN since they're not research papers.

## Solution

### ✅ Automatic Filtering (NEW)

The metadata collector now automatically filters out non-articles using two methods:

**1. CrossRef Type Checking**
```python
VALID_TYPES = [
    'journal-article',      # ✓ Keep
    'proceedings-article'   # ✓ Keep
]

# Filters out:
# - 'book-review'
# - 'editorial'
# - 'other'
```

**2. Title Keyword Filtering**
```python
NON_ARTICLE_KEYWORDS = [
    'front matter', 'back matter', 'cover', 'covers',
    'book review', 'books received', 'editorial',
    'erratum', 'corrigendum', 'correction',
    'retraction', 'index', 'table of contents',
    'announcements', 'masthead', 'issue information',
    'title pages', 'copyright page', 'contents list'
]
```

### How It Works

When collecting metadata, each item is checked:

```python
def is_valid_article(article):
    # 1. Check CrossRef type
    if article['type'] not in ['journal-article', 'proceedings-article']:
        return False  # ❌ Not a research article
    
    # 2. Check title for non-article keywords
    title = article['title'].lower()
    if any(keyword in title for keyword in NON_ARTICLE_KEYWORDS):
        return False  # ❌ Likely not a research article
    
    # 3. Must have DOI
    if not article.get('DOI'):
        return False  # ❌ No DOI
    
    return True  # ✅ Valid research article
```

## Usage

### For New Collections

Filtering is automatic! Just collect as normal:

```bash
# New collections automatically exclude non-articles
poetry run cite-hustle collect --field accounting --year-start 2023
```

You'll see filtering messages:
```
  ✓ 2023: 45 articles collected
  ℹ️  Filtered out 8 non-article items
```

### For Existing Database

Clean up non-articles already in your database:

```bash
# Run the cleanup script
poetry run python scripts/cleanup_non_articles.py
```

The script will:
1. Search for non-article content
2. Show you what it found
3. Ask for confirmation
4. Delete the items
5. Rebuild search indexes

**Example output:**
```
Found 23 non-article items:

  • Front Matter...
    The Accounting Review (2023)
  • Book Review: Accounting Theory and Practice...
    Journal of Accounting Research (2022)
  • Issue Information...
    Contemporary Accounting Research (2023)
  
  ... and 20 more

Delete these 23 items? (yes/no): yes

✓ Cleanup complete!
  Before: 1,250 items
  After: 1,227 items
  Deleted: 23 non-articles
```

## Verify Filtering

### Check what would be scraped

```bash
# See what articles are pending SSRN scrape
poetry run cite-hustle sample --limit 20

# You should no longer see:
# ❌ "Front Matter"
# ❌ "Book Review: ..."
# ❌ "Covers"
# ❌ "Issue Information"
```

### Test scraping

```bash
# Try scraping - should only process real articles
poetry run cite-hustle scrape --limit 10 --no-headless
```

## Customization

### Add More Keywords

Edit `src/cite_hustle/collectors/metadata.py`:

```python
NON_ARTICLE_KEYWORDS = [
    'front matter', 'back matter', 'cover', 'covers',
    'book review', 'books received', 'editorial',
    # Add your own:
    'conference report',
    'letter to editor',
    'short communication',
]
```

### Add More Valid Types

If you want to include other CrossRef types:

```python
VALID_TYPES = [
    'journal-article',
    'proceedings-article',
    # Add more if needed:
    # 'posted-content',  # Preprints
]
```

## Statistics

After filtering, you can check what was removed:

```python
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager

db = DatabaseManager(settings.db_path)
db.connect()

# Check processing log
filtered = db.conn.execute("""
    SELECT COUNT(*) 
    FROM processing_log 
    WHERE stage = 'metadata_collect'
    AND error_message LIKE '%Filtered out%'
""").fetchone()[0]

print(f"Collections that filtered non-articles: {filtered}")
```

## Benefits

✅ **Cleaner database** - Only research articles  
✅ **Better SSRN matching** - No wasted scraping attempts  
✅ **Accurate statistics** - Counts reflect actual papers  
✅ **Faster processing** - Skip items that won't match on SSRN  
✅ **Better search** - Search results are all real articles  

## Before and After

### Before Filtering
```
Collecting The Accounting Review (0001-4826):
  ✓ 2023: 58 items collected

Items included:
• Front Matter (won't match on SSRN)
• Covers (won't match on SSRN) 
• Book Review: The Theory of... (won't match on SSRN)
• Earnings Management and... ✓
• Financial Reporting Quality... ✓
...
```

### After Filtering
```
Collecting The Accounting Review (0001-4826):
  ✓ 2023: 50 articles collected
  ℹ️  Filtered out 8 non-article items

Items included:
• Earnings Management and... ✓
• Financial Reporting Quality... ✓
...
```

## Notes

- Filtering happens during `collect` command
- Already collected items need cleanup script
- Filtering is logged to `processing_log` table
- Safe to run cleanup multiple times
- Backup database before first cleanup if concerned

## Summary

1. ✅ **New collections**: Automatic filtering
2. 🧹 **Existing data**: Run cleanup script
3. 🔍 **Verify**: Check sample output
4. 🚀 **Scrape**: Only real articles processed

Your SSRN scraper will now only see actual research articles! 🎉
