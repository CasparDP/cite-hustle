# pi review: merge-duplicates (glm-5.2:cloud, 4 batches)

## Triage (Claude)

- b2 CRITICAL `src_doi` takes the replaced DOI: false positive. DuckDB check:
  `SELECT * EXCLUDE (keep) REPLACE (keep AS doi), doi AS src_doi` returns the original DOI.
  Now pinned by count assertions in `test_merge_moves_better_rows_fills_abstract_and_deletes_drop`.
- b2 MAJOR wiki skip wipes the kept DOI's children: false positive. Pruning removes the
  whole group (self-row included) from `_merge_map`, and every child DELETE is keyed on
  `_merge_map`, so a skipped group is untouched. Pinned by an assertion in the wiki test.
- b2 MAJOR partial repair across tables: the CLI raises before any repair if any key has
  differing rows; the method only ever collapses identical rows. Not changed.
- b2 MAJOR processing_log collisions: `processing_log` has only an `id` PK; no constraint
  on `doi`. False positive.
- b2 MAJOR no validation of overlapping groups: `_merge_map(doi PRIMARY KEY)` already raises
  before any change if a DOI appears twice (as keep or drop). Groups come from per-bucket
  union-find, disjoint by construction. Not changed.
- b1 MAJOR alias case mismatch: all stored DOIs are lowercase (0 exceptions on the snapshot)
  and both sides are lowercased in the comparison. False positive.
- b1 MAJOR conflicted cluster drops valid sub-merges: intended (a cluster merges only if
  every pair passes, as the module docstring says); 0 conflicted clusters on the snapshot.
- b1 MINOR prefix tie order-dependent: fixed (ties go to the larger prefix string).
- b1 MAJOR DOI tiebreak arbitrary: comment added; it only applies on a full tie.
- b3 MAJOR backup path: same pattern as cleanup-non-articles; `db.db_path` is the opened file.
- b4 test gaps (alias chains, sleep assertions): alias chains cannot reach a non-member
  because a redirect only proves a pair when it targets the partner DOI. Not added.

---

## Batch b1

# Code Review
**Overall:** MINOR ISSUES

## Summary
This is a carefully-thought-out deduplication module with clear invariants documented at the top. The pair-rule logic is genuinely conservative (within-prefix strictness on pages, reply-like filtering, alias resolution via CrossRef 301). The biggest skeptic concerns are: (1) the cluster conflict check allows transitive merges through `refused` only when *direct* pairs are refused, but the union-find can already union a chain before a refusal is discovered, leaving the final cluster containing a refused pair only if it surfaces in the final combination scan — this is actually handled, but the union happens before refusal is recorded, so a refused pair may end up in the same cluster and be moved to `conflicted` rather than split; (2) `current_prefixes` ties broken by `most_common(1)` insertion order are deterministic but undocumented; (3) the alias cache is written only every 50 DOIs, so a crash between checkpoints loses up to 50 lookups (acceptable, but the final write only happens inside the `if i % 50` block — confirmed it writes on the last iteration).

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Union-find unions before refusal is recorded; `_keep_rank` DOI tiebreak favors lexicographically *larger* DOI (likely unintended); alias self-redirect edge case |
| 2 | Structure | OK | Clear functions, small responsibilities, good docstrings |
| 3 | Reproducibility | WARN | `httpx`/`rapidfuzz` not pinned here (project-level presumably); delay/backoff deterministic-ish but no seed needed |
| 4 | Error handling | WARN | `resolve_aliases` swallows all non-Transport exceptions implicitly via status-code branches; `load_crossref_pages` silently skips corrupt cache files |
| 5 | Comments | OK | Strong "why" comments; module docstring is excellent |
| 6 | Style | OK | Consistent, PEP8-ish, line length a bit over 100 in places |
| 7 | Performance | OK | O(n²) within title buckets is expected; cache glob is fine |
| 8 | Security | OK | No secrets; httpx with timeout; no shell; input is DOIs |

## Issues

