# pi review (glm-5.2:cloud): CrossRef paging, abstract sources, VM runner Makefile

Date: 2026-10-03. Three batches.

## Triage

- Fixed: S2 positional alignment (length guard + test); CrossRef backfill logging documented; doctor.sh reports unloadable settings.
- Disagreed: httpx client never closed (short-lived CLI process); Retry-After HTTP-date (falls back to exponential backoff, acceptable); per-row abstract upsert (local DuckDB writes, 2,970 articles collected in 36 s; Dropbox syncs the file, not each write); COALESCE test request (already covered by test_ssrn_no_match_keeps_existing_enrichment_abstract); smoke-ssrn temp dir kept on purpose so PDFs can be inspected.
- Not acted on (minor): NBER multi-word surnames (false negatives only), S2 candidates count after a stopped run, unbound-error guards at a constant attempt count.

## Batch 1

# Code Review
**Overall:** MINOR ISSUES

## Summary
These two diffs replace a fragile CrossRef dependency with direct httpx cursor paging and improve SSRN queue logic so abstract-only enricher rows aren't mistaken for completed searches. The logic is generally sound and the bug fix is well-motivated, but there are real correctness concerns: an HTTP client lifetime leak, a retry-after parsing hole, an N+1-style per-row abstract upsert inside a bulk-insert loop, and a DuckDB/Dropbox interaction that deserves scrutiny. Nothing is catastrophic, but a skeptic would want these addressed before trusting the pipeline unattended.

## Category results

| # | Category | OK/WARN/FAIL | Findings |
|---|----------|--------------|----------|
| 1 | Correctness | WARN | `Retry-After` non-numeric forms ignored; `error` may be referenced when undefined; abstract upsert after bulk_insert duplicates work |
| 2 | Structure | WARN | `_get_works_page` swallows TransportError into a string; no `close()` on injected/owned httpx.Client |
| 3 | Reproducibility | OK | URL/rows/attempts as constants; mailto from settings |
| 4 | Error handling | WARN | `response.json()` not guarded for JSONDecodeError; final `error` variable scoping fragile |
| 5 | Comments | OK | Cursor-paging comment is genuinely helpful |
| 6 | Style | OK | Mostly consistent; minor line-length nits |
| 7 | Performance | WARN | Per-row `upsert_abstract` in a tight loop after `bulk_insert_articles`; could be batched |
| 8 | Security | OK | No hardcoded secrets; mailto user-agent good practice |

## Issues

1. **MAJOR — `httpx.Client` never closed.** `MetadataCollector.__init__` creates/owns an `httpx.Client` but there is no `close()`, `__del__`, or context-manager support. In a long-running CLI that fetches many journal-years, connection pools and sockets accumulate; under Dropbox-synced DuckDB load this can also delay file handles. 
   What would change my mind: evidence the CLI process is short-lived and exits after each invocation, or addition of `close()`/`__enter__`/`__exit__` and a call site that uses it. For injected clients, document ownership.

2. **MAJOR — `error` variable may be unbound on final failure path.** In `_get_works_page`, the loop body assigns `error` either in the `except` branch or in the `else` branch. But consider the case where `response.status_code == 200` is true yet `response.json()["message"]` raises `KeyError`/`json.JSONDecodeError` — that exception propagates uncaught, which is fine, but more subtly: if the very last iteration takes the `else` branch with a non-429/<500 status, `raise_for_status()` runs *before* `error` is assigned, so `raise_for_status` throws (good), but the `ConnectionError` fallback referencing `error` is then dead. The real risk: a 429/5xx with a non-digit `Retry-After` (e.g. HTTP-date format like `Wed, 21 Oct 2026 07:28:00 GMT`) falls through to `time.sleep(5.0 * 2**attempt)` — which is acceptable — but `error` was set, so that's OK. Still, the `error` reference after the loop relies on at least one iteration assigning it; if `CROSSREF_MAX_ATTEMPTS == 0` this is a `NameError`. Minor in practice, fragile in principle.
   What would change my mind: initialize `error = "no attempts made"` before the loop, or restructure so the post-loop raise doesn't depend on loop state.

3. **MAJOR — `Retry-After` only honors integer seconds, ignores HTTP-date.** CrossRef can (per HTTP spec) return an HTTP-date in `Retry-After`. `retry_after.isdigit()` will be `False` for `Wed, 21 Oct 2026 ...`, so the code falls back to exponential backoff instead of respecting the server's asked-for delay. For a skeptic replacing a retry library that "never fired," partially honoring the protocol is a regression risk on 429s.
   What would change my mind: parse HTTP-date via `email.utils.parsedate_to_datetime` as a fallback, or a comment explaining CrossRef only ever sends integer seconds (with evidence).

