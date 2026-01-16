# CLAUDE.md - Project Instructions for Claude Code

> **Purpose**: Make AI agents productive fast in this Python/Poetry project that collects CrossRef metadata, scrapes SSRN (abstracts), and downloads PDFs into DuckDB with FTS search.

## Quick Start Commands

```bash
# Install dependencies
poetry install

# Initialize database and FTS indexes
poetry run cite-hustle init

# Check project status
poetry run cite-hustle status

# Run tests
poetry run pytest

# Lint/format
poetry run black src/
poetry run ruff check src/
```

## Project Architecture

```
cite-hustle/
├── src/cite_hustle/
│   ├── cli/commands.py        # CLI entrypoint (Click) - all subcommands here
│   ├── config.py              # Settings (pydantic-settings), env vars CITE_HUSTLE_*
│   ├── database/
│   │   ├── models.py          # DatabaseManager, schema, FTS index creation
│   │   └── repository.py      # ArticleRepository - single gateway for all DB I/O
│   └── collectors/
│       ├── journals.py        # Journal registry (19 journals across 3 fields)
│       ├── metadata.py        # CrossRef API collector
│       ├── ssrn_scraper.py    # Selenium-based SSRN search + abstract extraction
│       ├── selenium_pdf_downloader.py  # Selenium PDF downloader (recommended)
│       └── pdf_downloader.py  # Legacy HTTP downloader (usually blocked by Cloudflare)
├── pyproject.toml             # Poetry config, dependencies, scripts
├── CLI-CHEATSHEET.md          # Complete CLI reference
└── README.md                  # User documentation
```

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| CLI | `cli/commands.py` | Click-based CLI; creates `DatabaseManager`, passes `ArticleRepository` to collectors |
| Config | `config.py` | `Settings` class from pydantic-settings; storage at `$HOME/Dropbox/Github Data/cite-hustle/` |
| Schema | `database/models.py` | DuckDB tables: `journals`, `articles`, `ssrn_pages`, `processing_log`; FTS indexes |
| Repository | `database/repository.py` | All DB operations: `insert_article`, `insert_ssrn_page`, `update_pdf_info`, `log_processing` |
| CrossRef | `collectors/metadata.py` | `MetadataCollector` fetches article metadata via `crossref_commons` |
| SSRN Scraper | `collectors/ssrn_scraper.py` | `SSRNScraper` searches SSRN, extracts abstracts using Selenium |
| PDF Download | `collectors/selenium_pdf_downloader.py` | `SeleniumPDFDownloader` downloads PDFs (Cloudflare-safe) |

## Storage Conventions

**Default paths** (relative to `Settings.dropbox_base` = `$HOME/Dropbox/Github Data/cite-hustle/`):

| Type | Path | Notes |
|------|------|-------|
| Database | `DB/articles.duckdb` | DuckDB with FTS extension |
| PDFs | `pdfs/` | Downloaded PDF files |
| HTML | `ssrn_html/` | Saved SSRN page HTML |
| Cache | `cache/` | CrossRef API response cache |

**Path portability**: Paths stored in DB use `$HOME/...` format (see `SSRNScraper._convert_to_portable_path`). Never store machine-specific absolute paths.

**Large blobs**: HTML content is saved to disk; DB stores only the file path + status flags.

## CLI Commands Reference

```bash
# Database initialization
poetry run cite-hustle init

# Collect metadata from CrossRef
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024
poetry run cite-hustle collect --field all --year-start 2024 --skip-fts-rebuild

# Force re-fetch (clears cache and bypasses "already in DB" check)
poetry run cite-hustle collect --field all --year-start 2024 --year-end 2025 --force

# Scrape SSRN for abstracts
poetry run cite-hustle scrape --limit 50 --delay 5 --threshold 85
poetry run cite-hustle scrape --no-headless  # Show browser for debugging

# Download PDFs (use Selenium - HTTP is blocked by Cloudflare)
poetry run cite-hustle download --use-selenium --limit 20
poetry run cite-hustle download --use-selenium --no-headless  # Debug mode

# Search articles (uses BM25 full-text search)
poetry run cite-hustle search "earnings management"
poetry run cite-hustle search "Smith" --author

# Utilities
poetry run cite-hustle status          # Database statistics
poetry run cite-hustle journals        # List supported journals
poetry run cite-hustle sample          # Show sample articles
poetry run cite-hustle rebuild-fts     # Rebuild FTS indexes
```

## Project-Specific Patterns

### Database Access Pattern
Always use `ArticleRepository` for DB I/O - never execute raw SQL outside the repository:

```python
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.config import settings

db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Insert/update operations
repo.insert_article(doi, title, authors, year, journal_issn, journal_name, publisher)
repo.insert_ssrn_page(doi, ssrn_url, html_content, html_file_path, abstract, match_score, error_message)
repo.update_pdf_info(doi, pdf_url, pdf_file_path, downloaded=True)
repo.log_processing(doi, stage='scrape_ssrn', status='success', error_message=None)

# Query operations
pending = repo.get_pending_ssrn_scrapes(limit=50)
articles = repo.get_articles_with_ssrn_urls(limit=20, downloaded=False)
results = repo.search_by_title("earnings management", limit=20)
stats = repo.get_statistics()
```

### SSRN Matching Algorithm
Results are scored using combined similarity:
- **70%** fuzzy match (`rapidfuzz.partial_ratio`)
- **30%** title-length similarity

Accept match if score ≥ threshold (default 85). See `SSRNScraper._calculate_combined_similarity`.

