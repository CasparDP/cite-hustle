#!/bin/zsh
# Pipeline wrapper for launchd. Usage: run_pipeline.sh {monthly|incremental}
#
# launchd starts with a minimal environment, so PATH and secrets are set here.
# caffeinate keeps the machine awake for the duration of the run.

set -euo pipefail

PROFILE="${1:-incremental}"
REPO_DIR="$HOME/Github/cite-hustle"
ENV_FILE="$HOME/.config/cite-hustle/env"
LOG_DIR="$HOME/Library/Logs/cite-hustle"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# Secrets and overrides (OLLAMA_API_KEY, CITE_HUSTLE_CROSSREF_EMAIL, ...)
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${OLLAMA_API_KEY:-}" ]]; then
  echo "WARNING: OLLAMA_API_KEY not set; LLM verification and wiki ingestion will be skipped/fail" >&2
fi

# Trim logs older than 60 days
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "*.log" -mtime +60 -delete 2>/dev/null || true

cd "$REPO_DIR"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') pipeline start (profile: $PROFILE) ==="
exec caffeinate -i poetry run cite-hustle pipeline --profile "$PROFILE"