4. **MAJOR — Per-row `upsert_abstract` after `bulk_insert_articles` is an N+1 pattern.** In diff 2, `collect`:
   ```python
   self.repo.bulk_insert_articles(transformed)
   for row in transformed:
       if row["abstract"]:
           self.repo.upsert_abstract(row["doi"], row["abstract"], force=False)
   ```
   Each call is a separate DuckDB write under Dropbox sync. For a page of 1000 articles this is 1000 small transactions. Given the Dropbox WAL caveat in the project context, this is both a performance and a sync-storm concern.
   What would change my mind: a batched `bulk_upsert_abstracts(rows)` method, or evidence that `transformed` is always small (≤~50) in practice.

5. **WARN — `get_articles_missing_abstract` `skip_stage` builds SQL with f-string LIMIT.** The `LIMIT {limit}` is f-interpolated. `limit` is typed `Optional[int]` so injection risk is low, but mixing parameterized (`?`) and string-interpolated values in one query is inconsistent and a latent footgun if the type ever loosens.
   What would change my mind: parameterize LIMIT too (`LIMIT ?`), or assert `isinstance(limit, int)`.

6. **WARN — `insert_ssrn_page` `COALESCE(EXCLUDED.abstract, ssrn_pages.abstract)` assumes column exists in upsert target.** The comment says "SSRN abstract wins; a no-match keeps a CrossRef/OpenAlex abstract." This is correct only if the existing row's `abstract` column is populated by the enricher path. If an earlier insert set `abstract=NULL` and a later no-match upsert comes in with `EXCLUDED.abstract=NULL`, the old NULL is kept — fine. But if `html_content`/`ssrn_url` are being upserted on a no-match while preserving abstract, ensure the `ON CONFLICT(doi)` set list doesn't clobber `scraped_at` semantics. Worth a test asserting abstract survives a no-match upsert.
   What would change my mind: a regression test covering "enricher abstract preserved across SSRN no-match upsert."

7. **WARN — `_SSRN_NOT_SEARCHED` as a class-level f-string template is fragile.** It's interpolated via `f"""..."""` in two methods. If a subclass or refactor renames `ssrn_url`/`html_file_path`/`error_message`, both queries silently break with runtime SQL errors. A constant SQL fragment referencing DB columns is fine, but embedding it as a class attribute used in `f""` strings (rather than plain `.format` or a method) invites drift.
   What would change my mind: make it a plain string constant (no f-string needed — no `{}` interpolation actually happens in it) and reuse directly.

8. **MINOR — `_SSRN_NOT_SEARCHED` comment says "no row, or an abstract-only row from an enricher."** The SQL implements exactly that, but the condition `s.doi IS NULL OR (...)` is correct only because the LEFT JOIN produces `s.doi IS NULL` when no row exists. Good, but the comment could note that an `error_message`-only row (failed search) is correctly *excluded* from "not searched," which is the intended retry behavior. Minor documentation gap.

9. **MINOR — `settings.crossref_email` read multiple times** (in `__init__` for UA, in `fetch_articles_by_issn` for params). Not wrong, but a single normalized `self.mailto` would reduce inconsistency risk.

10. **MINOR — `time` import added but `time.sleep` used in a method that could be on an async path later.** Fine now; just flagging for future-proofing if httpx.AsyncClient is ever introduced.

11. **MINOR — `crossref_abstract(article)` called per-row inside the transform loop (diff 2).** If `crossref_abstract` does any I/O or heavy parsing, it's per-article. Assuming it's a pure XML/JATS parser on already-fetched data, this is OK; verify it doesn't re-fetch.

## Score
Start: 100
- HTTP client lifetime leak: -8
- `error` unbound edge case: -4
- `Retry-After` HTTP-date ignored: -6
- N+1 abstract upsert: -7
- f-string LIMIT inconsistency: -3
- COALESCE lacks test: -3
- Minor doc/style: -2
**Final score: 67**

## Batch 2

# Code Review
**Overall:** MINOR ISSUES

## Summary
The code adds three abstract backfill sources with sensible fail-soft design and per-attempt logging so reruns can skip recent successes. Logic is mostly sound, but there are several correctness/robustness concerns: the S2 batch API assumes positional alignment between request ids and response papers (which is not guaranteed when the API elides missing records), the crossref backfill mutates `missing` while iterating batch files but never logs `no_match` for remaining DOIs, and the NBER author-surname extraction assumes single-token last names which breaks on "van Dijk" or "de la Fuente". None are data-corrupting on their own because abstracts are never force-overwritten, but the S2 alignment issue could silently attach the wrong abstract to a DOI.

