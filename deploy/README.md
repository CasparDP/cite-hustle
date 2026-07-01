# Runner laptop deployment

The pipeline runs unattended on a dedicated Mac laptop (always awake, user
logged in). SSRN's Cloudflare protection requires a **visible** Chrome window,
so everything runs as a LaunchAgent inside the GUI session, never as a
LaunchDaemon, and the screen must stay unlocked.

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

## Schedule

| Job | When | Profile |
|---|---|---|
| `com.citehustle.monthly` | 2nd of the month, 09:00 | collect → scrape → enrich → download → fallbacks → verify → ingest → index → fts |
| `com.citehustle.weekly` | Mon + Thu, 20:00 | scrape → download → fallbacks → verify → ingest → index → fts |

Manual trigger and logs:

```bash
launchctl kickstart gui/$UID/com.citehustle.weekly
tail -f ~/Library/Logs/cite-hustle/weekly.log
```

Run reports (per-stage outcomes, quarantined PDFs, flagged wiki pages) are
written to `~/Dropbox/Github Data/cite-hustle/reports/` and sync to every
machine.

## Single-writer discipline (DuckDB on Dropbox)

**This laptop is the only machine that writes to the database.** Other
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

## What runs where

| Concern | Machine |
|---|---|
| Scheduled pipeline (writes) | Runner laptop |
| Ad-hoc queries, wiki reading, deep-writer | Any machine (read-only) |
| Manual maintenance scripts | Runner laptop, outside run windows |
