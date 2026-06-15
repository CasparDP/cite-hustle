# Cite-Hustle

A personal research tool for building a local, searchable corpus of accounting,
finance, economics, and management papers. It collects article metadata from
CrossRef, finds the matching SSRN working-paper pages to recover abstracts,
enriches missing abstracts from OpenAlex, and downloads the open-access SSRN
PDFs. Everything is stored in a local DuckDB database with full-text search.

> **Scope and respectful use.** This is a personal tool for academic literature
> review. It accesses only publicly available metadata and author-posted working
> papers, paces its requests, and does not redistribute downloaded content. If you
> reuse it, you are responsible for complying with the terms of service and rate
> limits of CrossRef, OpenAlex, and SSRN.

## What it does

1. **Collect** article metadata from the CrossRef API (by journal ISSN and year).
2. **Scrape** SSRN to match papers and recover abstracts.
3. **Enrich** any still-missing abstracts via the OpenAlex API.
4. **Download** the SSRN PDF when the author has posted full text.
5. **Search** titles and abstracts locally with BM25 full-text ranking.

## Setup

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/) for dependency management
- Google Chrome (required for the Selenium-based SSRN steps)

### Installation

```bash
git clone https://github.com/CasparDP/cite-hustle.git
cd cite-hustle
poetry install
```

### Configuration

Copy the example environment file and adjust as needed:

```bash
cp .env.example .env
```

Setting `CITE_HUSTLE_CROSSREF_EMAIL` is optional but recommended: it opts you
into CrossRef's faster "polite pool". No value is hardcoded, and nothing is sent
if you leave it blank.

### Initialize the database

```bash
poetry run cite-hustle init
```

## Usage

See [CLI-CHEATSHEET.md](./CLI-CHEATSHEET.md) for the full command reference.
A `Makefile` wraps the common workflow:

```bash
make update            # collect + enrich current year (fast, no browser)
make update YEAR=2024  # same, for a specific year
make download          # download pending SSRN PDFs (opens a browser)
```

### Typical workflow

```bash
# 1. Collect metadata from CrossRef
poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024

# 2. Recover abstracts from SSRN (use a generous delay for large runs)
poetry run cite-hustle scrape --limit 50 --delay 70

# 3. Fill any remaining abstracts from OpenAlex (no browser needed)
poetry run cite-hustle enrich-openalex --year-start 2023 --year-end 2024

# 4. Download available SSRN PDFs
poetry run cite-hustle download

# 5. Search the corpus
poetry run cite-hustle search "earnings management"
```

### Download SSRN PDFs

SSRN sits behind Cloudflare, so downloads run through a **real, visible Chrome
window** (via undetected-chromedriver). Headless mode is reliably blocked, so it
is off by default. No SSRN login is required: only author-posted, openly
available PDFs are downloaded.

```bash
# Download all pending PDFs
poetry run cite-hustle download

# Limit the batch, or watch a few in a visible browser
poetry run cite-hustle download --limit 50
poetry run cite-hustle download --limit 5 --delay 5
```

Key behavior:

- **Resumable.** Progress is written to the database after every paper, so an
  interrupted run continues where it left off.
- **Skips dead ends.** Papers with no posted full text are marked
  "not available" and skipped on later runs. Use `--retry-unavailable` to force
  a re-check.
- **Unattended-friendly.** Leave it running to work through a large backlog. On
  macOS, prevent the machine from sleeping:

  ```bash
  caffeinate -i poetry run cite-hustle download
  ```

It is normal for some papers to be unavailable (the author never posted the full
text). Those count as "not available", not failures.

### Search

```bash
poetry run cite-hustle search "earnings management"   # by title
poetry run cite-hustle search "Smith" --author        # by author
poetry run cite-hustle search "disclosure" --limit 50
```

### Status and utilities

```bash
poetry run cite-hustle status        # database statistics
poetry run cite-hustle dashboard     # coverage and recent activity
poetry run cite-hustle journals      # list supported journals
poetry run cite-hustle rebuild-fts   # rebuild search indexes if search is empty
```

## Data storage

Data is kept outside the repository (under Dropbox by default) and is **not**
included in git:

```
~/Dropbox/Github Data/cite-hustle/
├── DB/articles.duckdb   # main database
├── cache/               # CrossRef API response cache
├── ssrn_html/           # saved SSRN HTML pages
└── pdfs/                # downloaded PDFs
```

Override the location with `CITE_HUSTLE_DROPBOX_BASE`. Stored paths use the
`$HOME/...` form so the database is portable across machines.

## Supported journals

28 journals across 4 fields. Collect everything with `--field all`.

- **Accounting (6):** The Accounting Review; Journal of Accounting and
  Economics; Journal of Accounting Research; Contemporary Accounting Research;
  Accounting, Organizations and Society; Review of Accounting Studies
- **Finance (7):** Journal of Finance; Journal of Financial Economics; Review of
  Financial Studies; Journal of Financial and Quantitative Analysis; Financial
  Management; Management Science; Journal of Corporate Finance
- **Economics (9):** American Economic Review; Econometrica; Quarterly Journal of
  Economics; Journal of Political Economy; Review of Economic Studies; Journal of
  Economic Literature; Journal of Economic Perspectives; Journal of Labor
  Economics; Journal of Human Resources
- **Management (6):** Human Resource Management; Academy of Management Annals;
  Academy of Management Journal; Academy of Management Review; Administrative
  Science Quarterly; Journal of Management

The registry lives in [`journals.py`](src/cite_hustle/collectors/journals.py).

## Project structure

```
cite-hustle/
├── src/cite_hustle/
│   ├── config.py                       # settings (pydantic-settings)
│   ├── cli/commands.py                 # Click CLI
│   ├── database/
│   │   ├── models.py                   # schema + FTS indexes
│   │   └── repository.py               # all database I/O
│   └── collectors/
│       ├── journals.py                 # journal registry
│       ├── metadata.py                 # CrossRef collector
│       ├── ssrn_scraper.py             # SSRN abstract scraper
│       ├── openalex_enricher.py        # OpenAlex abstract enrichment
│       └── selenium_pdf_downloader.py  # SSRN PDF downloader
├── scripts/                            # maintenance utilities
├── Makefile
├── pyproject.toml
└── CLI-CHEATSHEET.md
```

## Database schema

| Table | Purpose |
|-------|---------|
| `journals` | Journal metadata (ISSN, name, field, publisher) |
| `articles` | Article metadata from CrossRef (DOI, title, authors, year) |
| `ssrn_pages` | SSRN data: URL, abstract, PDF status |
| `processing_log` | Per-step processing history |

Full-text search uses the DuckDB FTS extension (BM25) over article titles and
SSRN abstracts.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Search returns nothing | `poetry run cite-hustle rebuild-fts` |
| PDF downloads all fail | Make sure Chrome is installed and run without `--headless` (headless is blocked by Cloudflare) |
| `DuckDB lock` error | Close any other process holding the database open (CLI, notebook, MCP server) |
| ChromeDriver mismatch | Update Chrome: `brew upgrade --cask google-chrome` |
| Wrong paths across machines | Set `CITE_HUSTLE_DROPBOX_BASE`, or confirm the Dropbox folder exists |

## Development

```bash
poetry install
poetry run pytest          # tests
poetry run black src/      # format (line length 100)
poetry run ruff check src/ # lint
```

## License

Released under the [MIT License](LICENSE). Note this covers the code only; the
collected metadata, abstracts, and downloaded PDFs are not included in the
repository and remain subject to their original sources' terms.
