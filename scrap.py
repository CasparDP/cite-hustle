from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager

db = DatabaseManager(settings.db_path)
db.connect()

# Check processing log
filtered = db.conn.execute("""
    SELECT COUNT(*) 
    FROM processing_log 
    WHERE stage = 'metadata_collect'
    AND error_message LIKE '%Filtered out%'
""").fetchone()[0]

print(f"Collections that filtered non-articles: {filtered}")