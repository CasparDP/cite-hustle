# Institutional Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Get me this paper": per-DOI acquisition through an EZproxy institutional resolver, a request queue for non-runner machines, and pipeline integration, ending in a synced Claude skill.

**Architecture:** A new Selenium-based `InstitutionalDownloader` (persistent Chrome profile, one-time ERNA login via a `login` command) acquires publisher PDFs through EUR's EZproxy after the existing OA/NBER/arXiv fallbacks fail. A new `acquire.py` service module hosts the shared per-article source loop, the single-DOI `acquire_one` chain, and the institutional batch loop; CLI commands stay thin wrappers. A Dropbox-synced `requests.jsonl` queue lets read-only machines request papers that the runner drains as the first pipeline stage.

**Tech Stack:** Python 3.12, Click, DuckDB, httpx, undetected-chromedriver/Selenium, BeautifulSoup4+lxml, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-institutional-acquisition-design.md`

## Global Constraints

- Black + Ruff, line-length 100; Python 3.12; always `poetry run ...`.
- All DB I/O goes through `ArticleRepository` (`src/cite_hustle/database/repository.py`); never raw SQL elsewhere.
- Paths stored in the DB use the portable `$HOME/...` form; `upsert_pdf_file` / `update_pdf_info` already apply `to_portable()`, so callers pass plain absolute paths.
- No new dependencies. Everything needed (httpx, undetected-chromedriver, bs4, lxml, click, pytest) is already in `pyproject.toml`.
- PDF filenames come from `doi_slug_filename()` in `src/cite_hustle/collectors/http_pdf_downloader.py:16`.
- `pdf_candidates.status` values are exactly `downloaded | no_match | error`; do not invent new ones (failure detail goes in `error_message`).
- New read-only CLI commands must be added to `READ_ONLY_COMMANDS` (`src/cite_hustle/cli/commands.py:21`); commands that must not touch the DB at all go in the new `NO_DB_COMMANDS` set (Task 7).
- Run tests with `poetry run pytest tests/ -v`. Run `poetry run black src/ tests/ && poetry run ruff check src/ tests/` before each commit.
- The Chrome profile dir must stay on local disk (`~/.cache/cite-hustle/...`), never under Dropbox.
- Commit after every task with a conventional message; end commit messages with the Claude co-author trailer.

---

### Task 1: Test scaffolding (DuckDB repo fixture)

**Files:**
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: pytest fixture `repo` → `ArticleRepository` backed by a fresh tmp DuckDB with full schema; helper `add_article(repo, doi, title="Some Paper Title", authors="Alice Smith; Bob Jones", year=2024)`.

- [ ] **Step 1: Write the fixture (no test yet; later tasks consume it)**

```python
"""Shared fixtures: a fresh DuckDB-backed repository per test."""

import pytest

from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


@pytest.fixture()
def repo(tmp_path):
    db = DatabaseManager(tmp_path / "test.duckdb")
    db.connect()
    db.initialize_schema()
    yield ArticleRepository(db)
    db.close()


def add_article(
    repo,
    doi,
    title="Some Paper Title",
    authors="Alice Smith; Bob Jones",
    year=2024,
):
    repo.insert_article(doi, title, authors, year, "0000-0000", "Test Journal", "TestPub")
```

- [ ] **Step 2: Sanity-run existing suite**

Run: `poetry run pytest tests/ -v`
Expected: existing 11 tests PASS, no collection errors from conftest.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add DuckDB repository fixture"
```

---

### Task 2: `get_article_by_doi` repository method

**Files:**
- Modify: `src/cite_hustle/database/repository.py` (insert after `get_articles_by_year_range`, ~line 72)
- Test: `tests/test_repository.py` (create)

**Interfaces:**
- Produces: `ArticleRepository.get_article_by_doi(doi: str) -> Optional[Dict]` with keys `doi, title, authors, year, journal_issn, journal_name, publisher`.

- [ ] **Step 1: Write the failing test**

```python
"""Repository methods for per-DOI acquisition."""

from conftest import add_article


def test_get_article_by_doi_returns_row(repo):
    add_article(repo, "10.1111/test.1")
    row = repo.get_article_by_doi("10.1111/test.1")
    assert row["doi"] == "10.1111/test.1"
    assert row["title"] == "Some Paper Title"
    assert row["year"] == 2024


def test_get_article_by_doi_missing_returns_none(repo):
    assert repo.get_article_by_doi("10.9999/nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_repository.py -v`
Expected: FAIL with `AttributeError: 'ArticleRepository' object has no attribute 'get_article_by_doi'`

- [ ] **Step 3: Implement**

```python
    def get_article_by_doi(self, doi: str) -> Optional[Dict]:
        """Get one article's metadata by DOI."""
        result = self.conn.execute(
            """
            SELECT doi, title, authors, year, journal_issn, journal_name, publisher
            FROM articles WHERE doi = ?
        """,
            [doi],
        ).fetchone()
        if result:
            columns = ["doi", "title", "authors", "year", "journal_issn", "journal_name", "publisher"]
            return dict(zip(columns, result))
        return None
```

- [ ] **Step 4: Run test to verify it passes** — `poetry run pytest tests/test_repository.py -v` → PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: get_article_by_doi repository method"`

---

### Task 3: Status-aware recheck windows

An `error` candidate row (e.g. expired session, transient network) currently suppresses that `(doi, source)` pair for the full `--recheck-days` window (90 days), same as a genuine `no_match`. Split the cutoffs.

**Files:**
- Modify: `src/cite_hustle/database/repository.py:395-400` (`get_recent_candidate_checks`)
- Modify: `src/cite_hustle/config.py` (add `error_recheck_days: int = 2` next to `fallback_batch`)
- Modify: `src/cite_hustle/cli/commands.py:519-520` (the `resolve_fallbacks` caller)
- Test: `tests/test_repository.py`

**Interfaces:**
- Produces: `get_recent_candidate_checks(no_match_cutoff, error_cutoff) -> set[tuple[str, str]]` — both args are datetimes; `error`-status rows are only suppressed if checked since `error_cutoff`, all other statuses since `no_match_cutoff`.
- Produces: `settings.error_recheck_days` (default 2, env `CITE_HUSTLE_ERROR_RECHECK_DAYS`).

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timedelta

from conftest import add_article


def test_recheck_windows_distinguish_error_from_no_match(repo):
    add_article(repo, "10.1/a")
    add_article(repo, "10.1/b")
    repo.record_pdf_candidate("10.1/a", "oa", status="no_match")
    repo.record_pdf_candidate("10.1/b", "oa", status="error", error_message="http_503")

    long_cutoff = datetime.now() - timedelta(days=90)
    # error rows use a cutoff in the future -> nothing is "recent" for errors
    future = datetime.now() + timedelta(days=1)

    suppressed = repo.get_recent_candidate_checks(long_cutoff, future)
    assert ("10.1/a", "oa") in suppressed      # no_match still memoized
    assert ("10.1/b", "oa") not in suppressed  # error eligible for retry

    both_recent = repo.get_recent_candidate_checks(long_cutoff, long_cutoff)
    assert ("10.1/b", "oa") in both_recent
