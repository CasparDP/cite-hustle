# Code Review
**Overall:** MINOR ISSUES (with two MAJOR robustness/concurrency concerns worth fixing)

## Summary
This is a well-structured acquisition subsystem: clean separation between pure helpers (`publisher_pdf.py`), the Selenium driver wrapper (`institutional.py`), a service layer (`acquire.py`), a queue (`requests_queue.py`), and Click wiring (`commands.py`). The no-DB guarantees for `request`/`login` are correctly enforced, and atomic writes + read-only DB modes are handled thoughtfully. My biggest concerns are (1) `drain_requests` violates its own "queue is always rewritten" invariant if `acquire_fn` raises, and (2) the JSONL queue has a read-modify-write race that can silently drop entries when multiple machines append/drain concurrently. I also flag two things I cannot verify without the repository/`doi_slug_filename` source: DOI case normalization consistency and filename sanitization.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|---------|
| 1 | Correctness | WARN | `drain_requests` not rewrite-safe on exception; downloaded-but-mismatch dropped from queue; DOI case consistency unverified |
| 2 | Structure | OK | Clear layering; service layer free of Click; no dead code |
| 3 | Reproducibility | OK | Paths via `settings`; no hardcoded seeds needed; deps assumed declared in pyproject |
| 4 | Error handling | WARN | `drain_requests` lets exceptions skip `write_requests`; `quit()` silently swallows; `read_requests` no per-line resilience |
| 5 | Comments | OK | Good "why" comments (e.g., dotfile filtering, EZproxy hostname mangling) |
| 6 | Style | OK | Consistent naming, type hints on service layer, Click conventions in CLI |
| 7 | Performance | WARN | `acquire_one` fetches all pending PDFs then filters one DOI; browser launched per DOI in `drain_requests` |
| 8 | Security | WARN | `doi_slug_filename` sanitization unverified (DOIs contain `/`); no hardcoded secrets; truncation on error strings good |

## Issues

1. **[MAJOR] `drain_requests` does not guarantee the queue is rewritten if `acquire_fn` raises.**
   `acquire.py` drain_requests loop. The docstring states "The queue is always rewritten atomically via write_requests," but `write_requests(remaining)` is only reached after the loop completes normally. `acquire_one` → `fetch_crossref_article` explicitly propagates non-404 HTTP errors (e.g., a CrossRef 500/timeout), and any repo exception also propagates. On such a raise, the queue file is left as-is *and* all in-memory `remaining`/counts are lost — but more importantly, the invariant is violated and the current entry gets no attempts bump, so a transient network error can stall the queue indefinitely on retry.
   **What would change my mind:** Wrap the per-entry call in `try/except` (record a generic error status and bump attempts), or wrap the whole loop body so `write_requests(remaining)` runs in a `finally`. The `finally` approach is the minimal fix:
   ```python
   try:
       for i, entry in enumerate(entries):
           ...
   finally:
       requests_queue.write_requests(remaining)
   ```

2. **[MAJOR] `requests_queue` is not safe for concurrent multi-machine writers.**
   `requests_queue.py` `append_request` does read-all → dedup → write-all, and `drain_requests` does read-all → process → write-all. Both use atomic `os.replace`, so the file is never torn, but two writers can still lose updates: if machine A appends while the runner is draining, the runner's `write_requests(remaining)` overwrites A's new entry (it read a stale snapshot). The design explicitly invites "any machine (including read-only ones)" to queue, so this race is realistic on Dropbox.
   **What would change my mind:** Either (a) document the limitation and require append-only via true atomic append (`open(path, "a")` + `flock`/`os.O_APPEND`) with dedup handled at drain time, or (b) use a file lock (`fcntl`/`msvcrt`) around read-modify-write. Pure append with drain-time dedup is the cheaper fix and matches JSONL semantics.

3. **[MAJOR, needs confirmation] DOI case normalization consistency across the repo.**
   `acquire_one` does `doi = doi.strip().lower()` and then `repo.get_article_by_doi(doi)` / `repo.insert_article(**fetched)` (where `fetched["doi"]` is the lowercased DOI). `append_request` also lowercases. But `try_sources_for_article` and `run_institutional_batch` use `article["doi"]` straight from `row.to_dict()` (DataFrame from the repo) and from `resolve_fallbacks`. If the repo or the `collect` path ever stores/returns a DOI with non-lowercase characters, then `acquire_one`'s lookup misses an existing article and re-fetches/re-inserts, or `already_checked` `(doi, name)` set membership disagrees between the lowercased `acquire_one` path and the as-stored `resolve_fallbacks`/`institutional` paths.
   **What would change my mind:** Confirm that (a) `collect`/CrossRef ingestion lowercases DOIs on insert, (b) `repo.get_article_by_doi` compares case-insensitively, and (c) `doi_slug_filename` is invariant to case. If the schema enforces lowercase via a CHECK or the repository normalizes on read/write, this is fine.

