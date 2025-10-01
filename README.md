# Cite-Hustle

Academic literature research tool for automated collection of papers from top journals.

## Overview

Cite-Hustle automates the workflow of collecting academic papers:
1. **Collect** article metadata from CrossRef API
2. **Scrape** SSRN pages to find abstracts and PDF links
3. **Download** PDFs for offline reading

## Setup

### Prerequisites
- Python 3.12+
- Poetry for dependency management
- Chrome browser (for Selenium scraping)

### Installation

```bash
# Navigate to repository
cd /Users/casparm2/Local/GitHub/cite-hustle

# Install dependencies with Poetry
poetry install

# Activate the virtual environment (optional - Poetry handles this automatically)
poetry shell
```

### Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings
# Especially set CITE_HUSTLE_CROSSREF_EMAIL
```

### Initialize Database

```bash
# Initialize the DuckDB database schema
poetry run cite-hustle init

# Or if you activated the shell:
cite-hustle init
```

## Usage

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
# Collect article metadata from CrossRef
poetry run cite-hustle collect --field accounting --year-start 2020 --year-end 2024

# Note: This command needs migration of get_meta_articles.py logic
```

### Scrape SSRN

```bash
# Scrape SSRN for articles not yet processed
poetry run cite-hustle scrape --limit 100 --delay 5

# Note: This command needs migration of get_pdf_links.py logic
```

### Download PDFs

```bash
# Download PDFs from SSRN
poetry run cite-hustle download --limit 50 --delay 2
```

### Search

```bash
# Search articles by title (requires FTS setup)
poetry run cite-hustle search "earnings management" --limit 20
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
│       ├── journals.py        # Journal registry
│       ├── metadata.py        # CrossRef collector (TODO)
│       ├── ssrn_scraper.py    # SSRN scraper (TODO)
│       └── pdf_downloader.py  # PDF downloader
├── get_meta_articles.py       # Legacy script to migrate
├── get_pdf_links.py           # Legacy script to migrate
├── pyproject.toml
├── poetry.lock
├── .env.example
└── README.md
```

## Data Storage

All data is stored in Dropbox for easy syncing across machines:

```
~/Dropbox/Github Data/cite-hustle/
├── DB/
│   └── articles.duckdb        # Main database (syncs via Dropbox)
├── cache/                      # API response cache
├── metadata/                   # CSV exports (optional)
├── pdfs/                       # Downloaded PDFs
└── ssrn_html/                 # Saved HTML pages
```

## Migration TODO

The following legacy scripts need to be migrated:

1. **get_meta_articles.py** → `src/cite_hustle/collectors/metadata.py`
   - Migrate CrossRef API logic
   - Integrate with new database structure
   - Use JournalRegistry for journal definitions

2. **get_pdf_links.py** → `src/cite_hustle/collectors/ssrn_scraper.py`
   - Migrate Selenium scraping logic
   - Integrate with new database structure
   - Improve HTML storage and abstract extraction

## Database Schema

### Tables

- **journals**: Journal metadata (ISSN, name, field, publisher)
- **articles**: Article metadata from CrossRef
- **ssrn_pages**: SSRN page data (HTML, abstracts, PDF links)
- **processing_log**: Processing history and errors

### Full-Text Search

DuckDB FTS extension provides fast full-text search on:
- Article titles
- Abstracts

## Development

### Run Commands

```bash
# Check status
poetry run cite-hustle status

# Initialize fresh database
poetry run cite-hustle init

# View all commands
poetry run cite-hustle --help

# Or activate shell to avoid 'poetry run' prefix
poetry shell
cite-hustle status
```

### Add Dependencies

```bash
# Add a new package
poetry add package-name

# Add a dev dependency
poetry add --group dev package-name

# Update dependencies
poetry update
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
print(articles.head())
```

## Poetry Cheat Sheet

```bash
# Install project
poetry install

# Activate virtual environment
poetry shell

# Run command without activating shell
poetry run cite-hustle status

# Add dependency
poetry add requests

# Add dev dependency
poetry add --group dev pytest

# Update all dependencies
poetry update

# Show installed packages
poetry show

# Export requirements.txt (if needed)
poetry export -f requirements.txt --output requirements.txt

# Remove dependency
poetry remove package-name
```

## Future Enhancements

- [ ] Migrate legacy scripts
- [ ] Add abstract extraction from HTML
- [ ] Implement PDF text extraction
- [ ] Add web GUI (FastAPI + Streamlit/React)
- [ ] Deploy to cloud (Railway/Fly.io)
- [ ] Add citation graph analysis
- [ ] Implement full-text search across PDF content
- [ ] Add export to bibliography formats (BibTeX, RIS)

## License

Private research tool - not for redistribution.
