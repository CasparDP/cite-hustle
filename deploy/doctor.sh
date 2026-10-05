#!/usr/bin/env bash
# Readiness check for a cite-hustle runner (macOS or Linux). Read-only: opens
# nothing for writing. Usage: make doctor   (or bash deploy/doctor.sh)

cd "$(dirname "$0")/.." || exit 1
fails=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fails=$((fails + 1)); }

echo "cite-hustle doctor ($(uname -s), $(hostname))"

# Python environment in sync with poetry.lock (a stale venv lacks seleniumbase)
if poetry run python -c "import seleniumbase, duckdb, httpx, cite_hustle" 2>/dev/null; then
  ok "poetry env imports seleniumbase/duckdb/httpx"
else
  fail "poetry env incomplete: run 'poetry install'"
fi

# Chrome for the visible SeleniumBase UC browser
if [[ "$(uname -s)" == Darwin ]]; then
  [[ -d "/Applications/Google Chrome.app" ]] && ok "Google Chrome installed" \
    || fail "Google Chrome missing"
else
  if chrome=$(command -v google-chrome || command -v google-chrome-stable); then
    ok "$("$chrome" --version 2>/dev/null)"
  else
    fail "google-chrome missing (install the .deb from google.com/chrome; chromium snap is not supported)"
  fi
  # SSRN downloads need a real, unlocked desktop display; SSH sessions have none
  if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    ok "display available (${DISPLAY:-$WAYLAND_DISPLAY})"
  else
    fail "no DISPLAY: run browser stages from a terminal inside the desktop session"
  fi
fi

# Data root and DB state
read -r base email s2 oa ppdir < <(poetry run python -c "
from cite_hustle.config import settings as s
print(s.dropbox_base.as_posix().replace(' ', '%20'), bool(s.crossref_email),
      bool(s.s2_api_key), bool(s.openalex_api_key), s.process_paper_dir.as_posix().replace(' ', '%20'))
" 2>/dev/null)
base=${base//%20/ }; ppdir=${ppdir//%20/ }
[[ -z "$base" ]] && fail "could not load cite_hustle settings (check .env and 'poetry install')"
if [[ -f "$base/DB/articles.duckdb" ]]; then
  ok "database at $base/DB/articles.duckdb"
else
  fail "no database under '$base' (Dropbox not synced, or set CITE_HUSTLE_DROPBOX_BASE)"
fi
compgen -G "$base/DB/*conflicted copy*" >/dev/null && fail "Dropbox conflicted copy of the DB exists"
[[ -f "$base/DB/articles.duckdb.wal" ]] && warn "leftover articles.duckdb.wal (crashed run: make recover-db)"
pgrep -if dropbox >/dev/null && ok "Dropbox running" || warn "Dropbox not running"

# API identities and keys
[[ "$email" == True ]] && ok "CITE_HUSTLE_CROSSREF_EMAIL set" \
  || fail "CITE_HUSTLE_CROSSREF_EMAIL unset (CrossRef 429s without the polite pool)"
[[ "$oa" == True ]] && ok "OpenAlex API key set" || warn "CITE_HUSTLE_OPENALEX_API_KEY unset (shared per-IP budget)"
[[ "$s2" == True ]] && ok "Semantic Scholar API key set" || warn "CITE_HUSTLE_S2_API_KEY unset (slow shared pool)"
[[ -n "${OLLAMA_API_KEY:-}" ]] && ok "OLLAMA_API_KEY set" \
  || warn "OLLAMA_API_KEY unset: verify-pdfs skips the LLM step, wiki-ingest fails"

# Wiki ingestion (process-paper skill in its own poetry env)
if [[ -d "$ppdir" ]]; then
  (cd "$ppdir" && poetry run process-paper --help >/dev/null 2>&1) \
    && ok "process-paper runnable" || fail "process-paper env broken: cd '$ppdir' && poetry install"
else
  warn "process-paper not found at '$ppdir' (needed for 'make process')"
fi

command -v nvidia-smi >/dev/null && ok "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"

echo
[[ $fails -eq 0 ]] && echo "Ready." || echo "$fails blocking problem(s)."
exit $((fails > 0))
