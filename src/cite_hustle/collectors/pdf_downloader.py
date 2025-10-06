"""PDF downloader for SSRN papers

NOTE: As of October 2025, SSRN has implemented Cloudflare bot protection
that blocks automated PDF downloads. Direct HTTP requests to PDF URLs
now return HTML pages with JavaScript challenges instead of PDF content.

This means the PDF downloader will fail for SSRN papers unless:
1. SSRN changes their anti-bot policies, or
2. The implementation is updated to use browser automation (Selenium)
   to handle JavaScript challenges

For now, this module will detect Cloudflare protection and provide
helpful error messages.
"""
from pathlib import Path
from typing import Optional
import time
import re
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
    
    def extract_abstract_id(self, ssrn_url: str) -> Optional[str]:
        """
        Extract the abstract ID from an SSRN paper URL
        
        Args:
            ssrn_url: SSRN paper URL
            
        Returns:
            Abstract ID or None if not found
            
        Examples:
            https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567 -> "1234567"
            https://ssrn.com/abstract=1234567 -> "1234567"
            https://www.ssrn.com/abstract=1234567 -> "1234567"
        """
        if not ssrn_url:
            return None
        
        # Try different patterns
        patterns = [
            r'abstract_id=(\d+)',      # papers.cfm?abstract_id=1234567
            r'abstract=(\d+)',          # ssrn.com/abstract=1234567
            r'/abstract/(\d+)',         # ssrn.com/abstract/1234567
            r'abstractid=(\d+)',        # abstractid=1234567
        ]
        
        for pattern in patterns:
            match = re.search(pattern, ssrn_url)
            if match:
                return match.group(1)
        
        return None
    
    def construct_pdf_url(self, ssrn_url: str) -> Optional[str]:
        """
        Construct the PDF download URL from an SSRN paper URL
        
        Args:
            ssrn_url: SSRN paper URL
            
        Returns:
            PDF download URL or None if abstract ID cannot be extracted
            
        Examples:
            https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5010688
            -> https://papers.ssrn.com/sol3/Delivery.cfm/5010688.pdf?abstractid=5010688&mirid=1
        """
        abstract_id = self.extract_abstract_id(ssrn_url)
        
        if not abstract_id:
            return None
        
        # Construct the PDF download URL
        # SSRN's actual download endpoint format
        pdf_url = f"https://papers.ssrn.com/sol3/Delivery.cfm/{abstract_id}.pdf?abstractid={abstract_id}&mirid=1"
        
        return pdf_url
    
    def download_pdf(self, url: str, doi: str, ssrn_url: Optional[str] = None) -> Optional[Path]:
        """
        Download a PDF from SSRN
        
        Args:
            url: PDF URL (if None, will construct from ssrn_url)
            doi: DOI for filename
            ssrn_url: SSRN paper URL (used to construct PDF URL if url is None)
            
        Returns:
            Path to downloaded file or None if failed
        """
        # If no direct PDF URL provided, try to construct it from SSRN paper URL
        if not url and ssrn_url:
            url = self.construct_pdf_url(ssrn_url)
            if url:
                print(f"  → Constructed PDF URL from SSRN URL")
            else:
                print(f"  ✗ Could not construct PDF URL from: {ssrn_url}")
                return None
        
        if not url:
            print(f"  ✗ No URL available for {doi}")
            return None
        
        try:
            # Create safe filename from DOI
            safe_filename = doi.replace('/', '_').replace('\\', '_')
            filepath = self.storage_dir / f"{safe_filename}.pdf"
            
            # Skip if already exists
            if filepath.exists():
                print(f"✓ Already exists: {safe_filename}.pdf")
                return filepath
            
            # Download with progress
            response = self.session.get(url, stream=True, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            # Check if response is actually a PDF
            content_type = response.headers.get('content-type', '')
            if 'pdf' not in content_type.lower():
                # Check for Cloudflare protection specifically
                if 'text/html' in content_type.lower():
                    # Get the response content to check for Cloudflare
                    if hasattr(response, '_content') and response._content:
                        content_sample = response._content[:500].decode('utf-8', errors='ignore')
                    else:
                        # If content isn't cached, make a small request to get a sample
                        try:
                            sample_response = self.session.get(url, timeout=10)
                            content_sample = sample_response.content[:500].decode('utf-8', errors='ignore')
                        except:
                            content_sample = ""
                    
                    if 'Just a moment...' in content_sample or 'cloudflare' in content_sample.lower():
                        print(f"✗ SSRN Cloudflare protection detected: {url}")
                        print(f"  → SSRN now blocks automated downloads. Manual download required.")
                    else:
                        print(f"✗ Not a PDF: {url} (content-type: {content_type})")
                        print(f"  → First 100 chars: {content_sample[:100]}...")
                else:
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
            pdf_list: List of dicts with 'url', 'doi', and optionally 'ssrn_url' keys
            show_progress: Show overall progress bar
        """
        results = []
        cloudflare_blocked = 0
        
        iterator = tqdm(pdf_list, desc="Downloading PDFs") if show_progress else pdf_list
        
        for item in iterator:
            # Count Cloudflare blocks by checking if we're getting HTML responses
            url = item.get('url') or (self.construct_pdf_url(item.get('ssrn_url')) if item.get('ssrn_url') else None)
            if url:
                try:
                    test_response = self.session.head(url, timeout=10)
                    if 'text/html' in test_response.headers.get('content-type', ''):
                        cloudflare_blocked += 1
                except:
                    pass
            
            filepath = self.download_pdf(
                url=item.get('url'),
                doi=item['doi'],
                ssrn_url=item.get('ssrn_url')
            )
            results.append({
                'doi': item['doi'],
                'filepath': str(filepath) if filepath else None,
                'success': filepath is not None
            })
        
        # Summary
        successful = sum(1 for r in results if r['success'])
        print(f"\n✓ Downloaded {successful}/{len(pdf_list)} PDFs")
        
        if cloudflare_blocked > 0:
            print(f"\n⚠️  {cloudflare_blocked} PDFs blocked by SSRN's anti-bot protection")
            print("   SSRN now uses Cloudflare to prevent automated PDF downloads.")
            print("   Consider using a browser automation tool like Selenium for PDF downloads.")
        
        return results