## Category results

| # | Category | Rating | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | S2 response/DOI positional alignment assumed; NBER surname parsing breaks on multi-word surnames; `error` may be unbound in S2 fetch if first iteration throws TransportError then... actually ok, but fragile |
| 2 | Structure | OK | Clean separation; functions well-scoped |
| 3 | Reproducibility | WARN | No seed (not stochastic, acceptable); `cache_dir` from settings is fine; delays are configurable |
| 4 | Error handling | WARN | `error` variable in S2 `fetch` is referenced after loop but only reachable if loop completed without `return`/`raise`, so it's set; however if `httpx.TransportError` path runs and `attempt` exhausted, `error` is set — OK. Missing `no_match` logging for crossref candidates never found in cache |
| 5 | Comments | OK | Good "why" comments on backoff and NBER verification |
| 6 | Style | OK | Consistent, PEP 8-ish |
| 7 | Performance | OK | Batched S2; reasonable |
| 8 | Security | OK | API key from settings, not hardcoded; SSRN/NBER are public |

## Issues

1. **MAJOR — S2 batch response alignment.** `SemanticScholarSource.fetch` (lines ~97-101) builds the result dict by zipping `dois` with `papers`:
   ```python
   return {doi: clean_abstract((paper or {}).get("abstract"))
           for doi, paper in zip(dois, papers)}
   ```
   The S2 batch API returns one entry per requested id in the same order *in the documented happy path*, but if any id is not found the API may return `null` at that position (which the `paper or {}` guards) — that part is fine. However, the API has historically returned shorter arrays or reordered results in some error edge cases. If the response length != `len(dois)`, `zip` silently truncates and misaligns, potentially attaching paper A's abstract to DOI B.
   **What would change my mind:** an explicit `len(papers) == len(dois)` check that raises `ResolverError` on mismatch, or better, request the `externalIds`/`paperId` field back and key by DOI rather than position.

2. **MAJOR — CrossRef backfill never logs `no_match`.** `backfill_from_crossref_cache` only logs `success` (via `save_abstract`) for DOIs it finds. DOIs never found in any cache file get no `processing_log` row, so `recheck_days`/`skip_stage` logic for crossref never triggers — every rerun re-scans every missing DOI against every cache file. The docstring claims "logs one processing_log row per attempt" but this source doesn't.
   **What would change my mind:** after scanning all cache files, log `no_match` for each remaining DOI in `missing`, or document that crossref intentionally bypasses the skip logic because cache scans are cheap.

3. **MINOR — NBER surname extraction.** `name.split()[-1].lower()` (line ~140) treats only the last whitespace token as the surname. "Maria van der Berg" → "berg"; expected last name "van der Berg" won't intersect. This causes false negatives (missed valid matches), not false positives, since the title threshold is 95 and at least one surname must match. Low risk but worth noting given the stated goal of avoiding wrong-paper attachment.
   **What would change my mind:** using `author_last_names` (already imported from `cite_hustle.matching`) on the NBER authors too, for consistency with how expected names are parsed.

4. **MINOR — `error` unbound guard.** In `SemanticScholarSource.fetch`, if the loop body somehow completes all attempts without setting `error` (e.g., a future refactor adds a `continue`), the final `raise ResolverError(f"{error} after ...")` raises `UnboundLocalError`. Currently unreachable, but a defensive `error = "unknown"` before the loop would harden it.

5. **MINOR — `enrich_from_s2` stops on first batch failure.** Line ~178 `break`s on `ResolverError`, logging all DOIs in the failed batch as `failed` but leaving subsequent batches unlogged. On rerun they'll be retried (good), but the CLI reports `candidates` for the whole set while only processing a prefix. The echo says "stopping, rerun later" which is honest, but `stats['candidates']` overstates what was attempted.
   **What would change my mind:** either recompute candidates as actually-attempted, or continue to next batch after a 429 (since 429 is a rate-limit, not a data problem — though the generous backoff may have already exhausted retries).

6. **MINOR — `clean_abstract` strips leading "abstract" but not other boilerplate.** CrossRef/JATS abstracts sometimes begin with "Summary" or "Abstract:" with various casing/punctuation. The regex `^abstract\b[\s:.]*` handles one variant. Acceptable for a first pass but will let through short boilerplate prefixes. Not a correctness bug given the length floor.