4. **[MAJOR, needs confirmation / security] `doi_slug_filename` sanitization.**
   `institutional.py` and `acquire.py` build paths via `pdf_dir / doi_slug_filename(doi)` and `self.storage_dir / doi_slug_filename(doi)`. DOIs routinely contain `/` (e.g., `10.1016/j.audit.2024.1001`) and may contain other characters. If `doi_slug_filename` does not replace path separators / `..`, a malicious or malformed DOI could escape `pdf_dir`. I cannot see `http_pdf_downloader.doi_slug_filename`, so I'm flagging rather than guessing.
   **What would change my mind:** Confirm `doi_slug_filename` replaces `/`, `\`, `..`, spaces, and any shell-unsafe chars (e.g., `re.sub(r"[^A-Za-z0-9._-]", "_", doi)`), and that `download_pdf` refuses to write outside `pdf_dir`.

5. **[MAJOR] A "downloaded" paper that fails verification (mismatch/quarantined) is dropped from the request queue as if resolved.**
   `acquire_one` keeps `status="downloaded"` even when the verifier quarantines the PDF (it only nulls `path` and sets `verify_status="mismatch"`). `drain_requests` treats `status in ("already_have","downloaded")` as resolved and drops the entry. So a user-requested DOI that downloaded a *wrong* PDF is silently removed from the queue and never retried, even though the article no longer has a usable PDF (the verifier deletes the `pdf_files` row).
   **What would change my mind:** Either set `out["status"]` to a non-resolved value (e.g., `"verify_mismatch"`) when verification quarantines, or have `drain_requests` check `out["verify_status"]` and keep the entry when it's `"mismatch"`. The former is cleaner because `get_paper`'s `icon` mapping already falls back to `✗` for unknown statuses.

6. **[MINOR] `acquire_one` fetches the entire pending-verification set to filter one DOI.**
   `acquire.py`: `pending = repo.get_pdfs_pending_verification(); pending = pending[pending["doi"] == doi]`. On a large DB this pulls every pending row into a DataFrame just to locate one. Add a `repo.get_pdfs_pending_verification(doi=...)` filter or query with a WHERE clause.

7. **[MINOR] `drain_requests` launches (and tears down) a Chrome browser per queued DOI.**
   Each call to the default `acquire_one` builds and quits an `InstitutionalDownloader` when fallbacks fail. For a queue of N papers needing institutional access, that's N browser launches (~seconds each). Functionally correct, but a shared downloader across the drain loop would be far faster. Acceptable for small queues; worth a note.

8. **[MINOR] `read_requests` is not resilient to a single corrupt JSONL line.**
   `requests_queue.py`: `json.loads(line)` for every non-empty line. One bad line (e.g., Dropbox partial-sync artifact) raises and blocks both `append_request` and `drain_requests`. Consider skipping/forwarding corrupt lines with a warning, since the file is Dropbox-synced and the atomic-write guarantee only protects against torn full writes, not concurrent readers seeing a half-synced line.

9. **[MINOR] `InstitutionalDownloader.quit` silently swallows all exceptions.**
   `institutional.py`: `except Exception: pass`. Acceptable for cleanup, but a debug log would help when `undetected_chromedriver` leaves zombie processes. Not blocking.

10. **[MINOR] `proxify_url` edge: `cur.netloc.split(".", 1)[1]` assumes a dot exists after the first segment.**
    `publisher_pdf.py`. Guarded upstream by `EZPROXY_DOMAIN_MARKER not in cur.netloc` returning early, and EZproxy hosts always have the marker with a dotted domain, so this is safe in practice. A defensive `if "." not in cur.netloc: return url` would make the intent explicit.

## Score
Start 100.
- `drain_requests` rewrite invariant violated on exception: -8
- `requests_queue` multi-writer lost-update race: -8
- Downloaded-but-mismatch dropped from queue: -7
- DOI case consistency unverified: -5 (flagged, not confirmed)
- `doi_slug_filename` sanitization unverified: -4 (flagged, not confirmed)
- acquire_one full-table filter performance: -2
- read_requests not line-resilient: -2
- browser-per-DOI in drain: -1
- silent quit() / proxify_url edge: -1

**Final score: 62**