```

- [ ] **Step 2: Run** — `poetry run pytest tests/test_repository.py -v` → FAIL (`TypeError: get_recent_candidate_checks() takes 2 positional arguments but 3 were given`)

- [ ] **Step 3: Implement**

Replace the method:

```python
    def get_recent_candidate_checks(self, no_match_cutoff, error_cutoff) -> set:
        """(doi, source) pairs to skip: recent no_match/downloaded, or recent errors.

        Errors get their own (shorter) window so an auth-shaped failure does not
        suppress retries for the full recheck period.
        """
        rows = self.conn.execute(
            """
            SELECT doi, source FROM pdf_candidates
            WHERE (status = 'error' AND checked_at >= ?)
               OR (status != 'error' AND checked_at >= ?)
        """,
            [error_cutoff, no_match_cutoff],
        ).fetchall()
        return set(rows)
```

In `config.py`, under `fallback_batch`:

```python
    # Retry an 'error'-status candidate after this many days (no_match uses --recheck-days)
    error_recheck_days: int = 2
```

In `commands.py` `resolve_fallbacks` (lines 519-520), replace:

```python
    cutoff = datetime.now() - timedelta(days=recheck_days)
    error_cutoff = datetime.now() - timedelta(days=settings.error_recheck_days)
    already_checked = repo.get_recent_candidate_checks(cutoff, error_cutoff)
```

- [ ] **Step 4: Run full suite** — `poetry run pytest tests/ -v` → PASS

- [ ] **Step 5: Commit** — `git commit -am "feat: status-aware recheck windows for pdf_candidates"`

---

### Task 4: `get_articles_for_institutional` eligibility query

**Files:**
- Modify: `src/cite_hustle/database/repository.py` (insert after `get_articles_without_pdf`, ~line 393)
- Test: `tests/test_repository.py`

**Interfaces:**
- Produces: `get_articles_for_institutional(limit: Optional[int] = None) -> pd.DataFrame` with columns `doi, title, authors, year, journal_name`. Eligible = same predicate as `get_articles_without_pdf` (no `pdf_files` row; SSRN never matched or marked unavailable) AND at least one `pdf_candidates` row with `source IN ('oa','nber','arxiv')` (i.e. the fallback stage has already tried). Institutional runs last: most expensive, most rate-sensitive. (Spec said "all three sources tried"; requiring ≥1 attempt is the lean version — the fallback stage tries all sources per article in one pass anyway.)

- [ ] **Step 1: Write the failing test**

```python
def test_institutional_feeder_requires_fallbacks_tried(repo):
    add_article(repo, "10.1/tried")     # fallbacks tried, no PDF -> eligible
    add_article(repo, "10.1/untried")   # fallbacks never tried -> not eligible
    add_article(repo, "10.1/haspdf")    # already has a PDF -> not eligible
    repo.record_pdf_candidate("10.1/tried", "oa", status="no_match")
    repo.record_pdf_candidate("10.1/haspdf", "oa", status="downloaded")
    repo.upsert_pdf_file("10.1/haspdf", "oa", None, "http://x/y.pdf", "/tmp/y.pdf")

    dois = set(repo.get_articles_for_institutional()["doi"])
    assert dois == {"10.1/tried"}
```

- [ ] **Step 2: Run** — FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

```python
    def get_articles_for_institutional(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Articles eligible for the EZproxy institutional resolver.

        Same base predicate as get_articles_without_pdf, plus: the open-access
        fallback stage must already have tried (any oa/nber/arxiv candidate row),
        so the expensive browser path runs last.
        """
        query = """
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            LEFT JOIN pdf_files p ON a.doi = p.doi
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE p.doi IS NULL
              AND (
                  s.ssrn_url IS NULL
                  OR EXISTS (
                      SELECT 1 FROM processing_log pl
                      WHERE pl.doi = a.doi
                        AND pl.stage = 'download_pdf'
                        AND pl.status = 'unavailable'
                  )
              )
              AND EXISTS (
                  SELECT 1 FROM pdf_candidates c
                  WHERE c.doi = a.doi AND c.source IN ('oa', 'nber', 'arxiv')
              )
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query).fetchdf()
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git commit -am "feat: institutional-resolver feeder query"`

---

### Task 5: EZproxy URL + publisher PDF-link helpers (pure functions)

**Files:**
- Create: `src/cite_hustle/collectors/publisher_pdf.py`
- Test: `tests/test_publisher_pdf.py` (create)

**Interfaces:**
- Produces (all pure, no Selenium, no DB):
  - `build_ezproxy_url(prefix: str, doi: str) -> str`
  - `proxify_url(url: str, current_url: str) -> str` — rewrite an un-proxied absolute URL onto the EZproxy host scheme when the current page is proxied (`www.sciencedirect.com` → `www-sciencedirect-com.eur.idm.oclc.org`); pass through otherwise.
  - `extract_pdf_url(html: str, current_url: str) -> Optional[str]` — `citation_pdf_url` meta first, then anchor heuristics; result is urljoin'd against `current_url` and proxified.
  - `is_login_page(html: str, current_url: str) -> bool` — True when we are stuck on the EZproxy/SSO login instead of the target page.

- [ ] **Step 1: Write the failing tests**

```python
"""EZproxy URL handling and publisher PDF-link extraction (pure functions)."""

from cite_hustle.collectors.publisher_pdf import (
    build_ezproxy_url,
    extract_pdf_url,
    is_login_page,
    proxify_url,
)

PREFIX = "https://eur.idm.oclc.org/login?url="
PROXIED_PAGE = "https://www-sciencedirect-com.eur.idm.oclc.org/science/article/pii/S001"


def test_build_ezproxy_url():
    assert (
        build_ezproxy_url(PREFIX, "10.1016/j.jacceco.2024.1")
        == "https://eur.idm.oclc.org/login?url=https://doi.org/10.1016/j.jacceco.2024.1"
    )


def test_proxify_rewrites_unproxied_absolute_url():
    url = "https://www.sciencedirect.com/pdf/S001-main.pdf"
    assert (
        proxify_url(url, PROXIED_PAGE)
        == "https://www-sciencedirect-com.eur.idm.oclc.org/pdf/S001-main.pdf"
    )


def test_proxify_passthrough_when_not_proxied():
    url = "https://www.sciencedirect.com/pdf/x.pdf"
    assert proxify_url(url, "https://www.sciencedirect.com/article/1") == url


def test_proxify_passthrough_when_already_proxied():
    url = "https://www-wiley-com.eur.idm.oclc.org/doi/pdf/10.1111/x"
    assert proxify_url(url, PROXIED_PAGE) == url


def test_extract_citation_pdf_url_meta():
    html = '<html><head><meta name="citation_pdf_url" content="https://www.sciencedirect.com/pdf/S001-main.pdf"></head></html>'
    assert (
        extract_pdf_url(html, PROXIED_PAGE)
        == "https://www-sciencedirect-com.eur.idm.oclc.org/pdf/S001-main.pdf"
    )


def test_extract_doi_pdf_anchor():
    html = '<a class="pdf" href="/doi/pdf/10.1111/1475-679X.1?download=true">PDF</a>'
    page = "https://onlinelibrary-wiley-com.eur.idm.oclc.org/doi/10.1111/1475-679X.1"
    assert extract_pdf_url(html, page) == (
        "https://onlinelibrary-wiley-com.eur.idm.oclc.org/doi/pdf/"
        "10.1111/1475-679X.1?download=true"
    )


