"""Data access layer for articles and SSRN data"""

from typing import Dict, List, Optional

import pandas as pd

from cite_hustle.database.models import DatabaseManager


class ArticleRepository:
    """Repository for accessing and managing article data"""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.conn = db_manager.conn

    # Articles
    def insert_article(
        self,
        doi: str,
        title: str,
        authors: str,
        year: int,
        journal_issn: str,
        journal_name: str,
        publisher: str,
    ):
        """Insert or update a single article"""
        self.conn.execute(
            """
            INSERT INTO articles (doi, title, authors, year, journal_issn, journal_name, publisher)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                title = EXCLUDED.title,
                authors = EXCLUDED.authors,
                updated_at = now()
        """,
            [doi, title, authors, year, journal_issn, journal_name, publisher],
        )

    def bulk_insert_articles(self, articles: List[Dict]):
        """Efficiently insert many articles at once"""
        if not articles:
            return

        df = pd.DataFrame(articles)
        # Specify columns explicitly to avoid timestamp column issues
        self.conn.execute("""
            INSERT INTO articles (doi, title, authors, year, journal_issn, journal_name, publisher)
            SELECT doi, title, authors, year, journal_issn, journal_name, publisher FROM df
            ON CONFLICT (doi) DO UPDATE SET
                title = EXCLUDED.title,
                authors = EXCLUDED.authors,
                updated_at = now()
        """)

    def get_article_count(self) -> int:
        """Get total number of articles"""
        result = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()
        return result[0] if result else 0

    def get_articles_by_year_range(self, year_start: int, year_end: int) -> pd.DataFrame:
        """Get articles within a year range"""
        return self.conn.execute(
            """
            SELECT * FROM articles
            WHERE year BETWEEN ? AND ?
            ORDER BY year DESC, title
        """,
            [year_start, year_end],
        ).fetchdf()

    # SSRN Pages
    def insert_ssrn_page(
        self,
        doi: str,
        ssrn_url: Optional[str],
        html_content: Optional[str],
        html_file_path: Optional[str],
        abstract: Optional[str],
        match_score: Optional[int],
        error_message: Optional[str] = None,
    ):
        """Insert or update SSRN page data"""
        self.conn.execute(
            """
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
                scraped_at = now()
        """,
            [doi, ssrn_url, html_content, html_file_path, abstract, match_score, error_message],
        )

    def get_articles_missing_abstract(
        self,
        limit: Optional[int] = None,
        year_start: Optional[int] = None,
        year_end: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get articles missing abstracts (no SSRN abstract or empty)."""
        query = """
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE s.abstract IS NULL OR s.abstract = ''
        """
        if year_start is not None and year_end is not None:
            query += " AND a.year BETWEEN ? AND ?"
            params = [year_start, year_end]
        elif year_start is not None:
            query += " AND a.year >= ?"
            params = [year_start]
        elif year_end is not None:
            query += " AND a.year <= ?"
            params = [year_end]
        else:
            params = []

        query += " ORDER BY a.year DESC"
        if limit:
            query += f" LIMIT {limit}"

        return self.conn.execute(query, params).fetchdf()

    def upsert_abstract(self, doi: str, abstract: str, force: bool = False):
        """Insert or update abstract in ssrn_pages, optionally forcing overwrite."""
        if force:
            self.conn.execute(
                """
                INSERT INTO ssrn_pages (doi, abstract)
                VALUES (?, ?)
                ON CONFLICT (doi) DO UPDATE SET
                    abstract = EXCLUDED.abstract,
                    scraped_at = now()
                """,
                [doi, abstract],
            )
            return

        self.conn.execute(
            """
            INSERT INTO ssrn_pages (doi, abstract)
            VALUES (?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                abstract = CASE
                    WHEN ssrn_pages.abstract IS NULL OR ssrn_pages.abstract = '' THEN EXCLUDED.abstract
                    ELSE ssrn_pages.abstract
                END,
                scraped_at = CASE
                    WHEN ssrn_pages.abstract IS NULL OR ssrn_pages.abstract = '' THEN now()
                    ELSE ssrn_pages.scraped_at
                END
            """,
            [doi, abstract],
        )

    def update_pdf_info(
        self, doi: str, pdf_url: str, pdf_file_path: Optional[str] = None, downloaded: bool = False
    ):
        """Update PDF download information"""
        self.conn.execute(
            """
            UPDATE ssrn_pages
            SET pdf_url = ?,
                pdf_file_path = ?,
                pdf_downloaded = ?
            WHERE doi = ?
        """,
            [pdf_url, pdf_file_path, downloaded, doi],
        )

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
        """Get SSRN pages with URLs but no downloaded PDF"""
        query = """
            SELECT s.doi, a.title, s.ssrn_url, s.pdf_url, s.html_file_path
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE s.ssrn_url IS NOT NULL
              AND (s.pdf_downloaded = FALSE OR s.pdf_downloaded IS NULL)
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {limit}"

        return self.conn.execute(query).fetchdf()

    def get_articles_with_ssrn_urls(
        self, limit: Optional[int] = None, downloaded: bool = False
    ) -> pd.DataFrame:
        """Get articles with SSRN URLs, optionally filtering by download status"""
        query = """
            SELECT s.doi, a.title, s.ssrn_url, s.pdf_downloaded
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE s.ssrn_url IS NOT NULL
        """

        if not downloaded:
            query += " AND (s.pdf_downloaded = FALSE OR s.pdf_downloaded IS NULL)"

        query += " ORDER BY a.year DESC"

        if limit:
            query += f" LIMIT {limit}"

        return self.conn.execute(query).fetchdf()

    def get_articles_with_ssrn_urls(
        self, limit: Optional[int] = None, downloaded: bool = None
    ) -> pd.DataFrame:
        """Get articles that have SSRN URLs

        Args:
            limit: Maximum number of articles to return
            downloaded: If True, only downloaded PDFs. If False, only undownloaded. If None, all.
        """
        query = """
            SELECT s.doi, a.title, s.ssrn_url, s.pdf_downloaded, s.pdf_file_path
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE s.ssrn_url IS NOT NULL
        """

        if downloaded is not None:
            if downloaded:
                query += " AND s.pdf_downloaded = TRUE"
            else:
                query += " AND (s.pdf_downloaded = FALSE OR s.pdf_downloaded IS NULL)"

        query += " ORDER BY a.year DESC"

        if limit:
            query += f" LIMIT {limit}"

        return self.conn.execute(query).fetchdf()

    def get_ssrn_page_by_doi(self, doi: str) -> Optional[Dict]:
        """Get SSRN page data for a specific DOI"""
        result = self.conn.execute(
            """
            SELECT * FROM ssrn_pages WHERE doi = ?
        """,
            [doi],
        ).fetchone()

        if result:
            columns = [
                "doi",
                "ssrn_url",
                "ssrn_id",
                "html_content",
                "html_file_path",
                "abstract",
                "pdf_url",
                "pdf_downloaded",
                "pdf_file_path",
                "match_score",
                "scraped_at",
                "error_message",
            ]
            return dict(zip(columns, result))
        return None

    # Processing Log
    def log_processing(
        self, doi: str, stage: str, status: str, error_message: Optional[str] = None
    ):
        """Log processing stage for an article"""
        self.conn.execute(
            """
            INSERT INTO processing_log (doi, stage, status, error_message)
            VALUES (?, ?, ?, ?)
        """,
            [doi, stage, status, error_message],
        )

    def get_missing_abstract_count(self) -> int:
        """Count articles missing abstracts (null or empty)."""
        result = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE s.abstract IS NULL OR s.abstract = ''
        """
        ).fetchone()
        return result[0] if result else 0

    def get_openalex_enriched_count(self) -> int:
        """Count distinct articles enriched via OpenAlex."""
        result = self.conn.execute(
            """
            SELECT COUNT(DISTINCT doi)
            FROM processing_log
            WHERE stage = 'enrich_openalex' AND status = 'success'
        """
        ).fetchone()
        return result[0] if result else 0

    def get_missing_abstracts_by_journal(self, limit: int = 10) -> List[Dict]:
        """Get missing abstract counts by journal, including total and percentage missing."""
        result = self.conn.execute(
            """
            WITH missing AS (
                SELECT
                    COALESCE(a.journal_name, 'Unknown') AS journal_name,
                    COUNT(*) AS missing_count
                FROM articles a
                LEFT JOIN ssrn_pages s ON a.doi = s.doi
                WHERE s.abstract IS NULL OR s.abstract = ''
                GROUP BY COALESCE(a.journal_name, 'Unknown')
            ),
            totals AS (
                SELECT
                    COALESCE(journal_name, 'Unknown') AS journal_name,
                    COUNT(*) AS total_count
                FROM articles
                GROUP BY COALESCE(journal_name, 'Unknown')
            )
            SELECT
                m.journal_name,
                m.missing_count,
                m.missing_count * 1.0 / NULLIF(t.total_count, 0) AS missing_pct,
                t.total_count
            FROM missing m
            JOIN totals t ON t.journal_name = m.journal_name
            ORDER BY m.missing_count DESC, m.journal_name
            LIMIT ?
        """,
            [limit],
        ).fetchall()

        return [
            dict(zip(["journal_name", "missing_count", "missing_pct", "total_count"], row))
            for row in result
        ]

    def get_top_journals(self, limit: int = 10) -> List[Dict]:
        """Get top journals by article count."""
        result = self.conn.execute(
            """
            SELECT journal_name, COUNT(*) as count
            FROM articles
            GROUP BY journal_name
            ORDER BY count DESC, journal_name
            LIMIT ?
        """,
            [limit],
        ).fetchall()

        return [dict(zip(["journal_name", "count"], row)) for row in result]

    def get_recent_processing(self, limit: int = 10) -> List[Dict]:
        """Get most recent processing log entries."""
        result = self.conn.execute(
            """
            SELECT doi, stage, status, error_message, processed_at
            FROM processing_log
            ORDER BY processed_at DESC
            LIMIT ?
        """,
            [limit],
        ).fetchall()

        return [
            dict(zip(["doi", "stage", "status", "error_message", "processed_at"], row))
            for row in result
        ]

    def get_recent_openalex_abstracts(self, limit: int = 5) -> List[Dict]:
        """Get most recent OpenAlex-enriched abstracts."""
        result = self.conn.execute(
            """
            SELECT p.doi, a.title, s.abstract
            FROM processing_log p
            JOIN ssrn_pages s ON p.doi = s.doi
            JOIN articles a ON a.doi = s.doi
            WHERE p.stage = 'enrich_openalex' AND p.status = 'success'
            ORDER BY p.processed_at DESC
            LIMIT ?
        """,
            [limit],
        ).fetchall()

        return [dict(zip(["doi", "title", "abstract"], row)) for row in result]

    # Statistics
    def get_statistics(self) -> Dict:
        """Get database statistics"""
        stats = {}

        # Total articles
        stats["total_articles"] = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

        # Articles by year
        stats["by_year"] = (
            self.conn.execute("""
            SELECT year, COUNT(*) as count
            FROM articles
            GROUP BY year
            ORDER BY year DESC
        """)
            .fetchdf()
            .to_dict("records")
        )

        # SSRN pages scraped
        stats["ssrn_scraped"] = self.conn.execute(
            "SELECT COUNT(*) FROM ssrn_pages WHERE abstract IS NOT NULL"
        ).fetchone()[0]

        # PDFs downloaded
        stats["pdfs_downloaded"] = self.conn.execute(
            "SELECT COUNT(*) FROM ssrn_pages WHERE pdf_downloaded = TRUE"
        ).fetchone()[0]

        # Pending tasks
        stats["pending_ssrn_scrapes"] = (
            stats["total_articles"]
            - self.conn.execute("SELECT COUNT(*) FROM ssrn_pages").fetchone()[0]
        )

        stats["pending_pdf_downloads"] = self.conn.execute("""
            SELECT COUNT(*) FROM ssrn_pages
            WHERE pdf_url IS NOT NULL AND (pdf_downloaded = FALSE OR pdf_downloaded IS NULL)
        """).fetchone()[0]

        return stats

    # Search with FTS
    def search_by_title(self, query: str, limit: int = 50) -> List[Dict]:
        """
        Full-text search on article titles using DuckDB FTS extension

        Uses BM25 ranking for relevance scoring.
        """
        result = self.conn.execute(
            """
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name,
                   fts_main_articles.match_bm25(a.doi, ?) AS score
            FROM articles a
            WHERE fts_main_articles.match_bm25(a.doi, ?) IS NOT NULL
            ORDER BY score DESC
            LIMIT ?
        """,
            [query, query, limit],
        ).fetchall()

        return [
            dict(zip(["doi", "title", "authors", "year", "journal", "score"], row))
            for row in result
        ]

    def search_by_abstract(self, query: str, limit: int = 50) -> List[Dict]:
        """
        Full-text search on abstracts using DuckDB FTS extension

        Uses BM25 ranking for relevance scoring.
        """
        result = self.conn.execute(
            """
            SELECT s.doi, a.title, s.abstract, a.year, a.journal_name,
                   fts_main_ssrn_pages.match_bm25(s.doi, ?) AS score
            FROM ssrn_pages s
            JOIN articles a ON s.doi = a.doi
            WHERE fts_main_ssrn_pages.match_bm25(s.doi, ?) IS NOT NULL
            ORDER BY score DESC
            LIMIT ?
        """,
            [query, query, limit],
        ).fetchall()

        return [
            dict(zip(["doi", "title", "abstract", "year", "journal", "score"], row))
            for row in result
        ]

    def search_by_author(self, author_name: str, limit: int = 50) -> List[Dict]:
        """
        Search articles by author name using pattern matching

        Note: Author search uses LIKE matching, not FTS.
        """
        result = self.conn.execute(
            """
            SELECT doi, title, authors, year, journal_name
            FROM articles
            WHERE LOWER(authors) LIKE LOWER(?)
            ORDER BY year DESC, title
            LIMIT ?
        """,
            [f"%{author_name}%", limit],
        ).fetchall()

        return [dict(zip(["doi", "title", "authors", "year", "journal"], row)) for row in result]

    def get_sample_articles(self, limit: int = 10) -> pd.DataFrame:
        """Get a sample of articles from the database"""
        return self.conn.execute(
            """
            SELECT doi, title, authors, year, journal_name
            FROM articles
            ORDER BY year DESC
            LIMIT ?
        """,
            [limit],
        ).fetchdf()
