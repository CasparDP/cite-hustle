# Runner deployment

One dedicated runner is the **only machine that writes the database**; every other
machine stays read-only. Current runner (2026-10): the Ubuntu desktop VM
(`ubuntu-vm`, GPU), which expires in a few months. Nothing is VM-specific: all state
(DB, PDFs, wiki, CrossRef cache, reports) lives in Dropbox, and a runner holds only
the repo checkout, the Poetry venvs, `.env`, and the local EZproxy Chrome profile.

SSRN's Cloudflare protection requires a **visible** SeleniumBase UC Chrome window on
an unlocked desktop, so browser stages run inside the GUI session, never over SSH or
as a system service.

## Linux runner (Ubuntu desktop)

1. Install Google Chrome (the `.deb` from google.com/chrome; not the chromium snap),
   Dropbox (wait until `~/Dropbox/Github Data/cite-hustle/` is fully synced), git,
   make, and Poetry (`pipx install poetry`).
2. Clone both repos and install:
   ```bash
   git clone <cite-hustle remote> ~/Github/cite-hustle
   git clone <dot-files remote>   ~/Github/dot-files
   cd ~/Github/cite-hustle && poetry install
   cd ~/Github/dot-files/claude/skills/process-paper && poetry install
   ```
3. Configuration: copy `.env.example` to `.env` and set at least
   `CITE_HUSTLE_CROSSREF_EMAIL` (CrossRef answers 429 without it); optionally
   `CITE_HUSTLE_OPENALEX_API_KEY` and `CITE_HUSTLE_S2_API_KEY`. Export
   `OLLAMA_API_KEY` in `~/.profile`.
4. Keep the desktop unlocked and the display on:
   ```bash
   gsettings set org.gnome.desktop.session idle-delay 0
   gsettings set org.gnome.desktop.screensaver lock-enabled false
   ```
5. From a terminal **inside the desktop session**:
   ```bash
   make doctor       # must end with "Ready."
   make smoke-ssrn   # visible Cloudflare test on a throwaway DB; check PDFs appear
   ```
6. Regular runs (all resumable; rerun until nothing is pending):
   ```bash
   make update && make abstracts      # new issues + abstracts (no browser)
   make pdfs                          # slow SSRN batch + free fallbacks (visible browser)
   make process                       # verify PDFs, wiki ingestion, indexes
   ```
   Pace SSRN with `BATCH`, `SCRAPE_DELAY`, `DOWNLOAD_DELAY`. `scrape` exits non-zero
   on a suspected Cloudflare block, which stops `make pdfs` before the download step;
   wait (hours) before retrying.

The launchd schedule below is macOS-only; no Linux scheduler is set up yet.

## Moving to a new runner

1. Stop all runs on the old runner and let Dropbox finish syncing (no
   `articles.duckdb.wal`, no conflicted copies).
2. Provision the new machine with the checklist for its OS, then `make doctor`.
3. Run `cite-hustle login` once if the institutional stage is needed (the EZproxy
   session lives in the local Chrome profile, never in Dropbox).
4. Record the new runner here and in `CLAUDE.md`.

## macOS runner

UC mode on Apple Silicon needs Rosetta 2 (`softwareupdate --install-rosetta`):
SeleniumBase fetches an x86 `uc_driver`.

### Provisioning checklist (macOS)

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
Elsevier route is retained. Terminal Elsevier residuals go to pdfgrabba via the manual
`export-pdfgrabba` / `import-pdfgrabba` commands (not scheduled), so current
schedules may still log retryable ScienceDirect institutional failures.

## Single-writer discipline (DuckDB on Dropbox)

**The runner is the only machine that writes to the database.** Other
machines should stick to read-only commands (`status`, `dashboard`, `journals`,
`search`, `sample`, `wiki-index`, `export-pdfgrabba`). While a pipeline run holds the write lock, read-only
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
| All writes (collect, scrape, download, abstracts, verify, wiki) | Runner (Ubuntu VM) |
| Ad-hoc queries, wiki reading, deep-writer | Any machine (read-only) |
| Manual maintenance scripts | Runner, outside run windows |
