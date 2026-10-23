# pi review: wiki worker split mode (glm-5.2:cloud, 2026-10-23)

## Batch 1: code (skeptic): MINOR ISSUES, 79

# Code Review
**Overall:** MINOR ISSUES

## Summary
The split-mode handoff is a sensible design: the runner owns the DB and bib-key assignment, the worker only runs process-paper over a manifest. The state machine is mostly correct and the tests cover the key transitions. My main skepticism concerns the single-writer guarantee's fragility across Dropbox (manifest/pages as the transport) and a couple of correctness details in `handoff()` around `written` set construction and `get_verified_pdfs_not_ingested()` semantics.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | `written` dict built but `.keys()` passed to `isin`; `get_verified_pdfs_not_ingested` may exclude pending rows after reconcile, affecting re-runs; see issues 1, 2 |
| 2 | Structure | OK | Clean split between runner/worker; bridge methods cohesive |
| 3 | Reproducibility | WARN | `INGEST_BATCH ?=` empty and env-default behavior documented but worker env (`OLLAMA_API_KEY`, container) not pinned/checked reproducibly |
| 4 | Error handling | WARN | `wiki_work` checks env + manifest, but no DB-free validation of the manifest contents; silent on empty manifest |
| 5 | Comments | OK | Good why-comments on handoff/worker semantics |
| 6 | Style | OK | Consistent with existing module |
| 7 | Performance | OK | Batch sizes bounded; manifest rewrite fine |
| 8 | Security | OK | No secrets hardcoded; subprocess args are list form; no shell injection |

## Issues

1. **[MAJOR] `handoff()` reconciliation vs. `get_verified_pdfs_not_ingested()` re-entry semantics.** After `bridge.handoff()` runs the first time, `assign_bib_keys` upserts pending rows. On the next `wiki-ingest --handoff` (without the worker having written anything new), `repo.get_verified_pdfs_not_ingested()` must still return papers whose `wiki_pages.status == 'pending'`. The test confirms it does for the pending case, but the non-handoff path's `reconcile()` marks missing pages `failed` — meaning if a user ever runs a non-handoff `wiki-ingest` on the runner after starting handoff mode, every still-pending paper flips to `failed` and would drop out of `get_verified_pdfs_not_ingested()`. The two modes share `reconcile()` but have opposite invariants ("missing = not yet reached" vs "missing = failed"). Nothing in the code or comments guards against an operator running the wrong mode on the runner.

   What would change my mind: a guard in `wiki_ingest` non-handoff path that refuses to run when pending rows exist without source pages, or an explicit `--force-reconcile-as-failed` flag; or documentation in the docstring that non-handoff and handoff must not be interleaved.

2. **[MINOR] `written.keys()` passed to `isin` unnecessarily.** `remaining = batch[~batch["doi"].isin(written.keys())]` — `written` is a dict; `.keys()` works but `written` itself would be clearer. Not a bug. Also `written` is keyed by DOI, so the filter is correct; just stylistic.

3. **[MAJOR] Worker has no guarantee the manifest is not mid-write on Dropbox.** `write_manifest` does a direct `write_text` with no atomic temp+rename. On Dropbox, the worker's `wiki-work` could read a partially-flushed `ingest_manifest.json` (especially across machines with sync). The runner also rewrites the manifest every run, so a worker reading while the runner is writing a new batch could process a truncated file. `json.loads` on the worker side (in `wiki_work` via `run_process_paper`, which passes the path to process-paper) would fail mid-run.

   What would change my mind: `write_manifest` writing to a temp file in the same dir then `os.replace` to the final path; and the worker/process-paper validating manifest JSON before starting.

4. **[MINOR] `wiki_work` with empty/all-done manifest still invokes process-paper.** If every paper has a page, `handoff` writes a manifest with zero rows (since `remaining` would be empty). `wiki_work` calls `process-paper run --manifest <empty>`. Behavior depends on process-paper; nothing checks `len(rows) == 0` and exits cleanly. Likely benign given idempotency, but undocumented.

