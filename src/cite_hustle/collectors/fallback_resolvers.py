"""Fallback PDF resolvers for papers SSRN could not provide.

Sources, in default order:
- OAResolver: OpenAlex open-access locations. DOI-exact lookup (zero mismatch
  risk) whose locations often already point at the NBER/arXiv copy.
- NBERResolver: NBER working-paper search (unofficial endpoint; failures are
  recorded and non-fatal).
- ArXivResolver: arXiv Atom API title search.

All resolvers are plain synchronous httpx (these hosts don't Cloudflare-block)
and share the fuzzy title scoring used by the SSRN scraper.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx
from lxml import etree

from cite_hustle.config import settings
from cite_hustle.matching import author_last_names, combined_similarity


@dataclass
class Candidate:
    source: str
    candidate_url: Optional[str]  # landing page
    pdf_url: str
    match_score: float


class ResolverError(Exception):
    """Raised when a source could not be queried (network, HTTP, parse)."""


class BaseResolver:
    """Shared HTTP retry logic and fuzzy matching for fallback sources."""

    source = ""
    MAX_RETRIES = 4

    def __init__(self, threshold: float = 90.0, timeout_s: float = 30.0):
        self.threshold = threshold
        self.timeout_s = timeout_s

    def _get(self, client: httpx.Client, url: str, params: Optional[dict] = None) -> httpx.Response:
        """GET with backoff on 429/5xx; raises ResolverError when exhausted."""
        for attempt in range(self.MAX_RETRIES):
            try:
                response = client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt == self.MAX_RETRIES - 1:
                    raise ResolverError(f"request_error: {exc}") from exc
                time.sleep(2**attempt)
                continue

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 10.0 * (2**attempt)
                except ValueError:
                    wait = 10.0 * (2**attempt)
                time.sleep(min(wait, 180.0))
                continue

            if response.status_code in {500, 502, 503, 504}:
                if attempt == self.MAX_RETRIES - 1:
                    raise ResolverError(f"http_{response.status_code}")
                time.sleep(2**attempt)
                continue

            return response

        raise ResolverError("max_retries_exceeded")

    def resolve(self, client: httpx.Client, article: dict) -> Optional[Candidate]:
        """Return a Candidate above threshold, or None. Raises ResolverError."""
        raise NotImplementedError


class OAResolver(BaseResolver):
    """Open-access PDF via OpenAlex locations (DOI-exact, match_score 100)."""

    source = "oa"
    BASE_URL = "https://api.openalex.org"

    def resolve(self, client: httpx.Client, article: dict) -> Optional[Candidate]:
        doi = article["doi"].strip().lower()
        url = f"{self.BASE_URL}/works/https://doi.org/{quote(doi, safe='')}"
        params = {"mailto": settings.crossref_email} if settings.crossref_email else None

        response = self._get(client, url, params=params)
        if response.status_code == 404:
            return None
        if response.is_error:
            raise ResolverError(f"http_{response.status_code}")

        data = response.json()
        locations = []
        if data.get("best_oa_location"):
            locations.append(data["best_oa_location"])
        locations.extend(data.get("locations") or [])

        for loc in locations:
            pdf_url = (loc or {}).get("pdf_url")
            if not pdf_url:
                continue
            # SSRN links Cloudflare-block plain HTTP; that path is handled by
            # the Selenium downloader, not here.
            if "ssrn.com" in pdf_url:
                continue
            return Candidate(
                source=self.source,
                candidate_url=(loc or {}).get("landing_page_url"),
                pdf_url=pdf_url,
                match_score=100.0,  # DOI-exact lookup
            )
        return None


class NBERResolver(BaseResolver):
    """NBER working paper matched by fuzzy title search (unofficial endpoint)."""

    source = "nber"
    SEARCH_URL = (
        "https://www.nber.org/api/v1/working_page_listing/contentType/" "working_paper/_/_/search"
    )

    def resolve(self, client: httpx.Client, article: dict) -> Optional[Candidate]:
        response = self._get(
            client, self.SEARCH_URL, params={"page": 1, "perPage": 10, "q": article["title"]}
        )
        if response.is_error:
            raise ResolverError(f"http_{response.status_code}")

        try:
            results = response.json().get("results") or []
        except ValueError as exc:
            raise ResolverError(f"bad_json: {exc}") from exc

        best: Optional[Candidate] = None
        best_score = 0.0
        for item in results:
            title = re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip()
            page_url = str(item.get("url") or "")
            m = re.search(r"/papers/(w\d+)", page_url)
            if not title or not m:
                continue
            score = combined_similarity(article["title"], title)
            if score >= self.threshold and score > best_score:
                paper_id = m.group(1)
                best = Candidate(
                    source=self.source,
                    candidate_url=f"https://www.nber.org{page_url}",
                    pdf_url=(
                        f"https://www.nber.org/system/files/working_papers/"
                        f"{paper_id}/{paper_id}.pdf"
                    ),
                    match_score=score,
                )
                best_score = score
        return best


class ArXivResolver(BaseResolver):
    """arXiv preprint matched by fuzzy title search plus author overlap."""

    source = "arxiv"
    API_URL = "http://export.arxiv.org/api/query"
    ATOM = "{http://www.w3.org/2005/Atom}"

    def resolve(self, client: httpx.Client, article: dict) -> Optional[Candidate]:
        # Quotes make it a phrase search; strip embedded quotes from the title.
        title_query = article["title"].replace('"', " ")
        response = self._get(
            client,
            self.API_URL,
            params={"search_query": f'ti:"{title_query}"', "max_results": 5},
        )
        if response.is_error:
            raise ResolverError(f"http_{response.status_code}")

        try:
            root = etree.fromstring(response.content)
        except etree.XMLSyntaxError as exc:
            raise ResolverError(f"bad_xml: {exc}") from exc

        expected_names = set(author_last_names(article.get("authors") or ""))

        best: Optional[Candidate] = None
        best_score = 0.0
        for entry in root.findall(f"{self.ATOM}entry"):
            title = " ".join((entry.findtext(f"{self.ATOM}title") or "").split())
            score = combined_similarity(article["title"], title)
            if score < self.threshold or score <= best_score:
                continue

            entry_names = {
                (name.text or "").split()[-1].lower()
                for name in entry.findall(f"{self.ATOM}author/{self.ATOM}name")
                if (name.text or "").strip()
            }
            # Require at least one author in common when we know the authors
            if expected_names and not (expected_names & entry_names):
                continue

            pdf_url = None
            for link in entry.findall(f"{self.ATOM}link"):
                if link.get("type") == "application/pdf" or link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break
            entry_id = entry.findtext(f"{self.ATOM}id") or ""
            if not pdf_url and "/abs/" in entry_id:
                pdf_url = entry_id.replace("/abs/", "/pdf/")
            if not pdf_url:
                continue

            best = Candidate(
                source=self.source,
                candidate_url=entry_id or None,
                pdf_url=pdf_url,
                match_score=score,
            )
            best_score = score
        return best


RESOLVERS = {cls.source: cls for cls in (OAResolver, NBERResolver, ArXivResolver)}
