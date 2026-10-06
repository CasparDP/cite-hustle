"""OpenAlex abstract enrichment for missing abstracts.

DOIs are looked up 50 per request (OpenAlexBatchSource): one request per DOI exhausted
even a keyed daily allowance on ~20k articles (2026-10). A run stops after a few batches in
a row fail on rate limits instead of retrying every article. OpenAlex sometimes holds an
author biography or a citation header instead of an abstract; those are not stored.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, Optional

from cite_hustle import abstract_check
from cite_hustle.collectors.fallback_resolvers import ResolverError
from cite_hustle.database.repository import ArticleRepository

STAGE = "enrich_openalex"


class OpenAlexEnricher:
    """Fetch missing abstracts from OpenAlex using DOIs."""

    MAX_RATE_LIMITED_BATCHES = 3

    def __init__(
        self,
        repo: ArticleRepository,
        source=None,
        delay_s: float = 1.0,
        log: Callable[[str], None] = print,
    ):
        from cite_hustle.collectors.abstract_sources import OpenAlexBatchSource

        self.repo = repo
        self.source = source or OpenAlexBatchSource()
        self.delay_s = max(0.0, delay_s)
        self.log = log

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

    def enrich_missing_abstracts(self, articles: Iterable[Dict], force: bool = False) -> Dict:
        """Look up abstracts in batches; log success/no_match per DOI, nothing on errors."""
        stats = {"total": 0, "updated": 0, "no_abstract": 0, "invalid_doi": 0, "failed": 0}
        valid = []
        for row in articles:
            stats["total"] += 1
            normalized = self.normalize_doi(row.get("doi") or "")
            if normalized:
                valid.append((row["doi"], normalized, row.get("title")))
            else:
                self.repo.log_processing(row.get("doi"), STAGE, "failed", "invalid_doi")
                stats["invalid_doi"] += 1

        size = self.source.BATCH_SIZE
        rate_limited_in_a_row = 0
        for start in range(0, len(valid), size):
            batch = valid[start : start + size]
            try:
                found = self.source.fetch([normalized for _, normalized, _ in batch])
            except ResolverError as exc:
                # Not logged per DOI: failed attempts are retried on the next run
                stats["failed"] += len(batch)
                rate_limited_in_a_row = rate_limited_in_a_row + 1 if "429" in str(exc) else 0
                if rate_limited_in_a_row >= self.MAX_RATE_LIMITED_BATCHES:
                    self.log(
                        f"⚠️  Stopped: OpenAlex rate-limited {rate_limited_in_a_row} batches in a "
                        f"row ({start + len(batch):,}/{len(valid):,}). Try again later."
                    )
                    stats["stopped"] = True
                    break
                continue
            rate_limited_in_a_row = 0
            for doi, normalized, title in batch:
                abstract = found.get(normalized)
                if abstract and abstract_check.usable_comparator(title, abstract):
                    self.repo.upsert_abstract(doi, abstract, force=force)
                    self.repo.log_processing(doi, STAGE, "success")
                    stats["updated"] += 1
                else:
                    self.repo.log_processing(doi, STAGE, "no_match")
                    stats["no_abstract"] += 1
            done = start + len(batch)
            if (start // size) % 10 == 9 or done == len(valid):
                self.log(f"  {done:,}/{len(valid):,} looked up, {stats['updated']:,} abstracts")
            time.sleep(self.delay_s)
        return stats
