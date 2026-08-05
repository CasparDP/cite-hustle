# Handoff

Status doc for cross-session continuity. Newest session first.

## 2026-08-05: Cloudflare block diagnosis + scraper hardening

**Done and verified** (commit `a44b814`, pushed):

- Diagnosed why `scrape` was getting blocked on the dev machine. Root cause: the IP is
  challenge-flagged by Cloudflare (a plain `curl` to papers.ssrn.com returns an instant 403
  challenge page). In that state Chrome hangs in a challenge loop, Selenium's client gave up
  at 120s with `ReadTimeoutError`, and the old code retried blindly 4x per article
  (~9 min/article, confirmed in a live 40-paper diagnostic run: 531 s/article, 5/5 failed).
- Historical evidence: 335 `ERROR_*.png` screenshots in `ssrn_html/` show the interactive
  Turnstile checkbox page. Old detection false-positived on normal pages and false-passed on
  real challenges (`__cf_bm` is set on every Cloudflare response). Some past
  "No search results found" failures were masked blocks.
- Hardened `ssrn_scraper.py`: content-marker challenge detection, resolution judged by the
  page changing (never cookies), operator prompt for the Turnstile checkbox in headful mode,
  45s page-load timeout, block-error classification (no per-article retries), run-level
  circuit breaker (abort after 3 consecutive block failures), distinct "No results (timeout)"
  DB label, CLI `--delay` default 5 -> 20.
- 12 tests pass (`tests/test_ssrn_blocking.py` is new). Cross-model review (pi/glm-5.2):
  SOUND, 80/100; triage in `quality_reports/2026-08-05_pi_review_ssrn_cloudflare_hardening.md`.
  Kept as intentional: 3-strike abort pacing; abort threshold not CLI-configurable.

**Known state of the world:**

- Dev-machine IP remains challenge-flagged as of 2026-08-05; scraping throughput belongs on
  the runner laptop (also the sanctioned single DB writer per `deploy/README.md`).
- The hardened code is NOT yet exercised against a live challenge end-to-end (the diagnostic
  run predated the fix). First real run should be watched.
- DB in Dropbox last written 2026-07-24; ~9,895 articles pending SSRN scrape.
- Scrape wrote 5 diagnostic failure rows on 2026-08-05 (`ReadTimeoutError`); they have
  `ssrn_pages` entries, so they will not be retried until reset via
  `scripts/reset_failed_scrapes.py` (which does not yet match "Browser unresponsive" or
  "No results (timeout)" patterns).

**Next steps:**

1. Validate the hardening with a small headful run (`scrape --limit 5 --no-headless`),
   ideally on the runner laptop; on the dev machine expect either a checkbox prompt or a
   clean abort within minutes.
2. Consider extending `scripts/reset_failed_scrapes.py` to also reset the new block-type
   error messages ("Browser unresponsive", "No results (timeout)", "Cloudflare challenge")
   so recovered IPs can retry those DOIs.
3. Optionally port the same challenge-detection rework to `selenium_pdf_downloader.py`,
   which still has its own older Cloudflare handling.
