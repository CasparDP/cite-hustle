"""
Rebuild FTS indexes

Use this if search is not working after data collection.
Run: poetry run python scripts/rebuild_fts.py
"""
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


def rebuild_fts_indexes():
    """Rebuild full-text search indexes"""
    
    print("="*60)
    print("REBUILDING FTS INDEXES")
    print("="*60)
    
    # Connect to database
    print(f"\nConnecting to: {settings.db_path}")
    db = DatabaseManager(settings.db_path)
    db.connect()
    repo = ArticleRepository(db)
    
    # Check article count
    count = repo.get_article_count()
    print(f"Articles in database: {count:,}")
    
    if count == 0:
        print("\n⚠️  No articles to index!")
        print("Run: poetry run cite-hustle collect --field accounting --year-start 2023")
        return
    
    print("\nDropping existing FTS indexes (if any)...")
    try:
        db.conn.execute("DROP TABLE IF EXISTS fts_main_articles;")
        print("   ✓ Dropped fts_main_articles")
    except Exception as e:
        print(f"   Note: {e}")
    
    try:
        db.conn.execute("DROP TABLE IF EXISTS fts_main_ssrn_pages;")
        print("   ✓ Dropped fts_main_ssrn_pages")
    except Exception as e:
        print(f"   Note: {e}")
    
    print("\nCreating new FTS indexes...")
    db.create_fts_indexes()
    
    print("\nVerifying indexes...")
    try:
        result = db.conn.execute("""
            SELECT COUNT(*) FROM fts_main_articles.docs
        """).fetchone()
        print(f"   ✓ Articles indexed: {result[0]:,}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\nTesting search...")
    try:
        results = repo.search_by_title("accounting", limit=3)
        if results:
            print(f"   ✓ Search working! Found {len(results)} results:")
            for i, r in enumerate(results, 1):
                print(f"   {i}. {r['title'][:60]}...")
        else:
            print("   ⚠️  No results (might be normal if no titles contain 'accounting')")
    except Exception as e:
        print(f"   ❌ Search failed: {e}")
    
    print("\n" + "="*60)
    print("✓ FTS REBUILD COMPLETE")
    print("="*60)
    print("\nNow try: poetry run cite-hustle search 'your query'")
    
    db.close()


if __name__ == "__main__":
    rebuild_fts_indexes()