def test_extract_sciencedirect_pdfft_anchor():
    html = '<a href="/science/article/pii/S001/pdfft?isDTMRedir=true">Download</a>'
    assert extract_pdf_url(html, PROXIED_PAGE) == (
        "https://www-sciencedirect-com.eur.idm.oclc.org/science/article/pii/"
        "S001/pdfft?isDTMRedir=true"
    )


def test_extract_returns_none_without_link():
    assert extract_pdf_url("<html><body>Paywall teaser</body></html>", PROXIED_PAGE) is None


def test_login_page_detected_on_ezproxy_host():
    html = '<form><input type="password" name="pass"></form>'
    assert is_login_page(html, "https://eur.idm.oclc.org/login?url=https://doi.org/10.1/x")


def test_regular_proxied_page_is_not_login():
    assert not is_login_page("<html><body>Article</body></html>", PROXIED_PAGE)
```

- [ ] **Step 2: Run** — `poetry run pytest tests/test_publisher_pdf.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
"""EZproxy URL handling and publisher PDF-link extraction.

Pure functions: no Selenium, no DB. The InstitutionalDownloader feeds page
source + current URL in; these decide what to fetch next.
"""

from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

EZPROXY_DOMAIN_MARKER = ".idm.oclc.org"


def build_ezproxy_url(prefix: str, doi: str) -> str:
    """EZproxy-prefixed doi.org URL for one article."""
    return f"{prefix}https://doi.org/{doi}"


def proxify_url(url: str, current_url: str) -> str:
    """Rewrite an absolute publisher URL onto the EZproxy host when needed.

    EZproxy proxy-by-hostname turns dots into dashes and appends the proxy
    domain: www.sciencedirect.com -> www-sciencedirect-com.eur.idm.oclc.org.
    Relative URLs never reach here (urljoin resolves them against the already
    proxied current_url first).
    """
    cur = urlparse(current_url)
    if EZPROXY_DOMAIN_MARKER not in cur.netloc:
        return url
    tgt = urlparse(url)
    if not tgt.netloc or EZPROXY_DOMAIN_MARKER in tgt.netloc:
        return url
    # Proxy domain = everything after the mangled host segment of current_url
    proxy_domain = cur.netloc.split(".", 1)[1]
    mangled = tgt.netloc.replace(".", "-")
    return tgt._replace(netloc=f"{mangled}.{proxy_domain}").geturl()


def extract_pdf_url(html: str, current_url: str) -> Optional[str]:
    """Locate the PDF link on a publisher landing page.

    Order: citation_pdf_url meta (most publishers), then /doi/pdf anchors
    (Wiley, T&F, Chicago, INFORMS), then ScienceDirect pdfft anchors, then a
    generic .pdf anchor.
    """
    soup = BeautifulSoup(html, "lxml")

    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if meta and meta.get("content"):
        return proxify_url(urljoin(current_url, meta["content"]), current_url)

    for selector in ('a[href*="/doi/pdf"]', 'a[href*="/pdfft"]', 'a[href$=".pdf"]'):
        anchor = soup.select_one(selector)
        if anchor and anchor.get("href"):
            return proxify_url(urljoin(current_url, anchor["href"]), current_url)
    return None


def is_login_page(html: str, current_url: str) -> bool:
    """True when we are stuck on the EZproxy/SSO login instead of the target.

    A live session makes EZproxy redirect straight through to the proxied
    publisher host; an expired one leaves us on the login host with a
    credential form (ERNA password field or SURFconext chooser).
    """
    netloc = urlparse(current_url).netloc
    on_login_host = EZPROXY_DOMAIN_MARKER in netloc and not netloc.split(".", 1)[0].count("-")
    lower = html.lower()
    has_credentials_form = 'type="password"' in lower or "type='password'" in lower
    return on_login_host and (has_credentials_form or "surfconext" in lower)
```

Note on `on_login_host`: the bare proxy host (`eur.idm.oclc.org`) has no dash in its first label, while every proxied publisher host (`www-sciencedirect-com.eur...`) does. If a cleaner rule emerges during implementation, keep the tests green and adjust.

- [ ] **Step 4: Run** — PASS. **Step 5: Format + commit** — `poetry run black src/ tests/ && poetry run ruff check src/ tests/ && git add -A && git commit -m "feat: EZproxy URL + publisher PDF-link helpers"`

---

### Task 6: `InstitutionalDownloader` (persistent-profile Selenium)

**Files:**
- Create: `src/cite_hustle/collectors/institutional.py`
- Modify: `src/cite_hustle/config.py` (institutional settings block)
- Test: `tests/test_institutional.py` (create)

**Interfaces:**
- Produces settings (env prefix `CITE_HUSTLE_`):

```python
    # Institutional (EZproxy) acquisition
    ezproxy_prefix: str = "https://eur.idm.oclc.org/login?url="
    chrome_profile_dir: Path = Path.home() / ".cache" / "cite-hustle" / "chrome-profile"
    login_probe_url: str = "https://www.sciencedirect.com"
    institutional_batch: int = 50
    institutional_delay: int = 10
```

- Produces `cite_hustle.collectors.institutional`:
  - `class SessionExpired(Exception)` — raised when the EZproxy login page appears; carries the URL.
  - `class InstitutionalDownloader` with:
    - `__init__(self, storage_dir: Path, profile_dir: Path, ezproxy_prefix: str, headless: bool = False, page_timeout: int = 45, download_timeout: int = 90)`
    - `setup_webdriver(self)` — undetected-chromedriver with `--user-data-dir=<profile_dir>` (this is what persists the EZproxy/SSO cookies), download prefs into `storage_dir / "temp_downloads_inst"`, version pinning reused from `SeleniumPDFDownloader._detect_chrome_major_version`.
    - `quit(self)`
    - `acquire(self, article: dict) -> dict` — one article; result dict `{doi, status, pdf_url, filepath, error}` with `status ∈ downloaded | no_pdf_link | not_a_pdf | nav_error`; raises `SessionExpired`.
    - `_download_via_browser(self, pdf_url: str, doi: str) -> Path` — navigate to the PDF URL (profile cookies authorize it; `plugins.always_open_pdf_externally` forces download), wait for a stable non-`.crdownload` file, validate `%PDF-` magic, move to `storage_dir / doi_slug_filename(doi)`; raises `RuntimeError` with a reason string on failure.

- [ ] **Step 1: Write the failing tests** (mock the driver; never launch Chrome in tests)

```python
"""InstitutionalDownloader behavior with a mocked Selenium driver."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cite_hustle.collectors.institutional import InstitutionalDownloader, SessionExpired

LOGIN_HTML = '<form><input type="password"></form>'
LOGIN_URL = "https://eur.idm.oclc.org/login?url=https://doi.org/10.1/x"
ARTICLE_URL = "https://www-sciencedirect-com.eur.idm.oclc.org/science/article/pii/S1"
ARTICLE_HTML = (
    '<meta name="citation_pdf_url" '
    'content="https://www.sciencedirect.com/pdf/S1-main.pdf">'
)


def make_downloader(tmp_path):
    d = InstitutionalDownloader(
        storage_dir=tmp_path,
        profile_dir=tmp_path / "profile",
        ezproxy_prefix="https://eur.idm.oclc.org/login?url=",
    )
    d.driver = MagicMock()
    return d


def test_acquire_raises_session_expired_on_login_page(tmp_path):
    d = make_downloader(tmp_path)
    d.driver.page_source = LOGIN_HTML
    d.driver.current_url = LOGIN_URL
    with pytest.raises(SessionExpired):
        d.acquire({"doi": "10.1/x", "title": "T"})


def test_acquire_reports_no_pdf_link(tmp_path):
    d = make_downloader(tmp_path)
    d.driver.page_source = "<html><body>Paywall teaser</body></html>"
    d.driver.current_url = ARTICLE_URL
    result = d.acquire({"doi": "10.1/x", "title": "T"})
    assert result["status"] == "no_pdf_link"


def test_acquire_downloads_via_browser(tmp_path, monkeypatch):
    d = make_downloader(tmp_path)
    d.driver.page_source = ARTICLE_HTML
    d.driver.current_url = ARTICLE_URL
    dest = tmp_path / "10.1_x.pdf"
    monkeypatch.setattr(d, "_download_via_browser", lambda url, doi: dest)
    result = d.acquire({"doi": "10.1/x", "title": "T"})
    assert result["status"] == "downloaded"
    assert result["pdf_url"].startswith("https://www-sciencedirect-com.eur.idm.oclc.org/")
    assert result["filepath"] == str(dest)
```

- [ ] **Step 2: Run** — FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `institutional.py`**

Core of `acquire` (implementers: navigation waiting, temp-dir download watching, and browser lifecycle mirror `selenium_pdf_downloader.py`; read it first):

```python
"""EZproxy institutional PDF acquisition via a persistent Chrome profile.

