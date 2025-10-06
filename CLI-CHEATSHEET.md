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
- Initializes all tables (journals, articles, ssrn_pages, processing_log)
- Sets up full-text search indexes
- Creates required data directories

**When to use:** First time setup or after database corruption

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

### `journals`
List journals in the registry by research field.

```bash
cite-hustle journals [OPTIONS]
```

**Options:**
- `--field <field>` - Filter by field: `accounting`, `finance`, `economics`, or `all` (default: `all`)

**Examples:**
```bash
cite-hustle journals                          # List all journals
cite-hustle journals --field accounting       # List only accounting journals
cite-hustle journals --field finance          # List only finance journals
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
- `--field <field>` - Field to collect: `accounting`, `finance`, `economics`, or `all` (default: `all`)
- `--year-start <year>` - Start year (default: `2004`)
- `--year-end <year>` - End year (default: `2025`)
- `--parallel` / `--sequential` - Enable parallel processing (default: sequential)
- `--skip-fts-rebuild` - Skip rebuilding search indexes after collection

**Examples:**
```bash
cite-hustle collect --field accounting --year-start 2020 --year-end 2024
cite-hustle collect --field all --year-start 2023
cite-hustle collect --field finance --year-start 2020 --parallel
```

**What it does:**
- Fetches article metadata (title, authors, DOI, year) from CrossRef
- Caches API responses to avoid re-fetching
- Saves articles to database
- Automatically rebuilds FTS indexes (unless `--skip-fts-rebuild` used)
- Provides summary of articles collected per journal

**When to use:** First step - get article metadata before scraping/downloading

---

### 2. `scrape`
Scrape SSRN for article pages, abstracts, and PDF links.

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
cite-hustle scrape --limit 10                       # Scrape 10 articles
cite-hustle scrape --delay 3 --threshold 90         # Faster crawl, stricter matching
cite-hustle scrape --no-headless                    # Show browser (debugging)
cite-hustle scrape                                  # Scrape all pending articles
```

**What it does:**
- Searches SSRN for each article using title/author
- Uses fuzzy matching to find correct paper
- Extracts abstract, PDF URL, and SSRN metadata
- Saves HTML content to disk
- Updates database with scraping results
- Logs successes, failures, and no-matches

**When to use:** After collecting metadata, before downloading PDFs

**Rate limiting:** Respects `--delay` to avoid overwhelming SSRN servers

---

### 3. `download`
Download PDFs from SSRN for scraped articles.

```bash
cite-hustle download [OPTIONS]
```

**Options:**
- `--limit <n>` - Limit number of PDFs to download (default: all pending)
- `--delay <seconds>` - Delay between downloads (default: `2`)

**Examples:**
```bash
cite-hustle download --limit 50                # Download 50 PDFs
cite-hustle download --delay 1                 # Faster downloads
cite-hustle download                           # Download all pending PDFs
```

**What it does:**
- Downloads PDFs from SSRN URLs found during scraping
- Saves PDFs to configured directory
- Updates database with download status
- Logs successful/failed downloads

**When to use:** After scraping to get full paper PDFs

**Note:** Only downloads articles where PDF URL was found during scraping

---

## Search & Analysis

### `search`
Full-text search articles by title or author.

```bash
cite-hustle search <query> [OPTIONS]
```

**Options:**
- `<query>` - Search query (required)
- `--limit <n>` - Number of results (default: `20`)
- `--author` - Search by author instead of title

**Examples:**
```bash
cite-hustle search "earnings management"           # Search titles
cite-hustle search "earnings management" --limit 50
cite-hustle search "Smith" --author                # Search authors
cite-hustle search "accounting fraud"
```

**What it does:**
- Uses BM25 full-text search on article titles and abstracts
- Returns ranked results by relevance score
- Shows article details (title, authors, journal, year, DOI)

**When to use:** Find specific papers or topics in your database

---

### `sample`
Show a sample of recent articles from the database.

```bash
cite-hustle sample [OPTIONS]
```

**Options:**
- `--limit <n>` - Number of articles to show (default: `10`)

**Examples:**
```bash
cite-hustle sample                    # Show 10 recent articles
cite-hustle sample --limit 20         # Show 20 recent articles
```

**What it does:**
- Displays most recently added articles
- Shows title, authors, journal, year, and DOI

**When to use:** Quick check of what's in the database

---

### `rebuild-fts`
Rebuild full-text search indexes.

```bash
cite-hustle rebuild-fts
```

**What it does:**
- Drops and recreates FTS indexes
- Re-indexes all articles and abstracts
- Tests search functionality after rebuild

**When to use:**
- Search not returning expected results
- After manual database modifications
- After collecting articles with `--skip-fts-rebuild` flag

---

## Complete Workflow Example

```bash
# 1. Setup (first time only)
poetry env activate
cite-hustle init

# 2. Check what journals are available
cite-hustle journals --field accounting

# 3. Collect article metadata
cite-hustle collect --field accounting --year-start 2020 --year-end 2024

# 4. Check status
cite-hustle status

# 5. Scrape SSRN for abstracts and PDF links
cite-hustle scrape --limit 100 --delay 5

# 6. Download PDFs
cite-hustle download --limit 50 --delay 2

# 7. Search your collection
cite-hustle search "earnings management"
cite-hustle search "Smith" --author

# 8. Check final status
cite-hustle status
```

---

## Tips & Best Practices

### Rate Limiting
- **CrossRef API:** Built-in caching, parallel mode may hit limits
- **SSRN Scraping:** Use `--delay 5` minimum to be respectful
- **PDF Downloads:** Use `--delay 2` minimum

### Resumable Operations
- All operations save progress to database
- Interrupted commands can be resumed by running again
- Use `--limit` for testing before full runs

### Search Not Working?
```bash
cite-hustle rebuild-fts
```

### Parallel Collection
```bash
# Faster but may hit rate limits
cite-hustle collect --field accounting --year-start 2023 --parallel
```

### Progress Monitoring
```bash
# Run in another terminal to watch progress
watch -n 10 "poetry run cite-hustle status"
```

### Testing Commands
```bash
# Test with small limits first
cite-hustle collect --field accounting --year-start 2024 --year-end 2024
cite-hustle scrape --limit 5 --no-headless
cite-hustle download --limit 5
```

---

## Help

Get help for any command:

```bash
cite-hustle --help
cite-hustle <command> --help
```

Example:
```bash
cite-hustle collect --help
cite-hustle scrape --help
```
