"""Command-line interface for cite-hustle"""
import click
from pathlib import Path
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.collectors.ssrn_scraper import SSRNScraper
from cite_hustle.collectors.pdf_downloader import PDFDownloader
from cite_hustle.collectors.selenium_pdf_downloader import SeleniumPDFDownloader


@click.group()
@click.pass_context
def main(ctx):
    """
    Cite-Hustle: Academic Literature Research Tool
    
    A tool to automate the collection of academic papers from top journals.
    """
    # Initialize database connection
    db_manager = DatabaseManager(settings.db_path)
    db_manager.connect()
    
    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['db'] = db_manager
    ctx.obj['repo'] = ArticleRepository(db_manager)


@main.command()
@click.pass_context
def init(ctx):
    """Initialize the database schema"""
    db = ctx.obj['db']
    
    click.echo(f"📁 Database location: {settings.db_path}")
    click.echo(f"📁 Data directory: {settings.data_dir}")
    click.echo(f"📁 PDF storage: {settings.pdf_storage_dir}")
    click.echo(f"📁 HTML storage: {settings.html_storage_dir}")
    click.echo(f"📁 Cache directory: {settings.cache_dir}")
    
    click.echo("\nInitializing database schema...")
    db.initialize_schema()
    db.create_fts_indexes()
    
    click.echo("\n✓ Database initialized successfully!")


@main.command()
@click.option('--field', type=click.Choice(['accounting', 'finance', 'economics', 'all']), 
              default='all', help='Research field')
def journals(field):
    """List journals in the registry"""
    journals_list = JournalRegistry.get_by_field(field)
    
    click.echo(f"\n📚 {field.upper()} JOURNALS ({len(journals_list)} total)\n")
    
    for j in journals_list:
        click.echo(f"  • {j.name}")
        click.echo(f"    ISSN: {j.issn} | Publisher: {j.publisher}")
    
    click.echo()


@main.command()
@click.option('--field', type=click.Choice(['accounting', 'finance', 'economics', 'all']), 
              default='all', help='Research field to collect')
@click.option('--year-start', default=2004, type=int, help='Start year')
@click.option('--year-end', default=2025, type=int, help='End year')
@click.option('--parallel/--sequential', default=False, 
              help='Use parallel processing (may hit rate limits)')
@click.option('--skip-fts-rebuild', is_flag=True,
              help='Skip rebuilding FTS indexes after collection')
