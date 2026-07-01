"""DuckDB database models and schema management"""
import time
import duckdb
from pathlib import Path


class DatabaseManager:
    """Centralized DuckDB connection and schema management"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = None

    def connect(self, read_only: bool = False, max_wait: int = 0):
        """Connect to DuckDB and load the full-text search extension.

        DuckDB allows either a single read-write connection or several
        read-only connections to a file, never both at once.

        Args:
            read_only: open the database read-only so this process can coexist
                with other readers (e.g. an MCP server that keeps the DB open).
                Use for commands that only query.
            max_wait: if > 0, retry for up to this many seconds when the file is
                locked by another process before giving up. Use for write
                commands that must eventually obtain exclusive access.
        """
        deadline = time.monotonic() + max_wait
        delay = 2.0
        while True:
            try:
                self.conn = duckdb.connect(self.db_path, read_only=read_only)
                break
            except duckdb.Error as e:
                if "lock" not in str(e).lower() or time.monotonic() >= deadline:
                    raise
                print(
                    f"  ⏳ Database is held by another process; retrying in {delay:.0f}s "
                    f"(waiting up to {max_wait}s). Close other connections to speed this up."
                )
                time.sleep(delay)
                delay = min(delay * 1.5, 30.0)

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
        
        # Current PDF on disk per DOI, from any source (ssrn/nber/arxiv/oa)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pdf_files (
                doi VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                source_url VARCHAR,
                pdf_url VARCHAR,
                pdf_file_path VARCHAR NOT NULL,
                match_score DOUBLE,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verify_status VARCHAR DEFAULT 'pending',
                verify_method VARCHAR,
                verify_score DOUBLE,
                verify_model VARCHAR,
                verify_reason VARCHAR,
                verified_at TIMESTAMP,
                FOREIGN KEY (doi) REFERENCES articles(doi)
            );
        """)

        # Memo of fallback-resolution attempts so reruns skip known misses
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pdf_candidates (
                doi VARCHAR,
                source VARCHAR,
                candidate_url VARCHAR,
                pdf_url VARCHAR,
                match_score DOUBLE,
                status VARCHAR,
                error_message VARCHAR,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (doi, source)
            );
        """)

        # Wiki ingestion state per DOI
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wiki_pages (
                doi VARCHAR PRIMARY KEY,
                bib_key VARCHAR UNIQUE NOT NULL,
                source_page_path VARCHAR,
                extraction_depth VARCHAR,
                analyst_model VARCHAR,
                verifier_model VARCHAR,
                status VARCHAR DEFAULT 'pending',
                error_message VARCHAR,
                ingested_at TIMESTAMP,
                FOREIGN KEY (doi) REFERENCES articles(doi)
            );
        """)

        # Pipeline run/stage bookkeeping
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS pipeline_runs_seq START 1;
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY DEFAULT nextval('pipeline_runs_seq'),
                run_id VARCHAR,
                stage VARCHAR,
                status VARCHAR,
                detail VARCHAR,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
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
            "CREATE INDEX IF NOT EXISTS idx_pdf_files_verify ON pdf_files(verify_status);",
            "CREATE INDEX IF NOT EXISTS idx_wiki_pages_status ON wiki_pages(status);",
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
