"""Command-line interface for cite-hustle"""
import click
from pathlib import Path
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository
from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.collectors.pdf_downloader import PDFDownloader


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
@click.pass_context
def collect(ctx, field, year_start, year_end, parallel):
    """
    Collect article metadata from CrossRef
    
    This command fetches article metadata (title, authors, DOI, etc.)
    from the CrossRef API for the specified journals and year range.
    
    Examples:
        cite-hustle collect --field accounting --year-start 2020 --year-end 2024
        cite-hustle collect --field all --year-start 2023
    """
    repo = ctx.obj['repo']
    
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
    
    click.echo(f"\n{'='*60}\n")


@main.command()
@click.option('--limit', default=None, type=int, help='Limit number of articles to scrape')
@click.option('--delay', default=5, type=int, help='Delay between requests (seconds)')
@click.pass_context
def scrape(ctx, limit, delay):
    """
    Scrape SSRN for article pages and abstracts
    
    This command searches SSRN for articles in the database that haven't
    been scraped yet, extracts abstracts, and saves HTML content.
    """
    repo = ctx.obj['repo']
    
    # Get pending articles
    pending = repo.get_pending_ssrn_scrapes(limit=limit)
    
    if pending.empty:
        click.echo("✓ No articles pending SSRN scrape")
        return
    
    click.echo(f"\n🌐 Scraping SSRN for {len(pending)} articles")
    click.echo(f"Crawl delay: {delay} seconds\n")
    
    # TODO: Implement SSRN scraping
    # This should call your migrated ssrn_scraper logic
    click.echo("⚠️  This command needs implementation - migrate get_pdf_links.py logic here")
    click.echo("See: src/cite_hustle/collectors/ssrn_scraper.py")


@main.command()
@click.option('--limit', default=None, type=int, help='Limit number of PDFs to download')
@click.option('--delay', default=2, type=int, help='Delay between downloads (seconds)')
@click.pass_context
def download(ctx, limit, delay):
    """
    Download PDFs from SSRN
    
    This command downloads PDFs for articles where the PDF URL has been
    found but the file hasn't been downloaded yet.
    """
    repo = ctx.obj['repo']
    
    # Get pending downloads
    pending = repo.get_pending_pdf_downloads(limit=limit)
    
    if pending.empty:
        click.echo("✓ No PDFs pending download")
        return
    
    click.echo(f"\n📄 Downloading {len(pending)} PDFs")
    click.echo(f"Storage: {settings.pdf_storage_dir}")
    click.echo(f"Delay: {delay} seconds\n")
    
    # Initialize downloader
    downloader = PDFDownloader(settings.pdf_storage_dir, delay=delay)
    
    # Prepare download list
    download_list = [
        {'url': row['pdf_url'], 'doi': row['doi']}
        for _, row in pending.iterrows()
    ]
    
    # Download PDFs
    results = downloader.download_batch(download_list)
    
    # Update database
    for result in results:
        if result['success']:
            repo.update_pdf_info(
                doi=result['doi'],
                pdf_url=None,  # Keep existing URL
                pdf_file_path=result['filepath'],
                downloaded=True
            )
            repo.log_processing(result['doi'], 'download_pdf', 'success')
        else:
            repo.log_processing(result['doi'], 'download_pdf', 'failed')
    
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
@click.argument('query')
@click.option('--limit', default=20, type=int, help='Number of results')
@click.pass_context
def search(ctx, query, limit):
    """Search articles by title (requires FTS setup)"""
    repo = ctx.obj['repo']
    
    click.echo(f"\n🔍 Searching for: '{query}'")
    
    results = repo.search_by_title(query, limit)
    
    if not results:
        click.echo("No results found")
        return
    
    click.echo(f"\nFound {len(results)} results:\n")
    
    for i, result in enumerate(results[:limit], 1):
        click.echo(f"{i}. {result['title']}")
        click.echo(f"   {result['authors']} ({result['year']})")
        click.echo(f"   {result['journal']}")
        if result['score']:
            click.echo(f"   Score: {result['score']:.2f}")
        click.echo()


if __name__ == '__main__':
    main(obj={})
