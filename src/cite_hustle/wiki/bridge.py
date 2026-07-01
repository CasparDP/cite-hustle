"""Wiki ingestion bridge: DuckDB -> manifest -> process-paper -> reconcile.

process-paper is a separate Poetry project (heavy deps: docling, ollama), so
it is invoked via subprocess in its own venv rather than imported.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from cite_hustle.database.repository import ArticleRepository
from cite_hustle.paths import expand

# Words skipped when picking the title word for a bib_key
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "its",
    "no",
    "not",
    "of",
    "on",
    "or",
    "the",
    "their",
    "there",
    "this",
    "to",
    "under",
    "what",
    "when",
    "which",
    "who",
    "why",
    "with",
}

MANIFEST_NAME = "ingest_manifest.json"


def _ascii_slug(text: str) -> str:
    """Lowercase ASCII letters only (matches process-paper bib_key style)."""
    normalized = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z]", "", normalized.encode("ascii", "ignore").decode().lower())


def make_bib_key(authors: str, year: int, title: str, taken: set[str]) -> str:
    """authorYearFirstword key, deduped with b/c/... suffixes.

    Example: 'Ball, Ray; Brown, Philip', 2023, 'Earnings and prices' ->
    'ball2023earnings'.
    """
    first_author = (authors or "").split(";")[0].strip()
    if "," in first_author:
        last_name = first_author.split(",")[0]
    else:
        last_name = first_author.split()[-1] if first_author else "unknown"

    first_word = "paper"
    for word in re.findall(r"[A-Za-z]+", title or ""):
        if word.lower() not in STOPWORDS and len(word) > 2:
            first_word = word
            break

    base = f"{_ascii_slug(last_name) or 'unknown'}{year}{_ascii_slug(first_word)}"
    key = base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if key not in taken:
            break
        key = base + suffix
    taken.add(key)
    return key


class WikiBridge:
    """Runs verified PDFs through process-paper and tracks state in the DB."""

    def __init__(
        self,
        repo: ArticleRepository,
        wiki_dir: Path,
        pdf_dir: Path,
        process_paper_dir: Path,
        analyst_model: str,
        verifier_model: str,
        depth: str = "deep",
    ):
        self.repo = repo
        self.wiki_dir = wiki_dir
        self.pdf_dir = pdf_dir
        self.process_paper_dir = process_paper_dir
        self.analyst_model = analyst_model
        self.verifier_model = verifier_model
        self.depth = depth
        self.sources_dir = wiki_dir / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)

    # --- batch selection and bib keys --------------------------------------
    def assign_bib_keys(self, batch) -> dict[str, str]:
        """Assign (or reuse) a stable bib_key per DOI; persists pending rows."""
        taken = self.repo.get_existing_bib_keys()
        taken |= {p.stem for p in self.sources_dir.glob("*.md")}

        keys: dict[str, str] = {}
        for _, row in batch.iterrows():
            doi = row["doi"]
            existing = self.repo.get_wiki_page_by_doi(doi)
            if existing:
                keys[doi] = existing["bib_key"]
                continue
            key = make_bib_key(row["authors"], int(row["year"]), row["title"], taken)
            self.repo.upsert_wiki_page(doi, key, status="pending")
            keys[doi] = key
        return keys

    # --- manifest -----------------------------------------------------------
    def write_manifest(self, batch, keys: dict[str, str]) -> Path:
        """Write the process-paper manifest for the batch (rewritten each run)."""
        rows = []
        for _, row in batch.iterrows():
            doi = row["doi"]
            rows.append(
                {
                    "bib_key": keys[doi],
                    "status": "downloaded",
                    "target_filename": expand(row["pdf_file_path"]).name,
                    "doi": doi,
                    "title": row["title"],
                    "authors": [a.strip() for a in (row["authors"] or "").split(";") if a.strip()],
                    "year": int(row["year"]),
                    "journal": row["journal_name"],
                }
            )
        manifest_path = self.wiki_dir / MANIFEST_NAME
        manifest_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return manifest_path

    # --- process-paper invocation -------------------------------------------
    def run_process_paper(self, manifest_path: Path, keys: list[str], refresh: bool = False) -> int:
        """Invoke process-paper in its own venv; streams output. Returns exit code."""
        cmd = [
            "poetry",
            "run",
            "process-paper",
            "run",
            "--manifest",
            str(manifest_path),
            "--pdf-dir",
            str(self.pdf_dir),
            "--wiki",
            str(self.wiki_dir),
            "--depth",
            self.depth,
            "--analyst",
            self.analyst_model,
            "--verifier",
            self.verifier_model,
        ]
        for key in keys:
            cmd += ["--keys", key]
        if refresh:
            cmd.append("--refresh")

        result = subprocess.run(cmd, cwd=self.process_paper_dir)
        return result.returncode

    # --- reconciliation -------------------------------------------------------
    def reconcile(self, keys: dict[str, str]) -> dict[str, str]:
        """Inspect the written source pages and update wiki_pages state."""
        outcomes: dict[str, str] = {}
        for doi, bib_key in keys.items():
            page_path = self.sources_dir / f"{bib_key}.md"
            if not page_path.exists():
                self.repo.upsert_wiki_page(
                    doi, bib_key, status="failed", error_message="Source page not written"
                )
                outcomes[doi] = "failed"
                continue

            content = page_path.read_text(encoding="utf-8", errors="replace")
            frontmatter = self._parse_frontmatter(content)

            if str(frontmatter.get("extraction_failed", "false")).lower() == "true":
                status, error = "failed", "extraction_failed in frontmatter"
            elif "<!-- VERIFIER" in content:
                status, error = "flagged", None
            else:
                status, error = "ingested", None

            self.repo.upsert_wiki_page(
                doi,
                bib_key,
                source_page_path=str(page_path),
                extraction_depth=frontmatter.get("extraction_depth", self.depth),
                analyst_model=frontmatter.get("extraction_model", self.analyst_model),
                verifier_model=frontmatter.get("verifier_model", self.verifier_model),
                status=status,
                error_message=error,
            )
            self.repo.log_processing(doi, "wiki_ingest", status, error)
            outcomes[doi] = status
        return outcomes

    @staticmethod
    def _parse_frontmatter(content: str) -> dict:
        """Parse simple 'key: value' YAML frontmatter (no nesting needed)."""
        fields: dict[str, str] = {}
        if not content.startswith("---"):
            return fields
        for line in content.split("---", 2)[1].splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip().strip('"')
        return fields
