#!/bin/zsh
# Wiki worker wrapper for launchd: process-paper over the runner's handoff manifest.
# Never opens the database. See README.md "Wiki worker".

set -euo pipefail

REPO_DIR="$HOME/Github/cite-hustle"
ENV_FILE="$HOME/.config/cite-hustle/env"
LOG_DIR="$HOME/Library/Logs/cite-hustle"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "wikiwork*.log" -mtime +60 -delete 2>/dev/null || true

# After a reboot the docling-serve container needs a few minutes to come up;
# process-paper aborts at once if it is unreachable.
if [[ -n "${PROCESS_PAPER_DOCLING_URL:-}" ]]; then
  for _ in {1..30}; do
    curl -sf "$PROCESS_PAPER_DOCLING_URL/health" >/dev/null && break
    sleep 20
  done
fi

cd "$REPO_DIR"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') wiki-work start ==="
exec caffeinate -i poetry run cite-hustle wiki-work
