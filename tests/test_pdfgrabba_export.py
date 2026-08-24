"""Safe one-way export into pdfgrabba's existing manifest."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cite_hustle.cli import commands
from cite_hustle.cli.commands import READ_ONLY_COMMANDS
from cite_hustle.pdfgrabba_export import (
    PdfgrabbaExportError,
    export_to_pdfgrabba,
    normalize_doi,
)


def residual(
    doi,
    *,
    title="A Useful Paper",
    authors="Smith, Alice; Jones, Bob",
    journal="Journal of Tests",
    year=2025,
):
    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "journal_name": journal,
        "year": year,
    }


def write_manifest(path: Path, entries) -> bytes:
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path.read_bytes()


def test_creation_requires_create_flag(tmp_path):
    path = tmp_path / "download_manifest.json"

    with pytest.raises(PdfgrabbaExportError, match="--create"):
        export_to_pdfgrabba(path, [])
    assert not path.exists()

    summary = export_to_pdfgrabba(path, [], create=True)
    assert summary.created is True
    assert summary.added == 0
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_existing_entries_all_fields_order_and_statuses_are_preserved(tmp_path):
    path = tmp_path / "download_manifest.json"
    existing = [
        {
            "bib_key": "done2020paper",
            "doi": "10.1016/DONE",
            "url": "custom://done",
            "title": "Original title",
            "authors": ["Original, Author"],
            "journal": "Original journal",
            "journal_abbrev": "OJ",
            "year": 2020,
            "target_filename": "custom-name.pdf",
            "status": "downloaded",
            "notes": "keep this exactly",
            "last_attempt": "2025-01-01T00:00:00",
        },
        {"bib_key": "skip", "doi": "10.1016/skip", "status": "skipped", "notes": 7},
        {"bib_key": "nodoi", "doi": None, "status": "no_doi", "custom": [1, 2]},
        {"bib_key": "failed", "doi": "10.1016/failed", "status": "failed"},
        {
            "bib_key": "manual",
            "doi": "10.1016/manual",
            "status": "skipped_manual",
        },
    ]
    write_manifest(path, existing)
    rows = [
        residual("10.1016/done"),
        residual("10.1016/SKIP"),
        residual("10.1016/failed"),
        residual("10.1016/manual"),
        residual("10.1016/new"),
    ]

    summary = export_to_pdfgrabba(path, rows)
    result = json.loads(path.read_text(encoding="utf-8"))

    assert result[: len(existing)] == existing
    assert [entry["status"] for entry in result[: len(existing)]] == [
        "downloaded",
        "skipped",
        "no_doi",
        "failed",
        "skipped_manual",
    ]
    assert summary.already_present == 4
    assert summary.added == 1
    assert result[-1]["status"] == "pending"


def test_doi_normalization_deduplicates_and_new_entry_has_all_fields(tmp_path):
    path = tmp_path / "download_manifest.json"
    existing = [{"bib_key": "taken", "doi": " DOI: 10.1016/ABC ", "status": "failed"}]
    write_manifest(path, existing)

    summary = export_to_pdfgrabba(
        path,
        [
            residual("https://doi.org/10.1016/abc"),
            residual(
                " HTTPS://DOI.ORG/10.1016/NEW ",
                title="The New Finding",
                authors="Doe, Jane; Roe, Richard",
            ),
        ],
    )
    result = json.loads(path.read_text(encoding="utf-8"))
    new_entry = result[-1]

    assert normalize_doi(" doi: HTTPS://doi.org/10.1016/ABC ") == "10.1016/abc"
    assert summary.already_present == 1
    assert list(new_entry) == [
        "bib_key",
        "doi",
        "url",
        "title",
        "authors",
        "journal",
        "journal_abbrev",
        "year",
        "target_filename",
        "status",
        "notes",
    ]
    assert new_entry["doi"] == "10.1016/new"
    assert new_entry["url"] == "https://doi.org/10.1016/new"
    assert new_entry["authors"] == ["Doe, Jane", "Roe, Richard"]
    assert new_entry["target_filename"] == "10.1016_new.pdf"
    assert new_entry["bib_key"] == "doe2025new"


def test_append_order_is_deterministic_and_existing_bib_keys_are_taken(tmp_path):
    path = tmp_path / "download_manifest.json"
    write_manifest(path, [{"bib_key": "smith2025useful", "doi": None, "status": "no_doi"}])

    export_to_pdfgrabba(
        path,
        [residual("10.1016/z-last"), residual("10.1016/a-first")],
    )
    result = json.loads(path.read_text(encoding="utf-8"))

    assert [entry["doi"] for entry in result[1:]] == ["10.1016/a-first", "10.1016/z-last"]
    assert [entry["bib_key"] for entry in result[1:]] == [
        "smith2025usefulb",
        "smith2025usefulc",
    ]


def test_second_identical_run_adds_zero_and_does_not_rewrite(tmp_path):
    path = tmp_path / "download_manifest.json"
    write_manifest(path, [])
    rows = [residual("10.1016/idempotent")]

    first = export_to_pdfgrabba(path, rows)
    after_first = path.read_bytes()
    second = export_to_pdfgrabba(path, rows)

    assert first.added == 1
    assert second.added == 0
    assert second.already_present == 1
    assert path.read_bytes() == after_first


def test_dry_run_performs_no_writes_for_existing_or_creatable_manifest(tmp_path):
    existing_path = tmp_path / "download_manifest.json"
    original = write_manifest(existing_path, [])

    summary = export_to_pdfgrabba(existing_path, [residual("10.1016/dry")], dry_run=True)
    missing_path = tmp_path / "new_manifest.json"
    create_summary = export_to_pdfgrabba(
        missing_path, [residual("10.1016/create-dry")], create=True, dry_run=True
    )

    assert summary.added == 1
    assert create_summary.added == 1
    assert existing_path.read_bytes() == original
    assert not missing_path.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("content", ["not json", '{"root": "object"}', "[1]"])
def test_invalid_manifests_fail_without_alteration(tmp_path, content):
    path = tmp_path / "download_manifest.json"
    path.write_text(content, encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises(PdfgrabbaExportError):
        export_to_pdfgrabba(path, [residual("10.1016/new")])

    assert path.read_bytes() == original


def test_duplicate_normalized_manifest_dois_fail_without_alteration(tmp_path):
    path = tmp_path / "download_manifest.json"
    original = write_manifest(
        path,
        [
            {"bib_key": "one", "doi": "10.1016/dup", "status": "downloaded"},
            {"bib_key": "two", "doi": "https://doi.org/10.1016/DUP", "status": "failed"},
        ],
    )

    with pytest.raises(PdfgrabbaExportError, match="Ambiguous duplicate"):
        export_to_pdfgrabba(path, [])

    assert path.read_bytes() == original


def test_missing_parent_directory_fails_even_with_create(tmp_path):
    path = tmp_path / "absent" / "download_manifest.json"

    with pytest.raises(PdfgrabbaExportError, match="parent directory"):
        export_to_pdfgrabba(path, [], create=True)

    assert not path.parent.exists()


def test_atomic_replace_is_used_in_manifest_directory(tmp_path, monkeypatch):
    path = tmp_path / "download_manifest.json"
    write_manifest(path, [])
    calls = []
    from cite_hustle import pdfgrabba_export

    real_replace = pdfgrabba_export.os.replace

    def recording_replace(source, destination):
        source = Path(source)
        calls.append((source, Path(destination), source.exists()))
        return real_replace(source, destination)

    monkeypatch.setattr(pdfgrabba_export.os, "replace", recording_replace)
    export_to_pdfgrabba(path, [residual("10.1016/atomic")])

    assert len(calls) == 1
    assert calls[0][0].parent == path.parent
    assert calls[0][1] == path
    assert calls[0][2] is True
    assert not calls[0][0].exists()


def test_failed_atomic_replace_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "download_manifest.json"
    original = write_manifest(path, [{"bib_key": "old", "doi": None, "status": "no_doi"}])
    from cite_hustle import pdfgrabba_export

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(pdfgrabba_export.os, "replace", fail_replace)
    with pytest.raises(PdfgrabbaExportError, match="atomically replace"):
        export_to_pdfgrabba(path, [residual("10.1016/atomic-fail")])

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".download_manifest.json.*.tmp"))


def test_export_command_is_registered_and_opens_duckdb_read_only(tmp_path, monkeypatch):
    manifest_path = tmp_path / "download_manifest.json"
    write_manifest(manifest_path, [])
    connections = []

    class FakeDatabaseManager:
        def __init__(self, db_path):
            self.db_path = db_path

        def connect(self, read_only=False, max_wait=0):
            connections.append((read_only, max_wait))

    class EmptyResiduals:
        def to_dict(self, orient):
            assert orient == "records"
            return []

    class FakeRepository:
        def __init__(self, db):
            self.db = db

        def get_terminal_elsevier_residuals(self, limit=None):
            assert limit is None
            return EmptyResiduals()

    monkeypatch.setattr(commands, "DatabaseManager", FakeDatabaseManager)
    monkeypatch.setattr(commands, "ArticleRepository", FakeRepository)

    result = CliRunner().invoke(
        commands.main,
        ["export-pdfgrabba", "--manifest", str(manifest_path), "--dry-run"],
    )

    assert "export-pdfgrabba" in READ_ONLY_COMMANDS
    assert result.exit_code == 0, result.output
    assert connections == [(True, 0)]
