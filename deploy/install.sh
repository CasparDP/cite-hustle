#!/bin/zsh
# Provision the runner laptop: checks prerequisites, installs the LaunchAgents.
# Run from the repo root: ./deploy/install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROCESS_PAPER_DIR="$HOME/Github/dot-files/claude/skills/process-paper"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/cite-hustle"
ENV_FILE="$HOME/.config/cite-hustle/env"

echo "── cite-hustle runner setup ──────────────────────────────"

fail=0
check() {
  if eval "$2" >/dev/null 2>&1; then
    echo "  ✓ $1"
  else
    echo "  ✗ $1  ($3)"
    fail=1
  fi
}

check "poetry installed"            "command -v poetry"                       "brew install poetry"
check "Google Chrome installed"     "[ -d '/Applications/Google Chrome.app' ]" "install Chrome (SSRN downloads need it)"
check "Dropbox running"             "pgrep -x Dropbox"                        "install and sign in to Dropbox first"
check "data folder synced"          "[ -d \"$HOME/Dropbox/Github Data/cite-hustle/DB\" ]" "wait for Dropbox to sync Github Data/cite-hustle"
check "cite-hustle venv"            "cd '$REPO_DIR' && poetry env info -p"    "run: poetry install"
check "process-paper checkout"      "[ -f '$PROCESS_PAPER_DIR/pyproject.toml' ]" "clone dot-files to ~/Github/dot-files"
check "process-paper venv"          "cd '$PROCESS_PAPER_DIR' && poetry env info -p" "cd $PROCESS_PAPER_DIR && poetry install"

if [[ $fail -ne 0 ]]; then
  echo "\nFix the items above, then re-run this script."
  exit 1
fi

# Env file template (holds secrets; never committed)
mkdir -p "$(dirname "$ENV_FILE")" "$LOG_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'EOF'
# Secrets for the cite-hustle pipeline (sourced by run_pipeline.sh)
OLLAMA_API_KEY=
CITE_HUSTLE_CROSSREF_EMAIL=
EOF
  chmod 600 "$ENV_FILE"
  echo "  ⚠ Created $ENV_FILE — fill in OLLAMA_API_KEY before the first run"
else
  grep -q "OLLAMA_API_KEY=." "$ENV_FILE" \
    && echo "  ✓ OLLAMA_API_KEY configured" \
    || echo "  ⚠ OLLAMA_API_KEY empty in $ENV_FILE"
fi

# Install LaunchAgents with the username substituted
for plist in com.citehustle.monthly com.citehustle.weekly; do
  sed "s/__USER__/$USER/g" "$REPO_DIR/deploy/$plist.plist" > "$AGENT_DIR/$plist.plist"
  launchctl bootout "gui/$(id -u)/$plist" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENT_DIR/$plist.plist"
  echo "  ✓ LaunchAgent installed: $plist"
done

# Smoke test
echo "\nRunning smoke test (cite-hustle status)..."
cd "$REPO_DIR" && poetry run cite-hustle status >/dev/null && echo "  ✓ Database reachable"

cat <<'EOF'

── Done. Next steps ───────────────────────────────────────
1. Fill in OLLAMA_API_KEY in ~/.config/cite-hustle/env (if not done).
2. Keep the machine awake and the session logged in:
     sudo pmset -a sleep 0 displaysleep 10
   System Settings → Lock Screen → never require password after screen saver
   (the SSRN download stage needs a visible Chrome window).
3. Warm the docling model cache once (downloads ~1 GB of models):
     poetry run cite-hustle wiki-ingest --limit 1
4. Trigger a run manually to watch it end-to-end:
     launchctl kickstart gui/$UID/com.citehustle.weekly
     tail -f ~/Library/Logs/cite-hustle/weekly.log
Run reports land in Dropbox: Github Data/cite-hustle/reports/
EOF
