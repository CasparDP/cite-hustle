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
- Initializes all tables (`journals`, `articles`, `ssrn_pages`, `processing_log`)
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

- `--field <field>` - Filter by field: `accounting`, `finance`, `economics`, or `all` (default: `all`)

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

- `--field <field>` - `accounting`, `finance`, `economics`, or `all` (default: `all`)
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
- `--delay <seconds>` - Delay between requests (default: `5`)
- `--threshold <0-100>` - Minimum similarity threshold for matching (default: `85`)
- `--headless` / `--no-headless` - Run browser in headless mode (default: headless)

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
- `--concurrency <n>` - Concurrent OpenAlex requests (default: `8`)
- `--delay <seconds>` - Delay between OpenAlex requests (default: `0`)
- `--force` - Overwrite existing abstracts
- `--print-abstracts <n>` - Print the most recent enriched abstracts
- `--skip-fts-rebuild` - Skip rebuilding search indexes after enrichment

**When to use:** After `scrape`, before `download` to fill missing abstracts.

---

### 4. `download`

Download PDFs from SSRN for scraped articles.

```bash
cite-hustle download [OPTIONS]
```

**Options:**

- `--limit <n>` - Limit number of PDFs to download (default: all pending)
- `--delay <seconds>` - Delay between downloads (default: `2`)
- `--use-selenium` - Use browser automation (recommended for SSRN/Cloudflare)
- `--headless` / `--no-headless` - Run browser in headless mode (default: headless, Selenium path only)

**Examples:**

```bash
cite-hustle download --use-selenium --limit 50
cite-hustle download --use-selenium --no-headless
cite-hustle download --use-selenium --delay 5
cite-hustle download --limit 50
```

**What it does:**

- Downloads PDFs for articles with SSRN pages
- Uses browser automation when `--use-selenium` is enabled
- Saves PDFs to configured directory
- Updates database download status
- Logs successful/failed downloads

**When to use:** After `scrape` when you want local PDFs

**Note:** Direct HTTP downloads are often blocked by bot protection. Prefer `--use-selenium`.

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

# 6) Download PDFs
cite-hustle download --use-selenium --limit 50

# 7) Search collection
cite-hustle search "earnings management"
cite-hustle search "Smith" --author

# 8) Final status
cite-hustle status
```

---

## Practical Tips

### Rate limiting

- **CrossRef collection:** parallel mode is faster but may hit API limits
- **SSRN scraping:** use higher delays for reliability (e.g., `70+`)
- **PDF downloads:** with Selenium, small delays (e.g., `2-5`) are usually fine

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
cite-hustle download --use-selenium --no-headless --limit 5
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