1. **[MAJOR] Union-find unions a pair, then a later pair in the same group is refused — the cluster is reported as `conflicted` and dropped entirely, but the legitimately-merged sub-pairs are lost.** In `find_duplicates`, you union `x` and `y` immediately on `"merge"`, but a refused pair discovered *after* unioning (e.g., A-B merge, B-C refused, A-C merge) yields a cluster {A,B,C} that fails the final `any(frozenset(p) in refused)` check and is moved to `conflicted` — meaning A-B (a valid merge) is never emitted as a group. The caller thus loses valid merges that it could have applied safely (A-B only).
   What would change my mind: the caller treats `conflicted` as "needs manual review and applies nothing," which is safe-by-design. If that's the intended contract (never partially merge a conflicted cluster), this is fine — but it should be documented, because the module docstring says "A cluster merges only if every pair in it passes," implying the whole cluster is atomic, which matches. Still, the data-loss-adjacent risk (valid A-B never merged because C is a corrigendum) is worth a comment on the `conflicted` branch.

2. **[MAJOR] `_keep_rank` tiebreak on `r.doi` picks the lexicographically largest DOI, which has no semantic meaning and could pick an old-format DOI over a new one or vice versa.** Line: `return (..., r.doi)`. Since DOI strings don't sort by "recency" or "preferred publisher," this tiebreak is arbitrary and could, in a rare zero-citation tie with no current-prefix signal, keep the JSTOR-equivalent or stale DOI. The earlier tuple elements make this rare, but it's the final arbiter.
   What would change my mind: a demonstration that when all earlier keys tie, either DOI is genuinely equivalent (same metadata, same cites, same prefix-status), so the choice is immaterial. Given the DB rows move to whichever is kept, a wrong-but-arbitrary choice is still "correct" — so this is likely fine, but a one-line comment ("arbitrary; all prior keys tie") would close it.

3. **[MAJOR] Alias self-redirect: `known[doi.lower()] = "" if target == doi.lower() else target`** treats a redirect to *itself* as "not an alias" (`""`). But CrossRef returning a 301 to the same DOI is itself suspicious (could be a case-normalization redirect). If CrossRef redirects `10.2307/...` to `10.2307/...` (same), you record `""` and `pair_decision` won't merge via the alias path. That's conservative and safe, but worth noting: a 301 to the *case-differing* same DOI would be recorded as a real alias target, and later `x.alias_of == y.doi.lower()` compares against `y.doi` unlowercased — **mismatch**.
   What would change my mind: `Record.doi` is stored lowercased everywhere upstream (the cache keys are lowercased, `alias_of` is lowercased). If `Record.doi` is guaranteed lowercase by construction, `y.doi.lower()` is a harmless no-op and there's no bug. Verify that `Record.doi` is always lowercase; if not, this is a real missed-merge bug. Add an assertion or normalize at construction.

4. **[MINOR] `current_prefixes` tie-breaking is `Counter.most_common(1)`, which is insertion-order-stable but depends on the order records are iterated.** If two prefixes tie in the last 5 years, the "current" prefix is whichever appeared first. For reproducibility across different input orderings, sort the records by DOI/year before counting, or document that input order is canonical.
   What would change my mind: input order is fixed by the SQL query upstream (ORDER BY doi), so ties resolve deterministically. Confirm and add a comment.

5. **[MINOR] `load_crossref_pages` silently skips files that fail `json.loads`** (`except (OSError, ValueError): continue`). A corrupt cache file means missing pages → more `held:no-pages` → more alias lookups, but never a wrong merge. Safe, but `log`-it so operators notice cache corruption.
   What would change my mind: this is fire-and-forget batch tooling where silent skip-then-hold is the correct conservative behavior. Acceptable as-is.

6. **[MINOR] `resolve_aliases`: the final cache write happens only inside `if i % 50 == 0 or i == len(todo)`.** If `todo` is non-empty but every DOI's loop `break`s early via a non-retry status, the write still triggers on `i == len(todo)`. Confirmed correct. But if `todo` is empty, no write happens even if `known` was loaded from a corrupt/partial file — fine, no mutation.

7. **[MINOR] Politeness: `time.sleep(delay)` runs even after the last DOI.** Harmless (one extra 0.5s), but `if i < len(todo): time.sleep(delay)` is tidier. Also the 429/5xx path `continue`s the `for attempt` loop without re-sleeping *after* the retry sleep, then hits `time.sleep(delay)` at the bottom — so total gap is `retry_after + delay`, which is polite. Fine.

8. **[MINOR] `code in (301,302,303,307,308)` then reads `Location`, but if `Location` is missing/empty, `_alias_from_location("")` returns `None` and the DOI is left unknown (not cached).** That's correct (will retry next run), but the `break` after the redirect branch is *inside* the `if target is not None`? No — re-reading: the `if target is not None:` only sets `known`; the `break` is at the same indent as the `elif` chain, so it breaks regardless. Wait — the `break` is at the `else`-less level after the `if/elif` chain, so on a 301 with unparseable Location, nothing is set and it breaks (DOI left unknown). Correct and conservative.

