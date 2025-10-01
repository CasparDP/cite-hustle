"""CrossRef metadata collector for academic articles"""
from crossref.restful import Works, Etiquette
import concurrent.futures
import json
from pathlib import Path
from typing import List, Dict, Optional
from tenacity import retry, wait_exponential, stop_after_attempt
from tqdm import tqdm

from cite_hustle.config import settings
from cite_hustle.collectors.journals import Journal
from cite_hustle.database.repository import ArticleRepository


class MetadataCollector:
    """Collects article metadata from CrossRef API"""
    
    def __init__(self, repo: ArticleRepository, cache_dir: Optional[Path] = None):
        """
        Initialize metadata collector
        
        Args:
            repo: Article repository for database access
            cache_dir: Directory for caching API responses (defaults to settings.cache_dir)
        """
        self.repo = repo
        self.cache_dir = cache_dir or settings.cache_dir
        self.cache_dir.mkdir(exist_ok=True, parents=True)
        
        # Setup CrossRef API with etiquette
        my_etiquette = Etiquette(
            'Academic Researcher', 
            'cite-hustle', 
            'https://github.com/CasparDP/cite-hustle',
            settings.crossref_email
        )
        self.works = Works(etiquette=my_etiquette)
    
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        stop=stop_after_attempt(3)
    )
    def fetch_articles_by_issn(self, year: int, issn: str) -> List[Dict]:
        """
        Fetch articles from CrossRef API for a specific journal and year
        
        Args:
            year: Publication year
            issn: Journal ISSN
            
        Returns:
            List of article dictionaries from CrossRef
        """
        cache_file = self.cache_dir / f"cache_{issn}_{year}.json"
        
        # Check cache first
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(f"⚠️  Corrupted cache file, re-fetching: {cache_file}")
                cache_file.unlink()
        
        # Fetch from CrossRef API
        query = self.works.filter(
            issn=issn,
            from_pub_date=f'{year}-01-01',
            until_pub_date=f'{year}-12-31'
        )
        
        try:
            articles = list(query.select('DOI', 'title', 'author', 'issued', 'ISSN', 'publisher'))
            
            # Cache the results
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(articles, f, indent=2)
            
            return articles
            
        except Exception as e:
            print(f"✗ Error fetching {issn} for {year}: {e}")
            self.repo.log_processing(
                doi=f"{issn}_{year}",
                stage='metadata_fetch',
                status='failed',
                error_message=str(e)
            )
            return []
    
    def transform_articles(self, articles: List[Dict], journal: Journal) -> List[Dict]:
        """
        Transform CrossRef article data into database format
        
        Args:
            articles: Raw articles from CrossRef
            journal: Journal metadata
            
        Returns:
            List of article dictionaries ready for database insertion
        """
        transformed = []
        
        for article in articles:
            doi = article.get('DOI', '')
            if not doi:
                continue
                
            title = ' '.join(article.get('title', ['No Title Available']))
            year = article.get('issued', {}).get('date-parts', [[None]])[0][0]
            
            if not year:
                continue
            
            # Combine author names
            authors_list = article.get('author', [])
            if authors_list:
                author_names = []
                for author in authors_list:
                    given = author.get('given', '')
                    family = author.get('family', '')
                    if given and family:
                        author_names.append(f"{given} {family}")
                    elif family:
                        author_names.append(family)
                authors = '; '.join(author_names)
            else:
                authors = 'Unknown'
            
            publisher = article.get('publisher', 'Unknown')
            
            transformed.append({
                'doi': doi,
                'title': title,
                'authors': authors,
                'year': year,
                'journal_issn': journal.issn,
                'journal_name': journal.name,
                'publisher': publisher
            })
        
        return transformed
    
    def collect_for_journal(self, journal: Journal, years: List[int], 
                          show_progress: bool = True) -> int:
        """
        Collect articles for a specific journal across multiple years
        
        Args:
            journal: Journal to collect articles for
            years: List of years to collect
            show_progress: Show progress bar
            
        Returns:
            Total number of articles collected
        """
        total_articles = 0
        
        iterator = tqdm(years, desc=f"Collecting {journal.name}") if show_progress else years
        
        for year in iterator:
            # Check if already processed
            existing = self.repo.conn.execute("""
                SELECT COUNT(*) FROM articles 
                WHERE journal_issn = ? AND year = ?
            """, [journal.issn, year]).fetchone()[0]
            
            if existing > 0:
                if show_progress:
                    tqdm.write(f"  ✓ {year}: {existing} articles already in database")
                continue
            
            # Fetch from CrossRef
            articles = self.fetch_articles_by_issn(year, journal.issn)
            
            if not articles:
                continue
            
            # Transform and save
            transformed = self.transform_articles(articles, journal)
            
            if transformed:
                self.repo.bulk_insert_articles(transformed)
                total_articles += len(transformed)
                
                # Log success
                self.repo.log_processing(
                    doi=f"{journal.issn}_{year}",
                    stage='metadata_collect',
                    status='success',
                    error_message=f"Collected {len(transformed)} articles"
                )
                
                if show_progress:
                    tqdm.write(f"  ✓ {year}: {len(transformed)} articles collected")
        
        return total_articles
    
    def collect_for_journals(self, journals: List[Journal], years: List[int],
                           max_workers: Optional[int] = None) -> Dict[str, int]:
        """
        Collect articles for multiple journals in parallel
        
        Args:
            journals: List of journals to collect
            years: List of years to collect
            max_workers: Number of parallel workers (defaults to settings.max_workers)
            
        Returns:
            Dictionary mapping journal name to article count
        """
        max_workers = max_workers or settings.max_workers
        results = {}
        
        print(f"📚 Collecting metadata for {len(journals)} journals across {len(years)} years")
        print(f"Using {max_workers} parallel workers")
        print(f"Cache directory: {self.cache_dir}\n")
        
        # Process journals sequentially to show clear progress
        for journal in journals:
            print(f"\n{journal.name} ({journal.issn}):")
            count = self.collect_for_journal(journal, years, show_progress=True)
            results[journal.name] = count
            
            if count > 0:
                print(f"  ✓ Total: {count} articles collected")
            else:
                print(f"  ℹ️  No new articles")
        
        return results
    
    def collect_parallel(self, journals: List[Journal], years: List[int],
                        max_workers: Optional[int] = None) -> Dict[str, int]:
        """
        Collect articles for multiple journals with true parallelism
        
        Note: Use with caution as this may hit API rate limits
        
        Args:
            journals: List of journals to collect
            years: List of years to collect
            max_workers: Number of parallel workers
            
        Returns:
            Dictionary mapping journal name to article count
        """
        max_workers = max_workers or settings.max_workers
        results = {}
        
        print(f"📚 Collecting metadata (parallel mode)")
        print(f"Journals: {len(journals)} | Years: {len(years)} | Workers: {max_workers}\n")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.collect_for_journal, journal, years, False): journal 
                for journal in journals
            }
            
            with tqdm(total=len(journals), desc="Processing journals") as pbar:
                for future in concurrent.futures.as_completed(futures):
                    journal = futures[future]
                    try:
                        count = future.result()
                        results[journal.name] = count
                        pbar.set_postfix_str(f"{journal.name}: {count} articles")
                    except Exception as e:
                        print(f"\n✗ Error processing {journal.name}: {e}")
                        results[journal.name] = 0
                    finally:
                        pbar.update(1)
        
        return results
