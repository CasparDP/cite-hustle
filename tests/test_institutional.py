"""InstitutionalDownloader behavior with a mocked Selenium driver."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cite_hustle.collectors.institutional import InstitutionalDownloader, SessionExpired

LOGIN_HTML = '<form><input type="password"></form>'
LOGIN_URL = "https://eur.idm.oclc.org/login?url=https://doi.org/10.1/x"
ARTICLE_URL = "https://www-sciencedirect-com.eur.idm.oclc.org/science/article/pii/S1"
ARTICLE_HTML = (
    '<meta name="citation_pdf_url" ' 'content="https://www.sciencedirect.com/pdf/S1-main.pdf">'
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


def _driver_writing(temp_dir: Path, name: str, payload: bytes) -> MagicMock:
    """Mock driver whose .get() drops a finished download into temp_dir."""
    driver = MagicMock()
    driver.get.side_effect = lambda url: (temp_dir / name).write_bytes(payload)
    return driver


def test_download_via_browser_moves_pdf_to_storage(tmp_path):
    d = make_downloader(tmp_path)
    d.driver = _driver_writing(d.temp_download_dir, "S1-main.pdf", b"%PDF-1.7\nbody")

    path = d._download_via_browser("https://host/pdf", "10.1/x")

    assert path == tmp_path / "10.1_x.pdf"
    assert path.read_bytes().startswith(b"%PDF-")
    assert list(d.temp_download_dir.glob("*")) == []


def test_download_via_browser_ignores_dotfiles(tmp_path):
    """A Dropbox/Finder .DS_Store must not be mistaken for the download.

    The dotfile is made the *newest* file, so without the dotfile filter the
    max-by-mtime pick would return it and fail the %PDF- check.
    """
    d = make_downloader(tmp_path)
    d.driver = MagicMock()

    def _get(url):
        pdf = d.temp_download_dir / "S1-main.pdf"
        pdf.write_bytes(b"%PDF-1.7\nbody")
        junk = d.temp_download_dir / ".DS_Store"
        junk.write_bytes(b"\x00\x01")
        newer = pdf.stat().st_mtime + 10
        os.utime(junk, (newer, newer))

    d.driver.get.side_effect = _get

    assert d._download_via_browser("https://host/pdf", "10.1/x") == tmp_path / "10.1_x.pdf"


def test_download_via_browser_clears_stale_partial_and_times_out(tmp_path):
    """A leftover .crdownload is swept, and an empty temp dir times out."""
    d = InstitutionalDownloader(
        storage_dir=tmp_path,
        profile_dir=tmp_path / "profile",
        ezproxy_prefix="https://eur.idm.oclc.org/login?url=",
        download_timeout=1,
    )
    d.driver = MagicMock()
    d.driver.get.side_effect = lambda url: None  # nothing ever lands
    stale = d.temp_download_dir / "foo.crdownload"
    stale.write_bytes(b"partial")

    with pytest.raises(RuntimeError, match="download_timeout"):
        d._download_via_browser("https://host/pdf", "10.1/x")

    assert not stale.exists()
    assert not (tmp_path / "10.1_x.pdf").exists()


def test_download_via_browser_rejects_non_pdf(tmp_path):
    d = make_downloader(tmp_path)
    d.driver = _driver_writing(d.temp_download_dir, "login.html", b"<html>Sign in</html>")

    with pytest.raises(RuntimeError, match="not_a_pdf"):
        d._download_via_browser("https://host/pdf", "10.1/x")

    assert not (tmp_path / "10.1_x.pdf").exists()
