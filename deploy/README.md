# Runner deployment

The pipeline runs unattended on the dedicated runner machine (currently the
user's M2 Mac; always awake, user logged in). SSRN's Cloudflare protection
requires a **visible** SeleniumBase UC Chrome window, so browser stages run as a
LaunchAgent inside the GUI session, never as a LaunchDaemon, and the screen must
stay unlocked. The SSRN collectors use `uc_open_with_reconnect()` and are
live-verified on Chrome 151.

## Provisioning checklist

1. Install: Google Chrome, Dropbox (sign in, wait until
   `~/Dropbox/Github Data/cite-hustle/` is fully synced), Homebrew, Poetry.
2. Clone both repos:
   ```bash
   git clone <cite-hustle remote> ~/Github/cite-hustle
   git clone <dot-files remote>   ~/Github/dot-files
   cd ~/Github/cite-hustle && poetry install
   cd ~/Github/dot-files/claude/skills/process-paper && poetry install
   ```
3. Keep the machine awake and the session unlocked (this was the failure mode
   when downloads were scheduled on a locked screen):
   ```bash
   sudo pmset -a sleep 0 displaysleep 10
   ```
   System Settings → Lock Screen → require password: **Never**.
4. Run the installer:
   ```bash
   cd ~/Github/cite-hustle && ./deploy/install.sh
   ```
5. Put the Ollama Cloud key in `~/.config/cite-hustle/env`.
6. Warm the docling model cache (first run downloads ~1 GB):
   ```bash
   poetry run cite-hustle wiki-ingest --limit 1
   ```
7. One-time EZproxy/ERNA login for institutional PDF downloads (needed before
   the `institutional` stage can run unattended):
   ```bash
   poetry run cite-hustle login
   ```
   Opens a visible Chrome window on a persistent profile; complete the ERNA
   login (incl. MFA) manually, then press Enter. The session cookie persists
   in the profile until it expires.

## Schedule

| Job | When | Profile |
|---|---|---|
| `com.citehustle.monthly` | 2nd of the month, 09:00 | requests → collect → scrape → enrich → download → fallbacks → institutional → verify → ingest → index → fts |
| `com.citehustle.weekly` | Mon + Thu, 20:00 | requests → scrape → download → fallbacks → institutional → verify → ingest → index → fts |

Manual trigger and logs:

```bash
launchctl kickstart gui/$UID/com.citehustle.weekly
tail -f ~/Library/Logs/cite-hustle/weekly.log
```

Run reports (per-stage outcomes, quarantined PDFs, flagged wiki pages) are
written to `~/Dropbox/Github Data/cite-hustle/reports/` and sync to every
machine.

The institutional stage is live-verified for Wiley and OUP. It is **not** an
autonomous Elsevier/ScienceDirect route: a human-assisted UC diagnostic worked
after the user cleared **I am not a robot**, but the same persistent profile was
challenged again on the next fresh unattended run. The Elsevier API also needs
an issued API key; VPN entitlement alone is insufficient. No experimental
Elsevier route is retained. A focused cite-hustle-to-pdfgrabba export for
terminal residuals is planned but not implemented, so current schedules may log
retryable ScienceDirect institutional failures.

## Single-writer discipline (DuckDB on Dropbox)

**This machine is the only one that writes to the database.** Other
machines should stick to read-only commands (`status`, `dashboard`, `search`,
`sample`, `wiki-index`). While a pipeline run holds the write lock, read-only
commands on other machines will wait/fail with the standard lock message; the
schedule above tells you when runs happen.

The pipeline refuses to start when it detects:
- a Dropbox *conflicted copy* of the database (single-writer violation), or
- a leftover `articles.duckdb.wal` (crashed writer or another machine
  mid-write). If no other machine is writing, run
  `poetry run cite-hustle status` once on the machine that crashed so DuckDB
  recovers the WAL, then retry.

A concurrent second pipeline run is blocked by a local lockfile at
`~/.cache/cite-hustle/pipeline.lock`.

## Session expiry (EZproxy)

**Symptom:** the `institutional` stage (or `get`/`process-requests`) aborts
with `session_expired` in the run report (`~/Dropbox/Github Data/cite-hustle/reports/`).

**Fix:** run the login command headful on the runner and re-run:

```bash
poetry run cite-hustle login
```

The session cookie lives in the local Chrome profile
(`~/.cache/cite-hustle/chrome-profile` by default) and is not synced via
Dropbox, so this must be run on the runner machine itself.

Never start `login`, `institutional`, or another Chrome process against this
profile while it is already open; Chrome profiles are single-process resources.

## What runs where

| Concern | Machine |
|---|---|
| Scheduled pipeline (writes) | Runner (M2 machine) |
| Ad-hoc queries, wiki reading, deep-writer | Any machine (read-only) |
| Manual maintenance scripts | Runner (M2 machine), outside run windows |
