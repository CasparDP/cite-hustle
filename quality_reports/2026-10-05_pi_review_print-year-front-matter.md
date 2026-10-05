# pi review (glm-5.2:cloud): citation year, front-matter rule, cleanup-non-articles

Date: 2026-10-05. Two batches (skeptic).

## Triage

- Fixed: open-ended patterns (committee on, minutes of, index to, report of the committee) narrowed to the forms in the corpus, with negative tests; delete_articles itself refuses rows with pdf_files/wiki_pages; backup copies the connection's own file; temp table dropped in finally. Matched set on the live DB unchanged (4,727).
- Disagreed: single-transaction delete (tested: DuckDB rejects child-then-parent deletes in one transaction); settings not imported (module-level import; apply path covered by a test); Python/SQL divergence (identical sets on all ~80k stored titles); per-connect DROP INDEX IF EXISTS (idempotent, single-writer DB); int() on date-parts (pre-existing; CrossRef schema has integer parts).

## Batch 1

# Code Review
**Overall:** MAJOR ISSUES

## Summary
The changes introduce a citation-year preference (print > issue > issued) and a front-matter filter based on an anchored title regex plus issue-DOI patterns. The year logic is a reasonable improvement over the old single-field read, but the migration is unsafe (DROP INDEX on every non-read-only connection, racing with concurrent `_create_indexes`), and the front-matter regex has several patterns that will match legitimate research articles, causing silent deletion of data. The RE2/DuckDB compatibility claim is not fully validated.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Year fallback chain OK; `_crossref_year` now redundant duplication of `citation_year`. Front-matter regex has false-positive risk. |
| 2 | Structure | WARN | `_crossref_year` imports inside function; duplicated fallback logic between it and `citation_year`. |
| 3 | Reproducibility | OK | No hardcoded paths/seeds. |
| 4 | Error handling | WARN | `int(parts[0][0])` can raise on malformed CrossRef dates; no try/except. |
| 5 | Comments | OK | Comments explain the why (delete+insert FK violation, online-first vs issue year). |
| 6 | Style | WARN | In-function import; `.lower()` applied to DOI already known lowercase-only in places. |
| 7 | Performance | OK | Dropping index rationale documented; ~80k rows. |
| 8 | Security | OK | No secrets; regex on local data. |

## Issues

### 1. [CRITICAL] Front-matter regex false positives — will delete research articles

Several `_TITLES` patterns match real article titles under whole-title anchoring:

- `r"notes"` — A paper titled exactly "Notes" is unlikely, but `r"abstracts?"` matches any article titled "Abstract" and also "Abstracts"; some journals publish survey articles literally titled "Abstract" (e.g., dissertation abstracts in *Dissertation Abstracts International*). More dangerously, `r"committee on .*"` will match a research article titled "Committee on Banking Theory" — and `.*` with the `[\s.:]*$` suffix means any title beginning "Committee on …". Given the cleanup deletes child rows, this is data loss.
- `r"(subject \|author )?index( to .*)?"` — matches "Index" and "Author Index" — OK, but `.*` trailing makes it match "Index to the Literature on …" which *could* be a real review article.
- `r"minutes of .*"` and `r"committee on .*"` and `r"report of (the )?(finance committee\|committee on .*\|…)"` are all prefix-anchored (`.*` to end) so they match **any** title starting with those phrases, including genuine research articles titled e.g. "Committee on the Global Financial System: A Survey" or "Report of the Panel on …".
- `r"(the )?journal of( [a-z&,']+){1,4}( call for papers)?"` — matches "Journal of Finance" but **also** "Journal of Finance call for papers" *and* any 1–4 word tail like "Journal of Law Economics" (which is *Journal of Law & Economics*, a real research title if it ever appears alone). The character class `[a-z&,']+` excludes digits, but a paper titled "The Journal of Finance" as a **commentary about** that journal would be deleted.
- `r"practice summar(y\|ies)"` — fine.
- `r"abstracts?"` combined with `[\s.:]*$` — matches "Abstract." Some CrossRef records have title "Abstract" for genuine abstract-only entries, but also for some conference papers.
- `r"fellows of the econometric society"` / `r"officers of the society.*"` — society-specific; acceptable but the `.*` suffix is overly greedy.
- `r"recommendations for further reading"` — fine.

The core problem: patterns with `.*` suffixes turn "anchored whole title" into **prefix** anchoring, contradicting the module docstring ("anchored on the whole (lowercased) title"). `.*` to `$` means any continuation matches.

**What would change my mind:** A test corpus of all currently-stored titles run through `is_front_matter` showing zero matches on hand-verified research articles, plus tightening `.*` patterns to specific enumerated suffixes (e.g., `report of the treasurer('s report)?` without trailing `.*`, or `committee on .*( report)?` with a bounded suffix). At minimum, the cleanup command must dry-run-print affected DOIs and require confirmation.

