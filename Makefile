YEAR ?= $(shell date +%Y)
RUN   := poetry run cite-hustle

# SSRN runs in a visible browser and must stay slow to avoid Cloudflare blocks.
# Override per run, e.g. make pdfs BATCH=200 DOWNLOAD_DELAY=60
BATCH          ?= 50
SCRAPE_DELAY   ?= 30
DOWNLOAD_DELAY ?= 45
INGEST_BATCH   ?= 10

# Keep the machine awake during long runs (macOS: caffeinate; Linux: systemd-inhibit)
ifeq ($(shell uname -s),Darwin)
KEEP_AWAKE := caffeinate -i
else
KEEP_AWAKE := $(shell command -v systemd-inhibit >/dev/null && echo systemd-inhibit --what=idle:sleep --who=cite-hustle --why=downloads)
endif

# ── Quick status ──────────────────────────────────────────────────────────────

.PHONY: status dashboard journals

status:
	$(RUN) status

dashboard:
	$(RUN) dashboard

journals:
	$(RUN) journals

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: init rebuild-fts doctor smoke-ssrn

init:
	$(RUN) init

# Read-only readiness check (env, Chrome, display, Dropbox, keys, process-paper)
doctor:
	@bash deploy/doctor.sh

# Cloudflare smoke test against a throwaway DB (never touches the real one):
# collects current-year accounting papers, then scrapes + downloads a few, visibly.
SMOKE_N ?= 5
smoke-ssrn:
	@base=$$(mktemp -d); echo "Throwaway data root: $$base"; \
	export CITE_HUSTLE_DROPBOX_BASE=$$base; \
	$(RUN) init >/dev/null && \
	$(RUN) collect --field accounting --year-start $(YEAR) --year-end $(YEAR) --skip-fts-rebuild >/dev/null && \
	$(KEEP_AWAKE) $(RUN) scrape --limit $(SMOKE_N) --delay $(SCRAPE_DELAY) && \
	$(KEEP_AWAKE) $(RUN) download --limit $(SMOKE_N) --delay $(DOWNLOAD_DELAY) && \
	ls -l "$$base/pdfs"

rebuild-fts:
	$(RUN) rebuild-fts

# ── Data pipeline (individual steps) ─────────────────────────────────────────

.PHONY: collect scrape enrich enrich-year abstracts download fallbacks pdfs verify wiki process wiki-index

collect:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR)

# Visible SeleniumBase browser; resumable, so rerun until nothing is pending
scrape:
	$(KEEP_AWAKE) $(RUN) scrape --limit $(BATCH) --delay $(SCRAPE_DELAY)

enrich:
	$(RUN) enrich-openalex --concurrency 8

enrich-year:
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR) --concurrency 8

# All abstract sources, cheapest first; each fills only missing abstracts
abstracts:
	$(RUN) enrich-abstracts --source crossref --skip-fts-rebuild
	$(RUN) enrich-openalex --concurrency 3 --skip-fts-rebuild
	$(KEEP_AWAKE) $(RUN) enrich-abstracts --source s2 --source nber

download:
	$(KEEP_AWAKE) $(RUN) download --limit $(BATCH) --delay $(DOWNLOAD_DELAY)

fallbacks:
	$(RUN) resolve-fallbacks

# One slow SSRN batch (search, then PDF), then the free OA/NBER/arXiv fallbacks
pdfs: scrape download fallbacks

verify:
	$(RUN) verify-pdfs

wiki:
	$(RUN) wiki-ingest --limit $(INGEST_BATCH)

# Downloaded PDFs -> verified -> wiki (needs OLLAMA_API_KEY and process-paper)
process: verify wiki wiki-index

wiki-index:
	$(RUN) wiki-index

# ── Unattended pipeline (used by launchd on the runner laptop) ───────────────

.PHONY: pipeline pipeline-monthly

pipeline:
	$(RUN) pipeline --profile incremental

pipeline-monthly:
	$(RUN) pipeline --profile monthly

# ── Update (main workflow) ────────────────────────────────────────────────────
# make update           → collect for current year (fast, no browser; stores CrossRef abstracts)
# make update YEAR=2024 → same for a specific year
# make update-full      → collect + scrape + enrich (includes Selenium SSRN scrape)

.PHONY: update update-full

update:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR) --force

update-full:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR) --force
	$(KEEP_AWAKE) $(RUN) scrape --limit $(BATCH) --delay $(SCRAPE_DELAY)
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR) --concurrency 8

# ── Maintenance ───────────────────────────────────────────────────────────────

.PHONY: reset-failed search

reset-failed:
	poetry run python scripts/reset_failed_scrapes.py

search:
	$(RUN) search "$(Q)"