Unlike the fallback resolvers (plain httpx), publisher PDFs behind EUR's
EZproxy must be fetched inside the authenticated browser: the persistent
profile at settings.chrome_profile_dir holds the EZproxy/SSO cookies created
by the one-time `cite-hustle login`.
"""

import time
from pathlib import Path
from typing import Dict, Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import WebDriverException

from cite_hustle.collectors.http_pdf_downloader import doi_slug_filename
from cite_hustle.collectors.publisher_pdf import (
    build_ezproxy_url,
    extract_pdf_url,
    is_login_page,
)
from cite_hustle.collectors.selenium_pdf_downloader import SeleniumPDFDownloader


class SessionExpired(Exception):
    """The EZproxy/ERNA login page appeared: run `cite-hustle login`."""


class InstitutionalDownloader:
    def __init__(
        self,
        storage_dir: Path,
        profile_dir: Path,
        ezproxy_prefix: str,
        headless: bool = False,
        page_timeout: int = 45,
        download_timeout: int = 90,
    ):
        self.storage_dir = Path(storage_dir)
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.ezproxy_prefix = ezproxy_prefix
        self.headless = headless
        self.page_timeout = page_timeout
        self.download_timeout = download_timeout
        self.temp_download_dir = self.storage_dir / "temp_downloads_inst"
        self.temp_download_dir.mkdir(parents=True, exist_ok=True)
        self.driver = None

    def setup_webdriver(self):
        chrome_options = uc.ChromeOptions()
        chrome_options.add_argument(f"--user-data-dir={self.profile_dir}")
        chrome_options.add_argument("--window-size=1400,1000")
        chrome_options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.temp_download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
            },
        )
        kwargs = {"options": chrome_options, "headless": self.headless}
        major = SeleniumPDFDownloader._detect_chrome_major_version()
        if major is not None:
            kwargs["version_main"] = major
        self.driver = uc.Chrome(**kwargs)
        self.driver.set_page_load_timeout(self.page_timeout)
        return self.driver

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def acquire(self, article: Dict) -> Dict:
        """Try to fetch one article's publisher PDF through EZproxy."""
        doi = article["doi"]
        result = {"doi": doi, "status": None, "pdf_url": None, "filepath": None, "error": None}
        url = build_ezproxy_url(self.ezproxy_prefix, doi)
        try:
            self.driver.get(url)
        except WebDriverException as exc:
            result.update(status="nav_error", error=str(exc)[:200])
            return result
        time.sleep(3)  # let EZproxy redirects and the landing page settle

        html, current = self.driver.page_source, self.driver.current_url
        if is_login_page(html, current):
            raise SessionExpired(current)

        pdf_url = extract_pdf_url(html, current)
        if not pdf_url:
            result.update(status="no_pdf_link", error=current[:200])
            return result

        result["pdf_url"] = pdf_url
        try:
            filepath = self._download_via_browser(pdf_url, doi)
        except RuntimeError as exc:
            result.update(status="not_a_pdf", error=str(exc)[:200])
            return result
        result.update(status="downloaded", filepath=str(filepath))
        return result
```

`_download_via_browser`: clear `temp_download_dir` of stale files, `self.driver.get(pdf_url)` (a `TimeoutException` here is fine — Chrome keeps downloading; catch and continue), poll the temp dir until a file exists with no `.crdownload`/`.part` sibling and a stable size (up to `download_timeout`), check first 5 bytes are `b"%PDF-"` (else `raise RuntimeError("not_a_pdf")`), then `shutil.move` to `self.storage_dir / doi_slug_filename(doi)` and return that Path. Mirror the polling loop in `SeleniumPDFDownloader` rather than inventing a new one.

- [ ] **Step 4: Add the settings block** (shown under Interfaces) to `config.py` after `fallback_batch` / `error_recheck_days`.

- [ ] **Step 5: Run** — `poetry run pytest tests/ -v` → PASS. **Step 6: Format + commit** — `git commit -am "feat: InstitutionalDownloader with persistent Chrome profile"`

---### Task 7: Request queue + `request` command + `NO_DB_COMMANDS`

**Files:**
- Create: `src/cite_hustle/requests_queue.py`
- Modify: `src/cite_hustle/cli/commands.py` (NO_DB set in `main()`, new `request` command)
- Test: `tests/test_requests_queue.py` (create)

**Interfaces:**
- Produces `cite_hustle.requests_queue`:
  - `queue_path() -> Path` — `settings.dropbox_base / "requests.jsonl"` (do NOT use a Settings property that mkdirs).
  - `append_request(doi: str, note: Optional[str] = None) -> bool` — False if already queued; entry `{doi, requested_at, machine, note, attempts}` (`machine` = `platform.node()`, `attempts` starts 0).
  - `read_requests() -> list[dict]` — tolerant of a missing file (returns []).
  - `write_requests(entries: list[dict]) -> None` — atomic (write `.tmp`, `os.replace`) so a Dropbox-synced partial write never corrupts the queue.
- Produces CLI `request DOI [--note TEXT]` and the `NO_DB_COMMANDS = {"request", "login"}` early-return in `main()` (before `DatabaseManager` is constructed) — these commands must work on read-only machines and while the runner holds the write lock.

- [ ] **Step 1: Failing tests**

```python
"""Dropbox-synced request queue."""

import json

import pytest