9. **[MINOR] `authors_compatible` recomputes `_names` and `_ascii` three times** (`surnames(a)`, `surnames(b)`, then `_names(other)` and `_ascii` again). For large groups this is O(n) redundant work per pair. Not a correctness issue.

10. **[MINOR] `_REPLY_LIKE` regex** — `^(introduction|foreword|...)$` requires the *entire normalized title* to be exactly "introduction" etc. A title "Introduction to..." won't match (good, it's a real paper), but "Introduction" alone matching is correct. The `^(a )?{_KIND}\b` branch would match "A Reply to X" but also "Comments on the Literature" — intended. Seems sound; one nit: `memorium` is a misspelling of `memoriam` (both are listed, so coverage is fine, but drop the typo or keep both — keeping both is harmless).

## Score
Start: 100
- Conflicted-cluster drops valid sub-merges without documenting the contract: -8
- `_keep_rank` DOI tiebreak arbitrary/uncommented: -3
- `alias_of == y.doi.lower()` relies on `Record.doi` being lowercase (unverified): -5
- `current_prefixes` tie-break nondeterminism undocumented: -2
- Minor nits (redundant recomputation, misspelling, silent cache-skip): -3

**Final score: 79**

---

## Batch b2

# Code Review
**Overall:** MAJOR ISSUES

## Summary

This diff adds four repository methods for finding and repairing duplicate-DOI rows and merging article groups onto a kept DOI. The SQL is sophisticated (window-function-based winner selection, FK-transaction-splitting rationale documented) and the data-flow is mostly sound. However there are several real data-loss and correctness hazards: the `_new_ssrn` `SELECT` rewrites `doi` via `REPLACE` **before** aliasing the old `doi` as `src_doi`, so `src_doi` silently becomes the kept DOI (making the `moved()` audit meaningless), the identical-row "repair" path can run even when conflicting child rows exist for the same DOI, and the skip-wiki logic uses an over-broad `DELETE` that can drop merge groups whose *kept* DOI also has a wiki page.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | FAIL | `src_doi` aliasing order in `_new_ssrn`/`_new_pdf`/`_new_cand`; wiki-skip pruning; `COUNT(DISTINCT t)` semantics |
| 2 | Structure | OK | Methods well-scoped; helper closures reasonable |
| 3 | Reproducibility | WARN | No seed (n/a here); temp-table names not globally unique |
| 4 | Error handling | WARN | Transactions guarded, but temp-table leak on connection reuse; no guard against `repair_identical_duplicate_rows` running with conflicting dups |
| 5 | Comments | OK | Good "why" comments on FK timing, winner ordering |
| 6 | Style | OK | Consistent with surrounding code |
| 7 | Performance | OK | Window functions, set-based; fine for expected scale |
| 8 | Security | WARN | f-string interpolation of table/key names (internal constants, low risk) |

## Issues

1. **[CRITICAL] `src_doi` captures the *kept* DOI, not the source DOI.** In `_new_ssrn`:
   ```sql
   SELECT * EXCLUDE (keep, rn, best_abstract)
          REPLACE (keep AS doi, coalesce(...) AS abstract),
          doi AS src_doi, ...
   ```
   `SELECT *` expands to all columns of `ranked`, which includes `doi` (the original source DOI from `ssrn_pages s`). The `REPLACE (keep AS doi, ...)` then *redefines* the column named `doi` to `keep`. Because `EXCLUDE` runs before `REPLACE` and `keep` is excluded, the subsequent `, doi AS src_doi` refers to the **just-replaced** `doi` column (i.e. `keep`), not the original `s.doi`. DuckDB resolves the post-REPLACE value. Result: `src_doi == doi` for every row, so `moved("_new_ssrn")` always returns 0 and `pdf_files_moved`/`pdf_candidates_moved` are likewise wrong. Worse, downstream consumers reading `src_doi` get a lie. Same bug in `_new_pdf` and `_new_cand` (`SELECT * EXCLUDE (keep, rn) REPLACE (keep AS doi), doi AS src_doi`). **What would change my mind:** run `EXPLAIN` or a small test asserting `src_doi <> keep` for at least one moved row; if DuckDB resolves `doi AS src_doi` to the pre-REPLACE source value, this is not a bug — but I believe DuckDB's `REPLACE` rebinds the name before the trailing alias. The safe fix is to alias the source first: `SELECT * EXCLUDE (keep, rn, doi) REPLACE (keep AS doi), s.doi AS src_doi` (reference the qualified original via the CTE) or compute `src_doi` in the `WITH` before the outer projection.

