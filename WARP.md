# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

The cite-hustle project is a Python-based academic research tool that automates the collection and processing of academic articles from high-ranking journals. The project has been restructured into a modular package with a CLI interface.

**Dependency Management**: This project uses **Poetry** for dependency management.

## Architecture

### New Structure (v0.1.0)

The project uses a clean package structure under `src/cite_hustle/`:

```
src/cite_hustle/
├── config.py              # Configuration management with pydantic-settings
├── cli/
│   └── commands.py        # Click-based CLI commands
├── database/
│   ├── models.py          # DuckDB schema and connection management
│   └── repository.py      # Data access layer with clean abstractions
└── collectors/
    ├── journals.py        # Journal registry for all supported journals
    ├── metadata.py        # CrossRef metadata collector (TODO: migrate)
    ├── ssrn_scraper.py    # SSRN web scraper (TODO: migrate)
    └── pdf_downloader.py  # PDF download functionality
```

### Core Components

1. **Configuration** (`config.py`): 
   - Uses pydantic-settings for environment-based configuration
   - Manages all paths (Dropbox-based for cross-machine sync)
   - Centralized settings accessible via `from cite_hustle.config import settings`

2. **Database Layer** (`database/`):
   - `models.py`: DuckDB connection and schema initialization
   - `repository.py`: Clean data access methods for articles, SSRN pages, statistics
   - Single DuckDB file stored in Dropbox for easy syncing

3. **Collectors** (`collectors/`):
   - `journals.py`: Registry of top journals in accounting, finance, economics
   - `metadata.py`: Placeholder for CrossRef API logic (to be migrated)
   - `ssrn_scraper.py`: Placeholder for Selenium scraping (to be migrated)
   - `pdf_downloader.py`: Functional PDF download with progress tracking

4. **CLI** (`cli/commands.py`):
   - `init`: Initialize database schema
   - `journals`: List journals by field
   - `collect`: Collect metadata (needs implementation)
   - `scrape`: Scrape SSRN (needs implementation)
   - `download`: Download PDFs (working)
   - `status`: Show database statistics
   - `search`: Full-text search (requires FTS setup)

### Data Flow

1. **Metadata Collection**: CrossRef API → Cache → DuckDB `articles` table
2. **SSRN Scraping**: Articles → Selenium search → HTML storage → `ssrn_pages` table
3. **PDF Download**: `ssrn_pages.pdf_url` → Download → Local storage → Update `pdf_downloaded`

### Key Technologies

- **Poetry**: Dependency management and virtual environments
- **DuckDB**: Single-file embedded database (perfect for Dropbox sync)
- **pydantic-settings**: Type-safe configuration management
- **Click**: Modern CLI framework
- **Selenium**: Web scraping for SSRN
- **crossref-commons**: CrossRef API client
- **rapidfuzz**: Fuzzy string matching for paper identification

## Development Commands

### Environment Setup

```bash
# Install all dependencies (including dev dependencies)
poetry install

# Install only production dependencies
poetry install --only main

# Activate virtual environment
poetry shell

# Update dependencies
poetry update
```

### CLI Commands

All commands should be run with `poetry run` prefix (or activate shell first with `poetry shell`):

```bash
# Initialize database
poetry run cite-hustle init

# Check status
poetry run cite-hustle status

# List journals
poetry run cite-hustle journals --field accounting

# Collect metadata (needs implementation)
poetry run cite-hustle collect --field accounting --year-start 2020

# Scrape SSRN (needs implementation)
poetry run cite-hustle scrape --limit 100

# Download PDFs
poetry run cite-hustle download --limit 50 --delay 2

# Search articles
poetry run cite-hustle search "earnings management"

# Get help
poetry run cite-hustle --help
```

### Activated Shell (Alternative)

```bash
# Activate Poetry shell (then no need for 'poetry run' prefix)
poetry shell

# Now commands work directly
cite-hustle init
cite-hustle status
cite-hustle journals --field all

# Exit shell
exit
```

### Database Access

```python
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository

# Connect
db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Query
stats = repo.get_statistics()
articles = repo.get_articles_by_year_range(2020, 2024)
pending = repo.get_pending_ssrn_scrapes(limit=10)
```

## Poetry Dependency Management

### Adding Dependencies

```bash
# Add a production dependency
poetry add requests

# Add with specific version
poetry add "requests>=2.28.0,<3.0.0"

# Add development dependency
poetry add --group dev pytest

# Add multiple dependencies at once
poetry add selenium beautifulsoup4 pandas
```

### Removing Dependencies

```bash
# Remove a package
poetry remove requests

# Remove dev dependency
poetry remove --group dev pytest
```

### Viewing Dependencies

```bash
# Show all installed packages
poetry show

# Show dependency tree
poetry show --tree

# Show outdated packages
poetry show --outdated

# Show only production dependencies
poetry show --only main
```

### Lock File Management

```bash
# Update lock file without installing
poetry lock

# Update lock file and install
poetry lock --no-update
poetry install

# Update specific package
poetry update requests

# Update all packages
poetry update
```

### Exporting Requirements

```bash
# Export to requirements.txt (if needed for deployment)
poetry export -f requirements.txt --output requirements.txt

# Export without dev dependencies
poetry export -f requirements.txt --output requirements.txt --only main

# Export with hashes for security
poetry export -f requirements.txt --output requirements.txt --only main --with-credentials
```

## Configuration

### Environment Variables

Set in `.env` file (copy from `.env.example`):

```bash
CITE_HUSTLE_CROSSREF_EMAIL=your.email@example.com
CITE_HUSTLE_MAX_WORKERS=3
CITE_HUSTLE_CRAWL_DELAY=5
CITE_HUSTLE_SIMILARITY_THRESHOLD=85
CITE_HUSTLE_HEADLESS_BROWSER=true
CITE_HUSTLE_DUCKDB_MEMORY_LIMIT=4GB
CITE_HUSTLE_DUCKDB_THREADS=4
```

