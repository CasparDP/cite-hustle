"""DuckDB database models and schema management"""
import duckdb
from pathlib import Path


class DatabaseManager:
    """Centralized DuckDB connection and schema management"""
    
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = None
    
    def connect(self):
        """Connect to DuckDB with optimized settings"""
        self.conn = duckdb.connect(self.db_path)
        
        # Install and load full-text search extension
        self.conn.execute("INSTALL fts;")
        self.conn.execute("LOAD fts;")
        
        return self.conn
    
    def initialize_schema(self):
        """Create tables with proper schema"""
        
        # Journals table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS journals (
                issn VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                field VARCHAR NOT NULL,
                publisher VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Articles metadata table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                doi VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                authors VARCHAR,
                year INTEGER NOT NULL,
                journal_issn VARCHAR,
                journal_name VARCHAR,
                publisher VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # SSRN pages and PDFs table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ssrn_pages (
                doi VARCHAR PRIMARY KEY,
                ssrn_url VARCHAR,
                ssrn_id VARCHAR,
                html_content VARCHAR,
                html_file_path VARCHAR,
                abstract TEXT,
                pdf_url VARCHAR,
                pdf_downloaded BOOLEAN DEFAULT FALSE,
                pdf_file_path VARCHAR,
                match_score INTEGER,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message VARCHAR,
                FOREIGN KEY (doi) REFERENCES articles(doi)
            );
        """)
        
        # Create sequence for processing_log (must come before table creation)
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS processing_log_seq START 1;
        """)
        
        # Processing log for tracking workflow
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_log (
                id INTEGER PRIMARY KEY DEFAULT nextval('processing_log_seq'),
                doi VARCHAR,
                stage VARCHAR,
                status VARCHAR,
                error_message VARCHAR,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create indexes
        self._create_indexes()
        
        print("✓ Database schema initialized")
    
    def _create_indexes(self):
        """Create indexes for better query performance"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(year);",
            "CREATE INDEX IF NOT EXISTS idx_articles_journal ON articles(journal_issn);",
            "CREATE INDEX IF NOT EXISTS idx_ssrn_downloaded ON ssrn_pages(pdf_downloaded);",
            "CREATE INDEX IF NOT EXISTS idx_processing_log_doi ON processing_log(doi);",
        ]
        
        for idx in indexes:
            try:
                self.conn.execute(idx)
            except Exception as e:
                print(f"Index creation note: {e}")
    
    def create_fts_indexes(self):
        """Create full-text search indexes using DuckDB FTS extension"""
        try:
            # Full-text search on titles
            self.conn.execute("""
                PRAGMA create_fts_index(
                    'articles', 
                    'doi', 
                    'title', 
                    overwrite=1
                );
            """)
            print("✓ Full-text search index created for article titles")
            
            # Full-text search on abstracts
            self.conn.execute("""
                PRAGMA create_fts_index(
                    'ssrn_pages', 
                    'doi', 
                    'abstract',
                    overwrite=1
                );
            """)
            print("✓ Full-text search index created for abstracts")
        except Exception as e:
            print(f"⚠️  FTS index creation error: {e}")
            print("   Search will still work but may be slower")
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
