#!/usr/bin/env bash
# Merge a leftover articles.duckdb.wal after a killed run (runner only).
# Backs up DB + WAL first, then opens read-write and checkpoints, which replays
# the committed writes into the main file and removes the WAL.
# Usage: make recover-db [BACKUP_DIR=...]   (default: .db-backups/ in the repo)

set -euo pipefail
cd "$(dirname "$0")/.."

backup_root="${BACKUP_DIR:-$PWD/.db-backups}"
keep=3

db=$(poetry run python -c "from cite_hustle.config import settings; print(settings.db_path)")
wal="$db.wal"

if [[ ! -f "$wal" ]]; then
  echo "No WAL next to $db; nothing to recover."
  exit 0
fi
if pgrep -f "bin/cite-hustle" >/dev/null; then
  echo "A cite-hustle process is still running; stop it first:" >&2
  pgrep -af "bin/cite-hustle" >&2
  exit 1
fi
if compgen -G "$(dirname "$db")/*conflicted copy*" >/dev/null; then
  echo "Dropbox conflicted copy present; resolve that by hand first." >&2
  exit 1
fi

backup="$backup_root/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp -p "$db" "$wal" "$backup/"
echo "Backup: $backup ($(du -sh "$backup" | cut -f1))"

poetry run python -c "
import duckdb, sys
con = duckdb.connect(sys.argv[1])
con.execute('CHECKPOINT')
print('articles:', con.execute('SELECT COUNT(*) FROM articles').fetchone()[0])
con.close()
" "$db"

if [[ -f "$wal" ]]; then
  echo "WAL still present after checkpoint; inspect before running anything else." >&2
  exit 1
fi
echo "Recovered: WAL merged into $db"

# Keep only the newest backups (each is a full DB copy)
ls -1d "$backup_root"/*/ 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r old; do
  rm -rf "$old"
done
