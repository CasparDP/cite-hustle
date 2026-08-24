# cite-hustle session handoff

Updated: 2026-08-24 (pdfgrabba round trip complete; SSRN runner readiness rechecked)

## Current verified state

- **SSRN is migrated off `undetected-chromedriver`.** Both
  `collectors/ssrn_scraper.py` and `collectors/selenium_pdf_downloader.py` now use
  SeleniumBase `Driver(uc=True)` in visible mode and navigate with
  `uc_open_with_reconnect(url)`, never `.get()`. Browser-death errors abort/restart
  safely, and the Cloudflare classification/circuit-breaker contract in
  `tests/test_ssrn_blocking.py` remains unchanged.
- **The end-to-end SSRN smoke passed on Chrome 151.** With limit 1, DOI
  `10.1016/j.jacceco.2026.101901` matched SSRN, stored a 913-character abstract and
  HTML, then downloaded a valid 69-page PDF (1,761,695 bytes). `ssrn_pages`,
  `pdf_files`, and `processing_log` contain the expected success state;
  `pdf_candidates` semantics were not changed.
- **The full retained-code suite is green:** 106 passed after the pdfgrabba
  export/import work. `tests/test_ssrn_blocking.py` is unmodified.
- **Institutional acquisition remains locked and working.** The plain-Selenium
  EZproxy path is live-verified for Wiley (`10.1111/jofi.70055`) and OUP
  (`10.1093/rfs/hhag032`). Do not change `collectors/institutional.py` or
  `collectors/publisher_pdf.py` as part of SSRN or Elsevier work.
- **The verification backlog is drained.** The run processed the 1,050 rows that had
  been stuck pending: 996 matched, 48 mismatches were quarantined, 3 are uncertain,
  and 3 are unreadable. The exact `verify_status = 'pending'` count is now 0. A stale
  DuckDB ART index (`idx_pdf_files_verify`) had made the indexed predicate report 811
  instead of the full-scan count of 1,050; the index was rebuilt transactionally
  without altering row data.

## Elsevier result and acquisition boundary

- The Elsevier API route is unavailable without an Elsevier API key. eduVPN supplies
  institutional entitlement but does not replace API identity, and no key is
  available for this project.
- A human-assisted, visible SeleniumBase UC diagnostic did download a ScienceDirect
  PDF after the user clicked **I am not a robot**; the code then found the `/pdfft`
  link automatically. A fresh unattended run using the same persistent profile was
  challenged immediately again. This is therefore not an autonomous/background
  route, and no experimental Elsevier browser/API implementation was retained.
- The diagnostic PDF for `10.1016/j.aos.2025.101617` is valid and retained as source
  `elsevier_manual`: 16 pages, 905,728 bytes, deterministic verification score 100
  (title 100, authors 100%). Its first `resolve_elsevier` log is the expected
  `cloudflare_challenge` failure; the second records the human-clearance success.
- Publisher PDFs are a last resort. The autonomous order remains SSRN, then
  OA/NBER/arXiv, then the already-working institutional routes. ScienceDirect
  residuals belong in the focused, semi-interactive pdfgrabba workflow.

## pdfgrabba residual round trip (implemented and verified)

`export-pdfgrabba` is a small, one-way cite-hustle -> pdfgrabba bridge that merges
only **terminal Elsevier residuals** into an explicitly supplied
`download_manifest.json`:

```bash
poetry run cite-hustle export-pdfgrabba \
  --manifest /absolute/path/to/download_manifest.json --dry-run
```

Eligibility means:

1. no `pdf_files` row;
2. SSRN has no match or its PDF is explicitly unavailable;
3. each of `oa`, `nber`, and `arxiv` has a terminal `pdf_candidates.status = 'no_match'`;
4. the article is Elsevier by publisher metadata or DOI prefix (`10.1016/`).

A read-only live dry-run on 2026-08-22 selected 48 terminal residuals, matching the
dated baseline. The six Elsevier rows with retryable OA/arXiv `error` candidates
remain excluded by the exact query predicate.

The command is registered read-only for DuckDB. It preserves all existing manifest
entries and fields, deduplicates normalized DOIs, assigns collision-free bib keys and
DOI-slug filenames only to new `pending` entries, and replaces the JSON atomically.
`--create` is required for a missing manifest; `--dry-run` performs no writes. It is
not part of the scheduled pipeline and never imports/invokes pdfgrabba, downloads a
PDF, launches a browser, or calls an API.

The paper-agnostic corpus manifest now lives at
`$HOME/Dropbox/Github Data/cite-hustle/pdfs/download_manifest.json`; it contains the
48 exported residuals, currently all `pending`. After pdfgrabba changes entries to
`downloaded` (or filesystem-reconciled `skipped`), return them to cite-hustle with:

```bash
poetry run cite-hustle import-pdfgrabba \
  --manifest "$HOME/Dropbox/Github Data/cite-hustle/pdfs/download_manifest.json" \
  --dry-run
```

The importer is a runner-only DuckDB writer. It validates every candidate before
writing, accepts only `downloaded`/`skipped` entries with a known DOI and a real PDF
in the manifest directory, never overwrites an existing `pdf_files` row, and inserts
new rows as source `pdfgrabba` with verification `pending`. It never changes the
manifest. Run `verify-pdfs` and then `wiki-ingest` to complete the corpus path. The
import is deliberately not scheduled yet.

Verification: 106 tests passed; the temporary-manifest export smoke appended 48 entries on
the first run and zero on the second, while all four seeded entries (including
downloaded/skipped/failed/no_doi state and custom fields) remained identical. The
live return dry-run saw 48 pending, zero completed, and zero ready; both the manifest
hash and DuckDB size/mtime stayed unchanged. Never run the exporter while pdfgrabba
is actively rewriting the same manifest.

## Operational guardrails and remaining housekeeping

- A read-only readiness check on 2026-08-24 found no active cite-hustle, pytest,
  SeleniumBase, ChromeDriver, or pdfgrabba process. The runner is free for a visible
  SSRN run. CLI status reported 79,510 articles, 57,832 SSRN pages, 804 downloaded
  SSRN PDFs, and 9,888 pending SSRN scrapes. The unrelated `us_cpas` scrapers and
  their 30-minute latency monitor remain running by user choice.
- Runner = this M2 machine; it is the only DuckDB writer. Open the live DB read-only
  for inspection.
- The EZproxy login lives in `~/.cache/cite-hustle/chrome-profile`. Never launch a
  second Chrome against that profile while it is in use.
- For an SSRN live test, use at most 1-2 papers with a high delay. If Cloudflare
  returns an instant 403, stop; do not hammer retries or change IP while unrelated
  scrapers are running.
- `pdf_files`/`pdf_candidates` semantics are locked. Keep the full suite and the
  pinned Cloudflare tests green.
- `main` is 30 commits ahead of `origin/main`; push is still pending. The worktree
  contains the earlier SSRN UC migration plus the tested pdfgrabba export/return
  bridge and its documentation. Preserve all of it when committing or rebasing.
- The cross-machine `request` -> `process-requests` queue roundtrip is unit-tested but
  has not yet been human-tested from a second machine.
- docling remains the wiki-ingestion driver; pypdf is only the verifier's cheap text
  sniff.