### Data Directories

All data stored in Dropbox for cross-machine syncing:

```
~/Dropbox/Github Data/cite-hustle/
├── DB/
│   └── articles.duckdb        # Main database
├── cache/                      # API response cache
├── metadata/                   # CSV exports (optional)
├── pdfs/                       # Downloaded PDFs
└── ssrn_html/                 # Saved HTML pages
```

## Database Schema

### Tables

**journals**
- `issn` (PRIMARY KEY): Journal ISSN
- `name`: Journal name
- `field`: Research field (accounting/finance/economics)
- `publisher`: Publisher name
- `created_at`: Timestamp

**articles**
- `doi` (PRIMARY KEY): Digital Object Identifier
- `title`: Article title
- `authors`: Author names
- `year`: Publication year
- `journal_issn`: Foreign key to journals
- `journal_name`: Journal name
- `publisher`: Publisher
- `created_at`, `updated_at`: Timestamps

**ssrn_pages**
- `doi` (PRIMARY KEY, FOREIGN KEY): References articles
- `ssrn_url`: SSRN paper URL
- `ssrn_id`: SSRN paper ID
- `html_content`: Full HTML content
- `html_file_path`: Path to saved HTML file
- `abstract`: Extracted abstract text
- `pdf_url`: PDF download URL
- `pdf_downloaded`: Boolean flag
- `pdf_file_path`: Path to downloaded PDF
- `match_score`: Fuzzy match confidence (0-100)
- `scraped_at`: Timestamp
- `error_message`: Error details if scraping failed

**processing_log**
- `id` (PRIMARY KEY): Auto-increment
- `doi`: Article DOI
- `stage`: Processing stage (metadata/scrape/download)
- `status`: Status (success/failed/pending)
- `error_message`: Error details
- `processed_at`: Timestamp

### Indexes

- `idx_articles_year`: Speed up year-based queries
- `idx_articles_journal`: Speed up journal-based queries
- `idx_ssrn_downloaded`: Find pending downloads quickly
- `idx_processing_log_doi`: Track processing history

### Full-Text Search

DuckDB FTS extension provides BM25-based search:
- `fts_main_articles`: Search article titles
- `fts_main_ssrn_pages`: Search abstracts

## Migration TODO

### Priority 1: Migrate Existing Scripts

1. **get_meta_articles.py** → `src/cite_hustle/collectors/metadata.py`
   - Extract CrossRef API logic
   - Use `JournalRegistry` for journal definitions
   - Save to database via `ArticleRepository`
   - Integrate with CLI `collect` command

2. **get_pdf_links.py** → `src/cite_hustle/collectors/ssrn_scraper.py`
   - Extract Selenium setup and search logic
   - Improve HTML storage (save to `html_storage_dir`)
   - Extract abstract parsing
   - Save to database via `ArticleRepository`
   - Integrate with CLI `scrape` command

### Migration Steps

```python
# Example migration pattern:

# OLD (get_meta_articles.py):
def fetch_articles_by_issn(year, issn):
    # CrossRef API logic
    pass

# NEW (collectors/metadata.py):
from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.database.repository import ArticleRepository

class MetadataCollector:
    def __init__(self, repo: ArticleRepository):
        self.repo = repo
    
    def collect_for_journal(self, journal: Journal, year: int):
        # CrossRef API logic
        articles = self._fetch_from_crossref(journal.issn, year)
        
        # Save to database
        self.repo.bulk_insert_articles(articles)
```

## Testing

### Unit Tests (Future)

```bash
# Install dev dependencies
poetry install

# Run tests
poetry run pytest tests/

# With coverage
poetry run pytest --cov=cite_hustle tests/

# Watch mode (requires pytest-watch)
poetry run ptw
```

### Manual Testing

```bash
# Test database initialization
poetry run cite-hustle init
poetry run cite-hustle status

# Test journal registry
poetry run cite-hustle journals --field all

# Test PDF downloader (once SSRN scraping is implemented)
poetry run cite-hustle download --limit 5
```

## Working Across Multiple Machines

Since you're using Dropbox for data syncing:

1. **Initial Setup on New Machine**:
```bash
cd /Users/casparm2/Local/GitHub/cite-hustle
poetry install
cp .env.example .env
# Edit .env
```

2. **Database Automatically Syncs**: 
   - The DuckDB file in Dropbox syncs automatically
   - All machines share the same data

3. **Code Syncs via Git**:
```bash
git pull origin main
poetry install  # Install any new dependencies
```

## Best Practices

### Code Style
- Use Black for formatting (line length: 100)
- Use Ruff for linting
- Type hints encouraged
- Docstrings for public APIs

### Database
- Always use `ArticleRepository` methods
- Don't write raw SQL in CLI commands
- Use transactions for bulk operations
- Log all processing stages

### Error Handling
- Catch specific exceptions
- Log errors to `processing_log` table
- Provide user-friendly error messages
- Continue processing on individual failures

### Rate Limiting
- Respect API rate limits (CrossRef)
- Use crawl delays for web scraping (SSRN)
- Cache API responses
- Implement exponential backoff for retries

## Notes

- Poetry creates isolated virtual environments automatically
- Single DuckDB file makes syncing across machines easy via Dropbox
- All paths relative to Dropbox for portability
- CLI uses Click's context passing for database connection
- Repository pattern keeps database logic separate from business logic
- Journal registry provides single source of truth for journals
- Processing log enables tracking and debugging workflow issues
- Use `poetry run` prefix or activate shell with `poetry shell`