from cite_hustle import requests_queue as rq


@pytest.fixture(autouse=True)
def patched_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")


def test_append_and_read_roundtrip():
    assert rq.append_request("10.1/x", note="for lit review") is True
    entries = rq.read_requests()
    assert entries[0]["doi"] == "10.1/x"
    assert entries[0]["attempts"] == 0


def test_append_is_idempotent():
    rq.append_request("10.1/x")
    assert rq.append_request("10.1/x") is False
    assert len(rq.read_requests()) == 1


def test_read_missing_file_returns_empty():
    assert rq.read_requests() == []


def test_write_is_atomic_rewrite():
    rq.append_request("10.1/x")
    rq.append_request("10.1/y")
    entries = [e for e in rq.read_requests() if e["doi"] != "10.1/x"]
    rq.write_requests(entries)
    assert [e["doi"] for e in rq.read_requests()] == ["10.1/y"]
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement the module** (json-lines file, `datetime.now().isoformat()` timestamps, `os.replace` for atomicity).

- [ ] **Step 4: Wire the CLI.** In `commands.py`, next to `READ_ONLY_COMMANDS`:

```python
# Commands that never touch the database (usable on any machine, any time).
NO_DB_COMMANDS = {"request", "login"}
```

In `main()`, immediately after the `--help` early return:

```python
    if ctx.invoked_subcommand in NO_DB_COMMANDS:
        return
```

New command:

```python
@main.command("request")
@click.argument("doi")
@click.option("--note", default=None, help="Why you want this paper (lands in the queue entry)")
def request_paper(doi, note):
    """Queue a DOI for acquisition by the runner (works on any machine).

    Appends to <dropbox_base>/requests.jsonl; the runner's pipeline drains the
    queue as its first stage, or run 'cite-hustle process-requests' manually.
    """
    from cite_hustle.requests_queue import append_request, queue_path

    if append_request(doi, note=note):
        click.echo(f"✓ Queued {doi} in {queue_path()}")
    else:
        click.echo(f"ℹ️  {doi} is already queued")
```

- [ ] **Step 5: Verify manually** — `poetry run cite-hustle request 10.9999/plan-test && poetry run cite-hustle request 10.9999/plan-test` → first prints Queued, second already queued; then delete the test entry: `rm "$HOME/Dropbox/Github Data/cite-hustle/requests.jsonl"`.

- [ ] **Step 6: Run suite, format, commit** — `git commit -am "feat: request queue and no-DB request command"`

---

### Task 8: `login` command

**Files:**
- Modify: `src/cite_hustle/cli/commands.py` (new command; already in `NO_DB_COMMANDS` from Task 7)

**Interfaces:**
- Produces CLI `login`: headful Chrome on the persistent profile → EZproxy probe URL → user completes ERNA + MFA → session confirmed. No DB.

- [ ] **Step 1: Implement**

```python
@main.command("login")
def login():
    """One-time EZproxy/ERNA login for institutional PDF downloads.

    Opens a visible Chrome on the persistent profile and navigates through
    EZproxy. Complete the ERNA login (incl. MFA) in the browser, then press
    Enter here. The session cookie persists in the profile, so scheduled
    institutional runs work unattended until it expires.
    """
    from cite_hustle.collectors.institutional import InstitutionalDownloader
    from cite_hustle.collectors.publisher_pdf import is_login_page

    downloader = InstitutionalDownloader(
        storage_dir=settings.pdf_storage_dir,
        profile_dir=settings.chrome_profile_dir,
        ezproxy_prefix=settings.ezproxy_prefix,
        headless=False,
    )
    downloader.setup_webdriver()
    try:
        downloader.driver.get(settings.ezproxy_prefix + settings.login_probe_url)
        click.echo("🌐 Complete the ERNA login in the browser window (incl. MFA).")
        click.pause("   Press any key here once the publisher page has loaded...")
        html, current = downloader.driver.page_source, downloader.driver.current_url
        if is_login_page(html, current):
            click.echo("✗ Still on the login page; session NOT established. Try again.")
            sys.exit(1)
        click.echo(f"✓ Session established (landed on {current.split('?')[0]})")
    finally:
        downloader.quit()
```

- [ ] **Step 2: Verify** — `poetry run cite-hustle login --help` renders; full manual test is deferred to the human-verification step at the end of the project (needs ERNA credentials).

- [ ] **Step 3: Commit** — `git commit -am "feat: login command for persistent EZproxy session"`

---

### Task 9: `acquire.py` service module part 1 — shared fallback loop

Extract the per-article source loop from `resolve_fallbacks` so the batch command and the single-DOI `get` (Task 10) share one implementation.

