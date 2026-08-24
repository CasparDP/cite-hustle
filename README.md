# Cite-Hustle

A personal research tool for building a local, searchable corpus of accounting,
finance, economics, and management papers. It collects article metadata, finds
matching working papers and open-access PDFs, verifies every downloaded file,
and uses an authenticated institutional route only as a last resort. State lives
in local DuckDB, while PDFs and HTML stay on disk; titles and abstracts are
searchable with full-text search.

> **Scope and respectful use.** This is a personal tool for academic literature
> review. It paces requests and does not redistribute downloaded content. The
> institutional fallback uses only the user's own university entitlement. If you
> reuse it, you are responsible for the terms, licences, and rate limits of every
> metadata, working-paper, and publisher service you access.

## What it does

1. **Collect** article metadata from the CrossRef API (by journal ISSN and year).
2. **Scrape** SSRN to match papers and recover abstracts.
3. **Enrich** any still-missing abstracts via the OpenAlex API.
4. **Acquire** PDFs in order: SSRN, OA/NBER/arXiv, then EUR EZproxy.
5. **Verify** each PDF against its article metadata and quarantine mismatches.
6. **Search** titles and abstracts locally with BM25 full-text ranking.
7. **Ingest** verified PDFs into the local research wiki when requested.
8. **Export** terminal Elsevier residuals into pdfgrabba's existing manifest.
9. **Import** completed pdfgrabba downloads back into verification and wiki ingestion.

## Setup

### Prerequisites

- Python 3.11+
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

# 5. Try the remaining free PDF sources, then the authenticated last resort
poetry run cite-hustle resolve-fallbacks --limit 200
poetry run cite-hustle institutional --limit 50

# 6. Verify PDFs before downstream use
poetry run cite-hustle verify-pdfs

# 7. Search the corpus
poetry run cite-hustle search "earnings management"
```

### Download SSRN PDFs

SSRN sits behind Cloudflare, so scraping and downloads run through a **real,
visible Chrome window** via SeleniumBase UC mode. The collectors use
`uc_open_with_reconnect()` because ordinary WebDriver navigation is unreliable
with UC on Chrome 151. Headless mode is blocked. No SSRN login is required: only
author-posted, openly available PDFs are downloaded.

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
- **Run it while you're at the machine.** The visible browser only clears
  SSRN's Cloudflare challenge on an active, unlocked screen, so run downloads in
  resumable chunks rather than leaving them unattended on a machine that locks:

  ```bash
  caffeinate -i poetry run cite-hustle download --limit 200
  ```

  Fully unattended scheduling is reliable only on the dedicated runner laptop,
  which stays awake, logged in, and never locks (see
  [`deploy/README.md`](deploy/README.md)). On a normal machine a locked screen
  stalls the Cloudflare challenge and downloads crawl.

It is normal for some papers to be unavailable (the author never posted the full
text). Those count as "not available", not failures.

### Getting a specific paper

For a single DOI, skip the batch pipeline and fetch it directly: metadata,
then OA/NBER/arXiv fallbacks, then EZproxy institutional access, then
verification.

```bash
# On the runner (needs the DB write lock)
poetry run cite-hustle get 10.1234/example.doi

# From any other machine: queue it, the runner picks it up next run
poetry run cite-hustle request 10.1234/example.doi --note "for lit review"
poetry run cite-hustle process-requests   # or drain the queue manually on the runner
```

Institutional access goes through EUR's EZproxy and needs a one-time login
(and again whenever a run reports `session_expired`):

```bash
poetry run cite-hustle login
poetry run cite-hustle institutional --limit 50   # batch EZproxy resolution
```

Publisher copies are requested only after SSRN and the free OA/NBER/arXiv
fallbacks have failed. Wiley and OUP are live-verified through EZproxy.
ScienceDirect/Elsevier is a known exception: its **I am not a robot** challenge
returned on a fresh unattended run even after a human-assisted browser session,
and its API cannot be used without an Elsevier-issued API key. Handle true
Elsevier residuals with the semi-interactive pdfgrabba workflow. cite-hustle can
append only the fully exhausted residuals to an existing pdfgrabba manifest:

```bash
poetry run cite-hustle export-pdfgrabba \
  --manifest "$HOME/Dropbox/Github Data/cite-hustle/pdfs/download_manifest.json" --dry-run
```

Remove `--dry-run` only after checking the counts. The exporter opens DuckDB
read-only, preserves every existing manifest entry/status/field, appends only new
normalized DOIs as `pending`, and writes with atomic replacement. A missing manifest
fails unless `--create` is explicit. Do not run it while pdfgrabba is actively
rewriting the same file. The export does not invoke pdfgrabba, launch Chrome,
download a PDF, call an Elsevier API, or participate in the scheduled pipeline.

After the semi-interactive pdfgrabba run, return completed files to cite-hustle:

```bash
poetry run cite-hustle import-pdfgrabba \
  --manifest "$HOME/Dropbox/Github Data/cite-hustle/pdfs/download_manifest.json" \
  --dry-run