7. **MINOR — CLI `scrape` exit code.** The diff adds `ctx.exit(1)` in the `KeyboardInterrupt` handler, the generic `Exception` handler, *and* a post-block `if stats.get("aborted"): ctx.exit(1)`. The `KeyboardInterrupt` path already exits 1 before reaching the `stats.get("aborted")` check, so the aborted-check is only for a non-exception abort path (presumably `scrape` sets `stats["aborted"]` and returns normally). Fine, but the three exit points are slightly tangled; a comment clarifying that `aborted` is set without raising would help.

## Score
- Start: 100
- S2 alignment risk: -10
- CrossRef no_match logging gap: -8
- NBER surname parsing: -3
- Minor robustness/UX items: -4
- **Final: 75**

## Batch 3

# Code Review
**Overall:** SOUND

## Summary
The Makefile changes add sensible batch/delay knobs, a keep-awake wrapper, a throwaway smoke test, and a read-only doctor check. Both files are pragmatic and fit the described single-machine, Dropbox-backed workflow. The biggest concerns are minor robustness issues in shell word-splitting and error propagation in the `smoke-ssrn` recipe, not correctness of the core pipeline.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | `smoke-ssrn` suppresses failures via `&&` chains but the `ls` at the end can mask earlier non-zero exit semantics; `KEEP_AWAKE` Linux fallback may be empty silently. |
| 2 | Structure | OK | Clear phony grouping, logical flow, no dead targets. |
| 3 | Reproducibility | OK | Overridable vars, `YEAR` default, relative `deploy/doctor.sh`, throwaway root via env var. |
| 4 | Error handling | WARN | doctor.sh uses `set -e`-less script but tracks `fails`; `read` into vars relies on Python output ordering; smoke target doesn't surface a clean failure message. |
| 5 | Comments | OK | Good "why" comments: Cloudflare, slow SSRN, throwaway DB, polite pool. |
| 6 | Style | OK | Consistent tab indentation, aligned vars. |
| 7 | Performance | OK | Batches/delays configurable; no obvious N+1. |
| 8 | Security | WARN | `pgrep -if dropbox`, `command -v` usage fine; no hardcoded secrets; but `systemd-inhibit` command built via `echo` in `$(shell ...)` is safe. |

## Issues

1. **MINOR — `KEEP_AWAKE` Linux branch can be empty and silently run no inhibitor.** Makefile line: `KEEP_AWAKE := $(shell command -v systemd-inhibit >/dev/null && echo systemd-inhibit --what=idle:sleep --who=cite-hustle --why=downloads)`. If `systemd-inhibit` is absent, `KEEP_AWAKE` is empty and targets run normally — acceptable as a fallback, but there's no diagnostic. Consider `else echo "true"` or a `$(info ...)` warning so a long SSRN run doesn't sleep mid-download unexpectedly. Not blocking since behavior degrades gracefully.

2. **MINOR — `smoke-ssrn` error reporting is terse.** The one-line recipe chains `init`, `collect`, `scipe`, `download`, `ls` with `&&`; if `collect` fails, the user sees a nonzero exit but the throwaway `$$base` temp dir is not cleaned up (leaks `/tmp/...`). Consider a `trap 'rm -rf "$$base"' EXIT` at the start, or at least document that the dir is printed so the user can remove it. The `echo "Throwaway data root: $$base"` is good for that, but auto-cleanup would be nicer.

3. **MINOR — doctor.sh `read` relies on Python print ordering and space-encoding.** `read -r base email s2 oa ppdir < <(poetry run python -c "...")`. The script deliberately replaces spaces with `%20` to survive word-splitting, then decodes. This is fragile but works for the controlled output. The `2>/dev/null` hides import/config errors; if the Python line fails, all five vars are empty and subsequent checks produce confusing failures ("no database under ''"). Consider detecting an empty `base` and emitting a distinct fail: "could not load cite_hustle.settings."

4. **MINOR — `compgen -G` with a glob containing spaces.** `compgen -G "$base/DB/*conflicted copy*"` — if `$base` contains spaces, the glob pattern is quoted as one string, which `compgen -G` treats correctly (it takes a single pattern arg), so this is fine. No change needed; noting it was checked.

5. **MINOR — `pgrep -if dropbox` matches case-insensitively and could false-positive** (e.g., a file path containing "dropbox"). Low impact for a warn-level check. Acceptable.

6. **MINOR — `exit $((fails > 0))` works but is obscure.** Bash arithmetic yields 1/0; fine, but `exit 1` on failure is clearer. Pure style; let it slide per pragmatist stance.

## Score
Start 100.
- doctor.sh Python-failure diagnostic gap: -2
- smoke-ssrn temp dir cleanup: -2
- KEEP_AWAKE Linux fallback silent: -1

**Final score: 95**

