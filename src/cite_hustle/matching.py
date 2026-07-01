"""Shared title-matching helpers used by the SSRN scraper, fallback resolvers,
and the PDF-metadata verifier."""

from rapidfuzz import fuzz


def combined_similarity(
    db_title: str, candidate_title: str, length_similarity_weight: float = 0.3
) -> float:
    """Combined similarity of two titles (0-100).

    Weighted average of rapidfuzz partial_ratio and a word-count ratio, so a
    substring match on a much longer/shorter title is penalized. Default
    weights: 70% fuzzy match, 30% length similarity.
    """
    fuzzy_score = fuzz.partial_ratio(db_title.lower(), candidate_title.lower())

    db_words = len(db_title.split())
    candidate_words = len(candidate_title.split())
    if db_words == 0 or candidate_words == 0:
        length_score = 0
    else:
        word_ratio = min(db_words, candidate_words) / max(db_words, candidate_words)
        length_score = word_ratio * 100

    return (1 - length_similarity_weight) * fuzzy_score + length_similarity_weight * length_score


def author_last_names(authors: str) -> list[str]:
    """Extract lowercase author last names from the DB's '; '-joined authors string.

    Handles both 'Last, First' and 'First Last' name forms.
    """
    names = []
    for author in (authors or "").split(";"):
        author = author.strip()
        if not author:
            continue
        if "," in author:
            last = author.split(",")[0]
        else:
            last = author.split()[-1]
        last = last.strip().lower()
        if last:
            names.append(last)
    return names
