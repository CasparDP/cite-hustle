# Institutional Acquisition, Per-DOI Entry Point, and Skill Interface

**Date**: 2026-08-22
**Status**: Approved (design sign-off in session; execution by agents, Fable orchestrating)

## Context and approved decisions

Goal: a working end-to-end path from "get me this paper" (DOI) to a verified local PDF,
integrated with the existing `pdf_candidates` / `pdf_files` / pipeline machinery. Three
decisions were made after research and repo inspection:

1. **No two-machine split.** The single-writer runner (the user's M2 machine; the Mac
   mini is shelved, Docling does not run there) executes the full pipeline: acquire,
   verify, ingest. All other machines are read-only consumers via Dropbox. This
   preserves the existing single-writer discipline (`pipeline.py` preflight guards,
   fcntl lockfile, DuckDB file lock).
2. **Skill only; MCP server deferred.** The CLI is the service layer. A `cite-hustle`
   skill in the dot-files repo (`~/Local/GitHub/dot-files/claude/skills/`, symlinked
   into `~/.claude/skills/` on every machine) documents the interface. No long-running
   server, no extra DuckDB connection.
3. **EZproxy-first; BrowZine/LibKey dropped from v1.** Verified: the Third Iron API
   returns 401 without a library-issued key; libkey.io is a JS-only SPA whose
   robots.txt disallows everything. The sanctioned automatable route is EUR's EZproxy:
   `https://eur.idm.oclc.org/login?url=https://doi.org/{DOI}` with a one-time ERNA
   login in a persistent Chrome profile. For later reference: EUR's LibKey/BrowZine
   library ID is 2163; WAYFless deep link `https://libkey.io/libraries/2163/{DOI}`.

## Source order (target flow)

```
DOI -> existing local PDF (pdf_files row)
    -> SSRN (existing scrape/download path, batch)
    -> OA / NBER / arXiv fallback resolvers (existing, httpx)
    -> EZproxy institutional resolver (NEW, Selenium, authenticated)
    -> verify (existing PDFVerifier)
    -> store / wiki ingest (existing)
```

## WP1: Institutional resolver (`src/cite_hustle/collectors/institutional.py`)

The existing resolver contract (`fallback_resolvers.py` `BaseResolver.resolve()`
returning a `Candidate` whose `pdf_url` is fetchable by plain httpx) does not fit:
EZproxy-entitled PDFs must be fetched inside the authenticated browser. Therefore the
institutional resolver is a **separate Selenium-based stage** modeled on
`SeleniumPDFDownloader`, not a fourth entry in `RESOLVERS`.

### Components

- **`InstitutionalDownloader` class**: Selenium Chrome with
  `--user-data-dir=<settings.chrome_profile_dir>` (default
  `~/.cache/cite-hustle/chrome-profile`; local disk, never Dropbox). Reuses the
  restart-every-N and error-recovery patterns from `selenium_pdf_downloader.py`, but
  browser restarts must NOT lose auth state (the profile persists cookies on disk).
- **Session management**:
  - `cite-hustle login` command: opens headful Chrome on the profile, navigates to the
    EZproxy login, the user completes ERNA + MFA manually, command confirms an
    authenticated session (see health check) and exits.
  - Session health check: load an EZproxy-prefixed known-entitled URL; if the response
    lands on the ERNA/EZproxy login page, the session is expired. On expiry during a
    batch: abort the institutional stage with a clear "run `cite-hustle login`"
    message (logged to `pipeline_runs` detail); never loop retries against the login
    page.
- **Per-DOI flow**: build `settings.ezproxy_prefix + "https://doi.org/" + doi` ->
  navigate -> publisher landing page -> locate PDF:
  1. `<meta name="citation_pdf_url">` (generic, covers most publishers),
  2. per-publisher extractors for the journal registry's publishers (Elsevier/
     ScienceDirect, Wiley, Springer, Oxford UP, Chicago, Taylor & Francis, INFORMS),
  3. fail with a structured reason (`no_pdf_link`, `paywall`, `not_entitled`,
     `session_expired`, `nav_error`).
  Download in-browser to a temp dir, validate `%PDF-` magic and size (reuse
  `http_pdf_downloader.doi_slug_filename` for naming), move to
  `settings.pdf_storage_dir`.
