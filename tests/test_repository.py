"""Repository methods for per-DOI acquisition."""

from datetime import datetime, timedelta

import pytest

from conftest import add_article


def test_get_article_by_doi_returns_row(repo):
    add_article(repo, "10.1111/test.1")
    row = repo.get_article_by_doi("10.1111/test.1")
    assert row["doi"] == "10.1111/test.1"
    assert row["title"] == "Some Paper Title"
    assert row["year"] == 2024


def test_get_article_by_doi_missing_returns_none(repo):
    assert repo.get_article_by_doi("10.9999/nope") is None


def test_resolve_article_doi_handles_repeated_wrappers_and_rejects_ambiguity(repo):
    wrapped = " DOI: https://doi.org/10.1016/ABC "
    add_article(repo, wrapped)
    assert repo.resolve_article_doi("10.1016/abc") == wrapped

    add_article(repo, "10.1016/abc")
    with pytest.raises(ValueError, match="Ambiguous normalized DOI"):
        repo.resolve_article_doi("10.1016/abc")


def test_recheck_windows_distinguish_error_from_no_match(repo):
    add_article(repo, "10.1/a")
    add_article(repo, "10.1/b")
    repo.record_pdf_candidate("10.1/a", "oa", status="no_match")
    repo.record_pdf_candidate("10.1/b", "oa", status="error", error_message="http_503")

    long_cutoff = datetime.now() - timedelta(days=90)
    # error rows use a cutoff in the future -> nothing is "recent" for errors
    future = datetime.now() + timedelta(days=1)

    suppressed = repo.get_recent_candidate_checks(long_cutoff, future)
    assert ("10.1/a", "oa") in suppressed  # no_match still memoized
    assert ("10.1/b", "oa") not in suppressed  # error eligible for retry

    both_recent = repo.get_recent_candidate_checks(long_cutoff, long_cutoff)
    assert ("10.1/b", "oa") in both_recent


def test_institutional_feeder_requires_fallbacks_tried(repo):
    add_article(repo, "10.1/tried")  # fallbacks tried, no PDF -> eligible
    add_article(repo, "10.1/untried")  # fallbacks never tried -> not eligible
    add_article(repo, "10.1/haspdf")  # already has a PDF -> not eligible
    repo.record_pdf_candidate("10.1/tried", "oa", status="no_match")
    repo.record_pdf_candidate("10.1/haspdf", "oa", status="downloaded")
    repo.upsert_pdf_file("10.1/haspdf", "oa", None, "http://x/y.pdf", "/tmp/y.pdf")

    dois = set(repo.get_articles_for_institutional()["doi"])
    assert dois == {"10.1/tried"}


def test_get_pdf_file_by_doi(repo):
    add_article(repo, "10.1/pdf")
    repo.upsert_pdf_file("10.1/pdf", "oa", None, "http://x/y.pdf", "/tmp/y.pdf")
    assert repo.get_pdf_file_by_doi("10.1/pdf")["source"] == "oa"


def test_insert_pdf_file_if_absent_never_replaces_existing_state(repo):
    doi = "10.1/insert-only"
    add_article(repo, doi)

    assert repo.insert_pdf_file_if_absent(
        doi, "pdfgrabba", "https://doi.org/10.1/insert-only", None, "/tmp/new.pdf"
    )
    repo.set_pdf_verification(doi, "match", method="deterministic")

    assert not repo.insert_pdf_file_if_absent(
        doi, "pdfgrabba", "https://doi.org/10.1/insert-only", None, "/tmp/replacement.pdf"
    )
    row = repo.conn.execute(
        "SELECT source, pdf_file_path, verify_status FROM pdf_files WHERE doi = ?", [doi]
    ).fetchone()
    assert row == ("pdfgrabba", "/tmp/new.pdf", "match")


def add_terminal_residual(repo, doi, publisher="Elsevier"):
    repo.insert_article(
        doi,
        "Residual Paper",
        "Smith, Alice; Jones, Bob",
        2025,
        "0000-0000",
        "Journal of Residuals",
        publisher,
    )
    for source in ("oa", "nber", "arxiv"):
        repo.record_pdf_candidate(doi, source, status="no_match")


def terminal_residual_dois(repo):
    return set(repo.get_terminal_elsevier_residuals()["doi"])


def test_terminal_elsevier_residual_with_unavailable_ssrn_is_eligible(repo):
    doi = "10.9999/terminal"
    add_terminal_residual(repo, doi)
    repo.insert_ssrn_page(doi, "https://ssrn.com/abstract=1", None, None, None, 95)
    repo.mark_pdf_unavailable(doi)

    rows = repo.get_terminal_elsevier_residuals()

    assert rows.to_dict("records") == [
        {
            "doi": doi,
            "title": "Residual Paper",
            "authors": "Smith, Alice; Jones, Bob",
            "year": 2025,
            "journal_name": "Journal of Residuals",
        }
    ]


def test_terminal_elsevier_residual_detected_by_publisher(repo):
    doi = "10.9999/publisher"
    add_terminal_residual(repo, doi, publisher="Published by ELSEVIER B.V.")

    assert terminal_residual_dois(repo) == {doi}


def test_terminal_elsevier_residual_detected_by_normalized_doi_prefix(repo):
    doi = " DOI: https://doi.org/10.1016/ABC.123 "
    add_terminal_residual(repo, doi, publisher="Another Publisher")

    assert terminal_residual_dois(repo) == {doi}


def test_terminal_elsevier_residual_excludes_existing_pdf_file(repo):
    doi = "10.1016/has-pdf"
    add_terminal_residual(repo, doi)
    repo.upsert_pdf_file(doi, "manual", None, None, "/tmp/local.pdf")

    assert terminal_residual_dois(repo) == set()


def test_terminal_elsevier_residual_excludes_live_ssrn_without_unavailable_log(repo):
    doi = "10.1016/live-ssrn"
    add_terminal_residual(repo, doi)
    repo.insert_ssrn_page(doi, "https://ssrn.com/abstract=2", None, None, None, 95)

    assert terminal_residual_dois(repo) == set()


@pytest.mark.parametrize("missing_source", ["oa", "nber", "arxiv"])
def test_terminal_elsevier_residual_excludes_missing_fallback_row(repo, missing_source):
    doi = f"10.1016/missing-{missing_source}"
    repo.insert_article(
        doi,
        "Missing Fallback",
        "Smith, Alice",
        2025,
        "0000-0000",
        "Test Journal",
        "Elsevier",
    )
    for source in {"oa", "nber", "arxiv"} - {missing_source}:
        repo.record_pdf_candidate(doi, source, status="no_match")

    assert terminal_residual_dois(repo) == set()


@pytest.mark.parametrize("error_source", ["oa", "nber", "arxiv"])
def test_terminal_elsevier_residual_excludes_any_fallback_error(repo, error_source):
    doi = f"10.1016/error-{error_source}"
    add_terminal_residual(repo, doi)
    repo.record_pdf_candidate(doi, error_source, status="error", error_message="retry me")

    assert terminal_residual_dois(repo) == set()


def test_terminal_elsevier_residual_excludes_non_elsevier_paper(repo):
    doi = "10.9999/not-elsevier"
    add_terminal_residual(repo, doi, publisher="Wiley")

    assert terminal_residual_dois(repo) == set()
