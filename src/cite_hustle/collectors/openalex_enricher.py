"""OpenAlex abstract enrichment for missing abstracts."""

from __future__ import annotations

import asyncio
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import quote

import httpx

from cite_hustle.config import settings
from cite_hustle.database.repository import ArticleRepository


class OpenAlexEnricher:
    """Fetch missing abstracts from OpenAlex using DOIs."""

    BASE_URL = "https://api.openalex.org"

    def __init__(
        self,
        repo: ArticleRepository,
        concurrency: int = 8,
        timeout_s: float = 20.0,
        min_length: int = 40,
        max_retries: int = 3,
        delay_s: float = 0.0,
    ):
        self.repo = repo
        self.concurrency = max(1, concurrency)
        self.timeout_s = timeout_s
        self.min_length = min_length
        self.max_retries = max_retries
        self.delay_s = max(0.0, delay_s)
        self._semaphore = asyncio.Semaphore(self.concurrency)

    @staticmethod
    def normalize_doi(doi: str) -> Optional[str]:
        if not doi:
            return None
        normalized = doi.strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if normalized.startswith(prefix):
                normalized = normalized.replace(prefix, "", 1)
        return normalized or None

    @staticmethod
    def reconstruct_abstract(inverted_index: Optional[Dict[str, Iterable[int]]]) -> Optional[str]:
        if not inverted_index:
            return None
        word_index: Dict[int, str] = {}
        for word, positions in inverted_index.items():
            for pos in positions:
                word_index[pos] = word
        if not word_index:
            return None
        return " ".join(word_index[i] for i in sorted(word_index.keys()))

    async def _fetch_abstract(
        self, client: httpx.AsyncClient, doi: str
    ) -> Tuple[Optional[str], Optional[str]]:
        url = f"{self.BASE_URL}/works/https://doi.org/{quote(doi, safe='')}"
        params = {}
        if settings.crossref_email:
            params["mailto"] = settings.crossref_email

        for attempt in range(self.max_retries):
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt == self.max_retries - 1:
                    return None, f"request_error: {exc}"
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 404:
                return None, "not_found"
            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt == self.max_retries - 1:
                    return None, f"http_{response.status_code}"
                await asyncio.sleep(2**attempt)
                continue
            if response.is_error:
                return None, f"http_{response.status_code}"

            data = response.json()
            inverted = data.get("abstract_inverted_index")
            abstract = self.reconstruct_abstract(inverted)
            if not abstract or len(abstract.strip()) < self.min_length:
                return None, "empty_abstract"

            return abstract.strip(), None

        return None, "max_retries_exceeded"

    async def _process_article(self, client: httpx.AsyncClient, doi: str, force: bool) -> str:
        async with self._semaphore:
            normalized = self.normalize_doi(doi)
            if not normalized:
                self.repo.log_processing(doi, "enrich_openalex", "failed", "invalid_doi")
                return "invalid_doi"

            if self.delay_s:
                await asyncio.sleep(self.delay_s)

            abstract, error = await self._fetch_abstract(client, normalized)
            if abstract:
                self.repo.upsert_abstract(doi, abstract, force=force)
                self.repo.log_processing(doi, "enrich_openalex", "success")
                return "updated"

            self.repo.log_processing(doi, "enrich_openalex", "failed", error or "unknown_error")
            return error or "failed"

    async def enrich_missing_abstracts(
        self, articles: Iterable[Dict], force: bool = False
    ) -> Dict[str, int]:
        article_list = list(articles)
        stats = {
            "total": len(article_list),
            "updated": 0,
            "not_found": 0,
            "empty_abstract": 0,
            "invalid_doi": 0,
            "failed": 0,
        }

        if not article_list:
            return stats

        headers = {"User-Agent": "cite-hustle/0.1 (OpenAlex enrichment)"}
        async with httpx.AsyncClient(timeout=self.timeout_s, headers=headers) as client:
            tasks = [
                self._process_article(client, row.get("doi"), force)
                for row in article_list
                if row.get("doi")
            ]

            for result in await asyncio.gather(*tasks, return_exceptions=False):
                if result == "updated":
                    stats["updated"] += 1
                elif result == "not_found":
                    stats["not_found"] += 1
                elif result == "empty_abstract":
                    stats["empty_abstract"] += 1
                elif result == "invalid_doi":
                    stats["invalid_doi"] += 1
                else:
                    stats["failed"] += 1

        return stats