### 2. [CRITICAL] RE2/DuckDB regex compatibility not verified for the full pattern

The docstring claims the title regex is "RE2-compatible so DuckDB can evaluate the same pattern." DuckDB's `regexp_full_match` uses RE2. Several constructs need checking:

- `résumés` / `abstracts/résumés` — literal non-ASCII Unicode. RE2 in DuckDB handles UTF-8 but `lower()` in DuckDB on accented chars may not normalize `É`/`é` consistently with Python's `str.lower()`. Python: `"É".lower()` → `"é"`; DuckDB `lower('É')` → behavior depends on ICU. If the stored title is "Abstracts/Résumés" and DuckDB lowercases differently from Python, the SQL predicate and the Python filter disagree → dashboard flags records the collector kept, or vice versa.
- `[a-z&,']+` in the journal-name pattern — under RE2, `[a-z]` is ASCII-only by default; under Python `re` also ASCII unless `re.UNICODE`. Consistent here, but the pattern won't match a journal title containing accented letters or digits — so a French journal masthead could slip through (minor) but not a false positive.
- `editor'?s'? (introduction|...)` — the `'?` sequences are fine in RE2.
- `acknowledge?ments?` — fine.
- No backreferences, no lookahead — RE2-safe. Good.
- The `sql_predicate` uses `regexp_full_match` for title but `regexp_matches` (search, not full) for DOI. The Python `is_front_matter` uses `_doi_re.search` (also partial). Consistent. But the title side uses `regexp_full_match` in SQL vs `_title_re.match` in Python — `re.match` is start-anchored but not end-anchored except via the trailing `[\s.:]*$`, so they're equivalent. OK.

**What would change my mind:** A unit test running the same 200 sampled titles through both `is_front_matter(title, doi)` and `SELECT ... WHERE sql_predicate()` and asserting identical results, including accented titles.

### 3. [MAJOR] Migration is unsafe and repeated on every writable connection

```python
if not read_only:
    self.conn.execute("DROP INDEX IF EXISTS idx_articles_year")
```

This runs on **every** non-read-only `DatabaseManager` construction. Problems:

- **Races with `_create_indexes`**: `_create_indexes` previously had `CREATE INDEX IF NOT EXISTS idx_articles_year`. The diff removes it from `_create_indexes`, good — but only if the diff is the complete picture. If any code path calls `_create_indexes` without going through the DROP branch (e.g., an older client), the index gets recreated and the next connection drops it again. Verify no other caller.
- **Concurrent writers**: two processes opening writable connections simultaneously — one drops the index while the other mid-query using it. DuckDB serializes writes but the DROP commits independently of the caller's transaction, so a long-running query in process B could see the index vanish. In a single-process pipeline this is fine; in a dashboard + collector setup it's a hazard.
- **Not versioned**: a real migration should be guarded by a schema-version check, not executed unconditionally forever. After the index is gone, this is a no-op `DROP IF EXISTS` on every startup — harmless but sloppy, and signals the migration framework is ad hoc.
- **Idempotent claim is true** for the DROP, but the *reason* (DuckDB update-of-indexed-column = delete+insert → FK violation) should be verified against the current DuckDB version. This was a real DuckDB limitation historically; confirm it still applies, else the index could be restored.

**What would change my mind:** (a) confirmation that only `DatabaseManager.__init__` calls `_create_indexes` and the dropped index is not recreated elsewhere; (b) a schema-version table gating the DROP so it runs once; (c) a DuckDB version note in the comment pinning the behavior that motivated removal.

### 4. [MAJOR] `_crossref_year` duplicates `citation_year` with divergent fallback

```python
def _crossref_year(msg: dict) -> Optional[int]:
    from cite_hustle.collectors.metadata import MetadataCollector
    year = MetadataCollector.citation_year(msg)
    if year is None:
        parts = (msg.get("published-online") or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
    return year
```

`citation_year` already checks `published-print`, `journal-issue.published-print`, then `issued`. The `issued` field in CrossRef is typically the online-first/earliest date — so the fallback to `published-online` here is checking a *different* field that `citation_year` does **not** consult. This means `_crossref_year` and `citation_year` return different values for records that have only `published-online` (no `issued`): the collector path (`fetch_articles_by_issn`) would return None and skip, while `fetch_crossref_article` returns the online year. Inconsistent year semantics across the two entry points.

Also, the in-function import suggests a circular-import workaround — worth a comment, or better, refactor so `acquire.py` imports at module top.

