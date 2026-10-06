"""Abstract sources beyond SSRN and OpenAlex: CrossRef, Semantic Scholar, NBER.

Every source fills only missing abstracts (``upsert_abstract`` with force=False) and
logs under stage ``abstract_<source>``. The API sources (s2, nber) log every attempt so
repeat runs can skip recent ``success``/``no_match`` attempts; ``failed`` rows (network,
rate limit) are retried on the next run. The CrossRef cache backfill is a free local
scan, so it logs successes only and rescans everything each run.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from cite_hustle.collectors.fallback_resolvers import BaseResolver, NBERResolver, ResolverError
from cite_hustle.config import settings
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.matching import author_last_names

MIN_ABSTRACT_LENGTH = 100


def stage_for(source: str) -> str:
    return f"abstract_{source}"


def clean_abstract(raw: Optional[str]) -> Optional[str]:
    """Plain text from a JATS/HTML abstract; None if too short to be a real abstract."""
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    text = re.sub(r"^abstract\b[\s:.]*", "", text, flags=re.IGNORECASE)
    return text if len(text) >= MIN_ABSTRACT_LENGTH else None


def crossref_abstract(item: Dict) -> Optional[str]:
    return clean_abstract(item.get("abstract"))


def save_abstract(repo: ArticleRepository, doi: str, source: str, abstract: Optional[str]) -> bool:
    """Store a found abstract (never overwriting one) and log the attempt."""
    if abstract:
        repo.upsert_abstract(doi, abstract, force=False)
        repo.log_processing(doi, stage_for(source), "success")
        return True
    repo.log_processing(doi, stage_for(source), "no_match")
    return False


def backfill_from_crossref_cache(repo: ArticleRepository, cache_dir: Path) -> Counter:
    """Fill missing abstracts from cached CrossRef responses (no API calls)."""
    missing = {doi.lower(): doi for doi in repo.get_articles_missing_abstract()["doi"]}
    stats = Counter(candidates=len(missing))
    for cache_file in sorted(cache_dir.glob("cache_*.json")):
        try:
            items = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stats["bad_cache_files"] += 1
            continue
        for item in items:
            doi = missing.get((item.get("DOI") or "").lower())
            abstract = crossref_abstract(item) if doi else None
            if abstract:
                save_abstract(repo, doi, "crossref", abstract)
                del missing[doi.lower()]
                stats["updated"] += 1
    return stats


def crossref_abstracts_from_cache(cache_dir: Path, dois) -> Dict[str, str]:
    """CrossRef's own (DOI-exact) abstract per DOI from the cached responses, lowercased keys."""
    wanted = {doi.lower() for doi in dois}
    found: Dict[str, str] = {}
    for cache_file in sorted(cache_dir.glob("cache_*.json")):
        try:
            items = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for item in items:
            doi = (item.get("DOI") or "").lower()
            if doi in wanted and doi not in found:
                abstract = crossref_abstract(item)
                if abstract:
                    found[doi] = abstract
    return found


class SemanticScholarSource:
    """Semantic Scholar Graph API batch lookup (up to 500 DOIs per request)."""

    source = "s2"
    URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
    BATCH_SIZE = 500
    MAX_ATTEMPTS = 6

    def __init__(self, client: Optional[httpx.Client] = None, api_key: Optional[str] = None):
        api_key = settings.s2_api_key if api_key is None else api_key
        headers = {"x-api-key": api_key} if api_key else {}
        self.client = client or httpx.Client(timeout=60, headers=headers)

    def fetch(self, dois: List[str]) -> Dict[str, Optional[str]]:
        """Map each DOI to its abstract (None when S2 has no abstract or no record)."""
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = self.client.post(
                    self.URL,
                    params={"fields": "abstract"},
                    json={"ids": [f"DOI:{doi}" for doi in dois]},
                )
            except httpx.TransportError as exc:
                error = f"request_error: {exc}"
            else:
                if response.status_code == 200:
                    papers = response.json()
                    # Results are positional (null for unknown ids); never misalign.
                    if not isinstance(papers, list) or len(papers) != len(dois):
                        raise ResolverError("s2_batch_length_mismatch")
                    return {
                        doi: clean_abstract((paper or {}).get("abstract"))
                        for doi, paper in zip(dois, papers)
                    }
                if response.status_code != 429 and response.status_code < 500:
                    raise ResolverError(f"http_{response.status_code}")
                error = f"http_{response.status_code}"
            # Unauthenticated requests share one global pool, so back off generously.
            time.sleep(min(5.0 * 2**attempt, 120.0))
        raise ResolverError(f"{error} after {self.MAX_ATTEMPTS} attempts")


