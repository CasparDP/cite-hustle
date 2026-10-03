"""CrossRef metadata collector for academic articles"""

import concurrent.futures
import html
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from tqdm import tqdm

from cite_hustle.collectors.journals import Journal
from cite_hustle.config import settings
from cite_hustle.database.repository import ArticleRepository


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_ROWS = 1000
CROSSREF_MAX_ATTEMPTS = 5


class MetadataCollector:
    """Collects article metadata from CrossRef API"""

    # Keywords to identify non-article content
    # NOTE: These should be specific to avoid false positives
    NON_ARTICLE_KEYWORDS = [
        "front matter",
        "back matter",
        "cover",
        "covers",
        "book review",
        "books received",
        "editorial board",
        "editorial note",
        "erratum",
        "corrigendum",
        "correction",
        "retraction",
        "index to volume",
        "subject index",
        "author index",
        "table of contents",
        "masthead",
        "issue information",
        "title pages",
        "copyright page",
    ]

    # Patterns that indicate start/end of title (more precise)
    NON_ARTICLE_PATTERNS = [
        r"^announcements?$",  # "Announcements" by itself
        r"^announcements? and ",  # "Announcements and News"
        r"^editorial$",  # "Editorial" by itself
        r"^contents",  # Starts with "Contents"
        r"^volume \d+",  # "Volume 45 Issue 3"
        r"^issue \d+",  # "Issue 3"
    ]

    # Valid CrossRef types for research articles
    VALID_TYPES = ["journal-article", "proceedings-article"]

    def __init__(
        self,
        repo: ArticleRepository,
        cache_dir: Optional[Path] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        """
        Initialize metadata collector

        Args:
            repo: Article repository for database access
            cache_dir: Directory for caching API responses (defaults to settings.cache_dir)
            http_client: HTTP client for CrossRef (injectable for tests)
        """
        self.repo = repo
        self.cache_dir = cache_dir or settings.cache_dir
        self.cache_dir.mkdir(exist_ok=True, parents=True)
        user_agent = "cite-hustle/0.1"
        if settings.crossref_email:
            user_agent += f" (mailto:{settings.crossref_email})"
        self.http = http_client or httpx.Client(
            timeout=60, headers={"User-Agent": user_agent}, follow_redirects=True
        )

    def _get_works_page(self, params: Dict) -> Dict:
        """GET one /works page, backing off on 429 and 5xx (honors Retry-After)."""
        for attempt in range(CROSSREF_MAX_ATTEMPTS):
            try:
                response = self.http.get(CROSSREF_WORKS_URL, params=params)
            except httpx.TransportError as e:
                error = f"{type(e).__name__}: {e}"
            else:
                if response.status_code == 200:
                    return response.json()["message"]
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                error = f"API returned code {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(float(retry_after))
                    continue
            time.sleep(5.0 * 2**attempt)
        raise ConnectionError(f"{error} after {CROSSREF_MAX_ATTEMPTS} attempts")

    @staticmethod
    def clean_title(title: str) -> str:
        """
        Clean HTML tags and entities from article titles

        Args:
            title: Raw title string that may contain HTML

        Returns:
            Cleaned title string
        """
        if not title:
            return title

        # Use BeautifulSoup to remove HTML tags
        soup = BeautifulSoup(title, "html.parser")
        text = soup.get_text()

        # Decode HTML entities (e.g., &amp; -> &, &lt; -> <)
        text = html.unescape(text)

        # Clean up extra whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text

    @classmethod
    def is_valid_article(cls, article: Dict) -> bool:
        """
        Check if an item from CrossRef is a valid research article

        Uses both keyword matching and regex patterns to avoid false positives.

        Args:
            article: CrossRef article dictionary

        Returns:
            True if valid research article, False otherwise
        """
        # Check CrossRef type
        article_type = article.get("type", "")
        if article_type not in cls.VALID_TYPES:
            return False

        # Check title for non-article keywords
        title = " ".join(article.get("title", [""])).lower()

        # Check exact keyword matches (full phrases)
        for keyword in cls.NON_ARTICLE_KEYWORDS:
            if keyword in title:
                return False

        # Check regex patterns for more precise matching
        for pattern in cls.NON_ARTICLE_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                return False

        # Must have a DOI
        if not article.get("DOI"):
            return False

        return True

    def fetch_articles_by_issn(self, year: int, issn: str) -> List[Dict]:
        """
        Fetch articles from CrossRef API for a specific journal and year

        Args:
            year: Publication year
            issn: Journal ISSN

        Returns:
            List of article dictionaries from CrossRef
        """
        cache_file = self.cache_dir / f"cache_{issn}_{year}.json"

        # Check cache first
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(f"⚠️  Corrupted cache file, re-fetching: {cache_file}")
                cache_file.unlink()

        params = {
            "filter": f"issn:{issn},from-pub-date:{year}-01-01,until-pub-date:{year}-12-31",
            "rows": CROSSREF_ROWS,
            "cursor": "*",
        }
        if settings.crossref_email:
            params["mailto"] = settings.crossref_email

        try:
            # Deep paging: CrossRef omits next-cursor on the last page, so stop on
            # an empty or short page as well as on a missing cursor.
            articles = []
            while True:
                message = self._get_works_page(params)
                items = message.get("items", [])
                articles.extend(items)
                next_cursor = message.get("next-cursor")
                if not items or not next_cursor or len(items) < CROSSREF_ROWS:
                    break
                params["cursor"] = next_cursor

            # Cache the results
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(articles, f, indent=2)

            return articles

        except Exception as e:
            print(f"✗ Error fetching {issn} for {year}: {e}")
            self.repo.log_processing(
                doi=f"{issn}_{year}", stage="metadata_fetch", status="failed", error_message=str(e)
            )
            return []

    def transform_articles(self, articles: List[Dict], journal: Journal) -> List[Dict]:
        """
        Transform CrossRef article data into database format

        Filters out non-article content (book reviews, front matter, etc.)

        Args:
            articles: Raw articles from CrossRef
            journal: Journal metadata

        Returns:
            List of article dictionaries ready for database insertion
        """
        transformed = []
        filtered_count = 0

        for article in articles:
            # Filter out non-articles
            if not self.is_valid_article(article):
                filtered_count += 1
                continue

            doi = article.get("DOI", "")
            if not doi:
                continue

            # Get title and clean HTML tags/entities
            raw_title = " ".join(article.get("title", ["No Title Available"]))
            title = self.clean_title(raw_title)

            year = article.get("issued", {}).get("date-parts", [[None]])[0][0]

            if not year:
                continue

            # Combine author names
            authors_list = article.get("author", [])
            if authors_list:
                author_names = []
                for author in authors_list:
                    given = author.get("given", "")
                    family = author.get("family", "")
                    if given and family:
                        author_names.append(f"{given} {family}")
                    elif family:
                        author_names.append(family)
                authors = "; ".join(author_names)
            else:
                authors = "Unknown"

            publisher = article.get("publisher", "Unknown")

            transformed.append(
                {
                    "doi": doi,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "journal_issn": journal.issn,
                    "journal_name": journal.name,
                    "publisher": publisher,
                }
            )

        if filtered_count > 0:
            print(f"  ℹ️  Filtered out {filtered_count} non-article items")

        return transformed

    def collect_for_journal(
        self, journal: Journal, years: List[int], show_progress: bool = True, force: bool = False
    ) -> int:
        """
        Collect articles for a specific journal across multiple years

        Args:
            journal: Journal to collect articles for
            years: List of years to collect
            show_progress: Show progress bar
            force: If True, bypass the "already in database" check and re-fetch

        Returns:
            Total number of articles collected
        """
        total_articles = 0

        iterator = tqdm(years, desc=f"Collecting {journal.name}") if show_progress else years

        for year in iterator:
            # Check if already processed (skip if force=True)
            if not force:
                existing = self.repo.conn.execute(
                    """
                    SELECT COUNT(*) FROM articles
                    WHERE journal_issn = ? AND year = ?
                """,
                    [journal.issn, year],
                ).fetchone()[0]

                if existing > 0:
                    if show_progress:
                        tqdm.write(f"  ✓ {year}: {existing} articles already in database")
                    continue

            # Fetch from CrossRef
            articles = self.fetch_articles_by_issn(year, journal.issn)

            if not articles:
                continue

            # Transform and save (with filtering)
            transformed = self.transform_articles(articles, journal)

            if transformed:
                self.repo.bulk_insert_articles(transformed)
                total_articles += len(transformed)

                # Log success
                self.repo.log_processing(
                    doi=f"{journal.issn}_{year}",
                    stage="metadata_collect",
                    status="success",
                    error_message=f"Collected {len(transformed)} articles",
                )

                if show_progress:
                    tqdm.write(f"  ✓ {year}: {len(transformed)} articles collected")

        return total_articles

    def collect_for_journals(
        self,
        journals: List[Journal],
        years: List[int],
        max_workers: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, int]:
        """
        Collect articles for multiple journals in parallel

        Args:
            journals: List of journals to collect
            years: List of years to collect
            max_workers: Number of parallel workers (defaults to settings.max_workers)
            force: If True, bypass the "already in database" check and re-fetch

        Returns:
            Dictionary mapping journal name to article count
        """
        max_workers = max_workers or settings.max_workers
        results = {}

        print(f"📚 Collecting metadata for {len(journals)} journals across {len(years)} years")
        print(f"Using {max_workers} parallel workers")
        print(f"Cache directory: {self.cache_dir}")
        if force:
            print(f"Force mode: ON (bypassing DB checks)\n")
        else:
            print()

        # Process journals sequentially to show clear progress
        for journal in journals:
            print(f"\n{journal.name} ({journal.issn}):")
            count = self.collect_for_journal(journal, years, show_progress=True, force=force)
            results[journal.name] = count

            if count > 0:
                print(f"  ✓ Total: {count} articles collected")
            else:
                print(f"  ℹ️  No new articles")

        return results

    def collect_parallel(
        self,
        journals: List[Journal],
        years: List[int],
        max_workers: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, int]:
        """
        Collect articles for multiple journals with true parallelism

        Note: Use with caution as this may hit API rate limits

        Args:
            journals: List of journals to collect
            years: List of years to collect
            max_workers: Number of parallel workers
            force: If True, bypass the "already in database" check and re-fetch

        Returns:
            Dictionary mapping journal name to article count
        """
        max_workers = max_workers or settings.max_workers
        results = {}

        print(f"📚 Collecting metadata (parallel mode)")
        print(f"Journals: {len(journals)} | Years: {len(years)} | Workers: {max_workers}")
        if force:
            print(f"Force mode: ON (bypassing DB checks)\n")
        else:
            print()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.collect_for_journal, journal, years, False, force): journal
                for journal in journals
            }

            with tqdm(total=len(journals), desc="Processing journals") as pbar:
                for future in concurrent.futures.as_completed(futures):
                    journal = futures[future]
                    try:
                        count = future.result()
                        results[journal.name] = count
                        pbar.set_postfix_str(f"{journal.name}: {count} articles")
                    except Exception as e:
                        print(f"\n✗ Error processing {journal.name}: {e}")
                        results[journal.name] = 0
                    finally:
                        pbar.update(1)

        return results
