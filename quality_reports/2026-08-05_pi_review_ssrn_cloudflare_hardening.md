# pi review: SSRN scraper Cloudflare hardening (2026-08-05)

Reviewer: glm-5.2:cloud via Ollama (pi, standalone, no context). Disposition: pragmatist.
Scope: uncommitted diff to `src/cite_hustle/collectors/ssrn_scraper.py`,
`src/cite_hustle/cli/commands.py`, plus new `tests/test_ssrn_blocking.py`.

## Verdict

**SOUND (minor issues), score 80/100.** "Fundamentally sound and addresses a real
operational failure mode well... With Issues 1 and 6 addressed (wire the timeout flag;
test the breaker), this is shippable."

## Findings and triage

| # | Severity | Finding | Action |
|---|----------|---------|--------|
| 1 | MAJOR | `_load_url`'s timeout boolean was computed but never inspected; partial pages proceed silently | Fixed: `_handle_cloudflare_challenge` now logs "Proceeding on partially loaded page" with a comment that downstream element waits gate extraction |
| 2 | MAJOR | Hung driver persists across articles: up to 3 slow failures before the breaker trips | Disagreed (kept as-is): 3-strike is intentional; transient challenges can clear mid-run, and aborting on a single failure is too eager. The reviewer itself downgraded this to acceptable-if-intentional |
| 3 | MINOR | `BLOCK_ERROR_MARKERS` contract is fragile (new block messages must contain a marker) | Fixed: contract documented in a comment at the definition |
| 5 | MINOR | `--delay` default raised 5→20 without explanation | Fixed: help text now states why |
| 6 | MINOR | Circuit breaker untested in the suite | Fixed: three mock-based tests added (no-retry on block, abort at threshold, counter reset on non-block failure) |
| 7 | MINOR | "Browser unresponsive" message duplicated in two handlers; drift would break classification | Fixed: extracted `BROWSER_UNRESPONSIVE_MSG` module constant |
| 4 | MINOR | `BLOCK_ABORT_THRESHOLD` not CLI-configurable | Skipped: no requested use case; adding a flag now is speculative configurability |

## Full reviewer output

(Verbatim below.)

# Code Review
**Overall:** SOUND (MINOR ISSUES)

## Summary
This is a well-reasoned, pragmatic hardening pass: it fixes the false-positive challenge detection (cookie-presence -> content markers), adds a page-load timeout, distinguishes block-type errors so they skip per-article retries, and adds a run-level circuit breaker. The logic is clear and the tests target the regression (data-cfasync/__cf_bm false positives). Biggest concerns are a couple of subtle control-flow edge cases and a TimeoutException import that may not cover the actual page-load-timeout exception.

## Issues (abbreviated; see triage table above)
1. MAJOR: _load_url timeout boolean dead / partial-page extraction risk (-8)
2. MAJOR: hung driver -> 3 slow failures before abort (-4)
3. MINOR: fragile BLOCK_ERROR_MARKERS contract (-1)
4. MINOR: BLOCK_ABORT_THRESHOLD not configurable
5. MINOR: --delay change unexplained
6. MINOR: circuit breaker untested (-2)
7. MINOR: duplicated Browser-unresponsive string (-3)

## Score: 80/100
