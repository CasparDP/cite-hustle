"""Does a stored abstract belong to its paper?

The comparison is against the paper's verified PDF: the share of the abstract's content
words found in the PDF's first pages. Spaces are removed from the PDF text before the
lookup, because pypdf sometimes extracts text without them ("Weproposeamodel") and some
abstracts have words fused at line breaks. Working-paper PDFs often open with a cover sheet,
so the check reads more pages than the PDF verifier.

Also recognizes junk stored as an abstract: SSRN page text ("Download This PaperOpen PDF
in Browser...", the real abstract cut out of it is recoverable) and publisher placeholders
(Cambridge previews, JSTOR terms of use).
"""

from __future__ import annotations

import re
from typing import Optional

from cite_hustle.duplicates import normalize_title

PDF_PAGES = 5
MIN_WORD_LEN = 5
MIN_WORDS = 8  # fewer content words than this: too short to judge
MATCH_THRESHOLD = 0.65  # no wrong-paper pair of 1,017 reached it (2026-10 calibration)

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
