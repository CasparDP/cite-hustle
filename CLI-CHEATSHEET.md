# CLI Cheatsheet

Quick reference for all `cite-hustle` CLI commands.

## Environment Setup

```bash
# Activate Poetry virtual environment
poetry env activate

# Or run individual commands with poetry run prefix
poetry run cite-hustle <command>
```

---

## Core Commands

### `init`

Initialize the database schema and directory structure.

```bash
cite-hustle init
```

**What it does:**

- Creates DuckDB database at configured path
- Initializes the core, PDF, wiki, and pipeline tables
  (`journals`, `articles`, `ssrn_pages`, `processing_log`, `pdf_files`,
  `pdf_candidates`, `wiki_pages`, and `pipeline_runs`)
- Sets up full-text search indexes
- Creates required data directories

**When to use:** First-time setup or after database issues

---

### `status`

Show database statistics and current progress.

```bash
cite-hustle status
```

**What it shows:**

- Total articles in database
- Articles by year (recent 5 years)
- SSRN pages scraped count
- PDFs downloaded count
- Pending tasks (scrapes/downloads)
- Database file size and location

**When to use:** Check progress at any time

---

### `dashboard`

Show a dashboard-style overview of database contents.

```bash
cite-hustle dashboard
cite-hustle dashboard --top-journals 5 --recent 5
```

**Options:**

- `--top-journals <n>` - Number of top journals to show
- `--recent <n>` - Recent processing entries to show

**When to use:** Quick snapshot of coverage, gaps, and recent activity

---

### `journals`

List journals in the registry by research field.

```bash
cite-hustle journals [OPTIONS]
```

**Options:**

- `--field <field>` - Filter by field: `accounting`, `finance`, `economics`, `management`, or `all` (default: `all`)

**Examples:**

```bash
cite-hustle journals
cite-hustle journals --field accounting
cite-hustle journals --field finance
```

**When to use:** See which journals are supported before collecting metadata

---

## Data Collection Pipeline

### 1. `collect`

Collect article metadata from CrossRef API.

```bash
cite-hustle collect [OPTIONS]
```

**Options:**

- `--field <field>` - `accounting`, `finance`, `economics`, `management`, or `all` (default: `all`)
- `--year-start <year>` - Start year (default: `2004`)
- `--year-end <year>` - End year (default: `2025`)
- `--parallel` / `--sequential` - Enable parallel processing (default: sequential)
- `--skip-fts-rebuild` - Skip rebuilding search indexes after collection
- `--force` - Force re-fetch by clearing cache and bypassing DB checks for specified years

**Examples:**

```bash
cite-hustle collect --field accounting --year-start 2020 --year-end 2024
cite-hustle collect --field all --year-start 2023
cite-hustle collect --field finance --year-start 2020 --parallel
cite-hustle collect --field all --year-start 2024 --year-end 2025 --force
```

**What it does:**

- Fetches article metadata (title, authors, DOI, year) from CrossRef
- Caches API responses to avoid re-fetching
- Saves articles to database
- Rebuilds FTS indexes automatically (unless `--skip-fts-rebuild` is used)
- Prints collection summary by journal

**When to use:** First step in the workflow

---

### 2. `scrape`

Scrape SSRN for article pages and abstracts.

```bash
cite-hustle scrape [OPTIONS]
```

**Options:**

- `--limit <n>` - Limit number of articles to scrape (default: all pending)
- `--delay <seconds>` - Delay between requests (default: `20`; raised from 5 to reduce Cloudflare challenges)
- `--threshold <0-100>` - Minimum similarity threshold for matching (default: `85`)
- `--headless` / `--no-headless` - Compatibility flag. The CLI default is still
  `--headless`, but the SSRN scraper forces visible SeleniumBase UC mode because
  headless Chrome is blocked

**Examples:**

```bash
cite-hustle scrape --limit 10
cite-hustle scrape --delay 70 --no-headless
cite-hustle scrape --delay 90 --limit 500
cite-hustle scrape --delay 3 --threshold 90
cite-hustle scrape --no-headless
cite-hustle scrape
```

**What it does:**

