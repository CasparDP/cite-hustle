# pi review: abstract cross-check (glm-5.2:cloud, 2 batches)

Triage (Claude): fixed no_comparator staleness (re-checked after 30 days) and the titles dict rebuilt per source. False positives: substring inflation (thresholds were calibrated with the same rule), DOI casing (both sides lowercased), 'junk CrossRef comparator yields a wrong match' (junk lowers overlap; replacement is guarded by junk/header checks). Rest minor.

## Batch x1
# Code Review
**Overall:** MINOR ISSUES

## Summary
`abstract_check.py` is a focused, well-documented module that scores abstracts against reference text and detects junk/placeholders. The logic is mostly sound and the calibration thresholds are clearly explained. The main concerns are around edge cases in the `content_words` / `overlap` space-stripping approach (word-boundary-free substring matching can produce false matches), fragile regex anchors in `cut_abstract_from_page_text`, and some silent-skip behavior in `usable_comparator` that could mask borderline junk.

## Category results

| # | Category | OK/WARN/FAIL | Findings |
|---|----------|--------------|----------|
| 1 | Correctness | WARN | Space-stripped substring matching can match substrings of longer words; `_ABSTRACT_START` regex assumes SSRN date format strictly; `is_citation_header` prefix bound uses first 400 chars only |
| 2 | Structure | OK | Small functions, clear responsibilities, no dead code |
| 3 | Reproducibility | OK | No hardcoded paths, thresholds explained with calibration dates |
| 4 | Error handling | WARN | `Optional` returns silently `None`/`False` on short/None inputs; `cut_abstract_from_page_text` swallows malformed page text |
| 5 | Comments | OK | Docstrings explain *why* (calibration, junk markers); threshold rationale documented |
| 6 | Style | OK | Consistent, PEP-8-ish, type hints present |
| 7 | Performance | OK | O(n) scans over short texts; fine |
| 8 | Security | OK | No shell, no secrets, input is text only |

## Issues

