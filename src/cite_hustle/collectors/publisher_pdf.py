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
    lower = html.lower()
    has_credentials_form = 'type="password"' in lower or "type='password'" in lower
    # A real credential prompt on the SSO host (SURFconext/ADFS) means the ERNA
    # session is gone. The SURFconext account *chooser* (no password field)
    # auto-continues from the persisted session and must NOT be flagged.
    if "surfconext" in netloc and has_credentials_form:
        return True
    on_login_host = EZPROXY_DOMAIN_MARKER in netloc and not netloc.split(".", 1)[0].count("-")
    return on_login_host and (has_credentials_form or "surfconext" in lower)