- Searches SSRN for each pending article
- Uses SeleniumBase `Driver(uc=True)` and `uc_open_with_reconnect()` in a visible browser
- Uses similarity matching to identify best results
- Extracts abstract and SSRN page metadata
- Saves SSRN HTML to disk
- Updates database with scrape status and errors

**When to use:** After `collect`, before `download`

---

### 3. `enrich-openalex`

Enrich missing abstracts using OpenAlex.

```bash
cite-hustle enrich-openalex --limit 200
cite-hustle enrich-openalex --year-start 2020 --year-end 2024 --concurrency 8 --delay 0.5
cite-hustle enrich-openalex --force
cite-hustle enrich-openalex --limit 50 --print-abstracts 5
```

**Options:**

- `--limit <n>` - Limit number of articles to enrich (default: all missing)
- `--year-start <year>` - Start year filter (optional)
- `--year-end <year>` - End year filter (optional)
- `--concurrency <n>` - Concurrent OpenAlex requests (default: `3`)
- `--delay <seconds>` - Delay between OpenAlex requests (default: `0`)
- `--force` - Overwrite existing abstracts
- `--print-abstracts <n>` - Print the most recent enriched abstracts
- `--skip-fts-rebuild` - Skip rebuilding search indexes after enrichment

**When to use:** After `scrape`, before `download` to fill missing abstracts.

---

### 4. `download`

Download available SSRN PDFs for scraped articles.

```bash
cite-hustle download [OPTIONS]
```

**Options:**

- `--limit <n>` - Limit number of PDFs to download (default: all pending)
- `--delay <seconds>` - Base delay between downloads, jittered (default: `3`)
- `--headless` / `--no-headless` - Run browser headless (default: visible). Headless is blocked by SSRN's Cloudflare; leave it off.
- `--retry-unavailable` - Also re-check papers previously marked "not available for download"

**Examples:**

```bash
cite-hustle download                  # all pending papers
cite-hustle download --limit 50
cite-hustle download --limit 5 --delay 5
caffeinate -i poetry run cite-hustle download   # macOS: run unattended without sleeping
```

**What it does:**

- Opens a real, visible SeleniumBase UC Chrome window and navigates with
  `uc_open_with_reconnect()` to pass SSRN's Cloudflare protection
- Downloads only author-posted, openly available PDFs (no login required)
- Marks papers with no full text "not available" and skips them next time
- Saves progress after every paper, so runs are resumable. Unattended runs require
  the dedicated runner to remain awake, logged in, and unlocked

**When to use:** After `scrape`/`enrich-openalex`, when you want local PDFs

**Note:** Some papers are unavailable because the author never posted full text;
those are reported as "not available", not failures.

---

## Institutional Acquisition & Requests

### `get`

Get one paper end-to-end: metadata -> OA/NBER/arXiv fallbacks -> EZproxy institutional -> verify.

```bash
cite-hustle get <doi> [OPTIONS]
```

**Options:**

- `--no-institutional` - Skip the EZproxy browser stage
- `--no-verify` - Skip immediate PDF verification

**Examples:**

```bash
cite-hustle get 10.1234/example.doi
cite-hustle get 10.1234/example.doi --no-institutional --no-verify
```

**What it does:**

- Looks up or fetches CrossRef metadata for the DOI
- Returns immediately if a verified local PDF already exists
- Otherwise tries OA/NBER/arXiv fallback resolvers, then the EZproxy institutional resolver
- Verifies the downloaded PDF against metadata (unless `--no-verify`)

**When to use:** You want one specific paper right away, on the runner. Runner-only (takes the DB write lock).

---

### `request`

Queue a DOI for acquisition by the runner. Works on any machine, never opens the DB.

```bash
cite-hustle request <doi> [OPTIONS]
```

**Options:**

- `--note <text>` - Why you want this paper (lands in the queue entry)

**Examples:**

```bash
cite-hustle request 10.1234/example.doi
cite-hustle request 10.1234/example.doi --note "for lit review"
```

**What it does:**

- Appends `{doi, requested_at, machine, note}` to `<dropbox_base>/requests.jsonl`
- Idempotent: skips if the DOI is already queued

**When to use:** You're on a read-only machine and want a paper fetched next time the runner drains the queue

