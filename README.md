# Cite-Hustle

Academic literature research tool for automated collection of papers from top journals.

## Overview

Cite-Hustle automates the workflow of collecting academic papers:
1. **Collect** article metadata from CrossRef API ✅
2. **Scrape** SSRN pages to find abstracts and PDF links ✅  
3. **Download** PDFs for offline reading ✅

## Setup

### Prerequisites
- Python 3.12+
- Poetry for dependency management
- Chrome browser (for Selenium scraping)
- BeautifulSoup4 (for HTML parsing)

### Installation

```bash
# Navigate to repository
cd ~/Github/cite-hustle

# Install dependencies with Poetry
poetry install

# Install BeautifulSoup4 for abstract extraction
poetry add beautifulsoup4

# Activate the virtual environment (optional - Poetry handles this automatically)
poetry shell
```

### Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings
# Especially set CITE_HUSTLE_CROSSREF_EMAIL=your.email@example.com
```

### Initialize Database

```bash
# Initialize the DuckDB database schema and FTS indexes
poetry run cite-hustle init
```

## Usage

📋 **Quick Reference:** See [CLI-CHEATSHEET.md](./CLI-CHEATSHEET.md) for a complete command reference with examples.

### Complete Workflow

```bash
# 1. Collect article metadata from CrossRef
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024

# 2. Scrape SSRN for abstracts
poetry run cite-hustle scrape --limit 50

# 3. Check progress
poetry run cite-hustle status

# 4. Search articles
poetry run cite-hustle search "earnings management"

# 5. Download PDFs (when PDF URLs are available)
poetry run cite-hustle download --limit 20
```

### Check Status

```bash
# View current database statistics
poetry run cite-hustle status
```

### List Journals

```bash
# List all journals
poetry run cite-hustle journals --field all

# List accounting journals only
poetry run cite-hustle journals --field accounting
```

### Collect Metadata

```bash
# Collect accounting papers from 2020-2024
poetry run cite-hustle collect --field accounting --year-start 2020 --year-end 2024

# Collect all fields for 2023
poetry run cite-hustle collect --field all --year-start 2023 --year-end 2023
```

### Scrape SSRN

```bash
# Scrape 50 articles with default settings
poetry run cite-hustle scrape --limit 50

# Scrape with custom settings
poetry run cite-hustle scrape --delay 15 --threshold 90

# Show browser for debugging
poetry run cite-hustle scrape --no-headless --limit 5
```

### Extract Abstracts from Saved HTML

If some abstracts failed during scraping, you can re-extract them from the saved HTML files:

```bash
# Re-extract abstracts for papers where it failed
poetry run python extract_abstracts_from_html.py --missing-only

# Re-extract ALL abstracts (overwrites existing)
poetry run python extract_abstracts_from_html.py --all

# Process only first 10 papers
poetry run python extract_abstracts_from_html.py --missing-only --limit 10

# Dry run (see what would happen)
poetry run python extract_abstracts_from_html.py --missing-only --dry-run
```

### Search

```bash
# Search by title (uses full-text search with BM25 ranking)
poetry run cite-hustle search "earnings management"

# Search by author
poetry run cite-hustle search "Smith" --author

# Get more results
poetry run cite-hustle search "financial reporting" --limit 50
```

### Other Commands

```bash
# Show sample articles
poetry run cite-hustle sample --limit 10

# Rebuild FTS indexes (if search not working)
poetry run cite-hustle rebuild-fts

