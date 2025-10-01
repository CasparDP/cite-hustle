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
    
    # API Settings
    crossref_email: str = "spiny.bubble0v@icloud.com"
    max_workers: int = 3
    
    # Scraping Settings
    crawl_delay: int = 5
    similarity_threshold: int = 85
    headless_browser: bool = True
    
    # DuckDB Settings
    duckdb_memory_limit: str = "4GB"
    duckdb_threads: int = 4
    
    class Config:
        env_file = ".env"
        env_prefix = "CITE_HUSTLE_"


# Global settings instance
settings = Settings()
