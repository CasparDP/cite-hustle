"""
Migration tracking and notes

STATUS: ✅ = Complete | ⏳ = In Progress | ❌ = Not Started

MIGRATION STATUS:
=================

✅ 1. Metadata Collection (get_meta_articles.py → collectors/metadata.py)
   - Migrated CrossRef API logic
   - Integrated with JournalRegistry
   - Saves to database via ArticleRepository
   - Connected to CLI 'collect' command
   - Maintains caching mechanism
   - Retry logic and rate limiting preserved
   - Both sequential and parallel modes available

❌ 2. SSRN Scraping (get_pdf_links.py → collectors/ssrn_scraper.py)
   - TODO: Extract Selenium setup
   - TODO: Extract search and scraping logic
   - TODO: Extract abstract extraction
   - TODO: Save HTML to html_storage_dir
   - TODO: Integrate with ArticleRepository
   - TODO: Connect to CLI 'scrape' command

✅ 3. PDF Download (collectors/pdf_downloader.py)
   - Already implemented
   - Connected to CLI 'download' command
   - Working with progress tracking

TESTING THE METADATA COLLECTOR:
================================

Option 1: Using CLI
-------------------
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024

Option 2: Using test script
---------------------------
poetry run python scripts/test_metadata.py

Option 3: Direct usage
---------------------
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.collectors.metadata import MetadataCollector

# Setup
db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Collect
collector = MetadataCollector(repo)
journals = JournalRegistry.get_by_field('accounting')
results = collector.collect_for_journals(journals, [2023, 2024])

COMPARISON: OLD vs NEW
======================

OLD (get_meta_articles.py):
- Hardcoded journal dict
- Saves to CSV files
- Manual cache management
- Difficult to track progress
- Not integrated with database

NEW (collectors/metadata.py):
- Uses JournalRegistry (single source of truth)
- Saves directly to DuckDB database
- Automatic cache management
- Progress bars and status updates
- Full integration with repository pattern
- Logging for debugging
- Sequential and parallel modes
- Skips already collected data

NEXT STEPS:
===========

1. Test the metadata collector:
   poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2023

2. Verify data in database:
   poetry run cite-hustle status

3. Once confirmed working, migrate SSRN scraper next

4. Remove old get_meta_articles.py when fully migrated
"""
