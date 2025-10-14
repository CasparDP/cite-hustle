"""Cleanup SSRN HTML files that are Cloudflare "are you human" pages (default ~20,800 bytes).

This script:
- Removes HTML artifacts in ``$HOME/Dropbox/Github Data/cite-hustle/ssrn_html`` that match a target size (default around 20,800 bytes with tolerance)
- Deletes matching rows from DuckDB table ``ssrn_pages`` so the DOIs will be scraped again
- Prints a summary and uses portable ``$HOME``-prefixed paths for logging

Usage::

    poetry run python scripts/cleanup_bad_ssrn_html.py [--size-bytes 20833] [--tolerance-bytes 500]
"""

import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cite_hustle.database.models import DatabaseManager
HOME = Path.home()
BASE_DIR = HOME / "Dropbox" / "Github Data" / "cite-hustle"
HTML_DIR = BASE_DIR / "ssrn_html"
DB_PATH = BASE_DIR / "DB" / "articles.duckdb"

# Default size for Cloudflare "are you human" pages (around 20,800-20,900 bytes)
DEFAULT_SIZE_BYTES = 20833
DEFAULT_TOLERANCE_BYTES = 500


def to_portable(path: Path) -> str:
    """Return a `$HOME`-prefixed string for logging or DB storage."""
    try:
        return f"$HOME/{path.relative_to(HOME)}"
    except ValueError:
        return str(path)


def portable_to_absolute(portable_path: Optional[str]) -> Optional[Path]:
    """Convert a portable $HOME-prefixed path to an absolute Path."""
    if not portable_path:
        return None
    if portable_path.startswith("$HOME/"):
        return HOME / portable_path[len("$HOME/") :]
    if portable_path.startswith("~/"):
        return HOME / portable_path[2:]
    return Path(portable_path)


def find_files_by_size(directory: Path, target_size: int, tolerance: int) -> List[Path]:
    files: List[Path] = []
    all_files = list(directory.glob("*.html"))
    print(f"\nDebug: Found {len(all_files)} HTML files in {directory}")
    
    for p in all_files:
        try:
            if p.is_file():
                size = p.stat().st_size
                in_range = target_size - tolerance <= size <= target_size + tolerance
                print(f"  {p.name}: {size} bytes {'✓' if in_range else '✗'}")
                if in_range:
                    files.append(p)
        except FileNotFoundError:
            print(f"  {p.name}: FILE NOT FOUND")
            continue
    
    print(f"Debug: {len(files)} files match size criteria")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove SSRN HTML files of a specific size.")
    parser.add_argument(
        "--size-bytes",
        type=int,
        default=DEFAULT_SIZE_BYTES,
        help="Target file size in bytes (default: 20833)",
    )
    parser.add_argument(
        "--tolerance-bytes",
        type=int,
        default=DEFAULT_TOLERANCE_BYTES,
        help="Allowed +/- byte tolerance when matching size (default: 500)",
    )
    args = parser.parse_args()

    target_size = max(0, args.size_bytes)
    tolerance = max(0, args.tolerance_bytes)

    print(f"HTML directory: {to_portable(HTML_DIR)}")
    print(f"Database path: {to_portable(DB_PATH)}")
    print(
        f"Target size: {target_size} bytes (~{target_size/1024:.1f} KiB)"
        + (f" with +/-{tolerance} bytes tolerance" if tolerance else "")
    )

    db = DatabaseManager(DB_PATH)
    conn = db.connect()

    try:
        rows = conn.execute(
            "SELECT doi, html_file_path FROM ssrn_pages WHERE html_file_path IS NOT NULL"
        ).fetchall()
        
        print(f"\nDebug: Found {len(rows)} DB records with HTML paths")

        doi_to_abs: Dict[str, Path] = {}
        for doi, portable in rows:
            abs_path = portable_to_absolute(portable)
            if abs_path:
                doi_to_abs[str(doi)] = abs_path
                # Show a few examples
                if len(doi_to_abs) <= 3:
                    print(f"  {doi}: {portable} -> {abs_path}")
        
        if len(rows) > 3:
            print(f"  ... and {len(rows) - 3} more records")

        to_delete_db: List[Tuple[str, Path]] = []
        print(f"\nDebug: Checking DB-referenced files for size match...")
        
        for doi, path in doi_to_abs.items():
            try:
                if path.exists():
                    size = path.stat().st_size
                    in_range = target_size - tolerance <= size <= target_size + tolerance
                    if len(to_delete_db) < 5:  # Show first few for debugging
                        print(f"  {doi}: {size} bytes at {to_portable(path)} {'✓' if in_range else '✗'}")
                    if in_range:
                        to_delete_db.append((doi, path))
                else:
                    if len(to_delete_db) < 5:
                        print(f"  {doi}: FILE MISSING at {to_portable(path)}")
            except FileNotFoundError:
                continue
        
        print(f"Debug: {len(to_delete_db)} DB records match size criteria")

        dir_matches = set(find_files_by_size(HTML_DIR, target_size, tolerance))
        db_match_files = {p for _, p in to_delete_db}
        orphan_files = dir_matches - db_match_files

        deleted_db = 0
        deleted_files = 0
        deleted_dois: List[str] = []

        for doi, path in to_delete_db:
            conn.execute("DELETE FROM ssrn_pages WHERE doi = ?", [doi])
            deleted_db += 1
            deleted_dois.append(doi)
            try:
                if path.exists():
                    path.unlink()
                    deleted_files += 1
            except Exception as exc:  # pragma: no cover
                print(f"  Warning: could not delete file {to_portable(path)}: {exc}")

        for opath in orphan_files:
            try:
                opath.unlink()
                deleted_files += 1
            except Exception as exc:  # pragma: no cover
                print(f"  Warning: could not delete orphan file {to_portable(opath)}: {exc}")

        print("\nCleanup complete:")
        print(f"  DB rows removed: {deleted_db}")
        print(f"  HTML files deleted: {deleted_files}")
        if deleted_dois:
            print("  DOIs reset:")
            for doi in deleted_dois:
                print(f"    - {doi}")
        else:
            print("  No matching entries were found for the selected size.")
        print("  Note: deleted entries will show up as pending for SSRN scraping.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