5. **[MINOR] `wiki_work` constructs a `WikiBridge(repo=None, ...)` but `run_process_paper` never uses `self.repo`, so that's safe — however there's no static/type assertion that `repo=None` won't crash if a future refactor touches `repo` in the worker path.** A comment asserting "worker path must never dereference self.repo" would help future maintainers.

6. **[MINOR] `reconcile` writes `failed` rows even in handoff mode for DOIs in `written` whose page vanished between the `.exists()` check and `read_text`.** `handoff` filters `written` by `.exists()`, then `reconcile` re-checks `.exists()` and marks `failed` if gone. Narrow race, but on Dropbox a file can appear in directory listing then fail to download. This would incorrectly mark a paper failed when the worker actually succeeded. The handoff-mode docstring promises pending stays pending for unprocessed papers — but a *processed-then-sync-glitch* paper becomes failed, not pending.

   What would change my mind: in handoff mode, treat missing-after-listed as a transient error (skip, don't mark failed), or retry the existence check.

7. **[MINOR] `settings` is referenced in `wiki_work` but not imported at module top in commands.py.** It's used elsewhere in the file presumably; consistent with existing pattern, but worth noting the import is implicit.

## Score
Start: 100
- Manifest non-atomic write on Dropbox transport (shared medium, concurrent runner/worker): -8
- No guard against interleaving handoff/non-handoff modes on the runner (shared `reconcile` with opposite invariants): -8
- Empty-manifest / transient-missing-page edge cases undocumented: -3
- Minor style/clarity: -2

**Final score: 79**

## Batch 2: deploy and docs (pragmatist): SOUND WITH MINOR ISSUES, 94

# Code Review
**Overall:** SOUND WITH MINOR ISSUES

## Summary
These deploy scripts, LaunchAgent, and docs add a split-mode wiki worker: the runner hands off a manifest, an Intel Mac runs `process-paper` over it via launchd, and state is reconciled via Dropbox. The scripts are well-structured, defensive about the single-writer invariant, and the documentation is thorough. The main concerns are: a silent fallback when `PROCESS_PAPER_DOCLING_URL` is unset on the worker (it will skip the health-check wait and then `process-paper` may abort at once), a health-check loop that doesn't actually fail if docling never comes up, and a `sed` in the install script that could break on usernames containing `/` or `&`.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Health loop never verifies docling is reachable before exec; no failure if all 30 retries fail. |
| 2 | Structure | OK | Clear separation; wrapper + installer + plist + docs. |
| 3 | Reproducibility | WARN | `$HOME`-based paths are good; but `REPO_DIR` is hardcoded in `run_wiki_work.sh` rather than derived. |
| 4 | Error handling | WARN | `find ... -delete \|\| true` is fine; health-check silently proceeds on exhaustion. |
| 5 | Comments | OK | Comments explain why (reboot delay, single-writer, Intel Mac constraints). |
| 6 | Style | OK | Consistent zsh conventions. |
| 7 | Performance | OK | N/A for these scripts. |
| 8 | Security | OK | No secrets in files; env sourced from user config. |

## Issues

1. **MINOR — Health-check loop doesn't fail on exhaustion** (`run_wiki_work.sh`, lines ~27-31):
   ```zsh
   for _ in {1..30}; do
     curl -sf "$PROCESS_PAPER_DOCLING_URL/health" >/dev/null && break
     sleep 20
   done
   ```
   If all 30 attempts fail, the loop exits normally and `exec` runs `wiki-work`, which "aborts at once if it is unreachable." The comment says this is the expected behavior, so it's arguably intentional — the worker will just retry at the next launchd interval. But it would be cleaner to `exit 0` (so launchd retries per its schedule) rather than letting `wiki-work` crash and produce an error log. **What would change my mind:** the author confirms the crash-then-retry is deliberate and launchd `StartInterval`/calendar retry is sufficient (it is, given daily 03:00 + login). Low priority.

2. **MINOR — No health check when `PROCESS_PAPER_DOCLING_URL` is unset** (`run_wiki_work.sh`, line 24):
   The guard `if [[ -n "${PROCESS_PAPER_DOCLING_URL:-}" ]]` means on Apple Silicon (native Docling) the wait is skipped — correct. But on an Intel Mac where the env var was accidentally not set, the script silently proceeds and `process-paper` will fail opaquely. The install script checks docling reachability *only if the var is set* (line ~35), creating a gap: install passes, runtime fails. Consider warning in `run_wiki_work.sh` if on Intel (or just always) and the var is unset. **What would change my mind:** evidence that `process-paper` itself emits a clear error when `PROCESS_PAPER_DOCLING_URL` is missing on a platform without native Docling.

3. **MINOR — `sed` username substitution is not robust** (`install_worker.sh`, line ~42):
   ```zsh
   sed "s/__USER__/$USER/g" "$REPO_DIR/deploy/$PLIST.plist" > "$AGENT_DIR/$PLIST.plist"
   ```
   If `$USER` contains `&` or `/` (unlikely on macOS but possible in some configs), the substitution breaks. Use a different delimiter and escape, or use `envsubst`. Pragmatist view: macOS usernames are constrained, so this is low-risk in practice. **What would change my mind:** confirmation that usernames here are always simple alphanumeric (they are on stock macOS).

4. **MINOR — `REPO_DIR` hardcoded in wrapper but derived in installer** (`run_wiki_work.sh`, line 7 vs `install_worker.sh`, line 8):
   The installer derives `REPO_DIR` from script location; the wrapper hardcodes `$HOME/Github/cite-hustle`. Since the plist is generated from a template with `__USER__`, the path is consistent — but if someone clones to a different location, only the wrapper breaks. The plist points to the wrapper at that same hardcoded path, so they're internally consistent. Acceptable, but a comment or derivation would be more robust. **What would change my mind:** the repo location is conventionally fixed (the docs say `~/Github/cite-hustle`), so this is fine.

5. **MINOR — `bootout` before `bootstrap` may emit to stderr despite `2>/dev/null`** (`install_worker.sh`, line ~44): Already suppressed. No action needed; just noting the pattern is correct.

6. **OK — Single-writer guarantee preserved**: The worker never opens the DB (`NO_DB_COMMANDS` in `main()`, per context; the scripts and docs reinforce "never opens the database"). The runner reconciles state after the fact. No DB write path is exposed to the worker. Good.

7. **OK — Bib-key stability**: Docs explain pending keys are stored first and stay stable; `--refresh`/`--keys` are refused in handoff mode. The state machine (pending → ingested/flagged, never pending → failed) is clearly documented. The scripts don't touch this logic directly.

8. **OK — launchd behavior**: `RunAtLoad` + `StartCalendarInterval` at 03:00 is appropriate. launchd won't start a second instance while one runs (documented). `caffeinate -i` prevents sleep during the run — sensible for a long multi-day first pass. The `exec` ensures signals propagate to `poetry run`.

## Score
- Start: 100
- −3: Health-check exhaustion doesn't explicitly fail/retry cleanly (issue 1)
- −2: No guard against unset docling URL on Intel worker at runtime (issue 2)
- −1: `sed` substitution not robust (issue 3, low practical risk)
- **Final: 94**

## Triage (Claude)

- B1 #3 (non-atomic manifest write): fixed; write_manifest writes a temp file and renames it.
- B1 #1 (interleaving modes flips pending to failed): disagree. Local mode reconciles only the batch process-paper just ran on, and `get_verified_pdfs_not_ingested` retries `failed` rows (it excludes only ingested/flagged).
- B1 #6 (page vanishes between check and read): self-healing; the row is marked failed, is selected again on the next run, and becomes ingested once the page is present.
- B1 #4 (empty manifest): checked; process-paper writes 0 pages and exits 0.
- B1 #2, #5, #7: style or not applicable (`settings` is imported at module top).
- B2 #1, #2 (docling not up / URL unset): intentional; process-paper's preflight aborts with a clear message before writing anything, and launchd retries at the next login or 03:00.
- B2 #3, #4: low risk (stock macOS usernames; repo path fixed by convention, same as run_pipeline.sh).