**What would change my mind:** Either `citation_year` includes `published-online` in its chain (and `_crossref_year` becomes a one-line delegate), or a comment explains why the two callers intentionally use different year semantics. The in-function import replaced with a top-level import or a documented reason.

### 5. [MAJOR] `int(parts[0][0])` raises on malformed dates

In `citation_year`:
```python
parts = (date or {}).get("date-parts") or [[None]]
if parts[0] and parts[0][0]:
    return int(parts[0][0])
```
CrossRef occasionally has string dates or malformed entries. `int("2021-03")` raises `ValueError`. The old code had the same issue, so not a regression, but the new function is now the single chokepoint — wrapping it once would harden all callers.

**What would change my mind:** `try: return int(parts[0][0]) except (TypeError, ValueError): continue` — or a test showing CrossRef `date-parts` are always single-year integers in the corpus.

### 6. [MINOR] `sql_predicate` uses `regexp_matches` for DOI but Python uses `search`

`re.search` finds anywhere in the string; `regexp_matches` in DuckDB is also a search (returns first match). Equivalent. But `regexp_full_match` for the title vs `_title_re.match` + explicit `[\s.:]*$` — the SQL `coalesce(title, '')` then `trim` then `lower` matches Python's `.strip().lower()` on `(title or "")`. Subtle: Python's `strip()` strips all whitespace including newlines; DuckDB `trim()` strips only spaces by default. A title with a trailing tab or newline would match in Python but not in DuckDB. Minor divergence but could cause dashboard/collector disagreement on edge records.

**What would change my mind:** Use `trim(... from title)` or `btrim` in DuckDB to strip all whitespace, or document that titles are pre-cleaned to single-line.

### 7. [MINOR] Empty-string title pattern `r""`

`r""` in `_TITLES` joined into the alternation contributes nothing to the regex (empty alternative), so `^(?:...|...)[\s.:]*$` will match a title that is **empty after trim** or consists only of `. : ` characters — e.g., a title of `"."` matches. That's probably intended (no-title records are front matter), but the empty alternative is implicit; a comment would clarify intent, and `r"no title available"` already covers the placeholder case.

## Score
- Start: 100
- Front-matter false positives (CRITICAL, data loss risk): -25
- RE2 compatibility unverified for Unicode (CRITICAL): -15
- Migration safety (MAJOR): -12
- Year semantics divergence between entry points (MAJOR): -10
- `int()` on malformed dates (MAJOR, pre-existing): -5
- Trim divergence Python vs DuckDB (MINOR): -3
- Empty alternative / in-function import (MINOR): -2
- **Final: 28**

## Batch 2

# Code Review
**Overall:** MAJOR ISSUES

