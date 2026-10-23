#!/bin/zsh
# Provision a wiki worker Mac: checks prerequisites, installs the wiki-work LaunchAgent.
# The worker never opens the database; no Chrome or EZproxy login needed.
# Run from the repo root: ./deploy/install_worker.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROCESS_PAPER_DIR="$HOME/Github/dot-files/claude/skills/process-paper"
DATA_DIR="$HOME/Dropbox/Github Data/cite-hustle"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/cite-hustle"
PLIST=com.citehustle.wikiwork

echo "── cite-hustle wiki worker setup ─────────────────────────"

fail=0
check() {
  if eval "$2" >/dev/null 2>&1; then
    echo "  ✓ $1"
  else
    echo "  ✗ $1  ($3)"
    fail=1
  fi
}

check "poetry installed"       "command -v poetry"                         "brew install poetry"
check "Dropbox running"        "pgrep -x Dropbox"                          "install and sign in to Dropbox first"
check "pdfs folder synced"     "[ -d \"$DATA_DIR/pdfs\" ]"                 "include Github Data/cite-hustle/pdfs in Dropbox sync"
check "wiki folder synced"     "[ -d \"$DATA_DIR/wiki\" ]"                 "include Github Data/cite-hustle/wiki in Dropbox sync"
check "cite-hustle venv"       "cd '$REPO_DIR' && poetry env info -p"      "run: poetry install"
check "process-paper venv"     "cd '$PROCESS_PAPER_DIR' && poetry env info -p" "cd $PROCESS_PAPER_DIR && poetry install"
check "OLLAMA_API_KEY set"     "[ -n \"\${OLLAMA_API_KEY:-}\" ]"            "export it in ~/.zshenv.local or ~/.config/cite-hustle/env"
if [[ -n "${PROCESS_PAPER_DOCLING_URL:-}" ]]; then
  check "docling-serve reachable" "curl -sf '$PROCESS_PAPER_DOCLING_URL/health'" "start the docling container"
fi

if [[ $fail -ne 0 ]]; then
  echo "\nFix the items above, then re-run this script."
  exit 1
fi

mkdir -p "$LOG_DIR"
sed "s/__USER__/$USER/g" "$REPO_DIR/deploy/$PLIST.plist" > "$AGENT_DIR/$PLIST.plist"
launchctl bootout "gui/$(id -u)/$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_DIR/$PLIST.plist"
echo "  ✓ LaunchAgent installed: $PLIST (runs now, at every login, and daily at 03:00)"

cat <<'EOF2'

── Done ───────────────────────────────────────────────────
Follow progress:  tail -f ~/Library/Logs/cite-hustle/wikiwork.log
Run again now:    launchctl kickstart gui/$UID/com.citehustle.wikiwork
EOF2
