"""Configuration management for cite-hustle"""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with sensible defaults"""
    
    # Dropbox paths - consistent across machines
    dropbox_base: Path = Path.home() / "Dropbox" / "Github Data" / "cite-hustle"
    
    @property
    def data_dir(self) -> Path:
        """Main data directory"""
        return self.dropbox_base
    
    @property
    def cache_dir(self) -> Path:
        """Cache directory for API responses"""
        path = self.dropbox_base / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def db_path(self) -> Path:
        """DuckDB database path"""
        path = self.dropbox_base / "DB"
        path.mkdir(parents=True, exist_ok=True)
        return path / "articles.duckdb"
    
    @property
    def pdf_storage_dir(self) -> Path:
        """Directory for storing downloaded PDFs"""
        path = self.dropbox_base / "pdfs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def html_storage_dir(self) -> Path:
        """Directory for storing SSRN HTML pages"""
        path = self.dropbox_base / "ssrn_html"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def metadata_dir(self) -> Path:
        """Directory for CSV metadata files"""
        path = self.dropbox_base / "metadata"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def wiki_dir(self) -> Path:
        """Research wiki root (process-paper compatible layout)"""
        path = self.dropbox_base / "wiki"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def quarantine_dir(self) -> Path:
        """Quarantine for PDFs that failed metadata verification"""
        path = self.dropbox_base / "pdfs" / "quarantine"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def reports_dir(self) -> Path:
        """Pipeline run reports (markdown, synced via Dropbox)"""
        path = self.dropbox_base / "reports"
        path.mkdir(parents=True, exist_ok=True)
        return path


    # API Settings
    # Optional CrossRef "polite pool" / OpenAlex mailto; set via CITE_HUSTLE_CROSSREF_EMAIL.
    crossref_email: str = ""
    max_workers: int = 3
    
    # Scraping Settings
    crawl_delay: int = 10
    similarity_threshold: int = 90
    headless_browser: bool = False
    
    # DuckDB Settings
    duckdb_memory_limit: str = "4GB"
    duckdb_threads: int = 4

    # Wiki ingestion (process-paper bridge)
    process_paper_dir: Path = Path.home() / "Github" / "dot-files" / "claude" / "skills" / "process-paper"
    analyst_model: str = "kimi-k2.6:cloud"
    wiki_verifier_model: str = "gpt-oss:20b:cloud"
    wiki_ingest_batch: int = 10

    # PDF-metadata verification
    pdf_verifier_model: str = "gpt-oss:20b:cloud"
    verify_gray_zone_low: int = 55
    verify_gray_zone_high: int = 88

    # Fallback resolution (articles attempted per pipeline run)
    fallback_batch: int = 200
    
    class Config:
        env_file = ".env"
        env_prefix = "CITE_HUSTLE_"


# Global settings instance
settings = Settings()