2. **[MAJOR] Wiki-skip pruning can discard groups whose *kept* DOI has a wiki page.** The `DELETE FROM _merge_map WHERE keep IN (SELECT m.keep ... WHERE m.doi <> m.keep)` removes *all* entries (including the keep↔keep self-row) for any keep group where any dropped member has a wiki page. That is the intent ("skip the whole group"). But the join condition `m.doi <> m.keep` correctly excludes the self-row from the *selector*; the outer DELETE removes by `keep`, which also nukes the self-row — so the keep DOI is later *not* present in `_merge_map` and its own ssrn/pdf/candidates rows get deleted by `DELETE FROM ssrn_pages WHERE doi IN (SELECT doi FROM _merge_map)`. The kept article survives (children only), but its child rows are wiped because they're no longer re-inserted (the keep↔keep row that would have carried them was deleted). Net: kept article loses its ssrn/pdf/candidates silently, and `articles_deleted` excludes the dropped DOIs correctly, but the kept DOI is now childless. **What would change my mind:** confirm that the kept DOI's children are re-derived from a non-empty `_new_*` even after pruning (they aren't — `_merge_map` no longer contains `keep`), or that skipped groups are intended to be fully no-ops (in which case the child-DELETE must be guarded to only touch participating keeps). Fix: filter the child DELETE to `doi IN (SELECT doi FROM _merge_map)` *after* pruning, and additionally skip writing children for pruned keeps — but since pruning removes the keep, the cleanest fix is to add a separate `_active_keeps` set and guard every child DELETE/INSERT by membership.

3. **[MAJOR] `repair_identical_duplicate_rows` operates per-key without verifying the *child* tables are also conflict-free.** The method collapses identical duplicate rows in `articles`, `ssrn_pages`, etc. independently, but a DOI with conflicting (non-identical) duplicates in `pdf_files` (say, two different `verify_status`) is left as-is while `articles` is collapsed to one row. Now two `pdf_files` rows point at one article with different content and no audit trail. The docstring says callers must refuse to proceed when `n_distinct != 1`, but `repair_identical_duplicate_rows` itself silently partial-repairs across tables. **What would change my mind:** the method is only ever called after a full `find_duplicate_keys` guard in the caller and never alone, or the method itself bails atomically (no repairs) if any table has non-identical dups. A single "all clean or nothing" check at the top would resolve this.

4. **[MAJOR] `find_duplicate_keys` relies on `COUNT(DISTINCT t)` where `t` is the table alias/struct.** The comment claims "COUNT(DISTINCT t) counts distinct whole rows (t is the row as a struct)." In DuckDB, referencing the correlation name `t` inside `COUNT(DISTINCT t)` does construct a struct of all columns — that part is correct — but `t` is also the table alias used in `FROM {table} t`. If `t` is also a *column name* on any of these tables (none obviously, but `pdf_candidates` has `source`...), the binding is ambiguous. More importantly, the grouping key for `pdf_candidates` is `doi, source`, yet `SELECT {key}, ... GROUP BY {key}` expands to two columns — `dup["doi"]`/`dup["source"]` access works, but `repair_identical_duplicate_rows` builds `where = "doi = ? AND source = ?"` and `params = [dup[c] for c in cols]` which is correct. OK on inspection, but fragile: rename a key column and the dict access silently breaks. **What would change my mind:** add a small test enumerating each `_KEYED_TABLES` entry against a fixture with identical and divergent dups asserting `n_distinct` correctness; until then this is a WARN that the struct-count idiom is under-tested.

5. **[MAJOR] `processing_log` repointing loses history provenance.** `UPDATE processing_log SET doi = m.keep FROM _merge_map m WHERE processing_log.doi = m.doi AND m.doi <> m.keep` overwrites the original DOI in place. There is no `src_doi` column added to `processing_log`, unlike the child tables. If `processing_log.doi` is part of a unique/PK or referenced by other tables, rows for kept and dropped DOIs may collide (two log entries with the same `(doi, ...)` key). **What would change my mind:** `processing_log` has no PK/unique constraint involving `doi` and no downstream FK references it; confirm via schema. Otherwise this can raise a constraint error mid-transaction (rolled back, safe but failing) or, worse, silently merge audit rows.

6. **[MINOR] Temp-table names (`_merge_map`, `_new_ssrn`, `_one_row`) are connection-global, not transaction-scoped.** If two merge runs overlap on the same DuckDB connection (unlikely but possible in a long-lived repo), `CREATE OR REPLACE` clobbers the other's state. **What would change my mind:** single-threaded usage guarantee in the repo, or suffixed names.

7. **[MINOR] `INSERT INTO {table} BY NAME SELECT * EXCLUDE (src_doi...) FROM {new}` depends on exact column alignment between temp and target.** `BY NAME` mitigates ordering, but a renamed target column (e.g. `pdf_files.verify_status` → `status`) silently drops data. **What would change my mind:** pinned schema test for each child table.

8. **[MINOR] `get_articles_for_dedup` selects all rows with no `WHERE`.** Fine for the matching rule but worth a comment that this is intentional full-scan for in-memory dedup.

## Kept-DOI / wrong-merge concerns (per review focus)

- The *rule* lives in `duplicates.py` (not shown), so I can only judge the repository's trust of it. The repository accepts arbitrary `(keep, drops)` groups with zero validation that `keep` is not among `drops`, that groups are disjoint, or that a DOI isn't a keep in one group and a drop in another. **[MAJOR]** A malformed group set (e.g. crossref alias lookup returns a cycle) will silently corrupt the map. **What would change my mind:** assert at entry that `keep` is unique across groups, no DOI appears as both keep and drop, and no drop appears twice.
- Corrigendum/reply safety is entirely delegated to `duplicates.py`; the repository has no defense. Acceptable by separation of concerns, but the wiki-skip is the only structural guard and it's buggy (issue 2).

## CrossRef lookup (not in this diff)

The prompt mentions CrossRef 301 alias lookup and politeness. None of that is in this diff, so I can only note: ensure the lookup has a User-Agent identifying the project + contact, caches responses, and treats `relation.isIdenticalTo` / `relation.hasPreprint` etc. with care — 301 is *HTTP* redirect, not a bibliographic alias assertion. A redirect may point to a *different* work in rare cases. **What would change my mind:** the `duplicates.py` rule asserts `relation` semantics rather than trusting the redirect target.

## Score

- Start: 100
- `src_doi` aliasing bug (audit values silently wrong, downstream provenance lie): −18
- Wiki-skip pruning wipes kept-DOI children: −15
- Partial repair across tables without global guard: −10
- No input validation on `groups` (cycles/overlaps): −8
- `processing_log` repoint without provenance / PK check: −6
- Temp-table / schema fragility (WARNs): −4

**Final score: 39**

---

## Batch b3

# Code Review
**Overall:** MINOR ISSUES

## Summary
This diff adds a `merge-duplicates` CLI command (plus Makefile targets) that deduplicates journal articles stored under multiple DOIs, backed by a pure matching module (`duplicates.py`, not shown) and a repository layer. The command is well-structured: dry-run by default, pre-merge backup, duplicate-key guard, per-row report CSV, and transactional merge delegation to the repo. Main concerns are around a couple of correctness/robustness gaps in the CLI glue (alias re-scan interaction, backup-vs-checkpoint ordering, the "identical rows" key guard), and politeness of the CrossRef alias resolution is unverifiable from this diff alone.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | `dup_keys` "DIFFERENT rows" check runs only when `apply` is set, but duplicate-key echo before that doesn't gate on apply; logic is fine but the `repair_identical_duplicate_rows()` call is unconditional on `dup_keys` truthiness only loosely. Backup-then-repair ordering is correct. |
| 2 | Structure | OK | Clean separation: pure matching in `duplicates.py`, repo for persistence, CLI for orchestration. `row()` helper is tidy. |
| 3 | Reproducibility | WARN | No seed (n/a here). Timestamped report default is good. `cache_dir`/`report` default to settings; no hardcoded paths. Fine. |
| 4 | Error handling | WARN | `raise click.ClickException` for differing duplicate keys is good. But several `.get`/attribute accesses on scan results assume structure; no guard if `scan.groups`/`scan.held_no_pages`/`scan.conflicted` shapes differ. |
| 5 | Comments | OK | Docstring explains the rule and ordering constraint ("Run after refresh-metadata"). Inline comment on alias resolution explains *why*. Good. |
| 6 | Style | OK | Consistent with surrounding commands. Local imports inside function match existing pattern (`cleanup_non_articles` does similar). |
| 7 | Performance | OK | Single pass over groups; alias resolution only for DOIs missing pages. DataFrame built once. Fine. |
| 8 | Security | OK | No shell commands, no secrets. `mailto` comes from settings. Paths validated via `click.Path`. |

## Issues

1. **MAJOR — Backup may not reflect on-disk state at merge time.** `db.conn.execute("CHECKPOINT")` is called, then `shutil.copy2(db_file, ...)` is taken. But the merge itself happens *after* the copy via `repo.merge_duplicate_articles(...)`. This is actually correct ordering (backup is pre-merge). However, the comment "the file this connection opened" and reliance on `db.db_path` being the live file: if the connection was opened with a different path than `db.db_path` (e.g. temp/URI), the backup silently backs up the wrong file. *What would change my mind:* evidence that `db.db_path` is always the canonical file path the connection writes to (e.g. a test or the DB wrapper guaranteeing this), or an assertion `Path(db.conn.execute("PRAGMA database_list").fetchone()[2]) == db_file`.

2. **MAJOR — Duplicate-key repair is unconditional when `dup_keys` is non-empty, mixing identical and differing rows.** Lines: `if dup_keys: click.echo(f"Repaired duplicate keys: {repo.repair_identical_duplicate_rows()}")`. Earlier the code raises if *any* key has `n_distinct != 1`, so by this point all are identical — but the guard is `if dup_keys` (truthy dict) rather than `if any identical keys`. If a future repo change lets differing rows coexist, `repair_identical_duplicate_rows()` could silently dedupe differing data. *What would change my mind:* an explicit filter `if any(k["n_distinct"] == 1 for keys in dup_keys.values() for k in keys)` or documentation that the preceding raise guarantees only-identical.

3. **MINOR — Alias re-scan runs unconditionally when any alias is found, even if no aliased DOI is in a candidate group.** `if any(aliases.values())` re-runs `find_duplicates` over all candidates. Harmless (cached), but the condition could be scoped to `aliases` that actually touch a candidate DOI to avoid a redundant scan and a potentially different `scan` object confusing log reconciliation.

4. **MINOR — `crossref.get(r.doi.lower(), (None, 0))` default cites `0`** silently treats "not in cache" as "zero citations" for any downstream tie-breaking in `find_duplicates` that uses `r.cites`. If `duplicates.py` uses citations for kept-DOI choice (context says it does: "then citations"), a missing cache entry could bias the winner toward the other DOI. *What would change my mind:* confirmation that `find_duplicates` only compares `cites` when both are non-None, or that the cache is guaranteed complete after `refresh-metadata`.

5. **MINOR — `aliases.get(r.doi.lower()) or None`** conflates "no alias" with falsy alias values (empty string). Unlikely from CrossRef but `or None` is a mild smell; `aliases.get(...) if ... else None` or explicit check is safer.

6. **MINOR — `report` parameter shadows the function arg.** The option `report` is reassigned on the default branch (`report = report or settings.reports_dir / ...`). Works, but reusing the parameter name for a `Path` after it may be `None` is slightly confusing; a local `report_path` would read better.

7. **MINOR — Makefile `.PHONY` split with backslash continuation** is fine but the new targets could be alphabetized/grouped with the `cleanup-*` pair for readability. Not a correctness issue.

8. **INFO — CrossRef politeness/alias correctness** cannot be verified here (lives in `duplicates.py`); the CLI passes `mailto` and a cache dir and limits the DOI set via `dois_to_check_for_alias`, which is the right shape. No rate-limit/Retry-After handling visible at this layer.

## Score
Start 100.
- Issue 1 (backup path assumption): -8
- Issue 2 (repair guard breadth): -6
- Issues 3–7 (minor robustness/style): -6 total
- Issue 8 (unverifiable, no deduction)

**Final score: 80**

---

## Batch b4

# Code Review
**Overall:** MINOR ISSUES

## Summary
This is a thorough test suite for the duplicate-DOI merging rule. Tests cover the main cases: JSTOR/publisher prefix merges, page tolerance, corrigendum/reply exclusion, author fuzzy matching, alias resolution, kept-DOI preference, and the transactional merge. The biggest gaps are around politeness/rate-limiting, alias-loop safety, and missing edge cases in the kept-DOI choice when alias targets are themselves droppable.

## Category results
| # | Category | OK/WARN/FAIL | findings |
|---|----------|--------------|----------|
| 1 | Correctness | WARN | No tests for alias chains, alias pointing to a drop-member, or JSTOR-loses-to-alias-target rule interaction with citations. |
| 2 | Structure | OK | Tests well-organized by concern; helpers `rec`/`_pair` reduce boilerplate. |
| 3 | Reproducibility | OK | Uses `tmp_path`, fixtures; no hardcoded paths. |
| 4 | Error handling | WARN | No test for malformed alias responses, network timeouts, or cache corruption beyond the one `cache_broken` case. |
| 5 | Comments | OK | Docstrings on module; test names self-document. |
| 6 | Style | OK | Consistent; PEP 8-ish. |
| 7 | Performance | OK | Tests are small; no concerns. |
| 8 | Security | OK | No secrets; DOI strings used safely. |

## Issues

1. **MINOR — Alias target may itself be a drop candidate, not tested.** `test_crossref_alias_proves_a_pair` sets `jstor.alias_of = wiley.doi` where `wiley` is the other member. But the rule says "kept DOI = alias target, never JSTOR." What happens if the alias target is *neither* pair member, or is itself in the drop set? E.g., three DOIs A→B→C where B is alias of C. Is the kept DOI guaranteed to be a surviving member? No test asserts this invariant.

2. **MINOR — Alias redirect chains/loops not tested.** The `handler` mock handles 301 to a 200, but not a 301→301 chain, nor a loop (301→301→first). A loop could hang or recurse. The "backs off" in the test name only covers 429, not redirect depth.

3. **MAJOR — No test that JSTOR never wins via citations when alias target exists.** The rule states alias target wins over JSTOR even when JSTOR has 10⁶ cites. `test_crossref_alias_proves_a_pair` covers JSTOR-as-alias-source, but doesn't test the case where the alias target is a *low-cite publisher DOI* against a *high-cite JSTOR* that is *not* the alias source. Given this is a stated kept-DOI rule, it's a coverage gap.
   - What would change my mind: a test asserting `find_duplicates([[jstor_high_cite, publisher_low_cite, third_alias_of_publisher]])` keeps `publisher_low_cite`.

4. **MINOR — Politeness not asserted.** `resolve_aliases` takes a `delay` param set to 0 in tests, but no test verifies the delay is actually applied between requests, or that `Retry-After` is honored (the 429 case just retries once). A regression that drops the sleep would pass silently.
   - What would change my mind: a test using a mock timer/monkeypatched `time.sleep` asserting call count ≈ N requests.

5. **MINOR — `merge_skips_groups_whose_dropped_doi_has_a_wiki_page` doesn't test the mixed case.** It tests a group where the drop has a wiki page. What if only *one of several* drop DOIs has a wiki page? Is the whole group skipped (current assertion implies yes), or is that DOI retained and others merged? This is a data-loss-adjacent behavior worth pinning down.
   - What would change my mind: a test with two drop DOIs, one with a wiki page, asserting the exact kept/dropped/partial-merge outcome.

6. **MINOR — No test for FK-violation safety across the two-transaction split.** The context note ("DuckDB checks FKs against the transaction's starting state") is the load-bearing detail. The merge test verifies rows moved and drop succeeds, but doesn't test what happens if a child row exists for a DOI *not* in the merge group (e.g., stale candidate referencing a to-be-dropped DOI from outside). A regression in transaction ordering could cause FK errors or orphaned rows.
   - What would change my mind: a test inserting a child row whose DOI is a drop target *after* the merge transaction begins (or simulating a pre-existing orphan), confirming behavior.

7. **MINOR — `test_merge_moves_better_rows` asserts kept-DOI candidate wins a clash but only for `source` clash.** What about clashing `(doi, source, status)` with a UNIQUE constraint? The test checks `cands == {"oa": "no_match", "nber": "no_match"}` but doesn't verify behavior when both keep and drop have the *same* source with *different* statuses and a unique index. If the upsert uses ON CONFLICT, order matters.

## Score
Start 100.
- −3 Issue 3 (MAJOR, missing kept-DOI invariant test)
- −2 Issue 4 (politeness unverified)
- −1 each Issues 1, 2, 5, 6, 7

**Final: 90**
