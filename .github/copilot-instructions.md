# Copilot instructions for cite-hustle

Purpose: Make AI agents productive fast in this Python/Poetry project that collects CrossRef metadata, scrapes SSRN (abstracts), and downloads PDFs into DuckDB with FTS search.

## Architecture (what matters)

- CLI entrypoint: `src/cite_hustle/cli/commands.py` (Click). Subcommands create DB via `DatabaseManager`, pass `ArticleRepository`, and dispatch collectors.
- Config: `src/cite_hustle/config.py` (`Settings` from pydantic-settings). Storage rooted at `$HOME/Dropbox/Github Data/cite-hustle/` unless overridden via `.env` `CITE_HUSTLE_*`.
- DB + FTS: `src/cite_hustle/database/models.py` (DuckDB). `initialize_schema()` creates tables and indexes; `create_fts_indexes()` installs/loads FTS and builds title/abstract indexes.
- Data access: `src/cite_hustle/database/repository.py`. Single gateway for inserts/updates/queries, search helpers, and processing logs.
- Collectors: `collectors/metadata.py` (CrossRef), `collectors/ssrn_scraper.py` (Selenium search + abstract), `collectors/selenium_pdf_downloader.py` (Selenium PDF). Journal registry in `collectors/journals.py`.

## Storage conventions

- Default locations (relative to `Settings.dropbox_base`): DB `DB/articles.duckdb`, PDFs `pdfs/`, HTML `ssrn_html/`, API cache `cache/`.
- Paths stored in DB should be portable: scraper converts absolute paths to `$HOME/...` before persisting (`SSRNScraper._convert_to_portable_path`). Do not store user-specific absolute paths.
- Large blobs (HTML) are saved to disk; DB stores only file path + status flags.

## Key workflows (run with Poetry)

- Init DB/FTS: `poetry run cite-hustle init`
- Collect metadata: `poetry run cite-hustle collect --field accounting --year-start 2023 --year-end 2024`
- Scrape SSRN: `poetry run cite-hustle scrape --limit 50 [--no-headless]`
- Download PDFs (Cloudflare-safe): `poetry run cite-hustle download --use-selenium --limit 20 [--no-headless]`
- Search/status/FTS: `status`, `search "earnings management" [--author]`, `rebuild-fts`.

## Project-specific patterns

- Always use `ArticleRepository` for DB I/O and logging: `insert_article`, `insert_ssrn_page`, `update_pdf_info`, `log_processing`.
- SSRN matching: results are scored by 70% fuzzy (`rapidfuzz.partial_ratio`) + 30% title-length similarity; accept if score ≥ threshold (default 85). See `SSRNScraper._calculate_combined_similarity`.
- HTML storage: call `save_html()` and persist path only (set `html_content=None` in DB).
- PDF downloads: legacy HTTP downloader is disabled due to Cloudflare; use the Selenium downloader. Both scraper/downloader auto-accept cookies and attempt Cloudflare challenge handling; support `--no-headless` for debugging.
- Anti-detection: Scraper rotates user-agents from a pool of 8 realistic browser strings, randomizes window dimensions (1920-2560 x 1080-1440), disables automation flags (`AutomationControlled`, `enable-automation`), and uses CDP commands to override `navigator.webdriver`.

## Integration points and deps

- CrossRef via `crossref_commons.iteration.iterate_publications_as_json`; set polite email with `CITE_HUSTLE_CROSSREF_EMAIL`.
- Selenium Chrome required; uses `selenium-stealth`, user-agent rotation, CDP commands to bypass anti-bot detection. Both scraper and downloader randomize window size and user-agent per session.
- DuckDB FTS installed/loaded on connect; search uses `fts_main_* .match_bm25` in repo methods.

## Extending safely

- Add a journal: update `collectors/journals.py` lists with `Journal(name, issn, field, publisher)`.
- Add fields: update schema in `models.py` and corresponding repo methods in lockstep; run `init` and consider index changes.
- New collector: implement under `collectors/`, persist via `ArticleRepository`, save large artifacts to disk with portable paths, and `log_processing` each step.

## Troubleshooting

- Search empty after collect: `poetry run cite-hustle rebuild-fts`.
- Cloudflare blocks: use `--use-selenium` and `--no-headless` to observe behavior.
- Paths wrong across machines: verify `$HOME/Dropbox/Github Data/cite-hustle` or override via `.env` with `CITE_HUSTLE_*`.

Decisions

- Dropbox base path is universal across all dev machines; use `$HOME/Dropbox/Github Data/cite-hustle` unless overridden via `.env`.
- Legacy HTTP PDF downloader remains disabled; always use the Selenium downloader path.