class OpenAlexBatchSource:
    """OpenAlex works filtered by up to 50 DOIs per request (DOI-exact abstracts)."""

    URL = "https://api.openalex.org/works"
    BATCH_SIZE = 50
    MAX_ATTEMPTS = 5

    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(timeout=60)

    def fetch(self, dois: List[str]) -> Dict[str, Optional[str]]:
        """Map each DOI (lowercased) to its abstract, None when OpenAlex has none."""
        from cite_hustle.collectors.openalex_enricher import OpenAlexEnricher

        # '|' separates filter values and ',' separates filters: such DOIs are skipped
        usable = [d.lower() for d in dois if "|" not in d and "," not in d]
        params = {
            "filter": "doi:" + "|".join(usable),
            "per-page": self.BATCH_SIZE,
            "select": "doi,abstract_inverted_index",
        }
        if settings.crossref_email:
            params["mailto"] = settings.crossref_email
        if settings.openalex_api_key:
            params["api_key"] = settings.openalex_api_key
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = self.client.get(self.URL, params=params)
            except httpx.TransportError as exc:
                error = f"request_error: {exc}"
            else:
                if response.status_code == 200:
                    found: Dict[str, Optional[str]] = {d: None for d in usable}
                    for work in response.json().get("results", []):
                        doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
                        text = OpenAlexEnricher.reconstruct_abstract(
                            work.get("abstract_inverted_index")
                        )
                        if doi in found:
                            found[doi] = clean_abstract(text)
                    return found
                if response.status_code != 429 and response.status_code < 500:
                    raise ResolverError(f"http_{response.status_code}")
                error = f"http_{response.status_code}"
            time.sleep(min(5.0 * 2**attempt, 120.0))
        raise ResolverError(f"{error} after {self.MAX_ATTEMPTS} attempts")


class NBERAbstractSource(BaseResolver):
    """NBER working-paper landing page, matched by title search.

    Abstracts get no downstream verification (unlike PDFs), so a match needs a
    near-exact title and at least one shared author surname.
    """

    source = "nber"
    TITLE_THRESHOLD = 95

    def fetch(self, client: httpx.Client, title: str, authors: str = "") -> Optional[str]:
        response = self._get(
            client, NBERResolver.SEARCH_URL, params={"page": 1, "perPage": 10, "q": title}
        )
        if response.is_error:
            raise ResolverError(f"http_{response.status_code}")
        try:
            results = response.json().get("results") or []
        except ValueError as exc:
            raise ResolverError(f"bad_json: {exc}") from exc

        expected_names = set(author_last_names(authors))
        best_url, best_score = None, 0.0
        for item in results:
            candidate = re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip()
            url = str(item.get("url") or "")
            if not candidate or not url.startswith("/papers/"):
                continue
            nber_names = {
                name.split()[-1].lower()
                for name in (
                    re.sub(r"<[^>]+>", "", str(a)).strip() for a in item.get("authors") or []
                )
                if name
            }
            score = fuzz.token_sort_ratio(title.lower(), candidate.lower())
            if score >= self.TITLE_THRESHOLD and expected_names & nber_names and score > best_score:
                best_url, best_score = url, score
        if not best_url:
            return None

        # The search API truncates abstracts; the landing page has the full text.
        page = self._get(client, f"https://www.nber.org{best_url}")
        if page.is_error:
            raise ResolverError(f"http_{page.status_code}")
        node = BeautifulSoup(page.text, "html.parser").select_one(".page-header__intro-inner")
        return clean_abstract(node.decode_contents()) if node else None


def enrich_from_s2(
    repo: ArticleRepository,
    articles: Iterable[Dict],
    source: Optional[SemanticScholarSource] = None,
    delay_s: float = 3.0,
) -> Counter:
    source = source or SemanticScholarSource()
    dois = [row["doi"] for row in articles]
    stats = Counter(candidates=len(dois))
    for start in range(0, len(dois), source.BATCH_SIZE):
        batch = dois[start : start + source.BATCH_SIZE]
        try:
            found = source.fetch(batch)
        except ResolverError as exc:
            for doi in batch:
                repo.log_processing(doi, stage_for(source.source), "failed", str(exc))
            stats["failed"] += len(batch)
            print(f"✗ Semantic Scholar batch failed ({exc}); stopping, rerun later")
            break
        for doi in batch:
            stats["updated" if save_abstract(repo, doi, "s2", found.get(doi)) else "no_match"] += 1
        print(f"  S2 batch {start // source.BATCH_SIZE + 1}: {stats['updated']} found so far")
        time.sleep(delay_s)
    return stats


def enrich_from_nber(
    repo: ArticleRepository,
    articles: Iterable[Dict],
    client: Optional[httpx.Client] = None,
    delay_s: float = 2.0,
) -> Counter:
    source = NBERAbstractSource()
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    articles = list(articles)
    stats = Counter(candidates=len(articles))
    for i, row in enumerate(articles, 1):
        try:
            abstract = source.fetch(client, row["title"], row.get("authors") or "")
        except ResolverError as exc:
            repo.log_processing(row["doi"], stage_for("nber"), "failed", str(exc))
            stats["failed"] += 1
        else:
            stats[
                "updated" if save_abstract(repo, row["doi"], "nber", abstract) else "no_match"
            ] += 1
        if i % 50 == 0:
            print(f"  NBER {i}/{len(articles)}: {stats['updated']} found")
        time.sleep(delay_s)
    return stats
