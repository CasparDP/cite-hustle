"""Papers stored under two DOIs (JSTOR beside the publisher DOI, old and new DOI formats).

A pair of articles is a duplicate only when all of these hold:

- same journal (ISSN) and the same normalized title;
- compatible authors: one surname set covers the other, tolerating typos in one source
  (``CAMPBEL``, ``BAE-YOSEF``); an empty or ``Unknown`` author list is accepted only
  across DOI prefixes with page evidence;
- years at most one apart (online-first versus print);
- the same CrossRef start page (within two pages across DOI prefixes, where JSTOR is
  often off by one or two). This separates a paper from its corrigendum or the authors'
  reply, which share title and authors;
- within one DOI prefix, never comments, replies, discussions, errata or recurring
  columns.

CrossRef registers some secondary DOIs (JSTOR for the Journal of Finance, Elsevier for the
Journal of Management) as aliases of the publisher DOI and stops listing them, so they have
no cached page. For such pairs CrossRef is asked directly: a redirect to the other DOI of
the pair, or of both to the same DOI, proves the duplicate. Pairs that pass everything
except that a start page is unknown are held back and listed. A cluster merges only if
every pair in it passes. The kept DOI: the alias target, never JSTOR, then the prefix the
journal uses for its recent articles (CrossRef moved DOI ownership with the journals, so
prefix alone does not identify the publisher), then the higher CrossRef citation count.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import httpx
from rapidfuzz import fuzz

JSTOR_PREFIX = "10.2307"
PAGE_TOLERANCE_ACROSS_PREFIXES = 2
SURNAME_MIN_SCORE = 80
RECENT_YEARS = 5

_KIND = r"(comments?|reply|replies|rejoinder|response|discussion|erratum|errata|corrigendum|correction|retraction)"
_REPLY_LIKE = re.compile(
    rf"^(a )?{_KIND}\b|\b(a )?{_KIND}$|:\s*(a )?{_KIND}\b|^(introduction|foreword|editorial"
    r"|letter from the editor|capsule commentary|correspondence|dialogue|memorium"
    r"|in memoriam|from the exsec s notebook)$"
)
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_DASHES = re.compile(r"[‐-―-]")


@dataclass
class Record:
    doi: str
    title: str
    authors: Optional[str]
    year: Optional[int]
    journal_issn: Optional[str]
    page: Optional[str] = None  # CrossRef "page", e.g. "65-91"
    cites: int = 0  # CrossRef is-referenced-by-count
    alias_of: Optional[str] = None  # lowercased DOI CrossRef redirects this DOI to

    @property
    def prefix(self) -> str:
        return self.doi.split("/", 1)[0]


@dataclass
class MergeGroup:
    keep: str
    drop: List[str]
    journal_issn: Optional[str]


@dataclass
class DuplicateScan:
    groups: List[MergeGroup] = field(default_factory=list)
    held_no_pages: List[Tuple[str, str]] = field(default_factory=list)
    conflicted: List[List[str]] = field(default_factory=list)


def _ascii(text: str, keep: str = "") -> str:
    """Lowercase words: accents stripped, apostrophes dropped, any other symbol (including
    Unicode hyphens, which Wiley titles use) a word break."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"['\u2018\u2019\u02bc]", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(rf"[^a-z0-9{keep}]+", " ", text).split())


def normalize_title(title: Optional[str]) -> str:
    """Lowercase ASCII words only: markup, accents and punctuation removed."""
    return _ascii(title or "")


def is_reply_like(title: Optional[str]) -> bool:
    """Comments, replies, discussions, errata and recurring columns."""
    return bool(_REPLY_LIKE.search(_ascii(title or "", keep=":")))


def _names(authors: Optional[str]) -> List[str]:
    text = html.unescape(authors or "")
    if text.strip().lower() in ("", "unknown"):
        return []
    return [n.strip() for n in text.split(";") if _ascii(n)]


def surnames(authors: Optional[str]) -> set:
    out = set()
    for name in _names(authors):
        if "," in name:  # "Last, First"
            name = name.split(",")[0]
        tokens = [t for t in _ascii(_DASHES.sub("", name)).split() if t not in _NAME_SUFFIXES]
        if tokens:
            out.add(tokens[-1])
    return out


def authors_compatible(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    """Every surname of the shorter list fuzzily found in the longer; None if one is empty."""
    sa, sb = surnames(a), surnames(b)
    if not sa or not sb:
        return None
    small, other = (sa, b) if len(sa) <= len(sb) else (sb, a)
    full = [_ascii(_DASHES.sub("", n)).replace(" ", "") for n in _names(other)]
    return all(max(fuzz.partial_ratio(s, f) for f in full) >= SURNAME_MIN_SCORE for s in small)


def start_page(page: Optional[str]) -> Optional[int]:
    first = (page or "").split("-")[0].strip()
    return int(first) if first.isdigit() else None


def pair_decision(x: Record, y: Record) -> str:
    """'merge', 'held:no-pages', or 'no:<reason>'."""
    if x.alias_of == y.doi.lower() or y.alias_of == x.doi.lower():
        return "merge"  # CrossRef itself says these are one work
    if x.alias_of and x.alias_of == y.alias_of:
        return "merge"  # both aliases of the same work
    cross = x.prefix != y.prefix
    px, py = start_page(x.page), start_page(y.page)
    pages_known = px is not None and py is not None
    tolerance = PAGE_TOLERANCE_ACROSS_PREFIXES if cross else 0
    if pages_known and abs(px - py) > tolerance:
        return "no:pages"
    compatible = authors_compatible(x.authors, y.authors)
    if compatible is False:
        return "no:authors"
    if compatible is None and not (cross and pages_known):
        return "no:authors-empty"
    if x.year is None or y.year is None or abs(x.year - y.year) > 1:
        return "no:year"
    if not cross and is_reply_like(x.title):
        return "no:reply-like"
    if not pages_known:
        return "held:no-pages"
    return "merge"


def current_prefixes(records: Iterable[Record]) -> Dict[str, str]:
    """Most common DOI prefix per journal among its articles of the last few years."""
    records = [r for r in records if r.year is not None and r.journal_issn]
    if not records:
        return {}
    since = max(r.year for r in records) - RECENT_YEARS
    counts: Dict[str, Counter] = defaultdict(Counter)
    for r in records:
        if r.year >= since:
            counts[r.journal_issn][r.prefix] += 1
    # Ties go to the larger prefix string, so the result does not depend on row order
    return {issn: max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] for issn, c in counts.items()}


def _keep_rank(r: Record, current: Dict[str, str]):
    # The DOI string only makes a full tie deterministic; it carries no meaning
    return (
        r.alias_of is None,
        r.prefix != JSTOR_PREFIX,
        current.get(r.journal_issn) == r.prefix,
        r.cites,
        r.doi,
    )


def same_title_groups(records: Iterable[Record]) -> List[List[Record]]:
    """Records sharing journal and normalized title, groups of two or more."""
    buckets: Dict[Tuple, List[Record]] = defaultdict(list)
    for r in records:
        key = normalize_title(r.title)
        if key:
            buckets[(r.journal_issn, key)].append(r)
    return [g for g in buckets.values() if len(g) > 1]


def find_duplicates(candidates: List[List[Record]], current: Dict[str, str]) -> DuplicateScan:
    """Apply the pair rule within each same-title group and cluster the merges."""
    scan = DuplicateScan()
    for group in candidates:
        by_doi = {r.doi: r for r in group}
        parent = {d: d for d in by_doi}

        def root(d):
            while parent[d] != d:
                d = parent[d]
            return d

        refused = set()
        for x, y in itertools.combinations(group, 2):
            decision = pair_decision(x, y)
            if decision == "merge":
                parent[root(x.doi)] = root(y.doi)
            else:
                refused.add(frozenset((x.doi, y.doi)))
                if decision == "held:no-pages":
                    scan.held_no_pages.append((x.doi, y.doi))
        clusters: Dict[str, List[str]] = defaultdict(list)
        for d in by_doi:
            clusters[root(d)].append(d)
        for members in clusters.values():
            if len(members) < 2:
                continue
            members.sort()
            if any(frozenset(p) in refused for p in itertools.combinations(members, 2)):
                scan.conflicted.append(members)
                continue
            keep = max((by_doi[d] for d in members), key=lambda r: _keep_rank(r, current))
            scan.groups.append(
                MergeGroup(
                    keep=keep.doi,
                    drop=[d for d in members if d != keep.doi],
                    journal_issn=keep.journal_issn,
                )
            )
    return scan


def load_crossref_pages(cache_dir: Path, dois: Iterable[str]) -> Dict[str, Tuple[str, int]]:
    """(page, citation count) per DOI from the cached CrossRef responses (lowercased DOIs)."""
    wanted = {d.lower() for d in dois}
    found: Dict[str, Tuple[str, int]] = {}
    for path in sorted(Path(cache_dir).glob("cache_*.json")):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in items if isinstance(items, list) else []:
            doi = str(item.get("DOI", "")).lower()
            if doi in wanted:
                found[doi] = (item.get("page"), item.get("is-referenced-by-count") or 0)
    return found


CROSSREF_WORK_URL = "https://api.crossref.org/works/"
ALIAS_CACHE_NAME = "crossref_aliases.json"


def dois_to_check_for_alias(scan: DuplicateScan, cached: Dict[str, Tuple[str, int]]) -> List[str]:
    """DOIs in held pairs that are missing from the CrossRef cache."""
    return sorted({d for pair in scan.held_no_pages for d in pair if d.lower() not in cached})


def _alias_from_location(location: str) -> Optional[str]:
    """The DOI in a CrossRef redirect, e.g. '/works/10.1111/j.1540-6261.1993.tb04702.x'."""
    path = unquote(urlsplit(location).path)
    return path.split("/works/", 1)[1].lower() if "/works/" in path else None


def resolve_aliases(
    dois: List[str],
    cache_dir: Path,
    mailto: str = "",
    delay: float = 0.5,
    max_attempts: int = 5,
    log=print,
) -> Dict[str, str]:
    """Lowercased DOI -> the DOI CrossRef redirects it to ('' if not an alias).

    One GET per DOI, redirects not followed: CrossRef answers an alias with a 301 to the
    primary DOI (HEAD gets 200 even for aliases). CrossRef allows 5 single-DOI requests per
    second, one at a time, hence the delay and the backoff on 429. Answers persist in cache_dir/crossref_aliases.json, so a dry run's
    lookups are reused by --apply and an interrupted run resumes. Failed lookups are left
    out (not cached), so their pairs stay held.
    """
    path = Path(cache_dir) / ALIAS_CACHE_NAME
    known: Dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    todo = [d.lower() for d in dois if d.lower() not in known]
    if todo:
        log(f"Asking CrossRef whether {len(todo):,} DOIs are aliases (cached in {path})")
    params = {"mailto": mailto} if mailto else {}
    with httpx.Client(follow_redirects=False, timeout=30.0) as http:
        i = 0
        while i < len(todo):  # grows when a redirect target needs checking (alias chains)
            doi = todo[i]
            i += 1
            for attempt in range(max_attempts):
                try:
                    response = http.get(CROSSREF_WORK_URL + doi, params=params)
                except httpx.TransportError:
                    time.sleep(5.0 * 2**attempt)
                    continue
                code = response.status_code
                if code in (301, 302, 303, 307, 308):
                    target = _alias_from_location(response.headers.get("Location", ""))
                    if target is not None:
                        known[doi] = "" if target == doi else target
                        if target != doi and target not in known and target not in todo:
                            todo.append(target)
                elif code in (200, 404):
                    known[doi] = ""
                elif code == 429 or code >= 500:
                    retry_after = response.headers.get("Retry-After", "")
                    time.sleep(float(retry_after) if retry_after.isdigit() else 5.0 * 2**attempt)
                    continue
                break  # answered, or another client error: leave unknown
            if i % 50 == 0 or i == len(todo):
                path.write_text(json.dumps(known, indent=0, sort_keys=True))
                log(f"  {i:,}/{len(todo):,}")
            time.sleep(delay)

    def final(doi: str) -> str:
        seen = {doi}
        while known.get(doi) and known[doi] not in seen:
            doi = known[doi]
            seen.add(doi)
        return doi

    return {
        d.lower(): (final(d.lower()) if known[d.lower()] else "")
        for d in dois
        if d.lower() in known
    }