---

### `process-requests`

Drain the requests queue, acquiring each queued DOI end-to-end.

```bash
cite-hustle process-requests
```

**What it does:**

- Runs the `get` flow for every DOI in `requests.jsonl`
- Drops resolved and `metadata_not_found` DOIs from the queue
- Keeps other failures queued with an incremented attempt count, dropped after 3 attempts

**When to use:** Runner-only (takes the DB write lock). Runs automatically as the `requests` pipeline stage, or manually to drain the queue outside a scheduled run.

---

### `institutional`

Fetch publisher PDFs through EUR's EZproxy, for articles where SSRN and OA/NBER/arXiv fallbacks failed.

```bash
cite-hustle institutional [OPTIONS]
```

**Options:**

- `--limit <n>` - Limit number of articles
- `--delay <seconds>` - Seconds between articles (default: `settings.institutional_delay`, 10)
- `--recheck-days <n>` - Re-try `no_match` pairs after N days (default: `90`)
- `--headless` / `--no-headless` - Run browser headless (default: visible; EZproxy usually needs a visible browser)

**Examples:**

```bash
cite-hustle institutional --limit 50
cite-hustle institutional --limit 20 --delay 15 --no-headless
```

**What it does:**

- Selects articles with no PDF and all fallback sources already exhausted
- Navigates each DOI through EZproxy in an authenticated browser and downloads the publisher PDF if found
- Aborts the run with a clear message if the login session has expired

**When to use:** Runner-only, after `resolve-fallbacks`. Needs a live session from `login`.

**Current publisher coverage:** Wiley and OUP are live-verified. The retained
institutional command is not an autonomous ScienceDirect/Elsevier route. A separate
visible SB-UC diagnostic succeeded only after a human cleared its recurring **I am
not a robot** challenge, and the same profile was challenged again on the next fresh
unattended run. The Elsevier API is also unavailable without an issued API key,
which eduVPN does not replace.

---

### `login`

One-time EZproxy/ERNA login for institutional PDF downloads.

```bash
cite-hustle login
```

**What it does:**

- Opens a visible Chrome window on the persistent profile and navigates through EZproxy
- Waits for you to complete the ERNA login (including MFA) in the browser
- Confirms the session landed on a publisher page, not the login page

**When to use:** Runner-only, once initially, and again whenever a run aborts with `session_expired`

## Elsevier Residual Handoff

### `export-pdfgrabba`

Append terminal Elsevier residuals to pdfgrabba's existing
`download_manifest.json` without launching pdfgrabba or mutating DuckDB.

```bash
cite-hustle export-pdfgrabba \
  --manifest /absolute/path/to/download_manifest.json [OPTIONS]
```

**Options:**

- `--manifest <path>` - Required manifest path; this is the only file the command may write
- `--dry-run` - Report eligibility and merge counts without any filesystem writes
- `--limit <n>` - Deterministic maximum number of eligible residuals
- `--create` - Explicitly allow creation when the manifest is missing

**Eligibility:** no `pdf_files` row; SSRN has no URL or an explicit
`download_pdf/unavailable` log; each of OA, NBER, and arXiv has an exact
`pdf_candidates.status = 'no_match'`; and publisher metadata contains Elsevier or
the normalized DOI starts with `10.1016/`. Missing fallback rows and all `error`
rows remain retryable and are excluded.

**Safety:** existing manifest entries always win, including downloaded, skipped,
no_doi, failed, and skipped_manual states. New DOIs are appended as `pending` with
DOI-slug filenames; writes use atomic replacement. A second identical run adds
zero. Invalid/non-list manifests, duplicate normalized DOIs, missing parents, and
missing manifests without `--create` fail without alteration. Do not run this
command while pdfgrabba is actively rewriting the same manifest.

This command is not part of the scheduled pipeline. It does not invoke pdfgrabba,
launch Chrome, download PDFs, or call an Elsevier API; ScienceDirect's recurring
human challenge remains a semi-interactive pdfgrabba step.

### `import-pdfgrabba`

Register completed pdfgrabba files in cite-hustle so the existing verifier and wiki
ingestion can process them.

