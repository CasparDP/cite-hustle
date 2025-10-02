"""
Clean up non-article content from the database

This script removes book reviews, front matter, covers, and other
non-article content that may have been collected before filtering was added.

Run: poetry run python scripts/cleanup_non_articles.py
"""
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


# Updated keywords - more specific to avoid false positives
NON_ARTICLE_KEYWORDS = [
    'front matter', 'back matter',
    'cover', 'covers',
    'book review', 'books received',
    'editorial board', 'editorial note',
    'erratum', 'corrigendum', 'correction',
    'retraction',
    'index to volume', 'subject index', 'author index',
    'table of contents',
    'masthead', 'issue information',
    'title pages', 'copyright page'
]


def cleanup_non_articles():
    """Remove non-article content from the database"""
    
    print("="*60)
    print("CLEANING UP NON-ARTICLE CONTENT")
    print("="*60)
    
    # Connect to database
    print(f"\nConnecting to: {settings.db_path}")
    db = DatabaseManager(settings.db_path)
    db.connect()
    repo = ArticleRepository(db)
    
    # Check total before cleanup
    total_before = repo.get_article_count()
    print(f"Articles before cleanup: {total_before:,}")
    
    # Find non-articles
    print("\nSearching for non-article content...")
    
    # Build SQL query to find matching titles
    conditions = []
    for keyword in NON_ARTICLE_KEYWORDS:
        conditions.append(f"LOWER(title) LIKE '%{keyword}%'")
    
    # Add pattern matches for titles that are JUST announcements/editorial
    conditions.append("LOWER(title) = 'announcements'")
    conditions.append("LOWER(title) = 'editorial'")
    conditions.append("LOWER(title) LIKE 'announcements and%'")
    conditions.append("LOWER(title) LIKE 'volume %'")
    conditions.append("LOWER(title) LIKE 'issue %'")
    conditions.append("LOWER(title) LIKE 'contents%'")
    
    where_clause = " OR ".join(conditions)
    
    # Find matching articles
    query = f"""
        SELECT doi, title, year, journal_name
        FROM articles
        WHERE {where_clause}
        ORDER BY year DESC, title
    """
    
    non_articles = db.conn.execute(query).fetchdf()
    
    if non_articles.empty:
        print("\n✓ No non-article content found!")
        print("Your database is clean.")
        return
    
    print(f"\nFound {len(non_articles)} potential non-article items:")
    print("\nShowing first 20 items (review carefully):\n")
    
    # Show sample
    for idx, row in non_articles.head(20).iterrows():
        print(f"  {idx+1}. {row['title'][:80]}...")
        print(f"     {row['journal_name']} ({row['year']})")
        print()
    
    if len(non_articles) > 20:
        print(f"  ... and {len(non_articles) - 20} more")
    
    # Confirm deletion
    print(f"\n{'='*60}")
    print("\n⚠️  IMPORTANT: Review the list above carefully!")
    print("Make sure these are NOT legitimate research articles.")
    print("(e.g., 'Earnings Announcements' articles should NOT be deleted)")
    print()
    response = input(f"Delete these {len(non_articles)} items? (yes/no): ")
    
    if response.lower() != 'yes':
        print("\nCleanup cancelled.")
        print("No items were deleted.")
        return
    
    # Delete non-articles
    print("\nDeleting non-article content...")
    
    dois_to_delete = non_articles['doi'].tolist()
    
    # Delete from ssrn_pages first (foreign key constraint)
    for doi in dois_to_delete:
        db.conn.execute("DELETE FROM ssrn_pages WHERE doi = ?", [doi])
    
    # Delete from articles
    for doi in dois_to_delete:
        db.conn.execute("DELETE FROM articles WHERE doi = ?", [doi])
    
    # Check total after cleanup
    total_after = repo.get_article_count()
    deleted = total_before - total_after
    
    print(f"\n✓ Cleanup complete!")
    print(f"  Before: {total_before:,} items")
    print(f"  After: {total_after:,} items")
    print(f"  Deleted: {deleted:,} non-articles")
    
    # Rebuild FTS indexes
    print("\nRebuilding search indexes...")
    try:
        db.create_fts_indexes()
        print("✓ Search indexes rebuilt")
    except Exception as e:
        print(f"⚠️  Warning: Failed to rebuild indexes: {e}")
        print("   Run: poetry run cite-hustle rebuild-fts")
    
    print("\n" + "="*60)
    print("✓ DATABASE CLEANED")
    print("="*60)
    print("\nYour database now contains only research articles.")
    print("Future collections will automatically filter non-articles.\n")
    
    db.close()


if __name__ == "__main__":
    cleanup_non_articles()