1. **MINOR — Space-stripping causes spurious partial-word matches.** `overlap()` does `normalize_title(pdf_text).replace(" ", "")` then checks `w in blob`. A 5-letter abstract word like "model" will match inside "modeling", "remodel", "models", inflating the overlap score. Given the threshold (0.65) this is unlikely to flip clean mismatches into matches, but it weakens the calibration guarantee. What would change my mind: evidence from the calibration set that no wrong pair crosses 0.65 *with this exact matching rule* (the comment cites the wrong-pair max but doesn't state the matching method is identical). Fix: match on word boundaries in the blob, e.g. `blob.split()` membership, or pad `blob` with delimiters and search `f" {w} "` style — but that conflicts with the pypdf no-spaces rationale. A middle ground: tokenize the abstract normally but match against both the spaced and space-stripped blobs.

2. **MINOR — `content_words` dedupes via set, losing count signal.** `sorted({w for w in ...})` treats repeated content words as one. An abstract that says "arbitrage arbitrage arbitrage" scores the same as one mention. Probably intentional (robustness to repetition), but not documented. Fine for the stated purpose; flag only for a one-line comment.

3. **MINOR — `_ABSTRACT_START` regex is narrow.** It only matches `Date Written: <Month> <day>, <year>` or `Date Written: <Month> <year>`. If SSRN ever renders `Date Written: 13 September 2025` or a two-line date, `cut_abstract_from_page_text` returns `None` and the abstract is silently not recovered (falls through to "clear" in the repair pipeline per the test). This is a data-loss-ish path, but only for the cut fallback, which has CrossRef as a higher-priority source. Acceptable, but worth a comment noting the format assumption.

4. **MINOR — `is_citation_header` searches only the first 400 chars.** `x.find(t, 0, 400)` — if the title appears after 400 chars of preamble, it's not detected. Unlikely for citation headers, but the 400 is magic and undocumented.

5. **MINOR — `usable_comparator` strict title-word check uses `len(w) >= 4`, not the module's `MIN_WORD_LEN=5`.** Inconsistent thresholds between functions. Not wrong, but a reader will wonder why. Either align or comment.

6. **MINOR — `_loose` unifies quotes to `'` but `normalize_title` (in `duplicates`) behavior is unseen.** The module relies on `normalize_title` for the core scoring but it's imported and not visible here. Trust but verify: if `normalize_title` lowercases and strips punctuation, the space-stripped `blob` comparison is consistent; if it doesn't, `overlap` and `usable_comparator` could disagree on case. No issue visible from this file, but worth a cross-check.

7. **MINOR — `cut_abstract_from_page_text` length gate is a bare `>= 100`.** Magic number; a real abstract could be ~90 chars in edge cases, and a junk fragment could be 100+. The secondary `is_ssrn_page_text` guard mitigates the latter. Consider a named constant and a comment on why 100.

8. **MINOR — No retry/backoff concern in this file, but `no_comparator` semantics live in CLI.** The reviewer focus mentions "retry semantics of no_comparator." This module defines `usable_comparator` returning `False` for short/biography/OCR text, but the retry decision (whether to re-fetch from another source) is in `commands.cross_check_abstracts`, not shown. From what's visible, `no_comparator` is terminal per source per run; the test confirms `to check: 0` on re-run, meaning once recorded as `no_comparator` it's not retried even if a better source appears later. That's a MAJOR-ish data-staleness concern *if* new DOI-exact abstracts arrive in later cache snapshots, but it's outside this file. What would change my mind: seeing that `cross_check_abstracts` re-evaluates `no_comparator` rows when new cache files are present, or that the command is intended to be re-run with `--force`/new sources.

## Score
Start: 100
- Spurious partial-word matches in `overlap` (undocumented calibration-method gap): -4
- `no_comparator` terminality concern (deferred to CLI, flagged per focus): -3
- Magic numbers / inconsistent word-length thresholds (`>= 4` vs `MIN_WORD_LEN`): -3
- Narrow SSRN date regex with silent `None` fallback: -2
- Minor comment gaps: -1

**Final score: 87**

## Batch x2
# Code Review
**Overall:** MAJOR ISSUES

## Summary
The cross-check-abstracts command adds a valuable DOI-exact comparison layer on top of fuzzy SSRN/NBER abstracts, with sensible backup/dry-run plumbing. However, there are several correctness concerns: the `no_comparator`/`complete` interaction can silently re-check infinitely, the DOI key normalization is inconsistent across boundaries (some code lowercases, some doesn't), and `get_abstracts_for_source_check`'s "current check" predicate can re-fetch rows whose check exists but whose abstract hasn't changed. The OpenAlex batch source has polite retries but a fragile error path.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | `complete` semantics; `no_comparator` not skipped even when no source was ever tried for that DOI; title-key construction inside loop |
| 2 | Structure | OK | Clear flow: collect → compare → report → apply |
| 3 | Reproducibility | WARN | No seed not needed here, but report path uses seconds timestamp; cache dir defaults to settings (fine) |
| 4 | Error handling | WARN | `OpenAlexBatchSource.fetch` can raise `ResolverError` for 4xx but the caller only catches `ResolverError` — OK; but `error` may be unbound if first iteration's `except` sets it then loop exits via `raise` — actually fine. Empty `usable` returns `{}` silently for DOIs with `\|`/`,` |
| 5 | Comments | OK | Good "why" comments |
| 6 | Style | OK | Mostly consistent |
| 7 | Performance | WARN | `titles` dict rebuilt inside the source loop per source; `.itertuples()` fine |
| 8 | Security | OK | No secrets; OpenAlex mailto/api_key from settings |

## Issues

1. **[MAJOR] `no_comparator` is recorded even when no source returned anything for that DOI, causing re-check churn.** In the apply loop:
   ```python
   if status == "no_comparator" and not complete:
       continue  # a source failed: try these again next run
   ...
   repo.record_abstract_check(doi, status, score, source or "none")
   ```
   When `complete=True` (every source answered fully) but a DOI genuinely has no comparator across all sources, `no_comparator` is recorded permanently. That's fine. But when `complete=False` because *one* batch of *one* source failed, DOIs that were never queried by the failed source (because they were already covered by an earlier source) still hit the `not complete` branch and are skipped — but DOIs that *were* covered and returned nothing are also skipped, so they get re-checked forever only if the failing source is the one that would cover them. The logic conflates "a source failed mid-stream" with "this DOI was queried by all sources." `complete` is a single global flag; it should be per-DOI: "was this DOI attempted by every source in the list?"

   **What would change my mind:** Track per-DOI whether each configured source was actually queried for it (e.g., a `queried_by` set per DOI), and only skip `no_comparator` recording when some source didn't reach that DOI.

2. **[MAJOR] DOI key casing is inconsistent between `comparators` and `found`.** `comparators` is keyed by `doi.lower()`. `OpenAlexBatchSource.fetch` returns keys lowercased. `crossref_abstracts_from_cache` — its return key casing isn't shown here, but the code does `for doi, abstract in found.items(): ... comparators.setdefault(doi.lower(), ...)` so it normalizes on insert. But in the decision loop:
   ```python
   source, exact = comparators.get(row.doi.lower(), (None, None))
   ```
   `row.doi` comes from DuckDB `s.doi`; if stored mixed-case, `.lower()` normalizes — OK. But `titles = dict(zip(rows["doi"].str.lower(), rows["title"]))` and then `titles.get(doi.lower())` where `doi` is already lowercased from `found` — redundant but harmless. The real risk: if CrossRef cache returns non-lowercased keys and a DOI differs only by case across sources, `setdefault` could insert two entries. Low likelihood but the normalization should happen once at the `found` boundary.

   **What would change my mind:** Evidence that `crossref_abstracts_from_cache` and the fetchers all guarantee lowercase keys, or a single `.lower()` applied to every `found` key before merging.

3. **[MAJOR] `usable_comparator` with `strict=source != "crossref"` can import junk as a comparator from CrossRef, leading to wrong "match" decisions and failing to replace a bad SSRN abstract.** CrossRef is the *non-strict* path, meaning a CrossRef abstract that is a citation header or junk but passes the loose bar becomes the comparator; if the SSRN abstract overlaps it ≥0.45, it's marked `match` and kept — even though the SSRN abstract may be the wrong paper's abstract. The calibration note ("no same-journal wrong pair reached 0.45") is about *wrong* pairs, but here the comparator itself could be junk. This is the core risk the command is supposed to prevent.

   **What would change my mind:** A comment or evidence that CrossRef cached abstracts are pre-vetted as non-junk, or that `usable_comparator(strict=False)` still rejects citation headers (which the context says it does — but then why is strict needed at all?).

4. **[MINOR] `titles` dict is rebuilt inside the source loop** — once per source iteration. Move it above the loop:
   ```python
   titles = dict(zip(rows["doi"].str.lower(), rows["title"]))
   for source in ...:
   ```

5. **[MINOR] `OpenAlexBatchSource` silently drops DOIs containing `|` or `,`** — returns no entry for them, so they remain `no_comparator`. A DOI with `|`/`,` is malformed but should be logged, not silently swallowed.

6. **[MINOR] `error` variable referenced after loop** — if `self.MAX_ATTEMPTS == 0` (hypothetically), `error` is unbound. Not a real risk given constant, but a `error = "unknown"` init would harden it. Mirrors the existing `SemanticScholarSource` pattern, so consistent.

7. **[MINOR] `time.sleep(1.0)` after every OpenAlex/S2 batch** is polite but unconditional — even on the last batch. Harmless. The OpenAlex batch source already backs off on 429/5xx internally, so the outer 1s is double-politeness; fine.

8. **[MINOR] `get_abstracts_for_source_check` "current check" predicate** `c.abstract_md5 <> md5(s.abstract)` — if `c` has a check but `s.abstract` is unchanged, the row is excluded unless `rerun`. Good. But `LEFT JOIN abstract_checks c` with no dedup means if multiple checks exist per DOI, the row duplicates. The schema isn't shown; if `abstract_checks` is one-row-per-DOI, fine. Worth confirming.

9. **[MINOR] `query += f" LIMIT {int(limit)}"`** — `int(limit)` guards injection, but the idiomatic DuckDB approach is `?` placeholder. Consistent with `rerun` being parameterized; inconsistent that `limit` isn't.

10. **[MINOR] `db.conn.execute("CHECKPOINT")` before backup** is good practice but if `db.db_path` points to a WAL-mode DB on a synced volume (Dropbox), the backup may still miss `.wal`. Context warns about this generally. Confirm `CHECKPOINT` fully closes the WAL.

## Score
Start 100.
- Issue 1 (no_comparator/per-DOI completeness): -10
- Issue 3 (strict=False CrossRef comparator quality): -8
- Issue 2 (casing boundary): -4
- Issues 4-10 (minor): -6 total
**Final: 72**
