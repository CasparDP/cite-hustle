"""Return completed pdfgrabba downloads to cite-hustle's PDF pipeline."""

import json

import pytest

from cite_hustle.cli.commands import READ_ONLY_COMMANDS
from cite_hustle.pdfgrabba_import import PdfgrabbaImportError, import_from_pdfgrabba
from cite_hustle.paths import to_portable
from conftest import add_article


def write_manifest(path, entries):
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path.read_bytes()


def entry(doi, target, status="downloaded", url=None):
    return {
        "bib_key": str(doi or "no_doi").replace("/", "_"),
        "doi": doi,
        "url": url or (f"https://doi.org/{doi}" if doi else ""),
        "title": "Paper",
        "authors": ["Smith, Alice"],
        "journal": "Journal",
        "journal_abbrev": "J",
        "year": 2025,
        "target_filename": target,
        "status": status,
        "notes": "preserve",
    }


def valid_pdf(path):
    path.write_bytes(b"%PDF-1.7\nreturn-path-test")


def test_imports_downloaded_and_skipped_files_without_touching_manifest(repo, tmp_path):
    for doi in (
        "10.1/done",
        "10.1/skipped",
        "10.1/pending",
        "10.1/missing",
        "10.1/no-target",
        "10.1/bad",
        "10.1/existing",
    ):
        add_article(repo, doi)

    valid_pdf(tmp_path / "done.pdf")
    valid_pdf(tmp_path / "skipped.pdf")
    valid_pdf(tmp_path / "unknown.pdf")
    valid_pdf(tmp_path / "existing.pdf")
    (tmp_path / "bad.pdf").write_text("not a pdf", encoding="utf-8")
    repo.upsert_pdf_file("10.1/existing", "oa", None, None, str(tmp_path / "existing.pdf"))
    repo.set_pdf_verification("10.1/existing", "match", method="deterministic")

    manifest_path = tmp_path / "download_manifest.json"
    original = write_manifest(
        manifest_path,
        [
            entry("10.1/done", "done.pdf", url="custom://doi-page"),
            entry("10.1/skipped", "skipped.pdf", status="skipped"),
            entry("10.1/pending", "pending.pdf", status="pending"),
            entry("10.1/missing", "missing.pdf"),
            entry("10.1/no-target", None),
            entry("10.1/bad", "bad.pdf"),
            entry("10.1/unknown", "unknown.pdf"),
            entry("10.1/existing", "existing.pdf"),
            entry(None, "no-doi.pdf"),
        ],
    )

    summary = import_from_pdfgrabba(repo, manifest_path)

    assert summary.manifest_entries == 9
    assert summary.terminal_entries == 8
    assert summary.ignored_status == 1
    assert summary.already_present == 1
    assert summary.empty_doi == 1
    assert summary.unresolved_doi == 1
    assert summary.missing_file == 2
    assert summary.invalid_pdf == 1
    assert summary.ready == summary.selected == summary.imported == 2
    assert manifest_path.read_bytes() == original

    done = repo.conn.execute(
        "SELECT source, source_url, pdf_file_path, verify_status FROM pdf_files WHERE doi = ?",
        ["10.1/done"],
    ).fetchone()
    assert done == (
        "pdfgrabba",
        "custom://doi-page",
        to_portable(tmp_path / "done.pdf"),
        "pending",
    )
    existing = repo.conn.execute(
        "SELECT source, verify_status FROM pdf_files WHERE doi = '10.1/existing'"
    ).fetchone()
    assert existing == ("oa", "match")

    second = import_from_pdfgrabba(repo, manifest_path)
    assert second.imported == 0
    assert second.already_present == 3


def test_dry_run_is_a_true_no_write(repo, tmp_path):
    doi = "10.1/dry"
    add_article(repo, doi)
    valid_pdf(tmp_path / "dry.pdf")
    manifest_path = tmp_path / "download_manifest.json"
    original = write_manifest(manifest_path, [entry(doi, "dry.pdf")])

    summary = import_from_pdfgrabba(repo, manifest_path, dry_run=True)

    assert summary.ready == summary.selected == 1
    assert summary.imported == 0
    assert repo.get_pdf_file_by_doi(doi) is None
    assert manifest_path.read_bytes() == original


def test_limit_selects_ready_files_in_normalized_doi_order(repo, tmp_path):
    for doi in ("10.1/z", "10.1/a"):
        add_article(repo, doi)
        valid_pdf(tmp_path / f"{doi[-1]}.pdf")
    manifest_path = tmp_path / "download_manifest.json"
    write_manifest(
        manifest_path,
        [entry("10.1/z", "z.pdf"), entry("10.1/a", "a.pdf")],
    )

    summary = import_from_pdfgrabba(repo, manifest_path, limit=1)

    assert summary.ready == 2
    assert summary.selected == summary.imported == 1
    assert repo.get_pdf_file_by_doi("10.1/a") is not None
    assert repo.get_pdf_file_by_doi("10.1/z") is None


def test_import_resolves_normalized_manifest_doi_to_article_primary_key(repo, tmp_path):
    article_doi = " DOI: https://doi.org/10.1016/ABC "
    add_article(repo, article_doi)
    valid_pdf(tmp_path / "wrapped.pdf")
    manifest_path = tmp_path / "download_manifest.json"
    write_manifest(manifest_path, [entry("https://doi.org/10.1016/abc", "wrapped.pdf")])

    summary = import_from_pdfgrabba(repo, manifest_path)

    assert summary.imported == 1
    assert repo.get_pdf_file_by_doi(article_doi)["source"] == "pdfgrabba"


@pytest.mark.parametrize("content", ["not json", '{"root": true}', "[1]"])
def test_invalid_manifest_fails_before_database_write(repo, tmp_path, content):
    manifest_path = tmp_path / "download_manifest.json"
    manifest_path.write_text(content, encoding="utf-8")

    with pytest.raises(PdfgrabbaImportError):
        import_from_pdfgrabba(repo, manifest_path)

    assert repo.conn.execute("SELECT COUNT(*) FROM pdf_files").fetchone()[0] == 0


def test_duplicate_normalized_dois_fail_before_database_write(repo, tmp_path):
    add_article(repo, "10.1/dup")
    valid_pdf(tmp_path / "one.pdf")
    valid_pdf(tmp_path / "two.pdf")
    manifest_path = tmp_path / "download_manifest.json"
    write_manifest(
        manifest_path,
        [
            entry("10.1/dup", "one.pdf"),
            entry("DOI: 10.1/DUP", "two.pdf"),
        ],
    )

    with pytest.raises(PdfgrabbaImportError, match="Ambiguous duplicate"):
        import_from_pdfgrabba(repo, manifest_path)

    assert repo.get_pdf_file_by_doi("10.1/dup") is None


@pytest.mark.parametrize("target", ["../escape.pdf", "/tmp/absolute.pdf", "nested/file.pdf"])
def test_unsafe_target_filename_fails_before_database_write(repo, tmp_path, target):
    doi = "10.1/unsafe"
    add_article(repo, doi)
    manifest_path = tmp_path / "download_manifest.json"
    write_manifest(manifest_path, [entry(doi, target)])

    with pytest.raises(PdfgrabbaImportError, match="Unsafe"):
        import_from_pdfgrabba(repo, manifest_path)

    assert repo.get_pdf_file_by_doi(doi) is None


def test_import_command_is_a_duckdb_writer():
    assert "import-pdfgrabba" not in READ_ONLY_COMMANDS
