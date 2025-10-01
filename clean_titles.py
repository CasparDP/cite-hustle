#!/usr/bin/env python
"""Script to clean HTML tags from existing article titles in the database"""

import re
import html
from bs4 import BeautifulSoup
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager


def clean_title(title):
    """Clean HTML tags and entities from article titles"""
    if not title:
        return title
    
    # Use BeautifulSoup to remove HTML tags
    soup = BeautifulSoup(title, 'html.parser')
    text = soup.get_text()
    
    # Decode HTML entities (e.g., &amp; -> &, &lt; -> <)
    text = html.unescape(text)
    
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def main():
    """Clean all titles in the database"""
    print(f"📁 Database: {settings.db_path}\n")
    
    # Connect to database
    db = DatabaseManager(settings.db_path)
    db.connect()
    
    # Get all articles with titles that contain HTML tags
    print("🔍 Finding articles with HTML in titles...")
    articles = db.conn.execute("""
        SELECT doi, title 
        FROM articles 
        WHERE title LIKE '%<%' OR title LIKE '%&%'
    """).fetchall()
    
    if not articles:
        print("✓ No articles with HTML tags found. Database is clean!")
        return
    
    print(f"Found {len(articles)} articles with HTML tags/entities\n")
    
    # Clean each title
    cleaned_count = 0
    for doi, title in articles:
        cleaned = clean_title(title)
        
        if cleaned != title:
            print(f"Cleaning: {title[:80]}...")
            print(f"    →     {cleaned[:80]}\n")
            
            db.conn.execute("""
                UPDATE articles 
                SET title = ?, 
                    updated_at = now()
                WHERE doi = ?
            """, [cleaned, doi])
            
            cleaned_count += 1
    
    print(f"\n✓ Cleaned {cleaned_count} article titles")
    db.close()


if __name__ == "__main__":
    main()