- **Recording** (same shape as the fallback stage in `commands.py`):
  - success: `record_pdf_candidate(doi, "ezproxy", status="downloaded", ...)`,
    `upsert_pdf_file(doi, source="ezproxy", ...)`,
    `log_processing(doi, "resolve_institutional", "success")`.
  - no PDF found: `record_pdf_candidate(..., status="no_match", error_message=reason)`.
  - transient/auth errors: `record_pdf_candidate(..., status="error", ...)`.
  The verifier's non-SSRN quarantine branch (`verifier.py` `_quarantine` else-branch)
  then handles mismatch recycling with no changes.
- **Politeness**: per-article delay (default `settings.crawl_delay`-class setting),
  small batches (`settings.institutional_batch`, default 50). Citation-workflow
  volumes only; publisher licenses prohibit systematic bulk downloading.

### Shared fix: status-aware recheck window

`ArticleRepository.get_recent_candidate_checks(cutoff)` filters only on `checked_at`,
so a `status='error'` row (e.g. expired session) suppresses that `(doi, source)` pair
for the full recheck window (default 90 days) exactly like a genuine `no_match`.
Change: the method takes two cutoffs (or a per-status mapping) so `error` rows re-enter
the pool after a short window (default 2 days, new setting `error_recheck_days`) while
`no_match`/`downloaded` keep the long window. Applied in `resolve-fallbacks` and the
new institutional stage. Benefits existing resolvers too.

### Eligibility query

New repository method `get_articles_for_institutional(limit)`: articles with no
`pdf_files` row, where the SSRN path is exhausted (same predicate as
`get_articles_without_pdf`) AND all three fallback sources have a non-`downloaded`
`pdf_candidates` row (i.e. fallbacks already tried). Institutional runs last because it
is the most expensive and most rate-sensitive source.

## WP2: Per-DOI entry point

The CLI today is batch-only. Two new commands, respecting single-writer:

- **`cite-hustle get <doi>`** (runner only; takes the write lock like other write
  commands): resolve one DOI end-to-end.
  1. `get_article_by_doi(doi)` (NEW repository method on `articles`);
  2. if unknown, fetch metadata for that single DOI from CrossRef and
     `insert_article` (reuse `MetadataCollector` plumbing; journal fields may be
     outside the registry, that is fine);
  3. if a verified local PDF exists, report the path and stop;
  4. else run OA/NBER/arXiv resolvers for this DOI (reuse resolver classes directly,
     bypassing the recheck memo with an explicit `--force` semantics for single-DOI);
  5. else run the institutional resolver for this DOI;
  6. on download, run `PDFVerifier.verify_one` immediately;
  7. print a human-readable result: status, source, path, verify status, or the
     failure reasons per source.
- **`cite-hustle request <doi> [--note ...]`** (any machine): append a JSON line
  `{doi, requested_at, machine, note}` to `<dropbox_base>/requests.jsonl`. No DB
  access at all (do not even open the DB; also avoid `Settings` path properties that
  mkdir where not needed). Idempotent: skip append if the DOI is already pending.
- **`requests` pipeline stage** (runner): drain `requests.jsonl`, run the `get` flow
  per DOI, rewrite the file with only still-unresolved entries, and list results in
  the run report. Runs first in both profiles so user requests get priority.

## WP3: `cite-hustle` skill (dot-files repo)

One skill directory `dot-files/claude/skills/cite-hustle/` (SKILL.md) documenting:

- machine roles: read-only commands (`status`, `dashboard`, `search`, `sample`,
  `journals`) safe anywhere; `request <doi>` safe anywhere; `get`, `login`,
  `pipeline`, and all write commands only on the runner;