### Anti-Detection (Selenium)
Both scraper and downloader use:
- `selenium-stealth` to avoid bot detection
- User-agent rotation from pool of 8 realistic browser fingerprints
- Randomized window dimensions (1920-2560 x 1080-1440)
- CDP commands to disable automation flags (`AutomationControlled`, `navigator.webdriver`)
- Automatic cookie acceptance and Cloudflare challenge handling

### HTML Storage Pattern
```python
# Save HTML to disk, store only path in DB
html_path = scraper.save_html(doi, html_content)
repo.insert_ssrn_page(doi, ssrn_url, html_content=None, html_file_path=html_path, ...)
```

### PDF Download
- **Recommended**: Use `SeleniumPDFDownloader` with `--use-selenium` flag
- **Legacy**: HTTP downloader in `pdf_downloader.py` is usually blocked by Cloudflare (kept for testing)

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `click` | CLI framework |
| `duckdb` | Database with FTS extension |
| `pydantic-settings` | Configuration management |
| `selenium` + `selenium-stealth` | Browser automation for scraping |
| `rapidfuzz` | Fuzzy string matching for SSRN results |
| `crossref-commons` | CrossRef API client |
| `beautifulsoup4` + `lxml` | HTML parsing |
| `tqdm` | Progress bars |
| `tenacity` | Retry logic with backoff |

## Extending the Project

### Add a New Journal
Update `collectors/journals.py`:
```python
# In appropriate list (ACCOUNTING, FINANCE, or ECONOMICS)
Journal("Journal Name", "ISSN-CODE", "field", "Publisher"),
```

### Add New Database Fields
1. Update schema in `database/models.py` (`initialize_schema` method)
2. Update corresponding repository methods in `database/repository.py`
3. Run `poetry run cite-hustle init` to apply changes
4. Consider adding indexes for query performance

### Add a New Collector
1. Create new file in `collectors/` directory
2. Persist data via `ArticleRepository` methods
3. Save large artifacts (HTML, etc.) to disk with portable paths
4. Call `repo.log_processing()` for each processing step
5. Add CLI command in `cli/commands.py`

## Database Schema

```sql
-- Core tables
journals (issn PK, name, field, publisher, created_at)
articles (doi PK, title, authors, year, journal_issn, journal_name, publisher, created_at, updated_at)
ssrn_pages (doi PK/FK, ssrn_url, ssrn_id, html_content, html_file_path, abstract, pdf_url, 
            pdf_downloaded, pdf_file_path, match_score, scraped_at, error_message)
processing_log (id PK, doi, stage, status, error_message, processed_at)

-- FTS indexes (BM25 ranking)
fts_main_articles (on title)
fts_main_ssrn_pages (on abstract)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Search returns empty results | `poetry run cite-hustle rebuild-fts` |
| Cloudflare blocks downloads | Use `--use-selenium` and `--no-headless` to debug |
| Paths wrong across machines | Check `$HOME/Dropbox/Github Data/cite-hustle` exists or set `CITE_HUSTLE_*` env vars |
| ChromeDriver not found | `brew install --cask chromedriver` |
| DuckDB lock error | Close other DuckDB connections (CLI tools, notebooks) |
| Collect shows "already in database" but missing new papers | Use `--force` flag to clear cache and re-fetch |

## Environment Variables

Set in `.env` file with `CITE_HUSTLE_` prefix:

```bash
CITE_HUSTLE_CROSSREF_EMAIL=your.email@example.com  # Polite pool for CrossRef API
CITE_HUSTLE_DROPBOX_BASE=/custom/path              # Override default storage location
CITE_HUSTLE_CRAWL_DELAY=10                         # Seconds between SSRN requests
CITE_HUSTLE_SIMILARITY_THRESHOLD=90                # SSRN match threshold
```

## Code Style

- **Formatter**: Black (line-length 100)
- **Linter**: Ruff (line-length 100)
- **Python**: 3.12+
- **Type hints**: Use throughout, especially in public APIs

## Utility Scripts

### Reset Failed Scrapes (`scripts/reset_failed_scrapes.py`)

Resets failed SSRN scrapes so they can be retried. Deletes `ssrn_pages` entries for articles that failed with "No search results found" or "Failed to search SSRN" errors.

```bash
# Dry run - see what would be reset
poetry run python scripts/reset_failed_scrapes.py --dry-run

# Reset failures from 2000 onwards (default)
poetry run python scripts/reset_failed_scrapes.py --year-cutoff 2000

# Reset failures from 2015 onwards
poetry run python scripts/reset_failed_scrapes.py --year-cutoff 2015

# Also include "No match above threshold" failures
poetry run python scripts/reset_failed_scrapes.py --include-low-match

# Then run scrape to retry
poetry run cite-hustle scrape --limit 100 --delay 5
```

**Why this exists**: The `get_pending_ssrn_scrapes()` method only returns articles with NO entry in `ssrn_pages`. Failed scrapes have entries (with error messages), so they won't be retried automatically. This script deletes those failed entries, making them "pending" again.

## Current Tasks / Known Issues

### Legacy PDF Downloader
The HTTP-based `pdf_downloader.py` is disabled due to Cloudflare protection. Keep for future testing but always use `--use-selenium` for production downloads.

## Decisions Log

- **Dropbox base path** is universal across dev machines: `$HOME/Dropbox/Github Data/cite-hustle`
- **Legacy HTTP PDF downloader** remains disabled; always use Selenium path
- **HTML content** stored on disk, not in DB (reduces DB size, enables external analysis)
- **Portable paths** use `$HOME/...` format for cross-machine compatibility