## Summary
The diff adds a cleanup command for deleting front-matter records and refactors the non-article predicate into a shared module. The delete path has a genuine data-loss risk: `delete_articles` deliberately runs child deletes and article deletes in separate transactions (to work around DuckDB's FK semantics), but the article delete itself is uncommitted-orphaned from the child deletes and has no rollback. The cleanup command also has a backup-path bug and an unprotected `settings` import. Separately, `delete_articles` uses f-string interpolation for table names in a way that is safe today but fragile.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | article delete outside child-delete txn; backup uses undefined `settings`; temp table leaks on exception |
| 2 | Structure | OK | clean split between CLI and repository |
| 3 | Reproducibility | WARN | `settings` not imported in commands.py; backup path not configurable relative to db |
| 4 | Error handling | WARN | backup failure before delete is fine, but delete exception path drops temp table and leaves DB in inconsistent state |
| 5 | Comments | OK | FK-check rationale documented |
| 6 | Style | OK | consistent with surrounding code |
| 7 | Performance | OK | temp table + bulk IN is fine |
| 8 | Security | WARN | table names interpolated via f-string (currently static, but pattern is unsafe) |

## Issues

1. **[MAJOR] `delete_articles` runs the article delete outside its own transaction.** 
   ```python
   self.conn.execute("COMMIT")  # child deletes committed
   ...
   counts["articles"] = self.conn.execute(
       "DELETE FROM articles WHERE doi IN (SELECT doi FROM _delete_dois)"
   ).fetchone()[0]
   ```
   The child-table deletes are committed, then the article delete runs without an explicit transaction. If the article delete fails partway (or the process dies), you're left with child rows already gone but articles still present — or worse, a partial article delete if DuckDB autocommit isn't atomic per statement. The comment claims the two-step is required for FK semantics, but the article delete should still be wrapped in its own `BEGIN/COMMIT` with `ROLLBACK` on failure. Better: do the whole thing in one transaction but delete in FK-valid order (children first, then articles) within that transaction — DuckDB checks against the transaction's *starting* state, but within a single transaction the deletes accumulate and the final state has no orphans, so the FK check at commit should pass. The comment's premise that two transactions are needed should be verified against current DuckDB behavior rather than assumed.
   **What would change my mind:** evidence (DuckDB version + test) showing a single transaction with children-then-articles order fails FK validation at commit, necessitating the split. Even then, the article delete needs its own `BEGIN/COMMIT/ROLLBACK`.

2. **[MAJOR] `settings` is referenced but not imported in `commands.py`.**
   ```python
   shutil.copy2(settings.db_path, backup / settings.db_path.name)
   ```
   `settings` is not imported at the top of the diff context (only `shutil` and `datetime` are imported locally). If `settings` isn't already a module-level import in commands.py, this raises `NameError` at runtime — but only when `--apply` is passed, so it won't surface in dry runs. This is the classic "tested dry, broke wet" failure mode.
   **What would change my mind:** confirmation that `settings` is imported at module level elsewhere in commands.py outside this diff hunk.

3. **[MAJOR] Backup uses `settings.db_path` but the DB connection may point elsewhere.**
   The command copies `settings.db_path`, but `db = ctx.obj["db"]` is the live connection. If `settings.db_path` and the actually-opened database differ (test fixtures, `:memory:`, a path override passed to the DB manager), the backup copies the wrong file or fails. The backup also happens *after* `CHECKPOINT` on the live connection, which is correct for flushing, but the source path should come from the DB object, not `settings`. Additionally, `shutil.copy2` on an in-use DuckDB file (even after CHECKPOINT) may copy `.wal` inconsistently if WAL isn't fully checkpointed to the main file.
   **What would change my mind:** `CHECKPOINT` is confirmed to fully flush WAL, and the DB object exposes its file path so the backup source is authoritative.

4. **[MAJOR] Temp table `_delete_dois` leaks on exception.**
   ```python
   self.conn.execute("CREATE OR REPLACE TEMP TABLE _delete_dois ...")
   ...
   self.conn.execute("DROP TABLE _delete_dois")
   ```
   If any exception is raised between CREATE and DROP (including the `raise` in the child-delete except block), the temp table survives in the session. On a subsequent call, `CREATE OR REPLACE` masks this, so it's not catastrophic, but on a connection pool or reused session this is sloppy. Wrap in `try/finally` or use a deterministic temp name scoped per call.
   **What would change my mind:** the connection is always closed after the command, so temp tables are guaranteed dropped.

5. **[MAJOR] Protected-row review is printed but not enforced against re-query races.**
   `cleanup_non_articles` computes `protected` and `deletable` once, then passes `deletable["doi"]` to `delete_articles`. Between the query and the delete, a PDF or wiki page could be inserted for one of those DOIs (unlikely in a single-user tool, but the code claims "never deleted" as a guarantee). The delete has no `WHERE NOT EXISTS (pdf_files/wiki_pages)` guard. For a destructive command, the safety check should be in the DELETE itself, not just the pre-query.
   **What would change my mind:** this is a single-user CLI with no concurrent writers, documented as such.

6. **[MINOR] f-string table interpolation in `delete_articles`.**
   ```python
   for table in ("ssrn_pages", "pdf_candidates", "processing_log"):
       counts[table] = self.conn.execute(
           f"DELETE FROM {table} WHERE doi IN (SELECT doi FROM _delete_dois)"
       )
   ```
   The table names are hardcoded constants, so this is not exploitable today. But it's a pattern that invites future parameterization mistakes. A static tuple of pre-built statements or a dict mapping would be cleaner and avoid the f-string.

7. **[MINOR] `cleanup_non_articles` doesn't handle `backup_dir` being inside the DB's directory.**
   If `--backup-dir` resolves inside the directory containing the live `.duckdb` file, `shutil.copy2` could copy a file that's mid-write. Default `.db-backups` is relative to CWD, which is fine, but a user could pass a bad path.

8. **[MINOR] `get_front_matter_candidates` uses `self.conn` directly.**
   Other methods in the repository may use `self.conn` or a wrapper; inconsistent access pattern. Not a correctness issue.

9. **[MINOR] `value_counts().items()` on a pandas Index returns tuples.** 
   `titles` is a Series (value_counts); `.items()` yields (label, count). The loop unpacks `for title, n in titles.head(30).items()` — correct, but worth confirming pandas version behavior.

## Score
- Start: 100
- Article delete outside transaction, no rollback: −15
- `settings` possibly undefined in commands.py: −10
- Backup source path may not match live DB: −8
- Temp table leak on exception: −5
- Protected-row guarantee not enforced in DELETE: −7
- f-string table interpolation pattern: −3
- Other minors: −2
- **Final: 50**

