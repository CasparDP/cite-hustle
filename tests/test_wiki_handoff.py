"""Split mode: the runner hands papers to a worker and records the pages that come back."""

import json

from cite_hustle.cli.commands import NO_DB_COMMANDS
from cite_hustle.wiki.bridge import MANIFEST_NAME, WikiBridge
from conftest import add_article


def _verified_pdf(repo, doi, title):
    add_article(repo, doi, title=title)
    repo.upsert_pdf_file(doi, "ssrn", None, None, f"/tmp/{doi.replace('/', '_')}.pdf")
    repo.set_pdf_verification(doi, "match")


def _bridge(repo, tmp_path):
    return WikiBridge(
        repo=repo,
        wiki_dir=tmp_path / "wiki",
        pdf_dir=tmp_path / "pdfs",
        process_paper_dir=tmp_path / "pp",
        analyst_model="a",
        verifier_model="v",
    )


def _manifest_dois(tmp_path):
    rows = json.loads((tmp_path / "wiki" / MANIFEST_NAME).read_text())
    return {row["doi"] for row in rows}


def test_handoff_keeps_unprocessed_papers_pending(repo, tmp_path):
    _verified_pdf(repo, "10.1/a", "Audit Fees and Quality")
    _verified_pdf(repo, "10.1/b", "Bank Loans and Disclosure")
    bridge = _bridge(repo, tmp_path)

    outcomes, n_handed = bridge.handoff(repo.get_verified_pdfs_not_ingested())
    assert outcomes == {} and n_handed == 2
    assert _manifest_dois(tmp_path) == {"10.1/a", "10.1/b"}
    assert repo.get_wiki_page_by_doi("10.1/a")["status"] == "pending"

    # The worker writes one page; Dropbox brings it back to the runner
    key_a = repo.get_wiki_page_by_doi("10.1/a")["bib_key"]
    (bridge.sources_dir / f"{key_a}.md").write_text("---\nbib_key: x\n---\nbody\n")

    outcomes, n_handed = bridge.handoff(repo.get_verified_pdfs_not_ingested())
    assert outcomes == {"10.1/a": "ingested"} and n_handed == 1
    assert _manifest_dois(tmp_path) == {"10.1/b"}
    assert repo.get_wiki_page_by_doi("10.1/b")["status"] == "pending"


def test_handoff_reuses_stored_bib_keys(repo, tmp_path):
    _verified_pdf(repo, "10.1/a", "Audit Fees and Quality")
    bridge = _bridge(repo, tmp_path)
    bridge.handoff(repo.get_verified_pdfs_not_ingested())
    first = repo.get_wiki_page_by_doi("10.1/a")["bib_key"]

    bridge.handoff(repo.get_verified_pdfs_not_ingested())
    assert repo.get_wiki_page_by_doi("10.1/a")["bib_key"] == first


def test_wiki_work_never_opens_the_database():
    assert "wiki-work" in NO_DB_COMMANDS