@click.pass_context
def collect(ctx, field, year_start, year_end, parallel, skip_fts_rebuild):
    """
    Collect article metadata from CrossRef
    
    This command fetches article metadata (title, authors, DOI, etc.)
    from the CrossRef API for the specified journals and year range.
    
    After collection completes, FTS indexes are automatically rebuilt
    to make the new articles searchable (use --skip-fts-rebuild to disable).
    
    Examples:
        cite-hustle collect --field accounting --year-start 2020 --year-end 2024
        cite-hustle collect --field all --year-start 2023
    """
    repo = ctx.obj['repo']
    db = ctx.obj['db']
    
    # Get journals for the field
    journals_list = JournalRegistry.get_by_field(field)
    years = list(range(year_start, year_end + 1))
    
    click.echo(f"\n{'='*60}")
    click.echo(f"📚 COLLECTING METADATA")
    click.echo(f"{'='*60}")
    click.echo(f"Field: {field}")
    click.echo(f"Journals: {len(journals_list)}")
    click.echo(f"Years: {year_start}-{year_end} ({len(years)} years)")
    click.echo(f"Database: {settings.db_path}")
    click.echo(f"Cache: {settings.cache_dir}")
    click.echo(f"Mode: {'Parallel' if parallel else 'Sequential'}")
    click.echo(f"{'='*60}\n")
    
    # Initialize collector
    collector = MetadataCollector(repo)
    
    # Collect metadata
    if parallel:
        results = collector.collect_parallel(journals_list, years)
    else:
        results = collector.collect_for_journals(journals_list, years)
    
    # Summary
    total_collected = sum(results.values())
    
    click.echo(f"\n{'='*60}")
    click.echo(f"✓ COLLECTION COMPLETE")
    click.echo(f"{'='*60}")
    click.echo(f"Total articles collected: {total_collected:,}")
    
    if results:
        click.echo(f"\nBreakdown by journal:")
        for journal_name, count in sorted(results.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                click.echo(f"  • {journal_name}: {count:,} articles")
    
    # Rebuild FTS indexes if new articles were collected
    if total_collected > 0 and not skip_fts_rebuild:
        click.echo(f"\n{'='*60}")
        click.echo("🔍 REBUILDING SEARCH INDEXES")
        click.echo(f"{'='*60}")
        click.echo("Updating full-text search indexes for new articles...")
        
        try:
            db.create_fts_indexes()
            click.echo("✓ Search indexes updated successfully!")
            click.echo("\nNew articles are now searchable via:")
            click.echo("  poetry run cite-hustle search 'your query'")
        except Exception as e:
            click.echo(f"⚠️  Warning: Failed to rebuild FTS indexes: {e}")
            click.echo("   You can rebuild manually with:")
            click.echo("   poetry run cite-hustle rebuild-fts")
    
    click.echo(f"\n{'='*60}\n")


@main.command()
@click.option('--limit', default=None, type=int, help='Limit number of articles to scrape')
@click.option('--delay', default=5, type=int, help='Delay between requests (seconds)')
@click.option('--threshold', default=85, type=int, help='Minimum similarity threshold (0-100)')
@click.option('--headless/--no-headless', default=True, help='Run browser in headless mode')
@click.pass_context
def scrape(ctx, limit, delay, threshold, headless):
    """
    Scrape SSRN for article pages and abstracts
    
    This command searches SSRN for articles in the database that haven't
    been scraped yet, extracts abstracts, and saves HTML content.
    
    Examples:
        cite-hustle scrape --limit 10
        cite-hustle scrape --delay 3 --threshold 90
        cite-hustle scrape --no-headless  # Show browser (for debugging)
    """
    repo = ctx.obj['repo']
    
    # Get pending articles
    pending = repo.get_pending_ssrn_scrapes(limit=limit)
    
    if pending.empty:
        click.echo("✓ No articles pending SSRN scrape")
        return
    
    click.echo(f"\n{'='*60}")
    click.echo(f"🌐 SCRAPING SSRN")
    click.echo(f"{'='*60}")
    click.echo(f"Articles to scrape: {len(pending)}")
    click.echo(f"Crawl delay: {delay} seconds")
    click.echo(f"Similarity threshold: {threshold}")
    click.echo(f"Headless mode: {'Yes' if headless else 'No'}")
    click.echo(f"HTML storage: {settings.html_storage_dir}")
    click.echo(f"{'='*60}\n")
    
    # Initialize scraper
    scraper = SSRNScraper(
        repo=repo,
        crawl_delay=delay,
        similarity_threshold=threshold,
        headless=headless
    )
    
    # Scrape articles
    try:
        stats = scraper.scrape_articles(pending, show_progress=True)
        
        # Summary
        click.echo(f"\n{'='*60}")
        click.echo(f"✓ SCRAPING COMPLETE")
        click.echo(f"{'='*60}")
        click.echo(f"Total processed: {stats['total']}")
        click.echo(f"✓ Successful: {stats['success']}")
        click.echo(f"⚠️  No match: {stats['no_match']}")
        click.echo(f"✗ Failed: {stats['failed']}")
        
        if stats['success'] > 0:
            click.echo(f"\nAbstracts saved to database and searchable via:")
            click.echo(f"  poetry run cite-hustle search 'your query'")
        
        click.echo(f"\n{'='*60}\n")
        
    except KeyboardInterrupt:
        click.echo("\n\n⚠️  Scraping interrupted by user")
        click.echo("Progress has been saved. Run the command again to continue.")
    except Exception as e:
        click.echo(f"\n❌ Error during scraping: {e}")
        import traceback
        traceback.print_exc()


@main.command()
@click.option('--limit', default=None, type=int, help='Limit number of PDFs to download')
@click.option('--delay', default=2, type=int, help='Delay between downloads (seconds)')
@click.option('--use-selenium', is_flag=True, help='Use Selenium browser automation (bypasses Cloudflare)')
@click.option('--headless/--no-headless', default=True, help='Run browser in headless mode (Selenium only)')
@click.pass_context
def download(ctx, limit, delay, use_selenium, headless):
    """
    Download PDFs from SSRN
    
    This command downloads PDFs for articles where SSRN URLs are available.
    Due to Cloudflare protection, direct HTTP downloads usually fail.
    Use --use-selenium for browser automation that can bypass protection.
    
    Examples:
        cite-hustle download --limit 10 --use-selenium
        cite-hustle download --use-selenium --no-headless  # Show browser
        cite-hustle download --delay 5 --use-selenium
    """
    repo = ctx.obj['repo']
    
    # Get articles with SSRN URLs for download
    if use_selenium:
        # For Selenium, we need articles with SSRN URLs (not necessarily PDF URLs)
        articles = repo.get_articles_with_ssrn_urls(limit=limit, downloaded=False)
        if articles.empty:
            click.echo("✓ No articles with SSRN URLs available for download")
            return
    else:
        # For HTTP, we need articles with PDF URLs
        pending = repo.get_pending_pdf_downloads(limit=limit)
        if pending.empty:
            click.echo("✓ No PDFs pending download")
            return
        articles = pending
    
    download_method = "Selenium browser automation" if use_selenium else "HTTP requests"
    click.echo(f"\n📄 Downloading {len(articles)} PDFs using {download_method}")
    click.echo(f"Storage: {settings.pdf_storage_dir}")
    click.echo(f"Delay: {delay} seconds")
    if use_selenium:
        click.echo(f"Browser mode: {'Headless' if headless else 'Visible'}")
    click.echo()
    
    if not use_selenium:
        # Show warning about Cloudflare protection
        click.echo("⚠️  Note: Direct HTTP downloads usually fail due to SSRN's Cloudflare protection.")
        click.echo("   Consider using --use-selenium for better success rates.\n")
    
    # Initialize appropriate downloader
    if use_selenium:
        from cite_hustle.collectors.selenium_pdf_downloader import SeleniumPDFDownloader
        downloader = SeleniumPDFDownloader(
            storage_dir=settings.pdf_storage_dir, 
            delay=delay,
            headless=headless
        )
        
        # Prepare download list for Selenium (uses SSRN URLs)
        download_list = [
            {
                'doi': row['doi'],
                'ssrn_url': row['ssrn_url']
            }
            for _, row in articles.iterrows() if row.get('ssrn_url')
        ]
    else:
        downloader = PDFDownloader(settings.pdf_storage_dir, delay=delay)
        
        # Prepare download list with PDF URLs (HTTP downloader will construct from SSRN URLs)
        download_list = [
            {
                'url': row.get('pdf_url'),  # May be None
                'doi': row['doi'],
                'ssrn_url': row['ssrn_url']  # Use this to construct PDF URL
            }
            for _, row in articles.iterrows()
        ]
    
    if not download_list:
        click.echo("✗ No valid articles found for download")
        return
    
    # Download PDFs
    results = downloader.download_batch(download_list)
    
    # Update database with results
    for result in results:
        if result['success']:
            # For HTTP downloader, construct PDF URL from SSRN URL
            if not use_selenium and hasattr(downloader, 'construct_pdf_url'):
                pdf_url = downloader.construct_pdf_url(result.get('ssrn_url')) if result.get('ssrn_url') else None
            else:
                pdf_url = None  # Selenium doesn't provide direct PDF URLs
            
            repo.update_pdf_info(
                doi=result['doi'],
                pdf_url=pdf_url,
                pdf_file_path=result['filepath'],
                downloaded=True
            )
            repo.log_processing(result['doi'], 'download_pdf', 'success')
        else:
            error_message = "Download failed" + (" (Cloudflare blocked)" if not use_selenium else "")
            repo.log_processing(result['doi'], 'download_pdf', 'failed', error_message)
    
    click.echo(f"\n✓ Download process complete")


@main.command()
@click.pass_context
def status(ctx):
    """Show database statistics and progress"""
    repo = ctx.obj['repo']
    
    stats = repo.get_statistics()
    
    click.echo("\n" + "="*50)
    click.echo("📊 CITE-HUSTLE STATUS")
    click.echo("="*50)
    
    click.echo(f"\n📁 Database: {settings.db_path}")
    db_size = settings.db_path.stat().st_size / 1024 / 1024 if settings.db_path.exists() else 0
    click.echo(f"   Size: {db_size:.2f} MB")
    
    click.echo(f"\n📚 Articles: {stats['total_articles']:,}")
    
    if stats['by_year']:
        click.echo("\n   Recent years:")
        for year_stat in stats['by_year'][:5]:
            click.echo(f"     {year_stat['year']}: {year_stat['count']:,} articles")
    
    click.echo(f"\n🌐 SSRN pages scraped: {stats['ssrn_scraped']:,}")
    click.echo(f"📄 PDFs downloaded: {stats['pdfs_downloaded']:,}")
    
    # Pending tasks
    if stats['pending_ssrn_scrapes'] > 0:
        click.echo(f"\n⏳ Pending SSRN scrapes: {stats['pending_ssrn_scrapes']:,}")
    
    if stats['pending_pdf_downloads'] > 0:
        click.echo(f"⏳ Pending PDF downloads: {stats['pending_pdf_downloads']:,}")
    
    if stats['pending_ssrn_scrapes'] == 0 and stats['pending_pdf_downloads'] == 0:
        click.echo("\n✓ All tasks complete!")
    
    click.echo("\n" + "="*50 + "\n")


@main.command()
@click.option('--limit', default=10, type=int, help='Number of articles to show')
@click.pass_context
def sample(ctx, limit):
    """Show a sample of articles in the database"""
    repo = ctx.obj['repo']
    
    articles = repo.get_sample_articles(limit)
    
    if articles.empty:
        click.echo("\n⚠️  No articles in database. Run 'cite-hustle collect' first.")
        return
    
    click.echo(f"\n📚 Sample of {len(articles)} most recent articles:\n")
    
    for idx, row in articles.iterrows():
        click.echo(f"{idx + 1}. {row['title']}")
        click.echo(f"   Authors: {row['authors']}")
        click.echo(f"   Journal: {row['journal_name']} ({row['year']})")
        click.echo(f"   DOI: {row['doi']}")
        click.echo()


@main.command()
@click.argument('query')
@click.option('--limit', default=20, type=int, help='Number of results')
@click.option('--author', is_flag=True, help='Search by author instead of title')
@click.pass_context
def search(ctx, query, limit, author):
    """
    Search articles by title or author using full-text search
    
    Examples:
        cite-hustle search "earnings management"
        cite-hustle search "Smith" --author
        cite-hustle search "accounting" --limit 50
    """
    repo = ctx.obj['repo']
    
    if author:
        click.echo(f"\n🔍 Searching authors for: '{query}'")
        results = repo.search_by_author(query, limit)
    else:
        click.echo(f"\n🔍 Searching titles for: '{query}'")
        results = repo.search_by_title(query, limit)
    
    if not results:
        click.echo(f"\n❌ No results found for '{query}'")
        click.echo("\nTips:")
        click.echo("  • Try different keywords or shorter terms")
        click.echo("  • Search uses full-text indexing with relevance ranking")
        click.echo("  • Use --author flag to search by author name")
        click.echo("  • Use 'cite-hustle sample' to see what's in the database")
        click.echo("\nIf you just added articles, the search index may need rebuilding:")
        click.echo("  poetry run cite-hustle rebuild-fts")
        return
    
    click.echo(f"\n✓ Found {len(results)} result{'s' if len(results) != 1 else ''}:\n")
    
    for i, result in enumerate(results, 1):
        click.echo(f"{i}. {result['title']}")
        click.echo(f"   Authors: {result['authors']}")
        click.echo(f"   Journal: {result['journal']} ({result['year']})")
        click.echo(f"   DOI: {result['doi']}")
        if 'score' in result and result['score']:
            click.echo(f"   Relevance: {result['score']:.2f}")
        click.echo()


@main.command(name='rebuild-fts')
@click.pass_context
def rebuild_fts(ctx):
    """
    Rebuild full-text search indexes
    
    Use this command if search is not returning expected results.
    This rebuilds the FTS indexes to include all articles in the database.
    """
    db = ctx.obj['db']
    repo = ctx.obj['repo']
    
    click.echo("\n" + "="*60)
    click.echo("🔍 REBUILDING FULL-TEXT SEARCH INDEXES")
    click.echo("="*60)
    
    # Check article count
    count = repo.get_article_count()
    click.echo(f"\nArticles in database: {count:,}")
    
    if count == 0:
        click.echo("\n⚠️  No articles to index!")
        click.echo("Run: poetry run cite-hustle collect --field accounting --year-start 2023")
        return
    
    click.echo("\nRebuilding indexes...")
    try:
        db.create_fts_indexes()
        click.echo("✓ FTS indexes rebuilt successfully!")
        
        # Test search
        click.echo("\nTesting search...")
        results = repo.search_by_title("accounting", limit=3)
        if results:
            click.echo(f"✓ Search working! Found {len(results)} results for 'accounting'")
        else:
            click.echo("⚠️  Search returned no results (may be normal)")
        
    except Exception as e:
        click.echo(f"❌ Error rebuilding indexes: {e}")
        return
    
    click.echo("\n" + "="*60)
    click.echo("✓ REBUILD COMPLETE")
    click.echo("="*60)
    click.echo("\nYou can now search with: poetry run cite-hustle search 'your query'\n")


if __name__ == '__main__':
    main(obj={})