# After reviewing the counts, omit --dry-run, then continue the normal pipeline:
poetry run cite-hustle verify-pdfs
poetry run cite-hustle wiki-ingest
```

`import-pdfgrabba` is runner-only because it writes new `pdf_files` rows. It accepts
only manifest entries marked `downloaded` or `skipped`, requires a known DOI and a
real PDF in the manifest directory, preserves every existing DuckDB PDF row, and
queues new source=`pdfgrabba` rows for verification. It never edits the manifest and
is not scheduled yet.

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
├── pdfs/                # downloaded PDFs + pdfgrabba manifest (mismatches under quarantine/)
├── reports/             # scheduled-pipeline run reports
└── wiki/                # generated research-wiki sources and indexes
```

Override the location with `CITE_HUSTLE_DROPBOX_BASE`. Stored paths use the
`$HOME/...` form so the database is portable across machines.

## Supported journals

Spans accounting, finance, economics, and management. Run `cite-hustle journals` for the current list, or collect everything with `--field all`. The registry lives in [`journals.py`](src/cite_hustle/collectors/journals.py).

## Project structure

```
cite-hustle/
├── src/cite_hustle/
│   ├── config.py                       # settings (pydantic-settings)
│   ├── cli/commands.py                 # Click CLI
│   ├── acquire.py                      # ordered PDF acquisition flows
│   ├── pdfgrabba_export.py             # one-way terminal Elsevier manifest bridge
│   ├── pdfgrabba_import.py             # completed-download return into PDF verification
│   ├── verifier.py                     # PDF/article matching + quarantine
│   ├── pipeline.py                     # scheduled profiles and run reports
│   ├── database/
│   │   ├── models.py                   # schema + FTS indexes
│   │   └── repository.py               # all database I/O
│   ├── wiki/                            # verified-PDF ingestion bridge + indexes
│   └── collectors/
│       ├── journals.py                 # journal registry
│       ├── metadata.py                 # CrossRef collector
│       ├── ssrn_scraper.py             # visible SeleniumBase-UC SSRN scraper
│       ├── openalex_enricher.py        # OpenAlex abstract enrichment
│       ├── selenium_pdf_downloader.py  # visible SeleniumBase-UC SSRN downloader
│       ├── fallback_resolvers.py        # OA/NBER/arXiv resolvers
│       ├── institutional.py             # authenticated EZproxy browser route
│       └── publisher_pdf.py             # publisher PDF-link extraction
├── deploy/                              # M2 runner LaunchAgents and operations
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
| `pdf_files` | Winning PDF per DOI and its verification state |
| `pdf_candidates` | Memoized OA, NBER, arXiv, and institutional attempts |
| `processing_log` | Per-step processing history |
| `wiki_pages` | Wiki-ingestion state and stable citation keys |
| `pipeline_runs` | Per-stage scheduled-run outcomes |

Full-text search uses the DuckDB FTS extension (BM25) over article titles and
SSRN abstracts.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Search returns nothing | `poetry run cite-hustle rebuild-fts` |
| SSRN scrape/download fails | Make sure Chrome is installed, run visibly, and use the current SeleniumBase UC implementation; raw `undetected-chromedriver` is incompatible with Chrome 151 |
| SSRN reports an instant Cloudflare block | Stop the run, keep a high delay, and wait; do not hammer retries on a challenge-flagged IP |
| Elsevier/ScienceDirect does not download | Expected for unattended runs: the recurring human-verification challenge has no autonomous route without an Elsevier API key; export terminal residuals with `export-pdfgrabba`, then use pdfgrabba semi-interactively |
| `export-pdfgrabba` refuses a manifest | Fix invalid/non-list JSON or duplicate normalized DOIs; use `--create` only when intentionally starting a new manifest, and never export during an active pdfgrabba rewrite |
| `import-pdfgrabba` imports nothing | Only `downloaded`/`skipped` entries with a known DOI and a valid target PDF are ready; run `--dry-run` for the missing/invalid/already-present breakdown |
| A pdfgrabba PDF is quarantined as a mismatch | The importer never rewrites manifest state. Inspect the mismatch first; if pdfgrabba should retry it, manually reset that manifest entry to a retryable status such as `failed` |
| `DuckDB lock` error | Close any other process holding the database open (CLI, notebook, MCP server) |
| Institutional ChromeDriver mismatch | Plain Selenium Manager should match Chrome automatically; update the environment before pinning a driver manually |
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
