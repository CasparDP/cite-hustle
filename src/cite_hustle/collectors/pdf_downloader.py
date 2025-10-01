"""PDF downloader for SSRN papers"""
from pathlib import Path
from typing import Optional
import time
import requests
from tqdm import tqdm


class PDFDownloader:
    """Download PDFs from SSRN"""
    
    def __init__(self, storage_dir: Path, delay: int = 2):
        """
        Initialize PDF downloader
        
        Args:
            storage_dir: Directory to save PDFs
            delay: Delay between downloads in seconds
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        
        # Setup session with headers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
    
    def download_pdf(self, url: str, doi: str) -> Optional[Path]:
        """
        Download a PDF from SSRN
        
        Args:
            url: PDF URL
            doi: DOI for filename
            
        Returns:
            Path to downloaded file or None if failed
        """
        try:
            # Create safe filename from DOI
            safe_filename = doi.replace('/', '_').replace('\\', '_')
            filepath = self.storage_dir / f"{safe_filename}.pdf"
            
            # Skip if already exists
            if filepath.exists():
                print(f"✓ Already exists: {safe_filename}.pdf")
                return filepath
            
            # Download with progress
            response = self.session.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Check if response is actually a PDF
            content_type = response.headers.get('content-type', '')
            if 'pdf' not in content_type.lower():
                print(f"✗ Not a PDF: {url} (content-type: {content_type})")
                return None
            
            # Save file
            total_size = int(response.headers.get('content-length', 0))
            with open(filepath, 'wb') as f:
                if total_size > 0:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc=safe_filename) as pbar:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                            pbar.update(len(chunk))
                else:
                    f.write(response.content)
            
            print(f"✓ Downloaded: {safe_filename}.pdf")
            
            # Respect rate limiting
            time.sleep(self.delay)
            
            return filepath
            
        except requests.exceptions.RequestException as e:
            print(f"✗ Download failed for {doi}: {e}")
            return None
        except Exception as e:
            print(f"✗ Error downloading {doi}: {e}")
            return None
    
    def download_batch(self, pdf_list: list, show_progress: bool = True):
        """
        Download multiple PDFs
        
        Args:
            pdf_list: List of dicts with 'url' and 'doi' keys
            show_progress: Show overall progress bar
        """
        results = []
        
        iterator = tqdm(pdf_list, desc="Downloading PDFs") if show_progress else pdf_list
        
        for item in iterator:
            filepath = self.download_pdf(item['url'], item['doi'])
            results.append({
                'doi': item['doi'],
                'filepath': str(filepath) if filepath else None,
                'success': filepath is not None
            })
        
        # Summary
        successful = sum(1 for r in results if r['success'])
        print(f"\n✓ Downloaded {successful}/{len(pdf_list)} PDFs")
        
        return results
