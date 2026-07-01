"""Data access layer for articles and SSRN data"""

from typing import Dict, List, Optional

import pandas as pd

from cite_hustle.database.models import DatabaseManager
from cite_hustle.paths import to_portable


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
            WHERE (s.abstract IS NULL OR s.abstract = '')
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
        """Update PDF download information (path stored in portable $HOME form)"""
        if pdf_file_path:
            pdf_file_path = to_portable(pdf_file_path)
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

    def reset_ssrn_download(self, doi: str):
        """Clear the SSRN download flags so a paper becomes pending again."""
        self.conn.execute(
            "UPDATE ssrn_pages SET pdf_downloaded = FALSE, pdf_file_path = NULL WHERE doi = ?",
            [doi],
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

    def get_articles_with_ssrn_urls(
        self,
        limit: Optional[int] = None,
        downloaded: Optional[bool] = None,
        include_unavailable: bool = True,
    ) -> pd.DataFrame:
        """Get articles that have SSRN URLs.

        Args:
            limit: Maximum number of articles to return
            downloaded: If True, only downloaded PDFs. If False, only
                undownloaded. If None, all.
            include_unavailable: If False, skip papers previously marked as
                "not available for download" (see mark_pdf_unavailable), so
                repeat runs don't keep retrying them.
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

        if not include_unavailable:
            query += """
              AND NOT EXISTS (
                  SELECT 1 FROM processing_log p
                  WHERE p.doi = s.doi
                    AND p.stage = 'download_pdf'
                    AND p.status = 'unavailable'
              )
            """

        query += " ORDER BY a.year DESC"

        if limit:
            query += f" LIMIT {int(limit)}"

        return self.conn.execute(query).fetchdf()

    def mark_pdf_unavailable(self, doi: str):
        """Record that a paper has no downloadable PDF on SSRN.

        Stored in processing_log so get_articles_with_ssrn_urls(
        include_unavailable=False) can skip it on later runs.
        """
        self.log_processing(doi, "download_pdf", "unavailable", "Not available for download")

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

    # PDF files (any source: ssrn/nber/arxiv/oa)
    def upsert_pdf_file(
        self,
        doi: str,
        source: str,
        source_url: Optional[str],
        pdf_url: Optional[str],
        pdf_file_path: str,
        match_score: Optional[float] = None,
    ):
        """Record the current PDF on disk for an article; resets verification state.

        The stored path is normalized to the $HOME/... portable convention.
        """
        pdf_file_path = to_portable(pdf_file_path)
        self.conn.execute(
            """
            INSERT INTO pdf_files (doi, source, source_url, pdf_url, pdf_file_path, match_score)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                source = EXCLUDED.source,
                source_url = EXCLUDED.source_url,
                pdf_url = EXCLUDED.pdf_url,
                pdf_file_path = EXCLUDED.pdf_file_path,
                match_score = EXCLUDED.match_score,
                downloaded_at = now(),
                verify_status = 'pending',
                verify_method = NULL,
                verify_score = NULL,
                verify_model = NULL,
                verify_reason = NULL,
                verified_at = NULL
        """,
            [doi, source, source_url, pdf_url, pdf_file_path, match_score],
        )

    def delete_pdf_file(self, doi: str):
        """Remove the pdf_files row (e.g. after quarantining a mismatched PDF)."""
        self.conn.execute("DELETE FROM pdf_files WHERE doi = ?", [doi])

    def get_pdfs_pending_verification(
        self, limit: Optional[int] = None, statuses: tuple = ("pending",)
    ) -> pd.DataFrame:
        """Get PDFs awaiting metadata verification, joined with article metadata."""
        placeholders = ", ".join("?" for _ in statuses)
        query = f"""
            SELECT p.doi, p.source, p.source_url, p.pdf_file_path,
                   a.title, a.authors, a.year, a.journal_name
            FROM pdf_files p
            JOIN articles a ON p.doi = a.doi
            WHERE p.verify_status IN ({placeholders})
            ORDER BY p.downloaded_at
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query, list(statuses)).fetchdf()

    def set_pdf_verification(
        self,
        doi: str,
        status: str,
        method: Optional[str] = None,
        score: Optional[float] = None,
        model: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        """Store the verification verdict for a downloaded PDF."""
        self.conn.execute(
            """
            UPDATE pdf_files
            SET verify_status = ?,
                verify_method = ?,
                verify_score = ?,
                verify_model = ?,
                verify_reason = ?,
                verified_at = now()
            WHERE doi = ?
        """,
            [status, method, score, model, reason, doi],
        )

    def get_articles_without_pdf(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Get articles with no PDF on disk where the SSRN path has failed.

        Eligible for fallback resolution: no pdf_files row, and either SSRN
        never matched the paper (no ssrn_url) or the SSRN download was marked
        unavailable. Articles still pending a first SSRN download attempt are
        left to the SSRN downloader.
        """
        query = """
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            LEFT JOIN pdf_files p ON a.doi = p.doi
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE p.doi IS NULL
              AND (
                  s.ssrn_url IS NULL
                  OR EXISTS (
                      SELECT 1 FROM processing_log pl
                      WHERE pl.doi = a.doi
                        AND pl.stage = 'download_pdf'
                        AND pl.status = 'unavailable'
                  )
              )
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query).fetchdf()

    def get_recent_candidate_checks(self, cutoff) -> set:
        """Get (doi, source) pairs already checked since cutoff (datetime)."""
        rows = self.conn.execute(
            "SELECT doi, source FROM pdf_candidates WHERE checked_at >= ?", [cutoff]
        ).fetchall()
        return set(rows)

    def record_pdf_candidate(
        self,
        doi: str,
        source: str,
        candidate_url: Optional[str] = None,
        pdf_url: Optional[str] = None,
        match_score: Optional[float] = None,
        status: str = "no_match",
        error_message: Optional[str] = None,
    ):
        """Memoize a fallback-resolution attempt so reruns skip known misses."""
        self.conn.execute(
            """
            INSERT INTO pdf_candidates
            (doi, source, candidate_url, pdf_url, match_score, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi, source) DO UPDATE SET
                candidate_url = EXCLUDED.candidate_url,
                pdf_url = EXCLUDED.pdf_url,
                match_score = EXCLUDED.match_score,
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                checked_at = now()
        """,
            [doi, source, candidate_url, pdf_url, match_score, status, error_message],
        )

    # Wiki pages
    def get_verified_pdfs_not_ingested(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Get verified PDFs not yet ingested into the wiki (pending/failed are retried)."""
        query = """
            SELECT p.doi, p.pdf_file_path, a.title, a.authors, a.year, a.journal_name
            FROM pdf_files p
            JOIN articles a ON p.doi = a.doi
            WHERE p.verify_status = 'match'
              AND NOT EXISTS (
                  SELECT 1 FROM wiki_pages w
                  WHERE w.doi = p.doi AND w.status IN ('ingested', 'flagged')
              )
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query).fetchdf()

    def upsert_wiki_page(
        self,
        doi: str,
        bib_key: str,
        source_page_path: Optional[str] = None,
        extraction_depth: Optional[str] = None,
        analyst_model: Optional[str] = None,
        verifier_model: Optional[str] = None,
        status: str = "pending",
        error_message: Optional[str] = None,
    ):
        """Insert or update wiki ingestion state for an article."""
        self.conn.execute(
            """
            INSERT INTO wiki_pages
            (doi, bib_key, source_page_path, extraction_depth, analyst_model,
             verifier_model, status, error_message, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? IN ('ingested', 'flagged') THEN now() ELSE NULL END)
            ON CONFLICT (doi) DO UPDATE SET
                bib_key = EXCLUDED.bib_key,
                source_page_path = EXCLUDED.source_page_path,
                extraction_depth = EXCLUDED.extraction_depth,
                analyst_model = EXCLUDED.analyst_model,
                verifier_model = EXCLUDED.verifier_model,
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                ingested_at = EXCLUDED.ingested_at
        """,
            [
                doi,
                bib_key,
                source_page_path,
                extraction_depth,
                analyst_model,
                verifier_model,
                status,
                error_message,
                status,
            ],
        )

    def get_wiki_page_by_doi(self, doi: str) -> Optional[Dict]:
        """Get wiki ingestion state for a DOI (for stable bib_keys across runs)."""
        result = self.conn.execute(
            "SELECT doi, bib_key, status FROM wiki_pages WHERE doi = ?", [doi]
        ).fetchone()
        if result:
            return dict(zip(["doi", "bib_key", "status"], result))
        return None

    def get_existing_bib_keys(self) -> set:
        """Get all bib_keys already assigned."""
        rows = self.conn.execute("SELECT bib_key FROM wiki_pages").fetchall()
        return {row[0] for row in rows}

    def get_ingested_wiki_pages(self) -> pd.DataFrame:
        """Get ingested/flagged wiki pages with article metadata (for index generation)."""
        return self.conn.execute(
            """
            SELECT w.doi, w.bib_key, w.status, a.title, a.authors, a.year, a.journal_name
            FROM wiki_pages w
            JOIN articles a ON w.doi = a.doi
            WHERE w.status IN ('ingested', 'flagged')
            ORDER BY a.journal_name, a.year DESC, a.title
        """
        ).fetchdf()

    # Pipeline runs
    def start_pipeline_stage(self, run_id: str, stage: str) -> int:
        """Record the start of a pipeline stage; returns the row id."""
        result = self.conn.execute(
            """
            INSERT INTO pipeline_runs (run_id, stage, status)
            VALUES (?, ?, 'running')
            RETURNING id
        """,
            [run_id, stage],
        ).fetchone()
        return result[0]

    def finish_pipeline_stage(self, stage_id: int, status: str, detail: Optional[str] = None):
        """Record the outcome of a pipeline stage (detail is a JSON stats blob)."""
        self.conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, detail = ?, finished_at = now()
            WHERE id = ?
        """,
            [status, detail, stage_id],
        )

    def get_pipeline_run_stages(self, run_id: str) -> List[Dict]:
        """Get all stages of a pipeline run (for the run report)."""
        result = self.conn.execute(
            """
            SELECT stage, status, detail, started_at, finished_at
            FROM pipeline_runs
            WHERE run_id = ?
            ORDER BY id
        """,
            [run_id],
        ).fetchall()
        return [
            dict(zip(["stage", "status", "detail", "started_at", "finished_at"], row))
            for row in result
        ]

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

        # PDFs on disk by source (any-source pipeline)
        stats["pdfs_by_source"] = dict(
            self.conn.execute(
                "SELECT source, COUNT(*) FROM pdf_files GROUP BY source ORDER BY source"
            ).fetchall()
        )

        # Verification breakdown
        stats["pdfs_by_verify_status"] = dict(
            self.conn.execute(
                "SELECT verify_status, COUNT(*) FROM pdf_files GROUP BY verify_status"
            ).fetchall()
        )

        # Quarantined PDFs (mismatches recorded in processing_log; row removed from pdf_files)
        stats["pdfs_quarantined"] = self.conn.execute("""
            SELECT COUNT(DISTINCT doi) FROM processing_log
            WHERE stage = 'verify_pdf' AND status = 'mismatch'
        """).fetchone()[0]

        # Wiki ingestion
        stats["wiki_ingested"] = self.conn.execute("""
            SELECT COUNT(*) FROM wiki_pages WHERE status IN ('ingested', 'flagged')
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
