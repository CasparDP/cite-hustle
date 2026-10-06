"""Data access layer for articles and SSRN data"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from cite_hustle import abstract_check, front_matter
from cite_hustle.database.models import DatabaseManager
from cite_hustle.paths import to_portable


class ArticleRepository:
    """Repository for accessing and managing article data"""

    # No SSRN search recorded yet: no row, or an abstract-only row from an enricher.
    # Every SSRN search outcome sets ssrn_url, html_file_path, or error_message.
    _SSRN_NOT_SEARCHED = """(s.doi IS NULL OR (
        s.ssrn_url IS NULL AND s.html_file_path IS NULL AND s.error_message IS NULL
    ))"""

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
                year = EXCLUDED.year,
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
        self.conn.execute(
            """
            INSERT INTO articles (doi, title, authors, year, journal_issn, journal_name, publisher)
            SELECT doi, title, authors, year, journal_issn, journal_name, publisher FROM df
            ON CONFLICT (doi) DO UPDATE SET
                title = EXCLUDED.title,
                authors = EXCLUDED.authors,
                year = EXCLUDED.year,
                updated_at = now()
        """
        )

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

    def get_article_by_doi(self, doi: str) -> Optional[Dict]:
        """Get one article's metadata by DOI."""
        result = self.conn.execute(
            """
            SELECT doi, title, authors, year, journal_issn, journal_name, publisher
            FROM articles WHERE doi = ?
        """,
            [doi],
        ).fetchone()
        if result:
            columns = [
                "doi",
                "title",
                "authors",
                "year",
                "journal_issn",
                "journal_name",
                "publisher",
            ]
            return dict(zip(columns, result))
        return None

    def resolve_article_doi(self, normalized_doi: str) -> Optional[str]:
        """Resolve a canonical DOI to the exact primary-key value in articles.

        Raises when legacy rows contain more than one spelling of the same
        normalized DOI; importing into either row would then be ambiguous.
        """
        rows = self.conn.execute(
            """
            SELECT doi
            FROM articles
            WHERE regexp_replace(
                      lower(trim(doi)),
                      '^(https?://(dx\\.)?doi\\.org/|doi:[[:space:]]*)+',
                      ''
                  ) = ?
            ORDER BY doi
            LIMIT 2
            """,
            [normalized_doi],
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(f"Ambiguous normalized DOI in articles: {normalized_doi}")
        return rows[0][0] if rows else None

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
        if abstract_check.is_ssrn_page_text(abstract):
            abstract = abstract_check.cut_abstract_from_page_text(abstract)
        elif abstract_check.is_placeholder(abstract):
            abstract = None
        self.conn.execute(
            """
            INSERT INTO ssrn_pages
            (doi, ssrn_url, html_content, html_file_path, abstract, match_score, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO UPDATE SET
                ssrn_url = EXCLUDED.ssrn_url,
                html_content = EXCLUDED.html_content,
                html_file_path = EXCLUDED.html_file_path,
                -- SSRN abstract wins; a no-match keeps a CrossRef/OpenAlex abstract
                abstract = COALESCE(EXCLUDED.abstract, ssrn_pages.abstract),
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
        skip_stage: Optional[str] = None,
        recheck_days: int = 90,
    ) -> pd.DataFrame:
        """Get articles missing abstracts (no SSRN abstract or empty).

        With skip_stage, leave out articles whose success/no_match attempt at that
        processing_log stage is newer than recheck_days (failed attempts are retried).
        """
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

        if skip_stage:
            query += """
              AND NOT EXISTS (
                  SELECT 1 FROM processing_log pl
                  WHERE pl.doi = a.doi AND pl.stage = ?
                    AND pl.status IN ('success', 'no_match')
                    AND pl.processed_at > ?
              )
            """
            params += [skip_stage, datetime.now() - timedelta(days=recheck_days)]

        query += " ORDER BY a.year DESC"
        if limit:
            query += f" LIMIT {limit}"

        return self.conn.execute(query, params).fetchdf()

    def upsert_abstract(self, doi: str, abstract: str, force: bool = False):
        """Insert or update abstract in ssrn_pages, optionally forcing overwrite."""
        if abstract_check.is_junk(abstract):
            return  # page text or a publisher placeholder is never an abstract
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
        """Get articles never searched on SSRN.

        Abstract enrichers (CrossRef, OpenAlex, ...) create ssrn_pages rows with only
        an abstract; those articles still need an SSRN search for the PDF route.
        """
        query = f"""
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE {self._SSRN_NOT_SEARCHED}
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

    def insert_pdf_file_if_absent(
        self,
        doi: str,
        source: str,
        source_url: Optional[str],
        pdf_url: Optional[str],
        pdf_file_path: str,
        match_score: Optional[float] = None,
    ) -> bool:
        """Record a pending PDF without replacing any existing PDF state."""
        pdf_file_path = to_portable(pdf_file_path)
        result = self.conn.execute(
            """
            INSERT INTO pdf_files
                (doi, source, source_url, pdf_url, pdf_file_path, match_score)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (doi) DO NOTHING
            RETURNING doi
            """,
            [doi, source, source_url, pdf_url, pdf_file_path, match_score],
        ).fetchone()
        return result is not None

    def delete_pdf_file(self, doi: str):
        """Remove the pdf_files row (e.g. after quarantining a mismatched PDF)."""
        self.conn.execute("DELETE FROM pdf_files WHERE doi = ?", [doi])

    def get_pdf_file_by_doi(self, doi: str) -> Optional[Dict]:
        """Get the current PDF row for one article, or None if there is none."""
        result = self.conn.execute(
            """
            SELECT doi, source, pdf_file_path, verify_status
            FROM pdf_files WHERE doi = ?
        """,
            [doi],
        ).fetchone()
        if result:
            return dict(zip(["doi", "source", "pdf_file_path", "verify_status"], result))
        return None

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

    def get_articles_for_institutional(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Articles eligible for the EZproxy institutional resolver.

        Same base predicate as get_articles_without_pdf, plus: the open-access
        fallback stage must already have tried (any oa/nber/arxiv candidate row),
        so the expensive browser path runs last.
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
              AND EXISTS (
                  SELECT 1 FROM pdf_candidates c
                  WHERE c.doi = a.doi AND c.source IN ('oa', 'nber', 'arxiv')
              )
            ORDER BY a.year DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query).fetchdf()

    def get_terminal_elsevier_residuals(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Get Elsevier papers whose SSRN and free-PDF routes are terminal.

        A paper is eligible only when it has no local PDF, SSRN has no URL or
        was explicitly marked unavailable, and each free fallback has an exact
        ``no_match`` row. In particular, missing and ``error`` candidate rows do
        not satisfy the predicate and remain retryable.
        """
        normalized_doi = """
            regexp_replace(
                lower(trim(a.doi)),
                '^(https?://(dx\\.)?doi\\.org/|doi:[[:space:]]*)+',
                ''
            )
        """
        query = f"""
            SELECT a.doi, a.title, a.authors, a.year, a.journal_name
            FROM articles a
            WHERE NOT EXISTS (
                      SELECT 1 FROM pdf_files p WHERE p.doi = a.doi
                  )
              AND (
                  NOT EXISTS (
                      SELECT 1 FROM ssrn_pages s
                      WHERE s.doi = a.doi
                        AND nullif(trim(s.ssrn_url), '') IS NOT NULL
                  )
                  OR EXISTS (
                      SELECT 1 FROM processing_log pl
                      WHERE pl.doi = a.doi
                        AND pl.stage = 'download_pdf'
                        AND pl.status = 'unavailable'
                  )
              )
              AND EXISTS (
                  SELECT 1 FROM pdf_candidates c
                  WHERE c.doi = a.doi AND c.source = 'oa' AND c.status = 'no_match'
              )
              AND EXISTS (
                  SELECT 1 FROM pdf_candidates c
                  WHERE c.doi = a.doi AND c.source = 'nber' AND c.status = 'no_match'
              )
              AND EXISTS (
                  SELECT 1 FROM pdf_candidates c
                  WHERE c.doi = a.doi AND c.source = 'arxiv' AND c.status = 'no_match'
              )
              AND (
                  lower(coalesce(a.publisher, '')) LIKE '%elsevier%'
                  OR {normalized_doi} LIKE '10.1016/%'
              )
            ORDER BY {normalized_doi}, a.doi
        """
        params = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        return self.conn.execute(query, params).fetchdf()

    def get_recent_candidate_checks(self, no_match_cutoff, error_cutoff) -> set:
        """(doi, source) pairs to skip: recent no_match/downloaded, or recent errors.

        Errors get their own (shorter) window so an auth-shaped failure does not
        suppress retries for the full recheck period.
        """
        rows = self.conn.execute(
            """
            SELECT doi, source FROM pdf_candidates
            WHERE (status = 'error' AND checked_at >= ?)
               OR (status != 'error' AND checked_at >= ?)
        """,
            [error_cutoff, no_match_cutoff],
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

    def get_run_attention_items(self, run_id: str) -> List[Dict]:
        """Quarantined PDFs and flagged/failed wiki pages since a run started."""
        result = self.conn.execute(
            """
            SELECT doi, stage, status, error_message
            FROM processing_log
            WHERE processed_at >= (
                    SELECT MIN(started_at) FROM pipeline_runs WHERE run_id = ?
                  )
              AND ((stage = 'verify_pdf' AND status = 'mismatch')
                   OR (stage = 'wiki_ingest' AND status IN ('flagged', 'failed')))
            ORDER BY processed_at
        """,
            [run_id],
        ).fetchall()
        return [dict(zip(["doi", "stage", "status", "error_message"], row)) for row in result]

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

    # Front matter (mastheads, reports, calls, issue-level DOIs); shared rule set
    _SUSPECTED_NON_ARTICLE = front_matter.sql_predicate("a")

    def get_dashboard_data(self, recent_per_journal: int = 25) -> Dict[str, List[Dict]]:
        """Everything the HTML dashboard needs, as JSON-ready lists of dicts."""
        base = f"""
            WITH a AS (SELECT a.*, {self._SUSPECTED_NON_ARTICLE} AS suspect FROM articles a)
            SELECT a.*,
                   (s.abstract IS NOT NULL AND s.abstract <> '') AS has_abstract,
                   NOT {self._SSRN_NOT_SEARCHED} AS ssrn_searched,
                   s.ssrn_url IS NOT NULL AS ssrn_found,
                   p.doi IS NOT NULL AS has_pdf, p.source AS pdf_source, p.verify_status,
                   w.status AS wiki_status
            FROM a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            LEFT JOIN pdf_files p ON a.doi = p.doi
            LEFT JOIN wiki_pages w ON a.doi = w.doi
        """

        def records(query: str, params=None) -> List[Dict]:
            df = self.conn.execute(query, params or []).fetchdf()
            for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
                df[col] = df[col].astype(str)
            return df.astype(object).where(df.notna(), None).to_dict("records")

        return {
            "journal_years": records(
                f"""
                SELECT journal_issn, any_value(journal_name) AS journal_name, year,
                       COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE has_abstract) AS n_abstract,
                       COUNT(*) FILTER (WHERE ssrn_searched) AS n_ssrn_searched,
                       COUNT(*) FILTER (WHERE ssrn_found) AS n_ssrn_found,
                       COUNT(*) FILTER (WHERE has_pdf) AS n_pdf,
                       COUNT(*) FILTER (WHERE verify_status = 'match') AS n_verified,
                       COUNT(*) FILTER (WHERE wiki_status = 'ingested') AS n_wiki,
                       COUNT(*) FILTER (WHERE suspect) AS n_suspect
                FROM ({base}) GROUP BY journal_issn, year ORDER BY journal_issn, year
            """
            ),
            "recent_articles": records(
                f"""
                SELECT journal_issn, doi, title, authors, year, has_abstract, ssrn_found,
                       pdf_source, verify_status, wiki_status, suspect
                FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY journal_issn, suspect ORDER BY year DESC, created_at DESC
                    ) AS rn
                    FROM ({base})
                )
                WHERE rn <= ?
                ORDER BY journal_issn, year DESC
            """,
                [recent_per_journal],
            ),
            "activity": records(
                """
                SELECT CAST(processed_at AS DATE) AS day, stage, status, COUNT(*) AS n
                FROM processing_log
                WHERE processed_at >= current_date - INTERVAL 30 DAY
                GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC
            """
            ),
            "pdf_sources": records(
                """
                SELECT source, verify_status, COUNT(*) AS n
                FROM pdf_files GROUP BY 1, 2 ORDER BY 3 DESC
            """
            ),
        }

    def get_front_matter_candidates(self) -> pd.DataFrame:
        """Front-matter records, with flags for rows that must not be deleted blindly."""
        return self.conn.execute(
            f"""
            SELECT a.doi, a.title, a.journal_name, a.year,
                   EXISTS (SELECT 1 FROM pdf_files p WHERE p.doi = a.doi) AS has_pdf,
                   EXISTS (SELECT 1 FROM wiki_pages w WHERE w.doi = a.doi) AS has_wiki
            FROM articles a
            WHERE {self._SUSPECTED_NON_ARTICLE}
            ORDER BY a.journal_name, a.year
        """
        ).fetchdf()

    def delete_articles(self, dois: List[str]) -> Dict[str, int]:
        """Delete articles and their ssrn_pages/pdf_candidates/processing_log rows.

        Callers must exclude articles with pdf_files or wiki_pages rows. DuckDB checks
        foreign keys against the transaction's starting state, so child rows and the
        articles are deleted in two separate transactions.
        """
        if not dois:
            return {}
        self.conn.execute("CREATE OR REPLACE TEMP TABLE _delete_dois (doi VARCHAR PRIMARY KEY)")
        try:
            self.conn.executemany("INSERT INTO _delete_dois VALUES (?)", [[d] for d in dois])
            # Never delete an article that has a PDF or wiki page, whatever the caller passed
            self.conn.execute(
                """
                DELETE FROM _delete_dois WHERE doi IN (SELECT doi FROM pdf_files)
                                            OR doi IN (SELECT doi FROM wiki_pages)
            """
            )
            counts = {}
            self.conn.execute("BEGIN")
            try:
                for table in ("ssrn_pages", "pdf_candidates", "processing_log", "abstract_checks"):
                    counts[table] = self.conn.execute(
                        f"DELETE FROM {table} WHERE doi IN (SELECT doi FROM _delete_dois)"
                    ).fetchone()[0]
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            # Single statement, so atomic on its own; must follow the children's commit
            counts["articles"] = self.conn.execute(
                "DELETE FROM articles WHERE doi IN (SELECT doi FROM _delete_dois)"
            ).fetchone()[0]
            return counts
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _delete_dois")

    def get_articles_for_dedup(self) -> pd.DataFrame:
        """Fields the duplicate-DOI rule needs, for every article."""
        return self.conn.execute(
            "SELECT doi, title, authors, year, journal_issn FROM articles"
        ).fetchdf()

    # Tables whose primary key involves the DOI; an inconsistent ART index once let
    # ssrn_pages hold two identical rows for one DOI
    _KEYED_TABLES = {
        "articles": "doi",
        "ssrn_pages": "doi",
        "pdf_files": "doi",
        "wiki_pages": "doi",
        "pdf_candidates": "doi, source",
    }

    def find_duplicate_keys(self) -> Dict[str, List[Dict]]:
        """Primary keys held by more than one row, per table, with an identical-rows flag."""
        out = {}
        for table, key in self._KEYED_TABLES.items():
            # COUNT(DISTINCT t) counts distinct whole rows (t is the row as a struct)
            rows = self.conn.execute(
                f"""
                SELECT {key}, COUNT(*) AS n_rows, COUNT(DISTINCT t) AS n_distinct
                FROM {table} t GROUP BY {key} HAVING COUNT(*) > 1
            """
            ).fetchdf()
            if not rows.empty:
                out[table] = rows.to_dict("records")
        return out

    def repair_identical_duplicate_rows(self) -> int:
        """Collapse primary-key duplicates whose rows are identical into one row.

        Rows that differ are left alone (callers must refuse to proceed). Returns the
        number of keys repaired.
        """
        repaired = 0
        for table, dups in self.find_duplicate_keys().items():
            cols = [k.strip() for k in self._KEYED_TABLES[table].split(",")]
            for dup in dups:
                if dup["n_distinct"] != 1:
                    continue
                where = " AND ".join(f"{c} = ?" for c in cols)
                params = [dup[c] for c in cols]
                # Separate statements: inside one transaction the delete and re-insert of
                # the corrupted key fail with a write-write conflict. The row waits in a
                # temp table in between (and is in the caller's backup).
                self.conn.execute(
                    f"CREATE OR REPLACE TEMP TABLE _one_row AS "
                    f"SELECT DISTINCT * FROM {table} WHERE {where}",
                    params,
                )
                self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)
                self.conn.execute(f"INSERT INTO {table} SELECT * FROM _one_row")
                self.conn.execute("DROP TABLE _one_row")
                repaired += 1
        return repaired

    def merge_duplicate_articles(self, groups: List[tuple]) -> Dict:
        """Merge each (keep_doi, [drop_dois]) group onto keep_doi, then delete the drops.

        Per keep DOI, the best ssrn_pages row (SSRN URL > downloaded PDF > abstract >
        searched > newest) and the best pdf_files row (match > uncertain > pending > other)
        win, with a missing abstract filled from another member; pdf_candidates keep the
        kept DOI's row per source; processing_log rows are re-pointed. Groups where a
        dropped DOI has a wiki page are skipped (the page's front matter names the DOI).

        Child rows change in one transaction; the articles are deleted in a second one,
        because DuckDB checks foreign keys against the transaction's starting state.
        """
        if not groups:
            return {"groups": 0, "skipped_wiki": []}
        c = self.conn
        c.execute("CREATE OR REPLACE TEMP TABLE _merge_map (doi VARCHAR PRIMARY KEY, keep VARCHAR)")
        try:
            rows = [[keep, keep] for keep, _ in groups]
            rows += [[d, keep] for keep, drops in groups for d in drops]
            c.executemany("INSERT INTO _merge_map VALUES (?, ?)", rows)
            skipped = [
                r[0]
                for r in c.execute(
                    """
                    SELECT DISTINCT m.keep FROM _merge_map m JOIN wiki_pages w ON w.doi = m.doi
                    WHERE m.doi <> m.keep ORDER BY 1
                """
                ).fetchall()
            ]
            c.execute(
                """
                DELETE FROM _merge_map WHERE keep IN (
                    SELECT m.keep FROM _merge_map m JOIN wiki_pages w ON w.doi = m.doi
                    WHERE m.doi <> m.keep)
            """
            )
            # Winning child rows, re-keyed to the kept DOI (src_doi records where they came from)
            c.execute(
                """
                CREATE OR REPLACE TEMP TABLE _new_ssrn AS
                WITH ranked AS (
                    SELECT m.keep, s.*,
                        row_number() OVER w AS rn,
                        first_value(nullif(s.abstract, '') IGNORE NULLS) OVER (
                            w ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        ) AS best_abstract
                    FROM _merge_map m JOIN ssrn_pages s ON s.doi = m.doi
                    WINDOW w AS (PARTITION BY m.keep ORDER BY
                        (s.ssrn_url IS NOT NULL) DESC,
                        coalesce(s.pdf_downloaded, false) DESC,
                        (coalesce(s.abstract, '') <> '') DESC,
                        (s.html_file_path IS NOT NULL OR s.error_message IS NOT NULL) DESC,
                        s.scraped_at DESC NULLS LAST,
                        (s.doi = m.keep) DESC)
                )
                SELECT * EXCLUDE (keep, rn, best_abstract)
                       REPLACE (keep AS doi, coalesce(nullif(abstract, ''), best_abstract) AS abstract),
                       doi AS src_doi,
                       coalesce(nullif(abstract, ''), '') = '' AND best_abstract IS NOT NULL
                           AS abstract_filled
                FROM ranked WHERE rn = 1
            """
            )
            c.execute(
                """
                CREATE OR REPLACE TEMP TABLE _new_pdf AS
                SELECT * EXCLUDE (keep, rn) REPLACE (keep AS doi), doi AS src_doi FROM (
                    SELECT m.keep, p.*, row_number() OVER (PARTITION BY m.keep ORDER BY
                        CASE p.verify_status WHEN 'match' THEN 0 WHEN 'uncertain' THEN 1
                                             WHEN 'pending' THEN 2 ELSE 3 END,
                        (p.doi = m.keep) DESC, p.downloaded_at DESC NULLS LAST) AS rn
                    FROM _merge_map m JOIN pdf_files p ON p.doi = m.doi
                ) WHERE rn = 1
            """
            )
            c.execute(
                """
                CREATE OR REPLACE TEMP TABLE _new_cand AS
                SELECT * EXCLUDE (keep, rn) REPLACE (keep AS doi), doi AS src_doi FROM (
                    SELECT m.keep, k.*, row_number() OVER (PARTITION BY m.keep, k.source ORDER BY
                        (k.doi = m.keep) DESC, k.checked_at DESC NULLS LAST) AS rn
                    FROM _merge_map m JOIN pdf_candidates k ON k.doi = m.doi
                ) WHERE rn = 1
            """
            )

            def moved(table):
                return c.execute(f"SELECT COUNT(*) FROM {table} WHERE src_doi <> doi").fetchone()[0]

            counts = {
                "groups": c.execute("SELECT COUNT(DISTINCT keep) FROM _merge_map").fetchone()[0],
                "ssrn_rows_taken_from_dropped": moved("_new_ssrn"),
                "abstracts_filled": c.execute(
                    "SELECT COUNT(*) FROM _new_ssrn WHERE abstract_filled"
                ).fetchone()[0],
                "pdf_files_moved": moved("_new_pdf"),
                "pdf_candidates_moved": moved("_new_cand"),
            }
            c.execute("BEGIN")
            try:
                for table, new in [
                    ("ssrn_pages", "_new_ssrn"),
                    ("pdf_files", "_new_pdf"),
                    ("pdf_candidates", "_new_cand"),
                ]:
                    c.execute(f"DELETE FROM {table} WHERE doi IN (SELECT doi FROM _merge_map)")
                    c.execute(
                        f"INSERT INTO {table} BY NAME "
                        f"SELECT * EXCLUDE (src_doi{', abstract_filled' if table == 'ssrn_pages' else ''}) FROM {new}"
                    )
                # Checks belong to the old abstracts; the kept DOI is re-checked next run
                c.execute("DELETE FROM abstract_checks WHERE doi IN (SELECT doi FROM _merge_map)")
                counts["processing_log_repointed"] = c.execute(
                    """
                    UPDATE processing_log SET doi = m.keep FROM _merge_map m
                    WHERE processing_log.doi = m.doi AND m.doi <> m.keep
                """
                ).fetchone()[0]
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            # Must follow the children's commit; a single statement, so atomic on its own
            counts["articles_deleted"] = c.execute(
                "DELETE FROM articles WHERE doi IN (SELECT doi FROM _merge_map WHERE doi <> keep)"
            ).fetchone()[0]
            counts["skipped_wiki"] = skipped
            return counts
        finally:
            for t in ("_merge_map", "_new_ssrn", "_new_pdf", "_new_cand"):
                c.execute(f"DROP TABLE IF EXISTS {t}")

    def get_abstracts_for_pdf_check(self, rerun: bool = False, limit: Optional[int] = None):
        """Verified PDFs whose current abstract has no PDF check yet (all of them if rerun)."""
        query = """
            SELECT p.doi, p.pdf_file_path, s.abstract
            FROM pdf_files p
            JOIN ssrn_pages s ON s.doi = p.doi
            LEFT JOIN abstract_checks c ON c.doi = p.doi
            WHERE p.verify_status = 'match' AND coalesce(s.abstract, '') <> ''
              AND (? OR c.doi IS NULL OR c.compared_with <> 'pdf'
                   OR c.abstract_md5 <> md5(s.abstract))
            ORDER BY p.doi
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.conn.execute(query, [rerun]).fetchdf()

    def record_abstract_check(
        self, doi: str, status: str, score: Optional[float], compared_with: str
    ):
        """Store a check result tied to the abstract as it is now (its md5)."""
        self.conn.execute(
            """
            INSERT INTO abstract_checks (doi, abstract_md5, status, score, compared_with)
            SELECT doi, md5(coalesce(abstract, '')), ?, ?, ? FROM ssrn_pages WHERE doi = ?
            ON CONFLICT (doi) DO UPDATE SET
                abstract_md5 = EXCLUDED.abstract_md5, status = EXCLUDED.status,
                score = EXCLUDED.score, compared_with = EXCLUDED.compared_with,
                checked_at = now()
        """,
            [status, score, compared_with, doi],
        )

    def get_flagged_abstracts(self) -> pd.DataFrame:
        """Current abstracts whose check did not pass."""
        return self.conn.execute(
            """
            SELECT c.doi, a.journal_name, a.year, a.title, c.status, c.score, c.compared_with,
                   left(s.abstract, 300) AS abstract_start
            FROM abstract_checks c
            JOIN ssrn_pages s ON s.doi = c.doi
            JOIN articles a ON a.doi = c.doi
            WHERE c.status <> 'match' AND c.abstract_md5 = md5(coalesce(s.abstract, ''))
            ORDER BY c.score NULLS FIRST
        """
        ).fetchdf()

    def get_junk_abstracts(self) -> pd.DataFrame:
        """Abstracts that are SSRN page text or a publisher placeholder."""
        markers = abstract_check._PAGE_TEXT_MARKERS + abstract_check._PLACEHOLDER_MARKERS
        where = " OR ".join("abstract ILIKE ?" for _ in markers)
        df = self.conn.execute(
            f"SELECT doi, abstract FROM ssrn_pages WHERE {where}", [f"%{m}%" for m in markers]
        ).fetchdf()
        return df[df["abstract"].map(abstract_check.is_junk)]

    def set_abstract(self, doi: str, abstract: Optional[str]):
        """Replace (or clear, with None) the stored abstract; repair only."""
        self.conn.execute("UPDATE ssrn_pages SET abstract = ? WHERE doi = ?", [abstract, doi])

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
            self.conn.execute(
                """
            SELECT year, COUNT(*) as count
            FROM articles
            GROUP BY year
            ORDER BY year DESC
        """
            )
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
        stats["pending_ssrn_scrapes"] = self.conn.execute(
            f"""
            SELECT COUNT(*) FROM articles a
            LEFT JOIN ssrn_pages s ON a.doi = s.doi
            WHERE {self._SSRN_NOT_SEARCHED}
        """
        ).fetchone()[0]

        stats["pending_pdf_downloads"] = self.conn.execute(
            """
            SELECT COUNT(*) FROM ssrn_pages
            WHERE pdf_url IS NOT NULL AND (pdf_downloaded = FALSE OR pdf_downloaded IS NULL)
        """
        ).fetchone()[0]

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
        stats["pdfs_quarantined"] = self.conn.execute(
            """
            SELECT COUNT(DISTINCT doi) FROM processing_log
            WHERE stage = 'verify_pdf' AND status = 'mismatch'
        """
        ).fetchone()[0]

        # Wiki ingestion
        stats["wiki_ingested"] = self.conn.execute(
            """
            SELECT COUNT(*) FROM wiki_pages WHERE status IN ('ingested', 'flagged')
        """
        ).fetchone()[0]

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
