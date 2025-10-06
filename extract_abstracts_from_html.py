#!/usr/bin/env python3
"""
Extract abstracts from saved SSRN HTML files.
Useful for re-processing papers where abstract extraction failed during scraping.

Usage:
    poetry run python extract_abstracts_from_html.py --all
    poetry run python extract_abstracts_from_html.py --missing-only
    poetry run python extract_abstracts_from_html.py --limit 10
"""

import argparse
import os
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional
from tqdm import tqdm

from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


def expand_portable_path(path_str: str) -> Path:
    """
    Expand portable path (like $HOME/...) to absolute path.
    
    Args:
        path_str: Path string that may contain $HOME or be absolute
        
    Returns:
        Path object with expanded absolute path
    """
    if path_str and path_str.startswith('$HOME/'):
        expanded = os.path.expandvars(path_str)
        return Path(expanded)
    return Path(path_str) if path_str else None


def extract_abstract_from_html(html_content: str) -> Optional[str]:
    """
    Extract abstract from SSRN HTML using multiple strategies with BeautifulSoup
    
    Args:
        html_content: HTML content of SSRN paper page
        
    Returns:
        Abstract text or None if not found
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    strategies = [
        # Strategy 1: div.abstract-text with paragraphs
        lambda: extract_by_class(soup, "abstract-text"),
        # Strategy 2: Find Abstract h3, get parent, extract paragraphs
        lambda: extract_after_header(soup, "Abstract"),
        # Strategy 3: Any div with class containing "abstract"
        lambda: extract_by_class_partial(soup, "abstract"),
        # Strategy 4: Find "Abstract" text, get next siblings
        lambda: extract_by_text_search(soup, "Abstract"),
    ]
    
    for strategy in strategies:
        try:
            abstract = strategy()
            if abstract and len(abstract.strip()) > 50:  # Minimum reasonable length
                # Clean up the abstract
                abstract = abstract.strip()
                # Remove "Abstract" header if it's at the start
                if abstract.startswith("Abstract"):
                    abstract = abstract[8:].strip()
                return abstract
        except Exception as e:
            # Strategy failed, try next one
            continue
    
    return None


def extract_by_class(soup: BeautifulSoup, class_name: str) -> Optional[str]:
    """Extract abstract from element with specific class"""
    div = soup.find('div', class_=class_name)
    if div:
        # Try to get paragraphs
        paragraphs = div.find_all('p')
        if paragraphs:
            text = " ".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
            return text if text else None
        # Otherwise get all text
        return div.get_text(strip=True)
    return None


def extract_after_header(soup: BeautifulSoup, header_text: str) -> Optional[str]:
    """Find header with text, then extract following paragraphs"""
    # Find all h3 tags
    for h3 in soup.find_all('h3'):
        if header_text.lower() in h3.get_text().lower():
            # Get parent element
            parent = h3.parent
            if parent:
                paragraphs = parent.find_all('p')
                if paragraphs:
                    text = " ".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
                    return text if text else None
    return None


def extract_by_class_partial(soup: BeautifulSoup, partial_class: str) -> Optional[str]:
    """Find div with class containing partial match"""
    for div in soup.find_all('div'):
        if div.get('class'):
            classes = ' '.join(div.get('class'))
            if partial_class.lower() in classes.lower():
                text = div.get_text(strip=True)
                # Remove "Abstract" header
                text = text.replace('Abstract\n', '').replace('Abstract', '').strip()
                return text if len(text) > 50 else None
    return None


def extract_by_text_search(soup: BeautifulSoup, search_text: str) -> Optional[str]:
    """Search for text and extract following content"""
    # Find all elements containing "Abstract"
    for elem in soup.find_all(string=lambda text: text and search_text in text):
        parent = elem.parent
        if parent:
            # Get all following siblings
            paragraphs = []
            for sibling in parent.find_next_siblings():
                if sibling.name == 'p':
                    paragraphs.append(sibling.get_text(strip=True))
                elif sibling.name in ['h1', 'h2', 'h3', 'h4']:
                    # Stop at next header
                    break
            
            if paragraphs:
                text = " ".join(paragraphs)
                return text if text else None
    
    return None


def process_html_file(filepath: Path) -> Optional[str]:
    """
    Process a single HTML file and extract abstract
    
    Args:
        filepath: Path to HTML file
        
    Returns:
        Abstract text or None
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        abstract = extract_abstract_from_html(html_content)
        return abstract
        
    except Exception as e:
        print(f"  Error reading {filepath.name}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Extract abstracts from saved SSRN HTML files')
    parser.add_argument('--all', action='store_true', help='Process all HTML files')
    parser.add_argument('--missing-only', action='store_true', help='Only process papers with missing abstracts')
    parser.add_argument('--limit', type=int, help='Limit number of papers to process')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without updating database')
    
    args = parser.parse_args()
    
    # Setup database
    db = DatabaseManager(settings.db_path)
    db.connect()
    repo = ArticleRepository(db)
    
    print("Abstract Extraction from Saved HTML Files")
    print("=" * 80)
    
    # Get papers to process
    if args.missing_only:
        # Only papers with HTML but no abstract
        query = """
            SELECT doi, html_file_path 
            FROM ssrn_pages 
            WHERE html_file_path IS NOT NULL 
            AND (abstract IS NULL OR abstract = '')
        """
        if args.limit:
            query += f" LIMIT {args.limit}"
        
        papers = db.conn.execute(query).fetchdf()
        print(f"Found {len(papers)} papers with missing abstracts")
    
    elif args.all:
        # All papers with HTML
        query = """
            SELECT doi, html_file_path, abstract
            FROM ssrn_pages 
            WHERE html_file_path IS NOT NULL
        """
        if args.limit:
            query += f" LIMIT {args.limit}"
        
        papers = db.conn.execute(query).fetchdf()
        print(f"Found {len(papers)} papers with saved HTML")
    
    else:
        print("Error: Specify --all or --missing-only")
        return
    
    if len(papers) == 0:
        print("No papers to process!")
        return
    
    # Process each paper
    stats = {
        'total': len(papers),
        'success': 0,
        'already_had': 0,
        'failed': 0,
        'file_not_found': 0
    }
    
    print("\nProcessing papers...")
    print("-" * 80)
    
    for idx, row in tqdm(papers.iterrows(), total=len(papers), desc="Extracting abstracts"):
        doi = row['doi']
        html_path = row.get('html_file_path')
        existing_abstract = row.get('abstract', '')
        
        # Check if file exists (expand portable paths like $HOME)
        html_file_path = expand_portable_path(html_path)
        if not html_path or not html_file_path or not html_file_path.exists():
            stats['file_not_found'] += 1
            print(f"\n✗ {doi}: HTML file not found at {html_path}")
            continue
        
        # Skip if already has abstract (unless --all)
        if existing_abstract and args.missing_only:
            stats['already_had'] += 1
            continue
        
        # Extract abstract
        abstract = process_html_file(html_file_path)
        
        if abstract:
            stats['success'] += 1
            
            if args.dry_run:
                print(f"\n✓ {doi}: Would update abstract ({len(abstract)} chars)")
                print(f"  Preview: {abstract[:150]}...")
            else:
                # Update database
                try:
                    db.conn.execute("""
                        UPDATE ssrn_pages 
                        SET abstract = ? 
                        WHERE doi = ?
                    """, [abstract, doi])
                    
                    tqdm.write(f"✓ {doi}: Updated abstract ({len(abstract)} chars)")
                except Exception as e:
                    print(f"\n✗ {doi}: Database error: {e}")
                    stats['failed'] += 1
                    stats['success'] -= 1
        else:
            stats['failed'] += 1
            tqdm.write(f"✗ {doi}: Could not extract abstract")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total processed: {stats['total']}")
    print(f"✓ Successfully extracted: {stats['success']}")
    if not args.missing_only:
        print(f"  Already had abstract: {stats['already_had']}")
    print(f"✗ Failed to extract: {stats['failed']}")
    print(f"✗ File not found: {stats['file_not_found']}")
    
    if args.dry_run:
        print("\n(DRY RUN - no changes were made to database)")
    else:
        print(f"\nDatabase updated!")
    
    db.close()


if __name__ == '__main__':
    main()