**Files:**
- Create: `src/cite_hustle/acquire.py`
- Modify: `src/cite_hustle/cli/commands.py:531-589` (rewire `resolve_fallbacks`'s inner loop)
- Test: `tests/test_acquire.py` (create)

**Interfaces:**
- Produces `try_sources_for_article(repo, article: dict, source_order: list[str], resolvers: dict, client, already_checked: set = frozenset(), pdf_dir: Path = None) -> Optional[str]` — runs the exact logic currently inlined at `commands.py:536-584` (record error / no_match / download+upsert+log, first hit wins), returns the winning source name or None. `pdf_dir` defaults to `settings.pdf_storage_dir`.
- `resolve_fallbacks` behavior is unchanged (same records, same echo lines — move the `click.echo(f"  ✓ {doi}: ...")` into the CLI by echoing on a non-None return).

- [ ] **Step 1: Failing test** (fake resolver objects; no network)

```python
"""Shared acquisition service functions."""

from unittest.mock import patch

from conftest import add_article

from cite_hustle import acquire
from cite_hustle.collectors.fallback_resolvers import Candidate, ResolverError


class FakeResolver:
    def __init__(self, outcome):
        self.outcome = outcome

    def resolve(self, client, article):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_try_sources_first_hit_wins_and_records(repo, tmp_path):
    add_article(repo, "10.1/x")
    article = repo.get_article_by_doi("10.1/x")
    cand = Candidate(source="nber", candidate_url="http://n/1", pdf_url="http://n/1.pdf", match_score=95.0)
    resolvers = {"oa": FakeResolver(None), "nber": FakeResolver(cand)}
    with patch.object(acquire, "download_pdf", return_value=(True, None)):
        won = acquire.try_sources_for_article(
            repo, article, ["oa", "nber"], resolvers, client=None, pdf_dir=tmp_path
        )
    assert won == "nber"
    row = repo.conn.execute(
        "SELECT source, verify_status FROM pdf_files WHERE doi = '10.1/x'"
    ).fetchone()
    assert row == ("nber", "pending")
    statuses = dict(
        repo.conn.execute("SELECT source, status FROM pdf_candidates WHERE doi='10.1/x'").fetchall()
    )
    assert statuses == {"oa": "no_match", "nber": "downloaded"}


def test_try_sources_resolver_error_recorded_and_continues(repo, tmp_path):
    add_article(repo, "10.1/y")
    article = repo.get_article_by_doi("10.1/y")
    resolvers = {"oa": FakeResolver(ResolverError("http_503"))}
    won = acquire.try_sources_for_article(
        repo, article, ["oa"], resolvers, client=None, pdf_dir=tmp_path
    )
    assert won is None
    status = repo.conn.execute(
        "SELECT status FROM pdf_candidates WHERE doi='10.1/y' AND source='oa'"
    ).fetchone()[0]
    assert status == "error"
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** — move the body of `commands.py:536-584` into `acquire.try_sources_for_article` verbatim (imports: `download_pdf`, `doi_slug_filename` from `http_pdf_downloader`; `ResolverError` from `fallback_resolvers`; `settings` for the default `pdf_dir`). The `(doi, name) in already_checked` skip stays inside the function. No `click` calls inside `acquire.py` — it is a service module.

- [ ] **Step 4: Rewire `resolve_fallbacks`** to build `resolvers`/`already_checked` as today, then per article call the function and echo on success:

```python
            won = acquire.try_sources_for_article(
                repo, article, source_order, resolvers, client, already_checked
            )
            if won:
                found += 1
            else:
                misses += 1
```

(The per-source score echo moves to a single `✓ {doi}: {won}` line; acceptable output change.)

- [ ] **Step 5: Run full suite** — PASS. **Step 6: Commit** — `git commit -am "refactor: extract shared fallback source loop into acquire.py"`

---

### Task 10: `acquire.py` part 2 — institutional batch loop + `institutional` command

**Files:**
- Modify: `src/cite_hustle/acquire.py`
- Modify: `src/cite_hustle/cli/commands.py` (new `institutional` command)
- Test: `tests/test_acquire.py`

**Interfaces:**
- Produces `run_institutional_batch(repo, downloader, articles: pd.DataFrame, delay: int, already_checked: set = frozenset()) -> dict` — returns `{"downloaded": n, "no_match": n, "error": n, "aborted": bool}`. Per row: skip if `(doi, "ezproxy") in already_checked`; call `downloader.acquire(article)`; map results:
  - `downloaded` → `record_pdf_candidate(doi, "ezproxy", candidate_url=result["pdf_url"], pdf_url=result["pdf_url"], match_score=100.0, status="downloaded")` + `upsert_pdf_file(doi, source="ezproxy", source_url=build_ezproxy_url(...), pdf_url=result["pdf_url"], pdf_file_path=result["filepath"], match_score=100.0)` + `log_processing(doi, "resolve_institutional", "success")` (match_score 100: DOI-exact navigation).
  - `no_pdf_link` → candidate `status="no_match"`, `error_message="no_pdf_link"`.
  - `nav_error` / `not_a_pdf` → candidate `status="error"`, `error_message=f"{status}: {error}"`.
  - `SessionExpired` → record candidate `status="error"`, `error_message="session_expired"` for the current DOI, log `resolve_institutional/failed`, set `aborted=True`, stop the loop (do NOT touch remaining articles).
  - `time.sleep(delay)` between articles.
- Produces CLI `institutional --limit N --delay S --headless/--no-headless` (defaults: `settings.institutional_batch` via pipeline, delay `settings.institutional_delay`, headless False): feeder `get_articles_for_institutional`, `already_checked` from the Task 3 two-cutoff call with `recheck_days=90` hardcoded default option `--recheck-days`, constructs `InstitutionalDownloader` from settings, `setup_webdriver()`, runs the batch, `quit()` in `finally`, echoes counts and — when aborted — the "run `cite-hustle login`" hint.

- [ ] **Step 1: Failing tests**

```python
from cite_hustle.collectors.institutional import SessionExpired


class FakeInstDownloader:
    def __init__(self, results):
        self.results = results  # doi -> result dict or Exception

    def acquire(self, article):
        r = self.results[article["doi"]]
        if isinstance(r, Exception):
            raise r
        return r


def _inst_result(doi, status, **kw):
    return {"doi": doi, "status": status, "pdf_url": kw.get("pdf_url"),
            "filepath": kw.get("filepath"), "error": kw.get("error")}


def test_institutional_batch_records_and_counts(repo, tmp_path):
    for doi in ("10.1/a", "10.1/b"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    fake = FakeInstDownloader({
        "10.1/a": _inst_result("10.1/a", "downloaded",
                               pdf_url="https://p/x.pdf", filepath=str(tmp_path / "a.pdf")),
        "10.1/b": _inst_result("10.1/b", "no_pdf_link", error="paywall"),
    })
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert counts["downloaded"] == 1 and counts["no_match"] == 1 and not counts["aborted"]
    assert repo.conn.execute(
        "SELECT source FROM pdf_files WHERE doi='10.1/a'"
    ).fetchone()[0] == "ezproxy"


def test_institutional_batch_aborts_on_session_expired(repo, tmp_path):
    for doi in ("10.1/c", "10.1/d"):
        add_article(repo, doi)
        repo.record_pdf_candidate(doi, "oa", status="no_match")
    articles = repo.get_articles_for_institutional()
    first = articles.iloc[0]["doi"]
    fake = FakeInstDownloader({d: SessionExpired("login") for d in articles["doi"]})
    counts = acquire.run_institutional_batch(repo, fake, articles, delay=0)
    assert counts["aborted"] is True
    rows = repo.conn.execute(
        "SELECT doi, status, error_message FROM pdf_candidates WHERE source='ezproxy'"
    ).fetchall()
    assert rows == [(first, "error", "session_expired")]  # only the first article touched
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** `run_institutional_batch` per the interface above.

- [ ] **Step 4: Add the CLI command** (thin wrapper; pattern follows `resolve_fallbacks`):

```python
@main.command("institutional")
@click.option("--limit", default=None, type=int, help="Limit number of articles")
@click.option("--delay", default=None, type=int, help="Seconds between articles")
@click.option("--recheck-days", default=90, type=int, help="Re-try no_match pairs after N days")
@click.option("--headless/--no-headless", default=False, help="EZproxy usually works headful; keep visible on the runner")
@click.pass_context
def institutional(ctx, limit, delay, recheck_days, headless):
    """Fetch publisher PDFs through EUR's EZproxy (after fallbacks failed).

    Needs a live login session in the persistent Chrome profile; run
    'cite-hustle login' once (and again whenever runs abort with
    session_expired).
    """
    from datetime import datetime, timedelta

    from cite_hustle import acquire
    from cite_hustle.collectors.institutional import InstitutionalDownloader

    repo = ctx.obj["repo"]
    articles = repo.get_articles_for_institutional(limit=limit)
    if articles.empty:
        click.echo("✓ No articles pending institutional resolution")
        return

    cutoff = datetime.now() - timedelta(days=recheck_days)
    error_cutoff = datetime.now() - timedelta(days=settings.error_recheck_days)
    already_checked = repo.get_recent_candidate_checks(cutoff, error_cutoff)

    downloader = InstitutionalDownloader(
        storage_dir=settings.pdf_storage_dir,
        profile_dir=settings.chrome_profile_dir,
        ezproxy_prefix=settings.ezproxy_prefix,
        headless=headless,
    )
    click.echo(f"🏛  Resolving {len(articles)} articles via EZproxy\n")
    downloader.setup_webdriver()
    try:
        counts = acquire.run_institutional_batch(
            repo, downloader, articles,
            delay=delay if delay is not None else settings.institutional_delay,
            already_checked=already_checked,
        )
    finally:
        downloader.quit()

    click.echo(
        f"\n✓ Institutional resolution: {counts['downloaded']} downloaded, "
        f"{counts['no_match']} without a PDF link, {counts['error']} errors"
    )
    if counts["aborted"]:
        click.echo("✗ Session expired: run 'poetry run cite-hustle login' and re-run")
```

- [ ] **Step 5: Run suite, format, commit** — `git commit -am "feat: institutional batch resolution command"`

---

### Task 11: `acquire.py` part 3 — CrossRef single fetch, `acquire_one`, `get` command

**Files:**
- Modify: `src/cite_hustle/acquire.py`
- Modify: `src/cite_hustle/cli/commands.py` (new `get` command)
- Test: `tests/test_acquire.py`

**Interfaces:**
- Produces `fetch_crossref_article(doi: str) -> Optional[dict]` — httpx GET `https://api.crossref.org/works/{doi}` (params `{"mailto": settings.crossref_email}` when set, 404 → None, other errors raise `httpx.HTTPStatusError` via `response.raise_for_status()`), returns `{doi, title, authors, year, journal_issn, journal_name, publisher}`: title = `MetadataCollector.clean_title(" ".join(msg["title"]))`; authors = `"; ".join(f"{a.get('given','')} {a.get('family','')}".strip() for a in msg.get("author", []))`; year from `msg["issued"]["date-parts"][0][0]` (fallback `published-print`/`published-online`, else None → treat as failure); journal_issn = first of `msg.get("ISSN", [None])`; journal_name = first of `msg.get("container-title", [""])`; publisher = `msg.get("publisher", "")`.
- Produces `acquire_one(repo, doi: str, downloader_factory=None, use_institutional: bool = True, run_verify: bool = True) -> dict` — the "get me this paper" chain. Returns `{doi, status, source, path, verify_status, detail}` with `status ∈ already_have | downloaded | metadata_not_found | no_source | session_expired`:
  1. `article = repo.get_article_by_doi(doi)`; if None → `fetch_crossref_article(doi)`; if that is None → `metadata_not_found`; else `repo.insert_article(**fetched)` and continue.
  2. If a `pdf_files` row exists (query via `repo.get_pdfs_pending_verification(statuses=("pending","match","uncertain","unreadable"))` is the wrong tool — add a tiny repo helper `get_pdf_file_by_doi(doi) -> Optional[Dict]` returning `doi, source, pdf_file_path, verify_status`; include it in this task with a one-assert test) → `already_have` with its path/verify_status.
  3. Fallback sources fresh (ignore the memo — a human asked): `resolvers = {name: RESOLVERS[name](threshold=settings.similarity_threshold) for name in ("oa","nber","arxiv")}`, one `httpx.Client` as in `resolve_fallbacks`, `won = try_sources_for_article(repo, article, [...], resolvers, client)` with `already_checked=frozenset()`.
  4. If not won and `use_institutional`: `downloader = downloader_factory()` (default factory builds the real `InstitutionalDownloader` from settings and calls `setup_webdriver()`), single-article `run_institutional_batch` over a one-row DataFrame, `quit()` in finally; `SessionExpired` → status `session_expired`.
  5. If a PDF landed and `run_verify`: build `PDFVerifier(repo=repo, quarantine_dir=settings.quarantine_dir, model=settings.pdf_verifier_model, gray_low=settings.verify_gray_zone_low, gray_high=settings.verify_gray_zone_high, use_llm=bool(os.environ.get("OLLAMA_API_KEY")))`, fetch the pending row via `repo.get_pdfs_pending_verification()` filtered to this doi, `verifier.verify_batch(df)`; report resulting `verify_status` from `get_pdf_file_by_doi`.
  6. Nothing found → `no_source`.
- Produces CLI `get DOI [--no-institutional] [--no-verify]` (write command, runner only): calls `acquire_one`, prints a readable outcome block.

- [ ] **Step 1: Failing tests** (mock `fetch_crossref_article` and use fakes; no network, no Chrome)

```python
def test_acquire_one_already_have(repo, tmp_path):
    add_article(repo, "10.1/have")
    repo.upsert_pdf_file("10.1/have", "oa", None, "http://x.pdf", str(tmp_path / "h.pdf"))
    out = acquire.acquire_one(repo, "10.1/have", use_institutional=False, run_verify=False)
    assert out["status"] == "already_have"
    assert out["source"] == "oa"


def test_acquire_one_unknown_doi_fetches_metadata(repo, monkeypatch):
    fetched = {"doi": "10.1/new", "title": "T", "authors": "A B", "year": 2024,
               "journal_issn": "1234-5678", "journal_name": "J", "publisher": "P"}
    monkeypatch.setattr(acquire, "fetch_crossref_article", lambda doi: fetched)
    monkeypatch.setattr(acquire, "try_sources_for_article", lambda *a, **k: None)
    out = acquire.acquire_one(repo, "10.1/new", use_institutional=False, run_verify=False)
    assert out["status"] == "no_source"
    assert repo.get_article_by_doi("10.1/new")["title"] == "T"


def test_acquire_one_metadata_not_found(repo, monkeypatch):
    monkeypatch.setattr(acquire, "fetch_crossref_article", lambda doi: None)
    out = acquire.acquire_one(repo, "10.1/ghost", use_institutional=False, run_verify=False)
    assert out["status"] == "metadata_not_found"
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (`get_pdf_file_by_doi` repo helper + the three functions). **Step 4: Add the CLI command:**

```python
@main.command("get")
@click.argument("doi")
@click.option("--no-institutional", is_flag=True, help="Skip the EZproxy browser stage")
@click.option("--no-verify", is_flag=True, help="Skip immediate PDF verification")
@click.pass_context
def get_paper(ctx, doi, no_institutional, no_verify):
    """Get one paper end-to-end: metadata -> OA fallbacks -> EZproxy -> verify.

    Runner-only (takes the DB write lock). From other machines use
    'cite-hustle request DOI' instead.
    """
    from cite_hustle import acquire

    repo = ctx.obj["repo"]
    out = acquire.acquire_one(
        repo, doi, use_institutional=not no_institutional, run_verify=not no_verify
    )
    icon = {"already_have": "✓", "downloaded": "✓"}.get(out["status"], "✗")
    click.echo(f"{icon} {doi}: {out['status']}")
    for key in ("source", "path", "verify_status", "detail"):
        if out.get(key):
            click.echo(f"   {key}: {out[key]}")
    if out["status"] == "session_expired":
        click.echo("   Run 'poetry run cite-hustle login' and retry.")
```

- [ ] **Step 5: Run suite, format, commit** — `git commit -am "feat: per-DOI acquisition (cite-hustle get)"`

---

### Task 12: `process-requests` command (queue drain)

**Files:**
- Modify: `src/cite_hustle/cli/commands.py` (new command)
- Modify: `src/cite_hustle/acquire.py` (drain function)
- Test: `tests/test_acquire.py`

**Interfaces:**
- Produces `acquire.drain_requests(repo, acquire_fn=None) -> dict` — reads the queue, for each entry calls `acquire_fn(repo, doi)` (default `acquire_one`); entries resolved (`already_have`/`downloaded`) or `metadata_not_found` are dropped (the latter with `log_processing(doi, "request", "failed", "metadata_not_found")`); failed entries get `attempts += 1` and are kept, except `attempts >= 3` → dropped with `log_processing(doi, "request", "failed", "gave_up_after_3")`; on `session_expired` stop draining (keep everything unprocessed, including the current entry, unchanged). Atomic rewrite via `write_requests`. Returns `{"resolved": n, "kept": n, "dropped": n, "session_expired": bool}`.
- Produces CLI `process-requests` (write command): calls `drain_requests`, echoes counts per DOI.

- [ ] **Step 1: Failing test**

```python
from cite_hustle import requests_queue as rq


def test_drain_requests_drops_resolved_keeps_failed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "queue_path", lambda: tmp_path / "requests.jsonl")
    rq.append_request("10.1/ok")
    rq.append_request("10.1/miss")

    def fake_acquire(repo_, doi):
        status = "downloaded" if doi == "10.1/ok" else "no_source"
        return {"doi": doi, "status": status, "source": None, "path": None,
                "verify_status": None, "detail": None}

    counts = acquire.drain_requests(repo, acquire_fn=fake_acquire)
    assert counts["resolved"] == 1 and counts["kept"] == 1
    remaining = rq.read_requests()
    assert [e["doi"] for e in remaining] == ["10.1/miss"]
    assert remaining[0]["attempts"] == 1
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement + CLI wrapper.** **Step 4: Run suite, commit** — `git commit -am "feat: process-requests queue drain"`

---

### Task 13: Pipeline integration

**Files:**
- Modify: `src/cite_hustle/pipeline.py:21-34` (PROFILES)
- Modify: `src/cite_hustle/cli/commands.py` (`stage_invokes` at :763-782, `--stages` help text at :730-735)
- Test: `tests/test_pipeline_profiles.py` (create)

**Interfaces:**
- `PROFILES["monthly"] = ["requests", "collect", "scrape", "enrich", "download", "fallbacks", "institutional", "verify", "ingest", "index", "fts"]`
- `PROFILES["incremental"] = ["requests", "scrape", "download", "fallbacks", "institutional", "verify", "ingest", "index", "fts"]`
- `stage_invokes` additions: `"requests": lambda: ctx.invoke(process_requests)`, `"institutional": lambda: ctx.invoke(institutional, limit=settings.institutional_batch)`.

- [ ] **Step 1: Failing test**

```python
"""Pin profile contents so --stages validation covers every stage."""

from cite_hustle import pipeline as pl


def test_new_stages_in_profiles():
    for stage in ("requests", "institutional"):
        assert stage in pl.PROFILES["monthly"]
        assert stage in pl.PROFILES["incremental"]
    assert pl.PROFILES["incremental"][0] == "requests"  # user requests run first
    m = pl.PROFILES["monthly"]
    assert m.index("institutional") > m.index("fallbacks")  # browser path runs last
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (profiles, `stage_invokes`, help text `"Comma-separated stage subset (requests,collect,scrape,enrich,download,fallbacks,institutional,verify,ingest,index,fts)"`). Remember: `--stages` validates against `PROFILES["monthly"]` (`commands.py:759`), which now contains both new stages.

- [ ] **Step 4: Run full suite** — PASS. **Step 5: Smoke test** — `poetry run cite-hustle pipeline --help` and `poetry run cite-hustle --help` render; `poetry run cite-hustle pipeline --stages nosuch` → BadParameter. **Step 6: Commit** — `git commit -am "feat: requests + institutional pipeline stages"`

---

### Task 14: Docs + deploy

**Files:**
- Modify: `CLAUDE.md` (CLI reference block, schema comment `source 'ssrn'|'nber'|'arxiv'|'oa'|'ezproxy'`, Decisions Log entries, env-var block)
- Modify: `README.md`, `CLI-CHEATSHEET.md` (new commands: `get`, `request`, `process-requests`, `institutional`, `login`)
- Modify: `deploy/README.md` (runner = the M2 machine; one-time `cite-hustle login` prerequisite; session-expiry runbook), `deploy/install.sh` (add commented `CITE_HUSTLE_EZPROXY_PREFIX=` / `CITE_HUSTLE_INSTITUTIONAL_BATCH=` lines to the env template)

- [ ] **Step 1: Update the docs.** Decisions Log additions (keep the existing style, one bullet each): EZproxy-first institutional access with LibKey/BrowZine deferred (Third Iron API needs a library-issued key; libkey.io robots-disallowed; EUR LibKey library ID 2163 recorded for later); Chrome profile for the EZproxy session lives on local disk, never Dropbox; requests.jsonl queue is the only cross-machine write channel and it never touches the DB; skill-only interface, MCP server deferred.
- [ ] **Step 2: Verify** — `poetry run cite-hustle --help` matches the documented command list; grep docs for the old two-profile stage lists and update all occurrences.
- [ ] **Step 3: Commit** — `git commit -am "docs: institutional acquisition commands, runner runbook, decisions"`

---

### Task 15: `cite-hustle` skill in dot-files (separate repo)

**Files:**
- Create: `/Users/casparm2/Local/GitHub/dot-files/claude/skills/cite-hustle/SKILL.md`

**Interfaces:** none (documentation artifact; synced to `~/.claude/skills/` by the existing symlink setup — check whether dot-files has an install/link script that needs a new entry, and add the symlink if links are explicit rather than directory-wide).

- [ ] **Step 1: Write SKILL.md** with frontmatter (`name: cite-hustle`, description triggering on "get me this paper", "find this paper's PDF", "queue a paper", "cite-hustle status/failures") and body covering: repo location (`~/Local/GitHub/cite-hustle`, always `poetry run cite-hustle ...`); machine roles (read-only commands anywhere: `status`, `dashboard`, `search`, `sample`, `journals`; `request DOI` anywhere, no DB; `get DOI`, `login`, `institutional`, `pipeline` only on the runner M2); the get-me-this-paper recipes; failure inspection (dashboard, `reports/run-*.md`, `pdf_candidates` statuses via dashboard); session maintenance (`session_expired` → `cite-hustle login` headful); explicit rule: the skill wraps the CLI, never raw SQL against the DuckDB.
- [ ] **Step 2: Verify the symlink** — after creating the directory, confirm `ls ~/.claude/skills/ | grep cite-hustle` (if dot-files symlinks per-skill, create `ln -s ~/Local/GitHub/dot-files/claude/skills/cite-hustle ~/.claude/skills/cite-hustle`).
- [ ] **Step 3: Commit in dot-files** — `cd ~/Local/GitHub/dot-files && git add claude/skills/cite-hustle && git commit -m "Add cite-hustle skill (paper acquisition interface)"`

---

### Task 16: Human verification (not agent-executable)

- [ ] Run `poetry run cite-hustle login` on the runner, complete ERNA + MFA.
- [ ] Supervised first download: `poetry run cite-hustle get 10.1111/1475-679X.<recent-JAR-doi> --no-verify` watching the browser; then `poetry run cite-hustle verify-pdfs --limit 1`.
- [ ] `poetry run cite-hustle request <doi>` from a second machine; `poetry run cite-hustle process-requests` on the runner; confirm the queue drains.
