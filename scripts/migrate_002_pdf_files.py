"""One-time backfill: populate pdf_files from ssrn_pages and the pdfs/ folder.

Two passes:
1. ssrn_pages rows with pdf_downloaded = TRUE. Stored paths may be
   machine-specific absolute paths from other machines; they are resolved by
   filename against the local pdfs/ folder and stored in portable $HOME form.
2. Orphaned PDFs on disk with no DB record (downloaded on a machine whose row
   was later lost). Matched back to articles by the DOI-slug filename; their
   ssrn_pages row is repaired too so the SSRN downloader does not re-fetch them.

All migrated rows get source='ssrn' and verify_status='pending' so the new
verify-pdfs stage picks them up.

Usage:
    poetry run python scripts/migrate_002_pdf_files.py [--dry-run]
"""

import argparse

from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.paths import expand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    db = DatabaseManager(settings.db_path)
    # Read-write even for --dry-run: the new tables must exist for the queries below.
    db.connect(read_only=False, max_wait=120)
    db.initialize_schema()
    repo = ArticleRepository(db)

    pdf_dir = settings.pdf_storage_dir
    migrated, repaired_paths, adopted, missing = 0, 0, 0, []
    handled_dois = set()

    # Pass 1: tracked downloads (paths possibly from other machines)
    rows = db.conn.execute(
        """
        SELECT s.doi, s.ssrn_url, s.pdf_file_path, s.match_score
        FROM ssrn_pages s
        LEFT JOIN pdf_files p ON s.doi = p.doi
        WHERE s.pdf_downloaded = TRUE
          AND s.pdf_file_path IS NOT NULL
          AND p.doi IS NULL
    """
    ).fetchall()

    for doi, ssrn_url, pdf_file_path, match_score in rows:
        local = expand(pdf_file_path)
        if not local.exists():
            candidate = pdf_dir / local.name
            if candidate.exists():
                local = candidate
                repaired_paths += 1
            else:
                missing.append((doi, pdf_file_path))
                continue
        if not args.dry_run:
            repo.upsert_pdf_file(
                doi=doi,
                source="ssrn",
                source_url=ssrn_url,
                pdf_url=None,
                pdf_file_path=str(local),
                match_score=match_score,
            )
            repo.update_pdf_info(doi=doi, pdf_url=None, pdf_file_path=str(local), downloaded=True)
        handled_dois.add(doi)
        migrated += 1

    # Pass 2: orphaned PDFs on disk with no pdf_files row
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        row = db.conn.execute(
            """
            SELECT a.doi, s.ssrn_url, s.match_score, p.doi IS NOT NULL AS tracked
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            LEFT JOIN pdf_files p ON a.doi = p.doi
            WHERE replace(a.doi, '/', '_') || '.pdf' = ?
        """,
            [pdf_path.name],
        ).fetchone()
        if row is None:
            missing.append((f"(no article for {pdf_path.name})", str(pdf_path)))
            continue
        doi, ssrn_url, match_score, tracked = row
        if tracked or doi in handled_dois:
            continue  # already recorded, or handled in pass 1
        if not args.dry_run:
            repo.upsert_pdf_file(
                doi=doi,
                source="ssrn",
                source_url=ssrn_url,
                pdf_url=None,
                pdf_file_path=str(pdf_path),
                match_score=match_score,
            )
            repo.update_pdf_info(
                doi=doi, pdf_url=None, pdf_file_path=str(pdf_path), downloaded=True
            )
        adopted += 1

    action = "Would migrate" if args.dry_run else "Migrated"
    print(f"✓ {action} {migrated} tracked PDFs ({repaired_paths} with repaired paths)")
    print(f"✓ {action.replace('migrate', 'adopt')} {adopted} orphaned PDFs from disk")
    if missing:
        print(f"⚠️  Skipped {len(missing)} entries:")
        for doi, path in missing:
            print(f"   {doi}: {path}")

    db.close()


if __name__ == "__main__":
    main()
