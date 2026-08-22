# cite-hustle session handoff

Updated: 2026-08-22 (institutional-acquisition session, Fable orchestrating)

## Done this session (all on `main`, verified)

- **Institutional acquisition shipped and live-tested**: `get <doi>` / `request <doi>` /
  `process-requests` / `institutional` / `login` commands; EZproxy resolver with persistent
  Chrome profile (`~/.cache/cite-hustle/chrome-profile`); `requests` + `institutional`
  pipeline stages; status-aware recheck (`error` rows retry after 2 days); `cite-hustle`
  skill in dot-files (synced + symlinked). 64+ tests passing. Spec/plan in
  `docs/superpowers/`; pi cross-model report in `quality_reports/`.
- **Live validation on the runner (M2)**: Wiley `10.1111/jofi.70055` and OUP
  `10.1093/rfs/hhag032` downloaded via EZproxy and verifier-`match`ed, fully unattended.
  SURFconext chooser is auto-clicked (visible `div.wayf__idp` with "erasmus universi";
  never "Erasmus MC"); Microsoft SSO silent (user chose "stay signed in" at `login`).
- **Institutional path migrated to plain Selenium** (Selenium Manager auto-matches
  chromedriver, Chrome auto-updates safe). undetected-chromedriver 3.5.5 is dead vs
  Chrome 136+ machine-wide.
- pypdf restored to the venv (was missing after the py3.11 rebuild; had silently blocked
  ALL PDF verification -> ~1050-row pending backlog).
- seleniumbase added (pytest bumped to 9.x for it); UC mode probe-verified to launch on
  Chrome 151. Use `uc_open_with_reconnect(url)`, NOT `.get()` (hits "no such window").

## Open items

1. **SSRN scrape/download is broken machine-wide** (uc cannot start Chrome 151): the next
   scheduled pipeline's scrape/download stages will fail. Migrate
   `collectors/ssrn_scraper.py` + `collectors/selenium_pdf_downloader.py` to SeleniumBase
   UC mode. Cloudflare behavior is pinned by `tests/test_ssrn_blocking.py` (must stay
   green). See CLAUDE.md Troubleshooting rows added this session.
2. **Elsevier/ScienceDirect blocked**: `cra_js_challenge` defeats plain Selenium (incl.
   stealth flags) and SB-UC (which also triggers a fresh Microsoft sign-in as a "new
   device"). Fix candidates: dedicated SB-UC login round, or Elsevier API + eduVPN.
   Failed candidates auto-retry after `error_recheck_days` (2).
3. **Verification backlog**: run `poetry run cite-hustle verify-pdfs` (overnight ok) to
   drain ~1050 pending rows; needs `OLLAMA_API_KEY` for gray-zone cases.
4. `git push` pending (main is far ahead of origin; secrets scan came back clean).
5. Human-verified except: `request` from a second machine -> `process-requests` drain
   (queue roundtrip untested cross-machine, unit-tested only).

## Key facts the next session should not re-derive

- Runner = the M2 machine; single-writer discipline; other machines read-only.
- EZproxy prefix `https://eur.idm.oclc.org/login?url=`; EUR LibKey ID 2163 (deferred).
- `login` session lives in the local Chrome profile, never Dropbox; on `session_expired`
  rerun `cite-hustle login`; "browser died" abort = Chrome/driver trouble, not auth.
- docling remains the wiki-ingestion driver (process-paper skill); pypdf is only the
  verifier's cheap text sniff.