# Get help
poetry run cite-hustle --help
```

## Project Structure

```
cite-hustle/
├── src/cite_hustle/
│   ├── __init__.py
│   ├── config.py              # Configuration management
│   ├── cli/
│   │   └── commands.py        # CLI commands
│   ├── database/
│   │   ├── models.py          # Database schema & connection
│   │   └── repository.py      # Data access layer
│   └── collectors/
│       ├── journals.py        # Journal registry (19 journals)
│       ├── metadata.py        # CrossRef collector ✅
│       ├── ssrn_scraper.py    # SSRN scraper ✅
│       └── pdf_downloader.py  # PDF downloader ✅
├── get_meta_articles.py       # Legacy script (reference)
├── get_pdf_links.py           # Legacy script (reference)
├── pyproject.toml
├── poetry.lock
├── .env.example
├── README.md
├── METADATA_MIGRATION.md      # Migration guide
└── SSRN_MIGRATION.md          # Migration guide
```

## Data Storage

All data is stored in Dropbox for easy syncing across machines:

```
~/Dropbox/Github Data/cite-hustle/
├── DB/
│   └── articles.duckdb        # Main database (syncs via Dropbox)
├── cache/                      # API response cache
├── ssrn_html/                 # Saved SSRN HTML pages
└── pdfs/                       # Downloaded PDFs
```

## Features

### ✅ Metadata Collection
- Fetches from CrossRef API for 19 top journals
- Automatic caching to reduce API calls
- Retry logic with exponential backoff
- Progress tracking with tqdm
- Automatic FTS index rebuilding

### ✅ SSRN Scraping  
- **Direct URL extraction** - Extracts URLs from search results (2 requests vs 4+)
- **Combined similarity scoring** - Fuzzy match (70%) + length similarity (30%)
- **Multi-strategy abstract extraction** - 4 different extraction methods for different HTML structures
- **Search box verification** - Ensures text is entered before clicking search
- **Standalone abstract extractor** - Re-extract abstracts from saved HTML files
- Configurable similarity threshold and weight parameters
- Automatic cookie handling
- HTML storage for later analysis
- Comprehensive error logging with full exception details
- Exponential backoff retry logic for rate limiting
- Screenshot capture on errors
- Resumable after interruption

### ✅ Full-Text Search
- DuckDB FTS extension with BM25 ranking
- Search titles and abstracts
- Relevance scoring
- Fast query performance

### ✅ PDF Download
- Rate limiting and retry logic
- Progress bars
- Automatic file organization

## Database Schema

### Tables

- **journals**: Journal metadata (ISSN, name, field, publisher)
- **articles**: Article metadata from CrossRef (DOI, title, authors, year)
- **ssrn_pages**: SSRN data (URL, HTML, abstract, PDF links)
- **processing_log**: Processing history and errors

### Full-Text Search

DuckDB FTS extension provides fast full-text search on:
- Article titles (fts_main_articles)
- Abstracts (fts_main_ssrn_pages)

## Supported Journals

19 top journals across 3 fields:

**Accounting (6)**: The Accounting Review, Journal of Accounting and Economics, Journal of Accounting Research, Contemporary Accounting Research, Accounting Organizations and Society, Review of Accounting Studies

**Finance (5)**: Journal of Finance, Journal of Financial Economics, Review of Financial Studies, Journal of Financial and Quantitative Analysis, Financial Management

**Economics (8)**: American Economic Review, Econometrica, Quarterly Journal of Economics, Journal of Political Economy, Review of Economic Studies, Journal of Economic Literature, Journal of Economic Perspectives, Journal of Labor Economics

## Development

### Poetry Commands

```bash
# Install project
poetry install

# Add dependency
poetry add package-name

# Add dev dependency
poetry add --group dev package-name

# Update dependencies
poetry update

# Activate virtual environment
poetry env activate

# Run command without activating environment
poetry run cite-hustle status
```

### Database Access

```python
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository

# Connect to database
db = DatabaseManager(settings.db_path)
db.connect()
repo = ArticleRepository(db)

# Query articles
articles = repo.get_articles_by_year_range(2020, 2024)
pending = repo.get_pending_ssrn_scrapes(limit=10)
stats = repo.get_statistics()
```

## Migration Status

✅ **Metadata Collection** - Complete  
✅ **SSRN Scraping** - Complete  
✅ **PDF Download** - Complete (needs PDF URL extraction)  
⏳ **Web GUI** - Future enhancement  

See `METADATA_MIGRATION.md` and `SSRN_MIGRATION.md` for detailed migration notes.

## Troubleshooting

### Search not working
```bash
poetry run cite-hustle rebuild-fts
```

### ChromeDriver not found
```bash
brew install --cask chromedriver
```

### No articles in database
```bash
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2023
```

### Want to see browser (debugging)
```bash
poetry run cite-hustle scrape --no-headless --limit 5
```

## Future Enhancements

- [ ] Extract PDF URLs from SSRN HTML pages
- [ ] Add web GUI (FastAPI + React/Streamlit)
- [ ] Deploy to cloud (Railway/Fly.io)
- [ ] Citation graph analysis
- [ ] Full-text search across PDF content
- [ ] Export to bibliography formats (BibTeX, RIS)
- [ ] Multi-user support with PostgreSQL
- [ ] Automated literature reviews

## License

Private research tool - not for redistribution.
