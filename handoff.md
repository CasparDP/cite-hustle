# cite-hustle session handoff

Updated: 2026-10-05 (duplicate-DOI merge; print years, front-matter cleanup, HTML dashboard)

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
- Tests: 125 passed (2026-10-05). pi review saved in
  `quality_reports/2026-10-03_pi_review_vm-runner-abstracts.md`.

## Data fixes ready to run on the VM (2026-10-05)

- `articles.year` is now the citation (print) year; re-collecting updates it. Write
  connections drop `idx_articles_year` automatically (it blocked year updates).
- Front matter is filtered at collect time and removable with `cleanup-non-articles`.
- Verified on a copy of the live DB: Management Science 2024/2025/2026 became 445/457/703
  (was 445/792/368); JFQA 2024 115 (CrossRef print-2024: 134); cleanup deleted 4,724
  records (3 with PDFs kept), no orphans, backup written, 5.7 s.
- Run order on the VM: `git pull`, `make refresh-metadata` (1980 to now, about 2,000
  CrossRef requests), `make cleanup-non-articles` (dry run), `make cleanup-non-articles-apply`.
- **Duplicate DOIs: `merge-duplicates` is implemented, not yet run on the VM.** Rule
  signed off 2026-10-05, plus the CrossRef alias check (see the CLAUDE.md decision).
  Applied to a snapshot copy: 2,141 groups merged, 2,162 articles removed, none with a PDF
  or wiki page, 6,418 `processing_log` rows re-pointed, no new orphans, 9 s; 19 pairs held
  (no page data: JAR `joar.*`, AMR `amr.10.*`, CAR early-view), 0 conflicts. CrossRef has turned many JF JSTOR
  and JoM Elsevier DOIs into aliases (301 to the publisher DOI), so the refreshed cache no
  longer lists them; the command asks CrossRef about DOIs missing from the cache (a few
  hundred to ~1,200 requests at 2/s, cached in `cache/crossref_aliases.json`; the snapshot
  run needed 579, with 429 backoffs taking about 15 minutes).
- Live `ssrn_pages` holds two identical rows for `10.1016/j.jcorpfin.2023.102427` (an ART
  index inconsistency); `merge-duplicates --apply` repairs identical duplicate-key rows
  after the backup and refuses if duplicate keys hold differing rows.
- Run order on the VM after `refresh-metadata` has finished and cleanup is applied:
  `make merge-duplicates` (dry run; review the CSV it writes to `reports/`), then
  `make merge-duplicates-apply`, then `make dashboard`.
- HTML dashboard: `make dashboard` writes `reports/dashboard.html`.

## Fallback resolution (2026-10-06)

- An unlimited `make pdfs` ran `resolve-fallbacks` over 57,046 articles. Until ~03:30 it found
  496 PDFs (oa 278, nber 124, arxiv 50, plus 44 SSRN); then OpenAlex rate-limited every
  lookup (12 min of retries per article). Fixed: `make pdfs` passes `FALLBACK_BATCH=200`,
  fully checked articles are skipped before the limit (no 3 s sleep), the run stops after 3
  rate-limited articles in a row, 429 waits are capped at 60 s, and the OA lookup sends
  `CITE_HUSTLE_OPENALEX_API_KEY` when set.

## Abstract checks (2026-10-06)

- `verify-abstracts` (in `make verify` and the pipeline verify stage) checks each abstract
  against its verified PDF; snapshot: 1,015 of 1,021 match, 6 flagged (published vs
  working-paper wording), no wrong-paper abstract found.
- `repair-abstracts` fixes junk abstracts; snapshot dry run: 262 (41 from CrossRef, 11 cut
  out of SSRN page text, 210 cleared). On the VM: `make repair-abstracts`, then
  `make repair-abstracts-apply`, then `make abstracts` to refill the cleared ones.
- `cross-check-abstracts` compares SSRN/NBER abstracts with DOI-exact ones and replaces
  mismatches (signed off). Snapshot, CrossRef only: 1,848 replaced, 13,258 confirmed; the
  OpenAlex/S2 pass covers ~60% of the ~14,700 rows without a CrossRef abstract.
- VM order: `make repair-abstracts` / `-apply`, `make abstracts`, `make verify`,
  `make cross-check-abstracts` (review CSV in reports/) / `-apply`.

## Not yet verified

- **Cloudflare on the VM passed:** `make smoke-ssrn` worked on the VM (2026-10-05). It cannot
  run on the M4 until Rosetta is installed (SeleniumBase UC fetches an x86 `uc_driver`).
- A killed VM run left a WAL on 2026-10-04; `make recover-db` merged it.
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
- Dead code to consider removing (not done): `collectors/pdf_downloader.py`.
