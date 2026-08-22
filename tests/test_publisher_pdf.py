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


def test_surfconext_credential_prompt_is_login():
    html = '<form><input type="password" name="pass"></form>'
    url = "https://engine.surfconext.nl/authentication/idp/single-sign-on/key:1?SAMLRequest=x"
    assert is_login_page(html, url)


def test_surfconext_account_chooser_is_not_login():
    html = "<html><body>Select an account to login</body></html>"
    url = "https://engine.surfconext.nl/authentication/idp/single-sign-on/key:1?SAMLRequest=x"
    assert not is_login_page(html, url)
