"""Verify that a downloaded PDF is actually the paper the metadata refers to.

Deterministic first: fuzzy-match the article title and author last names
against the text of the PDF's first pages. Only gray-zone cases go to a small
Ollama Cloud model (structured JSON verdict). Mismatches are quarantined so
the paper becomes eligible for re-scraping / fallback resolution.
"""

import json
import shutil
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from rapidfuzz import fuzz

from cite_hustle.database.repository import ArticleRepository
from cite_hustle.matching import author_last_names
from cite_hustle.paths import expand

HEAD_PAGES = 2  # pages of PDF text to inspect
HEAD_CHARS = 3000  # chars sent to the LLM for gray-zone cases


class VerificationResult(BaseModel):
    match: bool
    confidence: float
    reason: str


class PDFVerifier:
    """Checks downloaded PDFs against article metadata (title + authors)."""

    def __init__(
        self,
        repo: ArticleRepository,
        quarantine_dir: Path,
        model: str,
        gray_low: int = 55,
        gray_high: int = 88,
        use_llm: bool = True,
    ):
        self.repo = repo
        self.quarantine_dir = quarantine_dir
        self.model = model
        self.gray_low = gray_low
        self.gray_high = gray_high
        self.use_llm = use_llm

    # --- text extraction -------------------------------------------------
    @staticmethod
    def extract_head_text(pdf_path: Path) -> Optional[str]:
        """Text of the first pages, or None if unreadable (e.g. scanned)."""
        from pypdf import PdfReader

        try:
            reader = PdfReader(pdf_path)
            text = " ".join((page.extract_text() or "") for page in reader.pages[:HEAD_PAGES])
        except Exception:
            return None
        text = " ".join(text.split())
        return text if len(text) >= 100 else None

    # --- checks -----------------------------------------------------------
    @staticmethod
    def deterministic_check(title: str, authors: str, text: str) -> tuple[float, float]:
        """(title fuzzy score 0-100, fraction of author last names found)."""
        title_score = fuzz.partial_ratio(title.lower(), text.lower())
        last_names = author_last_names(authors)
        if not last_names:
            return title_score, 0.0
        found = sum(1 for name in last_names if name in text.lower())
        return title_score, found / len(last_names)

    def llm_check(self, title: str, authors: str, text: str) -> Optional[VerificationResult]:
        """Ask a small model for a structured verdict; None on repeated failure."""
        import ollama  # lazy import

        system = (
            "You verify whether a PDF is a specific academic paper. You are given "
            "the expected title and authors, and text from the PDF's first pages "
            "(which may be a working-paper version with a slightly different title). "
            'Respond with JSON only: {"match": bool, "confidence": 0-1, "reason": str}.'
        )
        user_msg = (
            f"Expected title: {title}\n"
            f"Expected authors: {authors}\n\n"
            f"PDF text (first pages):\n{text[:HEAD_CHARS]}"
        )
        for _ in range(2):
            try:
                response = ollama.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    format="json",
                    options={"temperature": 0.0},
                )
                content = (response.get("message") or {}).get("content") or ""
                return VerificationResult.model_validate(json.loads(content))
            except Exception:
                continue
        return None

    # --- orchestration ----------------------------------------------------
    def verify_one(self, row: dict) -> str:
        """Verify a single pdf_files row (as dict); returns the final status."""
        doi = row["doi"]
        pdf_path = expand(row["pdf_file_path"])

        if not pdf_path.exists():
            self.repo.set_pdf_verification(
                doi, "unreadable", method="deterministic", reason="File missing on disk"
            )
            return "unreadable"

        text = self.extract_head_text(pdf_path)
        if text is None:
            self.repo.set_pdf_verification(
                doi, "unreadable", method="deterministic", reason="No extractable text"
            )
            return "unreadable"

        title_score, author_frac = self.deterministic_check(row["title"], row["authors"], text)

        if title_score >= self.gray_high and author_frac >= 0.5:
            self.repo.set_pdf_verification(
                doi,
                "match",
                method="deterministic",
                score=title_score,
                reason=f"Title {title_score:.0f}, authors {author_frac:.0%}",
            )
            return "match"

        if title_score <= self.gray_low and author_frac == 0:
            self._quarantine(row, title_score, "deterministic", None)
            return "mismatch"

        # Gray zone: defer to the small model
        if not self.use_llm:
            self.repo.set_pdf_verification(
                doi,
                "uncertain",
                method="deterministic",
                score=title_score,
                reason=f"Gray zone (title {title_score:.0f}, authors {author_frac:.0%}), LLM disabled",
            )
            return "uncertain"

        verdict = self.llm_check(row["title"], row["authors"], text)
        if verdict is None:
            self.repo.set_pdf_verification(
                doi,
                "uncertain",
                method="llm",
                score=title_score,
                model=self.model,
                reason="LLM call failed",
            )
            return "uncertain"

        if verdict.match and verdict.confidence >= 0.7:
            self.repo.set_pdf_verification(
                doi,
                "match",
                method="llm",
                score=title_score,
                model=self.model,
                reason=verdict.reason,
            )
            return "match"
        if not verdict.match and verdict.confidence >= 0.7:
            self._quarantine(row, title_score, "llm", verdict.reason)
            return "mismatch"

        self.repo.set_pdf_verification(
            doi,
            "uncertain",
            method="llm",
            score=title_score,
            model=self.model,
            reason=verdict.reason,
        )
        return "uncertain"

    def _quarantine(self, row: dict, score: float, method: str, reason: Optional[str]):
        """Move a mismatched PDF aside and reset the article for re-resolution."""
        doi = row["doi"]
        pdf_path = expand(row["pdf_file_path"])
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        if pdf_path.exists():
            shutil.move(str(pdf_path), str(self.quarantine_dir / pdf_path.name))

        reason = reason or f"Title score {score:.0f}, no author match"
        self.repo.set_pdf_verification(
            doi,
            "mismatch",
            method=method,
            score=score,
            model=self.model if method == "llm" else None,
            reason=reason,
        )
        self.repo.log_processing(doi, "verify_pdf", "mismatch", reason)

        if row.get("source") == "ssrn":
            # Reset the SSRN download flags so the paper can be re-resolved
            self.repo.reset_ssrn_download(doi)
        else:
            # Don't re-fetch the same bad candidate from this source
            self.repo.record_pdf_candidate(
                doi, row["source"], status="no_match", error_message="Verified mismatch"
            )
        self.repo.delete_pdf_file(doi)

    def verify_batch(self, rows) -> dict:
        """Verify a DataFrame of pending PDFs; returns counts by outcome."""
        counts = {"match": 0, "mismatch": 0, "uncertain": 0, "unreadable": 0}
        for _, row in rows.iterrows():
            status = self.verify_one(row.to_dict())
            counts[status] += 1
            symbol = {"match": "✓", "mismatch": "✗", "uncertain": "?", "unreadable": "!"}[status]
            print(f"  {symbol} {row['doi']}: {status}")
        return counts
