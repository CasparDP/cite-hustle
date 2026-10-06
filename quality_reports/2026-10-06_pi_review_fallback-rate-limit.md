# pi review: resolve-fallbacks rate limit and batching (glm-5.2:cloud)

Triage (Claude): all MINOR, none changed. #2 misreads the order: the streak reset runs before the 5xx check, so 5xx resets it too (intended). #3: both DOI sets come from the same lowercase DB values. #1: 57k rows in memory is fine.

# Code Review
**Overall:** SOUND — MINOR ISSUES

## Summary
This diff adds rate-limit detection and batch-progress logic to the fallback PDF resolver. The approach is pragmatic: cap 429 waits, count consecutive rate-limited lookups per resolver, stop the run when a streak hits the threshold, and skip recently-checked articles before applying the limit so each batch makes forward progress. The implementation is clean and the tests cover the key behaviors. Main concerns are a potential memory issue from fetching all pending articles before filtering, and a subtle correctness question about the streak-reset semantics in `_get`.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Streak reset path in `_get` is slightly subtle; see issue 1 |
| 2 | Structure | OK | Clean separation; resolver owns streak state, command observes it |
| 3 | Reproducibility | OK | Makefile passes `FALLBACK_BATCH`; no hardcoded paths |
| 4 | Error handling | OK | Rate-limit path raises informative `ResolverError("rate_limited")`; messages guide user to API key |
| 5 | Comments | OK | Comments explain the "why" (skip before limit, streak purpose) |
| 6 | Style | OK | Consistent with existing code |
| 7 | Performance | WARN | `repo.get_articles_without_pdf()` with no limit fetches all pending rows (57k+) into a DataFrame |
| 8 | Security | OK | API key read from settings/env, not hardcoded |

## Issues

1. **[MINOR]** `commands.py` ~616: `repo.get_articles_without_pdf()` is now called with **no limit**, loading all 57k+ pending articles into memory before filtering and slicing to `limit`. This works but is wasteful for large backlogs.

   **What would change my mind:** A repo method like `get_articles_without_pdf(limit=None, exclude_dois=already_checked_dois)` that pushes the filter into SQL. Acceptable as-is for 57k rows on a local DuckDB, but worth a note.

2. **[MINOR]** `fallback_resolvers.py` `_get`: The `rate_limited_streak = 0` reset happens **inside** the retry loop only when the response is not 429 and not 5xx — i.e., on the success/other-response path. On a 5xx exhaustion, `rate_limited_streak` is not reset (falls through to `raise ResolverError("max_retries_exceeded")`). That's probably fine (5xx isn't a rate limit), but the reset is asymmetric: a 5xx after a 429-streak leaves the streak intact even though it wasn't a 429. This is arguably correct (only a real answer should reset), but it's worth a one-line comment clarifying that only non-error responses reset the streak.

   **What would change my mind:** The test `test_exhausted_429_raises_rate_limited_and_counts_streak` confirms a 200 resets to 0, which covers the main path. The 5xx interaction is an edge case unlikely to matter in practice.

3. **[MINOR]** `commands.py` ~617: `articles["doi"].map(lambda d: any((d, s) not in already_checked for s in source_order))` — `already_checked` is a set of tuples; `(d, s)` assumes `d` matches the exact form stored in `get_recent_candidate_checks` (case, normalization). The test passes `record_pdf_candidate("10.1/0", "oa", ...)`, suggesting DOI casing is consistent, but if `get_articles_without_pdf` returns DOIs with different casing than what was recorded, the filter silently fails to skip. A comment noting the DOI-format contract between these two repo methods would help.

4. **[MINOR]** Test `test_batch_skips_recently_checked_articles`: asserts `"1 of 2 pending"` — 3 articles added, 1 skipped, so 2 pending, limit 1 → "1 of 2 pending". Correct and good. The test for rate-limit streak (`test_rate_limit_streak_stops_the_run`) adds 6 articles, expects exactly 3 calls — correct given `max_rate_limited=3` and break after the 3rd. Solid coverage.

## Score
- Start: 100
- No-limit fetch of full pending set (performance): -3
- Streak-reset asymmetry on 5xx (minor correctness clarity): -2
- DOI-format contract undocumented between repo methods: -2
- **Final: 93**