```bash
cite-hustle import-pdfgrabba \
  --manifest "$HOME/Dropbox/Github Data/cite-hustle/pdfs/download_manifest.json" \
  --dry-run
```

**Options:**

- `--manifest <path>` - Required pdfgrabba manifest; files are resolved beside it
- `--dry-run` - Report all outcomes without writing DuckDB
- `--limit <n>` - Deterministic maximum number of ready PDFs to import

**Importable entries:** status is `downloaded` or `skipped`; DOI resolves uniquely to
an existing article; `target_filename` is a safe basename; and the target exists with
PDF magic bytes. Existing `pdf_files` rows always win. Missing DOIs, DOIs absent
from DuckDB, missing files, invalid PDFs, and non-terminal statuses are reported
separately without mutation.

New rows use source `pdfgrabba` and verification status `pending`. The manifest is
never edited. This is a runner-only DuckDB writer and is not scheduled. After a real
import, run `verify-pdfs`, then `wiki-ingest`. If verification quarantines a
mismatch, inspect it before manually resetting the preserved pdfgrabba entry to a
retryable status such as `failed`.

---

## Search & Inspection

### `search`

Search articles by title or author.

```bash
cite-hustle search <query> [OPTIONS]
```

**Options:**

- `<query>` - Search query (required)
- `--limit <n>` - Number of results (default: `20`)
- `--author` - Search by author instead of title

**Examples:**

```bash
cite-hustle search "earnings management"
cite-hustle search "earnings management" --limit 50
cite-hustle search "Smith" --author
cite-hustle search "accounting fraud"
```

**What it does:**

- Uses FTS-backed ranking for title search
- Supports author-name search
- Returns result details (title, authors, journal, year, DOI, relevance where available)

---

### `sample`

Show a sample of recent articles in the database.

```bash
cite-hustle sample [OPTIONS]
```

**Options:**

- `--limit <n>` - Number of articles to show (default: `10`)

**Examples:**

```bash
cite-hustle sample
cite-hustle sample --limit 20
```

---

### `rebuild-fts`

Rebuild full-text search indexes.

```bash
cite-hustle rebuild-fts
```

**What it does:**

- Recreates FTS indexes
- Re-indexes current database content
- Runs a small sanity-check search

**When to use:**

- Search results look stale or empty
- After manual DB edits
- After `collect --skip-fts-rebuild`

---

## Complete Workflow Example

```bash
# 1) First-time setup
poetry env activate
cite-hustle init

# 2) Explore supported journals
cite-hustle journals --field accounting

# 3) Collect metadata
cite-hustle collect --field accounting --year-start 2020 --year-end 2024

# 4) Check progress
cite-hustle status

# 5) Scrape SSRN
cite-hustle scrape --limit 100 --delay 70

# 6) Download author-posted SSRN PDFs
cite-hustle download --limit 50

# 7) Try the remaining free PDF sources, then institutional access
cite-hustle resolve-fallbacks --limit 200
cite-hustle institutional --limit 50

# 8) Verify downloaded files
cite-hustle verify-pdfs

# 9) Search collection
cite-hustle search "earnings management"
cite-hustle search "Smith" --author

# 10) Final status
cite-hustle status
```

---

## Practical Tips

### Rate limiting

- **CrossRef collection:** parallel mode is faster but may hit API limits
- **SSRN scraping:** use higher delays for reliability (e.g., `70+`)
- **SSRN PDF downloads:** with visible SeleniumBase UC, small delays (e.g., `2-5`) are usually fine

### Resumable operations

- Commands persist progress to DB
- Re-running continues from pending work
- Use `--limit` for safe incremental testing

### If search seems broken

```bash
cite-hustle rebuild-fts
```

### Debug with visible browser

```bash
cite-hustle scrape --no-headless --limit 5 --delay 70
cite-hustle download --no-headless --limit 5
```

### Progress monitoring

```bash
# macOS/Linux
watch -n 10 "poetry run cite-hustle status"
```

---

## Help

```bash
cite-hustle --help
cite-hustle <command> --help
```

Examples:

```bash
cite-hustle collect --help
cite-hustle scrape --help
cite-hustle download --help
```
