# Metadata Collection Migration - COMPLETE ✅

The metadata collection logic has been successfully migrated from `get_meta_articles.py` to the new modular structure.

## What Changed

### Old System (`get_meta_articles.py`)
- ❌ Hardcoded journal dictionary
- ❌ Saves to CSV files
- ❌ Manual cache file management
- ❌ No database integration
- ❌ Difficult to track what's been collected

### New System (`src/cite_hustle/collectors/metadata.py`)
- ✅ Uses `JournalRegistry` for journal definitions
- ✅ Saves directly to DuckDB database
- ✅ Automatic cache management
- ✅ Full database integration via `ArticleRepository`
- ✅ Progress tracking with tqdm
- ✅ Error logging in database
- ✅ Skips already-collected data automatically
- ✅ Sequential and parallel collection modes
- ✅ CLI integration

## Installation & Setup

```bash
# Install dependencies
poetry install

# Setup environment
cp .env.example .env
# Edit .env and set CITE_HUSTLE_CROSSREF_EMAIL=your.email@example.com

# Initialize database
poetry run cite-hustle init
```

## Usage Examples

### 1. Collect Accounting Papers (Recommended for Testing)

```bash
# Collect accounting papers from 2023-2024
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024
```

### 2. Collect All Fields

```bash
# Collect all journals (accounting, finance, economics) for 2020-2024
poetry run cite-hustle collect --field all --year-start 2020 --year-end 2024
```

### 3. Collect with Parallel Processing

```bash
# Use parallel mode (faster but may hit rate limits)
poetry run cite-hustle collect --field finance --year-start 2020 --parallel
```

### 4. Check Status

```bash
# View collection progress
poetry run cite-hustle status
```

### 5. Search Collected Articles

```bash
# Search by title
poetry run cite-hustle search "earnings management"
```

## How It Works

### 1. Journal Registry

Journals are now defined in `src/cite_hustle/collectors/journals.py`:

```python
from cite_hustle.collectors.journals import JournalRegistry

# Get all accounting journals
accounting_journals = JournalRegistry.get_by_field('accounting')

# Get all journals
all_journals = JournalRegistry.get_by_field('all')
```

### 2. Metadata Collector

The collector fetches from CrossRef API and saves to database:

```python
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.database.repository import ArticleRepository

collector = MetadataCollector(repo)

# Collect for specific journals and years
results = collector.collect_for_journals(journals, [2023, 2024])
```

### 3. Caching

API responses are cached in `~/Dropbox/Github Data/cite-hustle/cache/`:
- Format: `cache_{issn}_{year}.json`
- Automatic cache reuse
- Reduces API calls
- Syncs via Dropbox across machines

### 4. Database Schema

Articles are stored in the `articles` table:
- `doi`: Primary key
- `title`: Article title
- `authors`: Semicolon-separated author names
- `year`: Publication year
- `journal_issn`: Journal ISSN
- `journal_name`: Journal name
- `publisher`: Publisher name

## Testing

### Quick Test (Single Journal, Single Year)

```bash
poetry run python scripts/test_metadata.py
```

This will:
1. Connect to database
2. Collect articles from "The Accounting Review" for 2023
3. Show progress and results
4. Verify data was saved

### Full Test (All Journals)

```bash
# Collect a single year first to verify
poetry run cite-hustle collect --field all --year-start 2024 --year-end 2024

# Check what was collected
poetry run cite-hustle status

# Search to verify
poetry run cite-hustle search "financial"
```

## Features

### ✅ Automatic Skip Detection

The collector automatically skips years that have already been collected:

```
Collecting The Accounting Review (0001-4826):
  ✓ 2023: 45 articles already in database
  ✓ 2024: 32 articles collected
```

### ✅ Error Handling & Logging

All errors are logged to the `processing_log` table:

```python
# Check for errors
SELECT * FROM processing_log WHERE status = 'failed';
```

### ✅ Progress Tracking

Real-time progress bars show:
- Current journal being processed
- Articles collected per year
- Overall progress

### ✅ Rate Limiting

Built-in retry logic with exponential backoff:
- Respects CrossRef API etiquette
- Automatic retries on failures
- Configurable via settings

## Configuration

Edit `.env` to customize:

```bash
# Your email for CrossRef API (required for etiquette)
CITE_HUSTLE_CROSSREF_EMAIL=your.email@example.com

# Number of parallel workers
CITE_HUSTLE_MAX_WORKERS=3

# Cache and data directories (default to Dropbox)
# CITE_HUSTLE_DROPBOX_BASE=/Users/yourusername/Dropbox/...
```

## Comparison with Old Script

### Performance
- **Old**: Created many CSV files, required manual tracking
- **New**: Single database, automatic deduplication

### Data Quality
- **Old**: Author names split across multiple rows
- **New**: Clean author list, single row per article

### Maintenance
- **Old**: Hardcoded journals, manual updates needed
- **New**: Central registry, easy to add journals

### Integration
- **Old**: Standalone script, no integration
- **New**: Full CLI integration, database-driven workflow

## Common Issues & Solutions

### Issue: "crossref-commons not found"
```bash
# Reinstall dependencies
poetry install
```

### Issue: "Database file not found"
```bash
# Initialize database first
poetry run cite-hustle init
```

### Issue: "API rate limit errors"
```bash
# Use sequential mode (default) or reduce workers
poetry run cite-hustle collect --field accounting
# Or edit .env: CITE_HUSTLE_MAX_WORKERS=1
```

### Issue: "Cache directory not found"
```bash
# Check Dropbox path in .env
# Default: ~/Dropbox/Github Data/cite-hustle
```

## Next Steps

1. ✅ Metadata collection - **COMPLETE**
2. ⏳ SSRN scraping - **TODO**: Migrate `get_pdf_links.py`
3. ✅ PDF download - **COMPLETE**

Once SSRN scraping is migrated, the full workflow will be:

```bash
# 1. Collect metadata
poetry run cite-hustle collect --field accounting --year-start 2020

# 2. Scrape SSRN
poetry run cite-hustle scrape --limit 100

# 3. Download PDFs
poetry run cite-hustle download --limit 50

# 4. Check progress
poetry run cite-hustle status
```

## Files Changed

- ✅ `src/cite_hustle/collectors/metadata.py` - New metadata collector
- ✅ `src/cite_hustle/cli/commands.py` - Updated collect command
- ✅ `scripts/test_metadata.py` - Test script
- ✅ `scripts/migration_guide.py` - Migration tracking
- ⚠️ `get_meta_articles.py` - Legacy (keep for reference, can delete later)

## Summary

The metadata collection is now:
- **Modular**: Clean separation of concerns
- **Testable**: Easy to test individual components
- **Maintainable**: Clear code structure
- **Integrated**: Works with entire system
- **Robust**: Better error handling and logging
- **Efficient**: Caching and deduplication
- **User-friendly**: CLI interface with progress bars

Ready to collect! 🚀
