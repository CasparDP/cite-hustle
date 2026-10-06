YEAR ?= $(shell date +%Y)
UPDATE_FROM := $(shell echo $$(($(YEAR) - 2)))
RUN   := poetry run cite-hustle

# SSRN runs in a visible browser and must stay slow to avoid Cloudflare blocks.
# Override per run, e.g. make pdfs BATCH=200 DOWNLOAD_DELAY=60
BATCH          ?= 50
SCRAPE_DELAY   ?= 30
DOWNLOAD_DELAY ?= 45
FALLBACK_BATCH ?= 200
INGEST_BATCH   ?= 10

# Keep the machine awake during long runs (macOS: caffeinate; Linux: systemd-inhibit)
ifeq ($(shell uname -s),Darwin)
KEEP_AWAKE := caffeinate -i
else
# Best effort: polkit often denies inhibitors in remote-desktop (VM) sessions
INHIBIT    := systemd-inhibit --what=idle:sleep --who=cite-hustle --why=downloads
KEEP_AWAKE := $(shell $(INHIBIT) true >/dev/null 2>&1 && echo $(INHIBIT))
endif

# ── Quick status ──────────────────────────────────────────────────────────────

.PHONY: status dashboard journals

status:
	$(RUN) status

# Terminal summary + self-contained HTML (reports/dashboard.html), opened in the browser
dashboard:
	$(RUN) dashboard --open

journals:
	$(RUN) journals

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: init rebuild-fts doctor smoke-ssrn recover-db

init:
	$(RUN) init

# Read-only readiness check (env, Chrome, display, Dropbox, keys, process-paper)
doctor:
	@bash deploy/doctor.sh

# After a killed run: back up DB + WAL (to .db-backups/ or BACKUP_DIR), then merge the WAL
recover-db:
	@bash deploy/recover_db.sh

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

.PHONY: collect scrape enrich enrich-year abstracts download fallbacks pdfs verify wiki process wiki-index \
        repair-abstracts repair-abstracts-apply cross-check-abstracts cross-check-abstracts-apply

collect:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR)

# Visible SeleniumBase browser; resumable, so rerun until nothing is pending
scrape:
	$(KEEP_AWAKE) $(RUN) scrape --limit $(BATCH) --delay $(SCRAPE_DELAY)

enrich:
	$(RUN) enrich-openalex

enrich-year:
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR)

# All abstract sources, cheapest first; each fills only missing abstracts
abstracts:
	$(RUN) enrich-abstracts --source crossref --skip-fts-rebuild
	$(RUN) enrich-openalex --skip-fts-rebuild
	$(KEEP_AWAKE) $(RUN) enrich-abstracts --source s2 --source nber

download:
	$(KEEP_AWAKE) $(RUN) download --limit $(BATCH) --delay $(DOWNLOAD_DELAY)

fallbacks:
	$(RUN) resolve-fallbacks --limit $(FALLBACK_BATCH)

# One slow SSRN batch (search, then PDF), then the free OA/NBER/arXiv fallbacks
pdfs: scrape download fallbacks

verify:
	$(RUN) verify-pdfs
	$(RUN) verify-abstracts

# SSRN page text and publisher placeholders stored as abstracts: dry run, then apply
repair-abstracts:
	$(RUN) repair-abstracts

repair-abstracts-apply:
	$(RUN) repair-abstracts --apply --backup-dir $(CURDIR)/.db-backups

# SSRN/NBER abstracts vs a DOI-exact one (CrossRef cache, OpenAlex, S2); mismatches replaced
cross-check-abstracts:
	$(RUN) cross-check-abstracts

cross-check-abstracts-apply:
	$(RUN) cross-check-abstracts --apply --backup-dir $(CURDIR)/.db-backups

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
# make update           → collect the last three years (fast, no browser; stores CrossRef abstracts)
# make update YEAR=2024 → same for a specific year
# make update-full      → collect + scrape + enrich (includes Selenium SSRN scrape)

.PHONY: update update-full refresh-metadata cleanup-non-articles cleanup-non-articles-apply \
        merge-duplicates merge-duplicates-apply

# Three years: a paper is found under its online-first year (CrossRef's date filter) and
# moves to its print year once in an issue; online-to-print lags reach two years (JFQA)
update:
	$(RUN) collect --field all --year-start $(UPDATE_FROM) --year-end $(YEAR) --force

# Occasional full refresh: re-fetch every year so stored years follow CrossRef's print dates
REFRESH_START ?= 1980
refresh-metadata:
	$(RUN) collect --field all --year-start $(REFRESH_START) --year-end $(YEAR) --force

# Front matter (mastheads, reports, calls for papers): dry run, then apply (backs up first)
cleanup-non-articles:
	$(RUN) cleanup-non-articles

cleanup-non-articles-apply:
	$(RUN) cleanup-non-articles --apply --backup-dir $(CURDIR)/.db-backups

# Papers under two DOIs (JSTOR + publisher, old/new formats): dry run, then apply.
# Run after refresh-metadata: the rule reads start pages from the CrossRef cache.
merge-duplicates:
	$(RUN) merge-duplicates

merge-duplicates-apply:
	$(RUN) merge-duplicates --apply --backup-dir $(CURDIR)/.db-backups

update-full:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR) --force
	$(KEEP_AWAKE) $(RUN) scrape --limit $(BATCH) --delay $(SCRAPE_DELAY)
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR)

# ── Maintenance ───────────────────────────────────────────────────────────────

.PHONY: reset-failed search

reset-failed:
	poetry run python scripts/reset_failed_scrapes.py

search:
	$(RUN) search "$(Q)"