- the "get me this paper" recipes: on the runner `poetry run cite-hustle get <doi>`;
  elsewhere `poetry run cite-hustle request <doi>`;
- how to inspect failures: `pdf_candidates` statuses via `dashboard`, run reports in
  `<dropbox_base>/reports/`, `processing_log` stages;
- session maintenance: when institutional downloads fail with `session_expired`, run
  `cite-hustle login` headful on the runner;
- pointers, not duplicated logic: the skill wraps the CLI, never raw SQL.

The repo gains no `.claude/skills` copy; dot-files is the single home (synced by the
existing symlink setup).

## WP4: Pipeline, config, deploy integration

- `PROFILES` in `pipeline.py`: `monthly = [requests, collect, scrape, enrich,
  download, fallbacks, institutional, verify, ingest, index, fts]`; `incremental =
  [requests, scrape, download, fallbacks, institutional, verify, ingest, index, fts]`.
  Note the stage-validation gotcha: `--stages` validates against
  `PROFILES["monthly"]`, so both new stages must appear there.
- `stage_invokes` in `commands.py`: map `requests` and `institutional` via
  `ctx.invoke` with settings-driven batch sizes.
- New `Settings` fields (env prefix `CITE_HUSTLE_`):
  `ezproxy_prefix: str = "https://eur.idm.oclc.org/login?url="`,
  `chrome_profile_dir: Path = ~/.cache/cite-hustle/chrome-profile`,
  `institutional_batch: int = 50`,
  `institutional_delay: int = 10`,
  `error_recheck_days: int = 2`.
- `deploy/install.sh` env template and `deploy/README.md`: document the new stages,
  the one-time `cite-hustle login`, and that the M2 machine is the runner.
- Schema: **no migrations**. `pdf_files.source` has no CHECK constraint; `'ezproxy'`
  is a new value by convention. Docs (CLAUDE.md schema block, README) updated.

## WP5: Tests

TDD per work package; extend the `unittest.mock` style of `test_ssrn_blocking.py`
plus an in-memory DuckDB fixture (`DatabaseManager(":memory:")` if supported, else
tmp_path DB):

- EZproxy URL construction; PDF-link extraction from saved publisher-HTML fixtures
  (one fixture per extractor + the `citation_pdf_url` generic case);
- session-expiry detection (login-page markers) and the abort behavior;
- status-aware recheck windows (error vs no_match cutoffs);
- `get_article_by_doi`, `get_articles_for_institutional` eligibility;
- `request` queue append/idempotency/drain;
- `get` command control flow with mocked resolvers/downloader.

## Error handling summary

| Failure | Behavior |
|---|---|
| EZproxy session expired | Abort institutional stage/`get` step with "run cite-hustle login"; `pdf_candidates` rows `status='error'` (short recheck) |
| Publisher page has no PDF / not entitled | `status='no_match'` with reason (long recheck) |
| Download not a PDF / truncated | `status='error'`, file discarded |
| Verifier mismatch | Existing quarantine + `record_pdf_candidate(..., 'no_match', 'Verified mismatch')` |
| Queue file conflict (Dropbox) | Drain rewrites atomically (temp + rename); conflicted-copy files surfaced by existing preflight guards |

## Out of scope (v1)

- LibKey deep-link locator assist (documented above for later; keep low-volume if ever used).
- Preprint-to-version-of-record upgrades (`pdf_files` is one-row-per-DOI; upgrading
  needs new eligibility logic).
- MCP server (thin stdio wrapper over the CLI if ever needed).
- eduVPN routing.

## Execution model

Fable orchestrates. Producers: sonnet for plumbing (WP2 repo methods, queue, recheck
fix), opus or codex for the institutional resolver (WP1), sonnet for CLI/pipeline/
deploy (WP4), Fable for the skill prose (WP3). pi (glm-5.2:cloud via Ollama) reviews
each phase via the pi-review skill. Human-only steps: the one-time ERNA/MFA login and
a supervised first institutional download on the runner.
