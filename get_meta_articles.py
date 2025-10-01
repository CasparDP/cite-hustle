from crossref_commons.retrieval import get_publication_as_json
from crossref_commons.iteration import iterate_publications_as_json
import concurrent.futures
import time
import csv
import os
import json
import re
import html
from pathlib import Path
from tenacity import retry, wait_exponential, stop_after_attempt
from tqdm import tqdm
from bs4 import BeautifulSoup

# Journal dictionary remains the same
star_journals_dict = {
    'The Accounting Review': '0001-4826',
    'Journal of Accounting and Economics': '0165-4101',
    'Accounting, Organizations and Society': '0361-3682',
    'Review of Accounting Studies': '1380-6653',
    'Contemporary Accounting Review': '0823-9150',
    'Journal of Accounting Research': '0021-8456',
    'Journal of Finance': '0022-1082',
    'Journal of Financial Economics': '0304-405X',
    'Journal of Financial and Quantitative Analysis': '0022-1090',
    'Review of Finance': '1572-3097',
    'Review of Financial Studies': '0893-9454',
    'Quarterly Journal of Economics':'0033-5533',
    'Journal of Economic Literature': '0022-0515',
    'Journal of Political Economy': '0022-3808',
    'Journal of Economic Perspectives': '0895-3309',
    'Econometrica': '0012-9682',
    'American Economic Review': '0002-8282',
    'Journal of labor Economics': '0734-306X',
}


CACHE_DIR = Path(os.path.expandvars("$HOME/Dropbox/Github Data/cite-hustle/cache"))
METADATA_DIR = Path(os.path.expandvars("$HOME/Dropbox/Github Data/cite-hustle/metadata"))
MAX_WORKERS = 3  # Adjust based on API rate limits

# Create necessary directories
CACHE_DIR.mkdir(exist_ok=True, parents=True)
METADATA_DIR.mkdir(exist_ok=True, parents=True)

@retry(wait=wait_exponential(multiplier=1, min=4, max=10),
       stop=stop_after_attempt(3))
def fetch_articles_by_issn(year, issn):
    cache_file = CACHE_DIR / f"cache_{issn}_{year}.json"
    
    # Check cache first
    if cache_file.exists():
        with open(cache_file, 'r') as f:
            return json.load(f)
    
    try:
        # Set email for polite API usage via environment variable
        os.environ['CR_API_MAILTO'] = 'spiny.bubble0v@icloud.com'
        
        # Use new crossref_commons API
        filter_params = {
            'issn': issn,
            'from-pub-date': f'{year}-01-01',
            'until-pub-date': f'{year}-12-31'
        }
        
        # Collect all articles using iterate_publications_as_json
        articles = []
        for article in iterate_publications_as_json(filter=filter_params):
            articles.append(article)
        
        # Cache the results
        with open(cache_file, 'w') as f:
            json.dump(articles, f)
            
        return articles
    except Exception as e:
        print(f"Error fetching {issn} for {year}: {e}")
        return []

def process_article_batch(articles):
    rows = []
    for article in articles:
        doi = article.get('DOI', '')
        raw_title = ' '.join(article.get('title', ['No Title Available']))
        title = clean_title(raw_title)
        year = article.get('issued', {}).get('date-parts', [[None]])[0][0]
        issn = ', '.join(article.get('ISSN', []))
        publisher = article.get('publisher', 'Unknown')
        
        authors = article.get('author', [])
        if not authors:
            rows.append([doi, title, 'Unknown', 'Unknown', 'Unknown', 
                        'No Affiliation', year, issn, publisher])
        else:
            for author in authors:
                given = author.get('given', 'Unknown')
                family = author.get('family', 'Unknown')
                sequence = author.get('sequence', 'Unknown')
                affiliation = ', '.join(aff.get('name', 'No Affiliation') 
                                      for aff in author.get('affiliation', [])) \
                            if author.get('affiliation') else 'No Affiliation'
                rows.append([doi, title, given, family, sequence, 
                           affiliation, year, issn, publisher])
    return rows

def save_to_csv(rows, filename):
    headers = ['DOI', 'Title', 'Author Given Name', 'Author Family Name',
              'Author Sequence', 'Author Affiliation', 'Year', 'ISSN', 'Publisher']
    
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)

def process_year_issn(params):
    year, issn = params
    metadata_file = METADATA_DIR / f'articles_{issn}_{year}.csv'
    
    # Skip if file already exists and is not empty
    if metadata_file.exists() and metadata_file.stat().st_size > 0:
        print(f"Skipping existing file: {metadata_file}")
        return 0
        
    articles = fetch_articles_by_issn(year, issn)
    if articles:
        rows = process_article_batch(articles)
        save_to_csv(rows, metadata_file)
        return len(articles)
    return 0

def process_multiple_years_issns(years, issns):
    params = [(year, issn) for year in years for issn in issns]
    # Filter out already processed pairs
    new_params = [
        (year, issn) for year, issn in params 
        if not (METADATA_DIR / f'articles_{issn}_{year}.csv').exists() or 
        (METADATA_DIR / f'articles_{issn}_{year}.csv').stat().st_size == 0
    ]
    
    if not new_params:
        print("All articles have been previously downloaded.")
        return
        
    total = len(new_params)
    print(f"Processing {total} new year-ISSN pairs...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_year_issn, param) for param in new_params]
        
        with tqdm(total=total, desc="Processing articles") as pbar:
            for future in concurrent.futures.as_completed(futures):
                count = future.result()
                pbar.update(1)
                
#TODO: Add a main function to run the script
if __name__ == "__main__":
    years = list(range(2000, 2026))
    issns = list(star_journals_dict.values())
    process_multiple_years_issns(years, issns)