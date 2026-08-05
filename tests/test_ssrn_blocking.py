"""Tests for SSRN scraper Cloudflare challenge detection and block classification."""

from unittest.mock import MagicMock, patch

import pandas as pd

from cite_hustle.collectors.ssrn_scraper import BLOCK_ABORT_THRESHOLD, SSRNScraper


# SSRN's Turnstile challenge page (as captured in ERROR_*.png screenshots)
TURNSTILE_PAGE = """
<html><head><title>papers.ssrn.com</title></head>
<body>
<h1>papers.ssrn.com</h1>
<h2>Performing security verification</h2>
<p>This website uses a security service to protect against malicious bots.</p>
<div><label>Verify you are human</label>
<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile"></iframe>
</div>
</body></html>
"""

# Cloudflare's generic interstitial
CF_INTERSTITIAL = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Checking your browser before accessing papers.ssrn.com</body></html>"
)

# A normal Cloudflare-served SSRN page. Deliberately contains data-cfasync and
# __cf_bm -- the markers the old detection false-positived on.
NORMAL_SSRN_PAGE = """
<html><head><title>Search eLibrary :: SSRN</title>
<script data-cfasync="false" src="/cdn-cgi/bm/cv/669835187/api.js"></script>
</head>
<body>
<input id="term" name="term">
<h3 data-component="Typography"><a href="/abstract=1234">Earnings Management and Audit Quality</a></h3>
<script>document.cookie = "__cf_bm=abc123";</script>
</body></html>
"""


def test_turnstile_page_is_challenge():
    assert SSRNScraper._page_looks_like_challenge(TURNSTILE_PAGE)


def test_cf_interstitial_is_challenge():
    assert SSRNScraper._page_looks_like_challenge(CF_INTERSTITIAL)


def test_normal_page_is_not_challenge():
    """Regression: data-cfasync / __cf_bm appear on every Cloudflare-served page
    and must not be treated as challenge markers."""
    assert not SSRNScraper._page_looks_like_challenge(NORMAL_SSRN_PAGE)


def test_empty_page_is_not_challenge():
    assert not SSRNScraper._page_looks_like_challenge("")
    assert not SSRNScraper._page_looks_like_challenge(None)


def test_block_errors_classified():
    assert SSRNScraper._is_block_error("Could not bypass Cloudflare challenge on search page")
    assert SSRNScraper._is_block_error("Cloudflare challenge appeared while waiting for results")
    assert SSRNScraper._is_block_error(
        "Browser unresponsive: ReadTimeoutError (suspected Cloudflare block)"
    )


def test_ordinary_failures_not_classified_as_block():
    assert not SSRNScraper._is_block_error("No search results found")
    assert not SSRNScraper._is_block_error("No results (timeout)")
    assert not SSRNScraper._is_block_error("No match above threshold 85. Best: 70.3")
    assert not SSRNScraper._is_block_error("Timeout waiting for page elements. Current URL: x")
    assert not SSRNScraper._is_block_error(None)


def _result(error_message, block_suspected):
    return {
        'doi': 'x',
        'ssrn_url': None,
        'abstract': None,
        'html_file_path': None,
        'match_score': None,
        'error_message': error_message,
        'success': False,
        'block_suspected': block_suspected,
    }


def _articles(n):
    return pd.DataFrame({'doi': [f"10.1/{i}" for i in range(n)], 'title': [f"T{i}" for i in range(n)]})


def test_block_error_skips_per_article_retries():
    scraper = SSRNScraper(repo=MagicMock(), crawl_delay=0, headless=False)
    with patch.object(
        scraper,
        'search_ssrn_and_extract_urls',
        return_value=(False, "Could not bypass Cloudflare challenge on search page", []),
    ) as search:
        result = scraper.scrape_article("10.1/x", "Some Title")
    assert result['block_suspected'] and not result['success']
    assert search.call_count == 1  # a flagged IP must not be hammered with retries


def test_circuit_breaker_aborts_after_consecutive_blocks():
    blocked = _result("Could not bypass Cloudflare challenge on search page", True)
    with patch.object(SSRNScraper, 'setup_webdriver'), \
         patch.object(SSRNScraper, 'scrape_article', return_value=blocked):
        scraper = SSRNScraper(repo=MagicMock(), crawl_delay=0, headless=False)
        stats = scraper.scrape_articles(_articles(10), show_progress=False)
    assert stats['aborted'] is True
    assert stats['failed'] == BLOCK_ABORT_THRESHOLD


def test_non_block_failure_resets_breaker_counter():
    blocked = _result("Could not bypass Cloudflare challenge on search page", True)
    ordinary = _result("No search results found", False)
    # Two blocks, an ordinary failure, two blocks: never 3 consecutive -> no abort
    sequence = [blocked, blocked, ordinary, blocked, blocked]
    with patch.object(SSRNScraper, 'setup_webdriver'), \
         patch.object(SSRNScraper, 'scrape_article', side_effect=sequence):
        scraper = SSRNScraper(repo=MagicMock(), crawl_delay=0, headless=False)
        stats = scraper.scrape_articles(_articles(5), show_progress=False)
    assert stats['aborted'] is False
    assert stats['failed'] == 5
