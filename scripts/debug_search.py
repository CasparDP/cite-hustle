"""
Debug script to test FTS search functionality

Run this to diagnose search issues:
poetry run python scripts/debug_search.py
"""
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


def debug_search():
    """Debug FTS search issues"""
    
    print("="*60)
    print("DEBUGGING SEARCH FUNCTIONALITY")
    print("="*60)
    
    # Connect to database
    print(f"\n1. Connecting to database: {settings.db_path}")
    db = DatabaseManager(settings.db_path)
    db.connect()
    repo = ArticleRepository(db)
    print("   ✓ Connected")
    
    # Check if we have articles
    print("\n2. Checking for articles in database...")
    count = repo.get_article_count()
    print(f"   Total articles: {count}")
    
    if count == 0:
        print("\n❌ No articles in database!")
        print("   Run: poetry run cite-hustle collect --field accounting --year-start 2023")
        return
    
    # Show sample articles
    print("\n3. Sample articles:")
    sample = repo.get_sample_articles(5)
    for idx, row in sample.iterrows():
        print(f"   • {row['title'][:80]}...")
    
    # Check if FTS extension is loaded
    print("\n4. Checking FTS extension...")
    try:
        result = db.conn.execute("SELECT * FROM duckdb_extensions() WHERE extension_name = 'fts'").fetchone()
        if result:
            print(f"   ✓ FTS extension status: {result}")
        else:
            print("   ⚠️  FTS extension not found")
    except Exception as e:
        print(f"   ⚠️  Error checking FTS: {e}")
    
    # Check if FTS indexes exist
    print("\n5. Checking FTS indexes...")
    try:
        # Try to query the FTS index
        result = db.conn.execute("""
            SELECT COUNT(*) 
            FROM fts_main_articles.docs
        """).fetchone()
        print(f"   ✓ FTS index 'fts_main_articles' exists with {result[0]} documents")
    except Exception as e:
        print(f"   ❌ FTS index error: {e}")
        print("\n   FTS indexes may not be created. Try:")
        print("   1. Close all connections to the database")
        print("   2. Run: poetry run cite-hustle init")
        print("   3. Or create indexes manually:")
        print("      from cite_hustle.database.models import DatabaseManager")
        print("      db = DatabaseManager(settings.db_path)")
        print("      db.connect()")
        print("      db.create_fts_indexes()")
    
    # Test direct FTS query
    print("\n6. Testing direct FTS query...")
    try:
        test_query = "accounting"
        result = db.conn.execute("""
            SELECT a.doi, a.title, 
                   fts_main_articles.match_bm25(a.doi, ?) AS score
            FROM articles a
            WHERE fts_main_articles.match_bm25(a.doi, ?) IS NOT NULL
            ORDER BY score DESC
            LIMIT 5
        """, [test_query, test_query]).fetchall()
        
        if result:
            print(f"   ✓ FTS query worked! Found {len(result)} results for '{test_query}'")
            for i, row in enumerate(result, 1):
                print(f"   {i}. {row[1][:60]}... (score: {row[2]:.2f})")
        else:
            print(f"   ⚠️  FTS query returned no results for '{test_query}'")
    except Exception as e:
        print(f"   ❌ FTS query failed: {e}")
    
    # Test simple LIKE query as fallback
    print("\n7. Testing simple LIKE query (fallback)...")
    try:
        test_word = "Accounting"  # Capital A since it's in journal names
        result = db.conn.execute("""
            SELECT doi, title
            FROM articles
            WHERE title LIKE ?
            LIMIT 5
        """, [f'%{test_word}%']).fetchall()
        
        if result:
            print(f"   ✓ LIKE query worked! Found {len(result)} results")
            for i, row in enumerate(result, 1):
                print(f"   {i}. {row[1][:60]}...")
        else:
            print(f"   ⚠️  No results found with LIKE query")
    except Exception as e:
        print(f"   ❌ LIKE query failed: {e}")
    
    # Test repository search method
    print("\n8. Testing repo.search_by_title()...")
    try:
        results = repo.search_by_title("accounting", limit=5)
        if results:
            print(f"   ✓ Search method worked! Found {len(results)} results")
            for i, result in enumerate(results, 1):
                print(f"   {i}. {result['title'][:60]}...")
                if 'score' in result:
                    print(f"      Score: {result['score']:.2f}")
        else:
            print("   ⚠️  Search method returned no results")
    except Exception as e:
        print(f"   ❌ Search method failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("DIAGNOSIS COMPLETE")
    print("="*60)
    
    db.close()


if __name__ == "__main__":
    debug_search()
