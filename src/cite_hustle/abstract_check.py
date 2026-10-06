"""Does a stored abstract belong to its paper?

The comparison is against the paper's verified PDF (the share of the abstract's content
words found in the PDF's first pages) or, without a PDF, against a DOI-exact abstract of the
same paper (CrossRef, OpenAlex, Semantic Scholar) for abstracts from fuzzy-matched sources. Spaces are removed from the PDF text before the
lookup, because pypdf sometimes extracts text without them ("Weproposeamodel") and some
abstracts have words fused at line breaks. Working-paper PDFs often open with a cover sheet,
so the check reads more pages than the PDF verifier.

Also recognizes junk stored as an abstract: SSRN page text ("Download This PaperOpen PDF
in Browser...", the real abstract cut out of it is recoverable) and publisher placeholders
(Cambridge previews, JSTOR terms of use).
"""

from __future__ import annotations

import html
import re
from typing import Optional

from cite_hustle.duplicates import normalize_title

PDF_PAGES = 5
MIN_WORD_LEN = 5
MIN_WORDS = 8  # fewer content words than this: too short to judge
MATCH_THRESHOLD = 0.65  # no wrong-paper pair of 1,017 reached it (2026-10 calibration)
# Abstract vs a DOI-exact abstract: no wrong pair of 15,621 reached 0.45 (max 0.40)
SOURCE_MATCH_THRESHOLD = 0.45

_PAGE_TEXT_MARKERS = (
    "Add Paper to My Library",
    "Download This Paper",
    "Using these links will ensure access",
)
# Publisher text that is not an abstract (2026-10 snapshot: 20 Cambridge, 13 JSTOR, 7 generic)
_PLACEHOLDER_MARKERS = (
    "abstract is not available for this content",
    "your use of the jstor archive indicates your acceptance",
    "the author offers insight into the paper presented",
)
_ABSTRACT_START = re.compile(
    r"Date Written:\s*[A-Z][a-z]+\s+\d{1,2},\s*\d{4}|Date Written:\s*[A-Z][a-z]+\s+\d{4}"
)
_ABSTRACT_END = re.compile(r"Keywords:|JEL Classification|Suggested Citation|Suggested citation")


def content_words(text: Optional[str]) -> list[str]:
    return sorted({w for w in normalize_title(text).split() if len(w) >= MIN_WORD_LEN})


def overlap(abstract: Optional[str], pdf_text: Optional[str]) -> Optional[float]:
    """Share of the abstract's content words found in the PDF text (None: too short)."""
    words = content_words(abstract)
    if len(words) < MIN_WORDS or not pdf_text:
        return None
    blob = normalize_title(pdf_text).replace(" ", "")
    return sum(w in blob for w in words) / len(words)


def _loose(text: Optional[str]) -> str:
    """Lowercase with markup removed and dashes, quotes and spaces unified (keeps punctuation)."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or "")).lower()
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    return " ".join(text.split())


def is_citation_header(title: Optional[str], text: Optional[str]) -> bool:
    """'Authors, Title, Journal...' or 'Title by Authors' instead of an abstract."""
    t, x = _loose(title).rstrip("."), _loose(text)
    i = x.find(t, 0, 400) if len(t.split()) >= 2 else -1
    if i < 0:
        return False
    prefix = x[:i].rstrip()
    return (0 < len(prefix) < 200 and prefix.endswith((",", ";", ":"))) or x[i + len(t) :].lstrip(
        " ,.:"
    ).startswith("by ")


def usable_comparator(title: Optional[str], text: Optional[str], strict: bool = True) -> bool:
    """A DOI-exact abstract fit to judge (and replace) another one.

    Never junk or a citation header. With strict (OpenAlex, Semantic Scholar, which
    sometimes hold author biographies or OCR garbage) it must also mention a word of the
    title (4+ letters); publisher-deposited CrossRef abstracts need not.
    """
    if not text or is_junk(text) or is_citation_header(title, text):
        return False
    if not strict:
        return True
    blob = normalize_title(text).replace(" ", "")
    title_words = [w for w in normalize_title(title).split() if len(w) >= 4]
    return not title_words or any(w in blob for w in title_words)


def is_ssrn_page_text(abstract: Optional[str]) -> bool:
    return any(marker in (abstract or "") for marker in _PAGE_TEXT_MARKERS)


def is_placeholder(abstract: Optional[str]) -> bool:
    text = (abstract or "").lower()
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)


def is_junk(abstract: Optional[str]) -> bool:
    """Text that must never be stored as an abstract."""
    return is_ssrn_page_text(abstract) or is_placeholder(abstract)


def cut_abstract_from_page_text(text: str) -> Optional[str]:
    """The abstract inside SSRN page text: after 'Date Written: <date>', before keywords."""
    start = _ABSTRACT_START.search(text)
    if not start:
        return None
    rest = text[start.end() :]
    end = _ABSTRACT_END.search(rest)
    abstract = (rest[: end.start()] if end else rest).strip()
    return abstract if len(abstract) >= 100 and not is_ssrn_page_text(abstract) else None
