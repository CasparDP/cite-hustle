# cite-hustle session handoff

Updated: 2026-10-03 (VM runner, CrossRef fix, new abstract sources)

## Current state

- **Runner is the Ubuntu desktop VM** (`ubuntu-vm`, GPU, expires in a few months). It is
  the only DB writer and runs everything, including verify and wiki ingestion. The Macs
  stay read-only. Setup is machine-agnostic: `make doctor` plus the Linux/macOS
  checklists and "Moving to a new runner" in `deploy/README.md`.
- **`make update` is fixed.** The VM's run on 2026-10-03 logged 35 `metadata_fetch`
  failures (`KeyError 'next-cursor'` from `crossref-commons`, plus CrossRef 429s).
  `collectors/metadata.py` now pages CrossRef with httpx, sends `mailto`, and backs off
  on 429/5xx; `crossref-commons` is removed. Live check into a throwaway DB: 43 journals,
  2,970 articles for 2026, 36 s, no failures. **The live DB has not been re-collected
  for 2026 yet**: run `make update` on the VM after pulling.
- **SSRN queue bug fixed.** Enrichment abstracts create abstract-only `ssrn_pages` rows,
  and the scrape queue used to treat any row as "searched": 1,234 articles were never
  searched on SSRN. The queue now uses `_SSRN_NOT_SEARCHED`, and an SSRN no-match keeps
  an existing abstract.
- **Abstract sources** (`make abstracts`): CrossRef abstracts at collect time plus a
  cache backfill, OpenAlex (optional free key), Semantic Scholar (DOI-exact batch), and
  NBER landing pages (near-exact title plus a shared author surname). Snapshot test
  against a copy of the live DB (21,678 missing): CrossRef cache filled 2,395 with no
  API calls; S2 filled 220 of 1,000; NBER filled 23 of 150 recent econ/finance papers,
  all checked correct. PDF-based abstract extraction was deferred: no verified PDF lacks
  an abstract.
- **Makefile**: `doctor`, `smoke-ssrn`, `pdfs`, `abstracts`, `process`; slow SSRN defaults
  (`BATCH=50`, `SCRAPE_DELAY=30`, `DOWNLOAD_DELAY=45`); `KEEP_AWAKE` uses caffeinate or
  systemd-inhibit. `scrape` exits 1 on a Cloudflare abort, which stops `make pdfs`.
- Tests: 118 passed. pi review saved in
  `quality_reports/2026-10-03_pi_review_vm-runner-abstracts.md`.

## Not yet verified

- **Cloudflare on the VM.** `make smoke-ssrn` has not run anywhere yet. On this M4 it
  failed before reaching SSRN because SeleniumBase UC fetches an x86 `uc_driver` and
  Rosetta is not installed. Run it on the VM from a terminal inside the desktop session.
- SSH to `ubuntu-vm` was refused (publickey); probably the key needs `ssh-add`.
- The VM's `.env` likely lacks `CITE_HUSTLE_CROSSREF_EMAIL`; `make doctor` flags it.
- process-paper on the VM (dot-files clone, its Poetry env, `OLLAMA_API_KEY`, docling
  on the GPU) is untested.

## Next steps on the VM

```bash
git pull && poetry install
make doctor
make smoke-ssrn            # watch the browser; PDFs should land in the printed temp dir
make update && make abstracts
make pdfs                  # repeat; ~31k SSRN URLs await download at ~45 s each
make process
```

## Guardrails

- One writer only. Never run write commands on a Mac while the VM is the runner.
- If Cloudflare blocks (scrape aborts), stop and wait hours; do not retry in a loop.
- `pdf_files`/`pdf_candidates` semantics and `tests/test_ssrn_blocking.py` are locked.
- Dead code to consider removing (not done): `collectors/pdf_downloader.py`;
  `scripts/cleanup_non_articles.py` now hits foreign keys on articles with PDFs/wiki pages.
