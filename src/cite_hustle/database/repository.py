"""Data access layer for articles and SSRN data"""
from typing import List, Dict, Optional
import pandas as pd
from cite_hustle.database.models import DatabaseManager


class ArticleRepository:
    """Repository for accessing and managing article data"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.conn = db_manager.conn
    
    # Articles
    def insert_article(self, doi: str, title: str, authors: str, year: int, 
                      journal_issn: str, journal_name: str, publisher: str):
        """Insert or update a single article"""
        self.conn.execute("""
            INSERT INTO articles (doi, title, authors, year, journal_issn, journal_name, publisher)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                title = EXCLUDED.title,
                authors = EXCLUDED.authors,
                updated_at = CURRENT_TIMESTAMP
        """, [doi, title, authors, year, journal_issn, journal_name, publisher])
    
    def bulk_insert_articles(self, articles: List[Dict]):
        """Efficiently insert many articles at once"""
        if not articles:
            return
        
        df = pd.DataFrame(articles)
        self.conn.execute("""
            INSERT INTO articles 
            SELECT * FROM df
            ON CONFLICT (doi) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
        """)
    
    def get_article_count(self) -> int:
        """Get total number of articles"""
        result = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()
        return result[0] if result else 0
    
    def get_articles_by_year_range(self, year_start: int, year_end: int) -> pd.DataFrame:
        """Get articles within a year range"""
        return self.conn.execute("""
            SELECT * FROM articles 
            WHERE year BETWEEN ? AND ?
            ORDER BY year DESC, title
        """, [year_start, year_end]).fetchdf()
    
    # SSRN Pages
    def insert_ssrn_page(self, doi: str, ssrn_url: Optional[str], 
                        html_content: Optional[str], html_file_path: Optional[str],
                        abstract: Optional[str], match_score: Optional[int],
                        error_message: Optional[str] = None):
        """Insert or update SSRN page data"""
        self.conn.execute("""
            INSERT INTO ssrn_pages 
            (doi, ssrn_url, html_content, html_file_path, abstract, match_score, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                ssrn_url = EXCLUDED.ssrn_url,
                html_content = EXCLUDED.html_content,
                html_file_path = EXCLUDED.html_file_path,
                abstract = EXCLUDED.abstract,
                match_score = EXCLUDED.match_score,
                error_message = EXCLUDED.error_message,
                scraped_at = CURRENT_TIMESTAMP
        """, [doi, ssrn_url, html_content, html_file_path, abstract, match_score, error_message])
    
    def update_pdf_info(self, doi: str, pdf_url: str, pdf_file_path: Optional[str] = None, 
                       downloaded: bool = False):
        """Update PDF download information"""
        self.conn.execute("""
            UPDATE ssrn_pages 
            SET pdf_url = ?,
                pdf_file_path = ?,
                pdf_downloaded = ?
            WHERE doi = ?
        """, [pdf_url, pdf_file_path, downloaded, doi])
    
    def get_pending_ssrn_scrapes(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Get articles that need SSRN scraping"""
        query = """
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE s.doi IS NULL
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {limit}"
        
        return self.conn.execute(query).fetchdf()
    
    def get_pending_pdf_downloads(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Get SSRN pages with PDF URLs but no downloaded PDF"""
        query = """
            SELECT s.doi, a.title, s.ssrn_url, s.pdf_url, s.html_file_path
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE s.pdf_url IS NOT NULL 
              AND (s.pdf_downloaded = FALSE OR s.pdf_downloaded IS NULL)
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {limit}"
        
        return self.conn.execute(query).fetchdf()
    
    def get_ssrn_page_by_doi(self, doi: str) -> Optional[Dict]:
        """Get SSRN page data for a specific DOI"""
        result = self.conn.execute("""
            SELECT * FROM ssrn_pages WHERE doi = ?
        """, [doi]).fetchone()
        
        if result:
            columns = ['doi', 'ssrn_url', 'ssrn_id', 'html_content', 'html_file_path',
                      'abstract', 'pdf_url', 'pdf_downloaded', 'pdf_file_path',
                      'match_score', 'scraped_at', 'error_message']
            return dict(zip(columns, result))
        return None
    
    # Processing Log
    def log_processing(self, doi: str, stage: str, status: str, 
                      error_message: Optional[str] = None):
        """Log processing stage for an article"""
        self.conn.execute("""
            INSERT INTO processing_log (doi, stage, status, error_message)
            VALUES (?, ?, ?, ?)
        """, [doi, stage, status, error_message])
    
    # Statistics
    def get_statistics(self) -> Dict:
        """Get database statistics"""
        stats = {}
        
        # Total articles
        stats['total_articles'] = self.conn.execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()[0]
        
        # Articles by year
        stats['by_year'] = self.conn.execute("""
            SELECT year, COUNT(*) as count 
            FROM articles 
            GROUP BY year 
            ORDER BY year DESC
        """).fetchdf().to_dict('records')
        
        # SSRN pages scraped
        stats['ssrn_scraped'] = self.conn.execute(
            "SELECT COUNT(*) FROM ssrn_pages WHERE abstract IS NOT NULL"
        ).fetchone()[0]
        
        # PDFs downloaded
        stats['pdfs_downloaded'] = self.conn.execute(
            "SELECT COUNT(*) FROM ssrn_pages WHERE pdf_downloaded = TRUE"
        ).fetchone()[0]
        
        # Pending tasks
        stats['pending_ssrn_scrapes'] = stats['total_articles'] - self.conn.execute(
            "SELECT COUNT(*) FROM ssrn_pages"
        ).fetchone()[0]
        
        stats['pending_pdf_downloads'] = self.conn.execute("""
            SELECT COUNT(*) FROM ssrn_pages 
            WHERE pdf_url IS NOT NULL AND (pdf_downloaded = FALSE OR pdf_downloaded IS NULL)
        """).fetchone()[0]
        
        return stats
    
    # Search
    def search_by_title(self, query: str, limit: int = 50) -> List[Dict]:
        """Full-text search on article titles"""
        try:
            result = self.conn.execute("""
                SELECT a.doi, a.title, a.authors, a.year, a.journal_name,
                       fts_main_articles.match_bm25(a.doi, ?) AS score
                FROM articles a
                WHERE fts_main_articles.match_bm25(a.doi, ?) IS NOT NULL
                ORDER BY score DESC
                LIMIT ?
            """, [query, query, limit]).fetchall()
            
            return [dict(zip(['doi', 'title', 'authors', 'year', 'journal', 'score'], row)) 
                    for row in result]
        except Exception as e:
            print(f"Search error (FTS may not be enabled): {e}")
            # Fallback to LIKE search
            result = self.conn.execute("""
                SELECT doi, title, authors, year, journal_name, 0 as score
                FROM articles
                WHERE title ILIKE ?
                LIMIT ?
            """, [f'%{query}%', limit]).fetchall()
            
            return [dict(zip(['doi', 'title', 'authors', 'year', 'journal', 'score'], row)) 
                    for row in result]
