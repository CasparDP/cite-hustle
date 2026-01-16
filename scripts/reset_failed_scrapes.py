#!/usr/bin/env python
"""
Reset failed SSRN scrapes for retry.

This script deletes ssrn_pages entries for articles that failed with:
- "No search results found"
- "Failed to search SSRN"

After running this script, use `poetry run cite-hustle scrape` to retry these articles.

Usage:
    poetry run python scripts/reset_failed_scrapes.py [OPTIONS]

Options:
    --year-cutoff YEAR    Only reset articles from this year onwards (default: 2000)
    --dry-run             Show what would be deleted without making changes
    --include-low-match   Also include "No match above threshold" failures
"""

import argparse
import sys

from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager


def main():
    parser = argparse.ArgumentParser(
        description="Reset failed SSRN scrapes for retry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry run to see what would be reset
    poetry run python scripts/reset_failed_scrapes.py --dry-run

    # Reset failures from 2000 onwards (default)
    poetry run python scripts/reset_failed_scrapes.py

    # Reset failures from 2015 onwards
    poetry run python scripts/reset_failed_scrapes.py --year-cutoff 2015

    # Include low-match failures too
    poetry run python scripts/reset_failed_scrapes.py --include-low-match
        """,
    )
    parser.add_argument(
        "--year-cutoff",
        type=int,
        default=2000,
        help="Only reset articles from this year onwards (default: 2000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted without making changes"
    )
    parser.add_argument(
        "--include-low-match",
        action="store_true",
        help="Also include 'No match above threshold' failures",
    )

    args = parser.parse_args()

    # Connect to database
    print(f"📁 Database: {settings.db_path}")
    db = DatabaseManager(settings.db_path)
    db.connect()

    # Build error conditions
    error_conditions = [
        "s.error_message LIKE '%No search results%'",
        "s.error_message LIKE '%Failed to search SSRN%'",
    ]

    if args.include_low_match:
        error_conditions.append("s.error_message LIKE '%No match above threshold%'")

    error_where = " OR ".join(error_conditions)

    # Count articles to reset
    count_query = f"""
        SELECT COUNT(*)
        FROM ssrn_pages s
        JOIN articles a ON s.doi = a.doi
        WHERE ({error_where})
        AND a.year >= ?
    """

    total_count = db.conn.execute(count_query, [args.year_cutoff]).fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"🔄 RESET FAILED SCRAPES")
    print(f"{'=' * 60}")
    print(f"Year cutoff: {args.year_cutoff}+")
    print(f"Include low-match failures: {'Yes' if args.include_low_match else 'No'}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"{'=' * 60}")

    if total_count == 0:
        print("\n✓ No failed scrapes found matching criteria.")
        return 0

    print(f"\n📊 Found {total_count:,} articles to reset")

    # Show breakdown by year
    print("\n--- By Year ---")
    year_query = f"""
        SELECT a.year, COUNT(*) as count
        FROM ssrn_pages s
        JOIN articles a ON s.doi = a.doi
        WHERE ({error_where})
        AND a.year >= ?
        GROUP BY a.year
        ORDER BY a.year DESC
        LIMIT 10
    """
    years = db.conn.execute(year_query, [args.year_cutoff]).fetchall()
    for year, count in years:
        print(f"  {year}: {count:,}")
    if len(years) == 10:
        print("  ... (showing top 10 years)")

    # Show breakdown by error type
    print("\n--- By Error Type ---")

    # No search results
    no_results = db.conn.execute(
        f"""
        SELECT COUNT(*) FROM ssrn_pages s
        JOIN articles a ON s.doi = a.doi
        WHERE s.error_message LIKE '%No search results%'
        AND a.year >= ?
    """,
        [args.year_cutoff],
    ).fetchone()[0]
    print(f"  No search results: {no_results:,}")

    # Failed to search
    failed_search = db.conn.execute(
        f"""
        SELECT COUNT(*) FROM ssrn_pages s
        JOIN articles a ON s.doi = a.doi
        WHERE s.error_message LIKE '%Failed to search SSRN%'
        AND a.year >= ?
    """,
        [args.year_cutoff],
    ).fetchone()[0]
    print(f"  Failed to search SSRN: {failed_search:,}")

    if args.include_low_match:
        low_match = db.conn.execute(
            f"""
            SELECT COUNT(*) FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE s.error_message LIKE '%No match above threshold%'
            AND a.year >= ?
        """,
            [args.year_cutoff],
        ).fetchone()[0]
        print(f"  No match above threshold: {low_match:,}")

    # Show sample titles
    print("\n--- Sample Articles to Reset ---")
    sample_query = f"""
        SELECT a.title, a.year, a.journal_name
        FROM ssrn_pages s
        JOIN articles a ON s.doi = a.doi
        WHERE ({error_where})
        AND a.year >= ?
        ORDER BY a.year DESC
        LIMIT 5
    """
    samples = db.conn.execute(sample_query, [args.year_cutoff]).fetchall()
    for title, year, journal in samples:
        t = (title[:55] + "...") if len(title) > 55 else title
        print(f"  [{year}] {t}")

    if args.dry_run:
        print(f"\n{'=' * 60}")
        print("🔍 DRY RUN - No changes made")
        print(f"{'=' * 60}")
        print(f"\nTo actually reset these {total_count:,} entries, run:")
        print(
            f"  poetry run python scripts/reset_failed_scrapes.py --year-cutoff {args.year_cutoff}",
            end="",
        )
        if args.include_low_match:
            print(" --include-low-match", end="")
        print("\n")
        return 0

    # Confirm before deleting
    print(f"\n⚠️  This will DELETE {total_count:,} entries from ssrn_pages.")
    print("   They will be re-scraped when you run 'cite-hustle scrape'.")
    response = input("\nProceed? [y/N]: ").strip().lower()

    if response != "y":
        print("\n❌ Cancelled.")
        return 1

    # Delete the entries
    delete_query = f"""
        DELETE FROM ssrn_pages
        WHERE doi IN (
            SELECT s.doi
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE ({error_where})
            AND a.year >= ?
        )
    """

    print("\n🗑️  Deleting entries...")
    db.conn.execute(delete_query, [args.year_cutoff])

    # Verify deletion
    remaining = db.conn.execute(count_query, [args.year_cutoff]).fetchone()[0]
    deleted = total_count - remaining

    print(f"\n{'=' * 60}")
    print(f"✓ RESET COMPLETE")
    print(f"{'=' * 60}")
    print(f"Deleted: {deleted:,} entries")
    print(f"Remaining failures: {remaining:,}")

    # Show new pending count
    pending = db.conn.execute("""
        SELECT COUNT(*)
        FROM articles a
        LEFT JOIN ssrn_pages s ON a.doi = s.doi
        WHERE s.doi IS NULL
    """).fetchone()[0]
    print(f"\n📋 Total pending scrapes: {pending:,}")

    print(f"\nNext step:")
    print(f"  poetry run cite-hustle scrape --limit {min(deleted, 100)} --delay 5")
    print()

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
