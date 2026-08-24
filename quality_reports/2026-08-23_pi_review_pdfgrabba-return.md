# Code Review

**Overall:** MINOR ISSUES

## Summary

The pdfgrabba export/import implementation is well-structured, thoroughly tested,
and follows good security practices: path-traversal prevention, atomic manifest
writes, insert-only DB semantics, and true dry-run behavior. The biggest concerns
are a conflated `unknown_doi` counter that obscures two distinct failure modes, and
an aggressive fail-fast policy where a single unsafe `target_filename` aborts the
entire import. Neither affects correctness of the core import path.

## Category results

| # | Category | Result | Findings |
|---|---|---|---|
| 1 | Correctness | OK | Insert-only logic via `ON CONFLICT DO NOTHING RETURNING doi` is correct. Normalized DOI matching between Python and DuckDB regex is consistent. Limit selection is deterministic. |
| 2 | Structure | OK | Clear separation among export, import, and repository layers. |
| 3 | Reproducibility | OK | No hardcoded paths; deterministic ordering and portable stored paths. |
| 4 | Error handling | WARN | `unknown_doi` conflates empty and unresolved DOI. Unsafe target filenames fail the whole batch. |
| 5 | Comments | OK | Non-obvious contracts and normalization are documented. |
| 6 | Style | WARN | Legacy repository typing and modern union syntax differ; `terminal` naming is mildly ambiguous. |
| 7 | Performance | OK | Per-entry queries are acceptable for small in-process DuckDB manifests. |
| 8 | Security | OK | Traversal-shaped paths are rejected; atomic writes and no hardcoded secrets. |

## Issues

1. **MINOR — `unknown_doi` conflates two failure modes.** Split missing DOI from
   DOI-not-in-articles so the operator knows whether to fix the manifest or metadata.
2. **MINOR — Unsafe `target_filename` aborts the entire import.** This is defensible
   as fail-fast handling of manifest corruption, though less forgiving than skipping.
3. **MINOR — No file-level manifest lock.** Atomic replacement avoids corruption but
   cannot prevent lost concurrent updates; the documented sequential workflow is an
   acceptable operational control.
4. **MINOR — Bib-key reservation relies implicitly on `make_bib_key` mutating the
   passed set.** Make the reservation explicit for robustness.
5. **MINOR — Typing style differs between legacy and new modules.** No behavior impact.

## Score

90/100.

## Triage

- Fixed issue 1 by reporting `empty_doi` and `unresolved_doi` separately.
- Fixed issue 4 by explicitly adding the returned key to the reserved set.
- Retained issue 2 intentionally: unsafe paths indicate corrupt/tampered manifest
  state, and the importer guarantees validation of the whole batch before writing.
- Retained the documented single-operator/concurrency warning for issue 3.
- Issue 5 is pre-existing project style and does not justify a broad refactor.
