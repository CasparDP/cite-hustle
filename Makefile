YEAR ?= $(shell date +%Y)
RUN   := poetry run cite-hustle

# ── Quick status ──────────────────────────────────────────────────────────────

.PHONY: status dashboard journals

status:
	$(RUN) status

dashboard:
	$(RUN) dashboard

journals:
	$(RUN) journals

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: init rebuild-fts

init:
	$(RUN) init

rebuild-fts:
	$(RUN) rebuild-fts

# ── Data pipeline (individual steps) ─────────────────────────────────────────

.PHONY: collect scrape enrich enrich-year download fallbacks verify wiki wiki-index

collect:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR)

scrape:
	$(RUN) scrape --delay 5

enrich:
	$(RUN) enrich-openalex --concurrency 8

enrich-year:
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR) --concurrency 8

download:
	$(RUN) download

fallbacks:
	$(RUN) resolve-fallbacks

verify:
	$(RUN) verify-pdfs

wiki:
	$(RUN) wiki-ingest

wiki-index:
	$(RUN) wiki-index

# ── Unattended pipeline (used by launchd on the runner laptop) ───────────────

.PHONY: pipeline pipeline-monthly

pipeline:
	$(RUN) pipeline --profile incremental

pipeline-monthly:
	$(RUN) pipeline --profile monthly

# ── Update (main workflow) ────────────────────────────────────────────────────
# make update           → collect + enrich for current year (fast, no browser)
# make update YEAR=2024 → same for a specific year
# make update-full      → collect + scrape + enrich (includes Selenium SSRN scrape)

.PHONY: update update-full

update:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR) --force

update-full:
	$(RUN) collect --field all --year-start $(YEAR) --year-end $(YEAR) --force
	$(RUN) scrape --delay 5
	$(RUN) enrich-openalex --year-start $(YEAR) --year-end $(YEAR) --concurrency 8

# ── Maintenance ───────────────────────────────────────────────────────────────

.PHONY: reset-failed search

reset-failed:
	poetry run python scripts/reset_failed_scrapes.py

search:
	$(RUN) search "$(Q)"
