"""
Test script to verify metadata collection is working

This script can be run to test the metadata collector without using the CLI
"""
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.collectors.metadata import MetadataCollector


def test_metadata_collection():
    """Test metadata collection for a single journal and year"""
    
    print("="*60)
    print("TESTING METADATA COLLECTION")
    print("="*60)
    
    # Setup database
    print("\n1. Connecting to database...")
    db = DatabaseManager(settings.db_path)
    db.connect()
    db.initialize_schema()
    repo = ArticleRepository(db)
    print(f"   ✓ Connected to {settings.db_path}")
    
    # Get a single journal for testing
    print("\n2. Getting test journal...")
    journals = JournalRegistry.get_by_field('accounting')
    test_journal = journals[0]  # The Accounting Review
    print(f"   ✓ Testing with: {test_journal.name}")
    
    # Create collector
    print("\n3. Initializing metadata collector...")
    collector = MetadataCollector(repo)
    print(f"   ✓ Cache directory: {settings.cache_dir}")
    
    # Collect for a single year
    print("\n4. Collecting articles for 2023...")
    count = collector.collect_for_journal(test_journal, [2023], show_progress=True)
    
    # Results
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Articles collected: {count}")
    
    # Query database
    stats = repo.get_statistics()
    print(f"Total articles in database: {stats['total_articles']}")
    
    print("\n✓ Test complete!")
    print("="*60)
    
    db.close()


if __name__ == "__main__":
    test_metadata_collection()
