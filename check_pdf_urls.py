#!/usr/bin/env python3
"""
Check if PDF URLs exist in the database
"""
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


def main():
    print("="*60)
    print("CHECKING PDF URL STATUS IN DATABASE")
    print("="*60)
    
    db = DatabaseManager(settings.db_path)
    db.connect()
    repo = ArticleRepository(db)
    
    # Query to check PDF URL status
    result = db.conn.execute("""
        SELECT 
            COUNT(*) as total_ssrn_pages,
            SUM(CASE WHEN pdf_url IS NOT NULL THEN 1 ELSE 0 END) as with_pdf_url,
            SUM(CASE WHEN pdf_url IS NULL THEN 1 ELSE 0 END) as without_pdf_url,
            SUM(CASE WHEN pdf_downloaded = TRUE THEN 1 ELSE 0 END) as downloaded
        FROM ssrn_pages
    """).fetchone()
    
    total, with_url, without_url, downloaded = result
    
    print(f"\nTotal SSRN pages in database: {total:,}")
    print(f"  With PDF URL: {with_url:,}")
    print(f"  Without PDF URL: {without_url:,}")
    print(f"  PDFs downloaded: {downloaded:,}")
    
    if without_url > 0:
        print(f"\n❌ PROBLEM: {without_url:,} SSRN pages are missing PDF URLs")
        print("\nThis means the scraper is not extracting PDF URLs from the pages.")
        print("The PDF downloader cannot work without PDF URLs!")
    
    # Sample a few records to show what we have
    print("\n" + "="*60)
    print("SAMPLE RECORDS (first 3)")
    print("="*60)
    
    sample = db.conn.execute("""
        SELECT doi, ssrn_url, pdf_url, abstract IS NOT NULL as has_abstract
        FROM ssrn_pages
        LIMIT 3
    """).fetchall()
    
    for idx, (doi, ssrn_url, pdf_url, has_abstract) in enumerate(sample, 1):
        print(f"\n{idx}. DOI: {doi}")
        print(f"   SSRN URL: {ssrn_url if ssrn_url else 'None'}")
        print(f"   PDF URL: {pdf_url if pdf_url else 'None'}")
        print(f"   Has Abstract: {'Yes' if has_abstract else 'No'}")
    
    print("\n" + "="*60)
    print("CONCLUSION")
    print("="*60)
    
    if without_url == total and total > 0:
        print("\n❌ All scraped pages are missing PDF URLs")
        print("\n✅ SOLUTION: Update the SSRN scraper to extract PDF URLs")
        print("\nNext steps:")
        print("  1. Add PDF URL extraction to ssrn_scraper.py")
        print("  2. Re-scrape articles or extract from saved HTML files")
        print("  3. Then PDF downloader will work!")
    
    print()


if __name__ == "__main__":
    main()
