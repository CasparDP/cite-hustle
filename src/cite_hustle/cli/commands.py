"""Command-line interface for cite-hustle"""

import asyncio
import sys
from pathlib import Path

import click

from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.collectors.metadata import MetadataCollector
from cite_hustle.collectors.openalex_enricher import OpenAlexEnricher
from cite_hustle.collectors.selenium_pdf_downloader import SeleniumPDFDownloader
from cite_hustle.collectors.ssrn_scraper import SSRNScraper
from cite_hustle.config import settings
from cite_hustle.database.models import DatabaseManager
from cite_hustle.database.repository import ArticleRepository


# Commands that only read the database. These open it read-only so they can run
# alongside other readers (e.g. the rainer MCP server) without a lock conflict.
READ_ONLY_COMMANDS = {"status", "dashboard", "journals", "search", "sample"}

# Seconds a write command waits for another process to release the DB file.
WRITE_LOCK_WAIT_SECONDS = 120


@click.group()
@click.pass_context
def main(ctx):
    """
    Cite-Hustle: Academic Literature Research Tool

    A tool to automate the collection of academic papers from top journals.
    """
    ctx.ensure_object(dict)

    # Help text and bare invocation don't touch the database.
    if ctx.invoked_subcommand is None or "--help" in sys.argv or "-h" in sys.argv:
        return

    read_only = ctx.invoked_subcommand in READ_ONLY_COMMANDS
    db_manager = DatabaseManager(settings.db_path)
    db_manager.connect(
        read_only=read_only,
        max_wait=0 if read_only else WRITE_LOCK_WAIT_SECONDS,
    )

    ctx.obj["db"] = db_manager
    ctx.obj["repo"] = ArticleRepository(db_manager)


@main.command()
@click.pass_context
def init(ctx):
    """Initialize the database schema"""
    db = ctx.obj["db"]

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
@click.option(
    "--field",
    type=click.Choice(["accounting", "finance", "economics", "management", "all"]),
    default="all",
    help="Research field",
)
def journals(field):
    """List journals in the registry"""
    journals_list = JournalRegistry.get_by_field(field)

    click.echo(f"\n📚 {field.upper()} JOURNALS ({len(journals_list)} total)\n")

    for j in journals_list:
        click.echo(f"  • {j.name}")
        click.echo(f"    ISSN: {j.issn} | Publisher: {j.publisher}")

    click.echo()


@main.command()
@click.option(
    "--field",
    type=click.Choice(["accounting", "finance", "economics", "management", "all"]),
    default="all",
    help="Research field to collect",
)
@click.option("--year-start", default=2004, type=int, help="Start year")
@click.option("--year-end", default=2025, type=int, help="End year")
@click.option(
    "--parallel/--sequential", default=False, help="Use parallel processing (may hit rate limits)"
)
@click.option(
    "--skip-fts-rebuild", is_flag=True, help="Skip rebuilding FTS indexes after collection"
)
@click.option(
    "--force",
    is_flag=True,
    help="Force re-fetch by clearing cache and bypassing DB checks for specified years",
)
@click.pass_context
def collect(ctx, field, year_start, year_end, parallel, skip_fts_rebuild, force):
    """
    Collect article metadata from CrossRef

    This command fetches article metadata (title, authors, DOI, etc.)
    from the CrossRef API for the specified journals and year range.

    After collection completes, FTS indexes are automatically rebuilt
    to make the new articles searchable (use --skip-fts-rebuild to disable).

    Use --force to refresh metadata for years that were previously collected.
    This clears the cache files and fetches fresh data from CrossRef.

    Examples:
        cite-hustle collect --field accounting --year-start 2020 --year-end 2024
        cite-hustle collect --field all --year-start 2023
        cite-hustle collect --field all --year-start 2024 --year-end 2025 --force
    """
    repo = ctx.obj["repo"]
    db = ctx.obj["db"]

    # Get journals for the field
    journals_list = JournalRegistry.get_by_field(field)
    years = list(range(year_start, year_end + 1))

    click.echo(f"\n{'=' * 60}")
    click.echo(f"📚 COLLECTING METADATA")
    click.echo(f"{'=' * 60}")
    click.echo(f"Field: {field}")
    click.echo(f"Journals: {len(journals_list)}")
    click.echo(f"Years: {year_start}-{year_end} ({len(years)} years)")
    click.echo(f"Database: {settings.db_path}")
    click.echo(f"Cache: {settings.cache_dir}")
    click.echo(f"Mode: {'Parallel' if parallel else 'Sequential'}")
    click.echo(f"Force refresh: {'Yes' if force else 'No'}")
    click.echo(f"{'=' * 60}\n")

    # Initialize collector
    collector = MetadataCollector(repo)

    # If force flag is set, clear cache files for the specified years
    if force:
        click.echo("🗑️  Clearing cache files for specified years...")
        cache_cleared = 0
        for journal in journals_list:
            for year in years:
                cache_file = settings.cache_dir / f"cache_{journal.issn}_{year}.json"
                if cache_file.exists():
                    cache_file.unlink()
                    cache_cleared += 1
        click.echo(f"   Deleted {cache_cleared} cache files\n")

    # Collect metadata
    if parallel:
        results = collector.collect_parallel(journals_list, years, force=force)
    else:
        results = collector.collect_for_journals(journals_list, years, force=force)

    # Summary
    total_collected = sum(results.values())

    click.echo(f"\n{'=' * 60}")
    click.echo(f"✓ COLLECTION COMPLETE")
    click.echo(f"{'=' * 60}")
    click.echo(f"Total articles collected: {total_collected:,}")

    if results:
        click.echo(f"\nBreakdown by journal:")
        for journal_name, count in sorted(results.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                click.echo(f"  • {journal_name}: {count:,} articles")

    # Rebuild FTS indexes if new articles were collected
    if total_collected > 0 and not skip_fts_rebuild:
        click.echo(f"\n{'=' * 60}")
        click.echo("🔍 REBUILDING SEARCH INDEXES")
        click.echo(f"{'=' * 60}")
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

    click.echo(f"\n{'=' * 60}\n")


@main.command()
@click.option("--limit", default=None, type=int, help="Limit number of articles to scrape")
@click.option("--delay", default=5, type=int, help="Delay between requests (seconds)")
@click.option("--threshold", default=85, type=int, help="Minimum similarity threshold (0-100)")
@click.option("--headless/--no-headless", default=True, help="Run browser in headless mode")
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
    repo = ctx.obj["repo"]

    # Get pending articles
    pending = repo.get_pending_ssrn_scrapes(limit=limit)

    if pending.empty:
        click.echo("✓ No articles pending SSRN scrape")
        return

    click.echo(f"\n{'=' * 60}")
    click.echo(f"🌐 SCRAPING SSRN")
    click.echo(f"{'=' * 60}")
    click.echo(f"Articles to scrape: {len(pending)}")
    click.echo(f"Crawl delay: {delay} seconds")
    click.echo(f"Similarity threshold: {threshold}")
    click.echo(f"Headless mode: {'Yes' if headless else 'No'}")
    click.echo(f"HTML storage: {settings.html_storage_dir}")
    click.echo(f"{'=' * 60}\n")

    # Initialize scraper
    scraper = SSRNScraper(
        repo=repo, crawl_delay=delay, similarity_threshold=threshold, headless=headless
    )

    # Scrape articles
    try:
        stats = scraper.scrape_articles(pending, show_progress=True)

        # Summary
        click.echo(f"\n{'=' * 60}")
        click.echo(f"✓ SCRAPING COMPLETE")
        click.echo(f"{'=' * 60}")
        click.echo(f"Total processed: {stats['total']}")
        click.echo(f"✓ Successful: {stats['success']}")
        click.echo(f"⚠️  No match: {stats['no_match']}")
        click.echo(f"✗ Failed: {stats['failed']}")

        if stats["success"] > 0:
            click.echo(f"\nAbstracts saved to database and searchable via:")
            click.echo(f"  poetry run cite-hustle search 'your query'")

        click.echo(f"\n{'=' * 60}\n")

    except KeyboardInterrupt:
        click.echo("\n\n⚠️  Scraping interrupted by user")
        click.echo("Progress has been saved. Run the command again to continue.")
    except Exception as e:
        click.echo(f"\n❌ Error during scraping: {e}")
        import traceback

        traceback.print_exc()


@main.command(name="enrich-openalex")
@click.option("--limit", default=None, type=int, help="Limit number of articles to enrich")
@click.option("--year-start", default=None, type=int, help="Start year filter")
@click.option("--year-end", default=None, type=int, help="End year filter")
@click.option("--concurrency", default=3, type=int, help="Concurrent OpenAlex requests")
@click.option("--delay", default=0.0, type=float, help="Delay between OpenAlex requests (seconds)")
@click.option("--force", is_flag=True, help="Overwrite existing abstracts")
@click.option(
    "--print-abstracts", default=0, type=int, help="Print the most recent enriched abstracts"
)
@click.option(
    "--skip-fts-rebuild", is_flag=True, help="Skip rebuilding FTS indexes after enrichment"
)
@click.pass_context
def enrich_openalex(
    ctx,
    limit,
    year_start,
    year_end,
    concurrency,
    delay,
    force,
    print_abstracts,
    skip_fts_rebuild,
):
    """
    Enrich missing abstracts using OpenAlex.

    Examples:
        cite-hustle enrich-openalex --limit 200
        cite-hustle enrich-openalex --year-start 2020 --year-end 2024 --concurrency 3
        cite-hustle enrich-openalex --force --skip-fts-rebuild
    """
    repo = ctx.obj["repo"]
    db = ctx.obj["db"]

    pending = repo.get_articles_missing_abstract(
        limit=limit, year_start=year_start, year_end=year_end
    )

    if pending.empty:
        click.echo("✓ No articles missing abstracts")
        return

    click.echo(f"\n{'=' * 60}")
    click.echo("📚 ENRICHING ABSTRACTS (OPENALEX)")
    click.echo(f"{'=' * 60}")
    click.echo(f"Candidates: {len(pending)}")
    click.echo(f"Concurrency: {concurrency}")
    click.echo(f"Delay: {delay} seconds")
    click.echo(f"Force overwrite: {'Yes' if force else 'No'}")
    click.echo(f"{'=' * 60}\n")

    enricher = OpenAlexEnricher(repo, concurrency=concurrency, delay_s=delay)
    stats = asyncio.run(enricher.enrich_missing_abstracts(pending.to_dict("records"), force=force))

    click.echo(f"\n{'=' * 60}")
    click.echo("✓ ENRICHMENT COMPLETE")
    click.echo(f"{'=' * 60}")
    click.echo(f"Total candidates: {stats['total']}")
    click.echo(f"✓ Updated: {stats['updated']}")
    click.echo(f"⚠️  Not found: {stats['not_found']}")
    click.echo(f"⚠️  Empty abstracts: {stats['empty_abstract']}")
    click.echo(f"⚠️  Invalid DOIs: {stats['invalid_doi']}")
    click.echo(f"✗ Failed: {stats['failed']}")

    if print_abstracts and stats["updated"] > 0:
        abstracts = repo.get_recent_openalex_abstracts(limit=print_abstracts)
        if abstracts:
            click.echo("\nRecent OpenAlex abstracts:\n")
            for idx, row in enumerate(abstracts, 1):
                click.echo(f"{idx}. DOI: {row['doi']}")
                click.echo(f"   Title: {row['title']}")
                click.echo(f"   Abstract: {row['abstract']}\n")

    if stats["updated"] > 0 and not skip_fts_rebuild:
        click.echo("\nRebuilding FTS indexes...")
        try:
            db.create_fts_indexes()
            click.echo("✓ Search indexes updated successfully!")
        except Exception as e:
            click.echo(f"⚠️  Warning: Failed to rebuild FTS indexes: {e}")
            click.echo("   You can rebuild manually with:")
            click.echo("   poetry run cite-hustle rebuild-fts")

    click.echo(f"\n{'=' * 60}\n")


@main.command()
@click.option("--limit", default=None, type=int, help="Limit number of PDFs (default: all pending)")
@click.option(
    "--delay", default=3, type=int, help="Base delay between downloads in seconds (jittered)"
)
@click.option(
    "--headless/--no-headless",
    default=False,
    help="Run browser headless. NOTE: headless is blocked by SSRN's Cloudflare; leave it off.",
)
@click.option(
    "--use-selenium/--no-use-selenium",
    default=True,
    help="Deprecated: SSRN downloads always use the browser path.",
)
@click.option(
    "--retry-unavailable",
    is_flag=True,
    help="Also re-try papers previously marked 'not available for download'.",
)
@click.pass_context
def download(ctx, limit, delay, headless, use_selenium, retry_unavailable):
    """
    Download SSRN PDFs into the storage directory.

    Uses a visible Chrome browser (undetected-chromedriver) to pass SSRN's
    Cloudflare protection, then clicks the paper's download button. Papers with
    no posted full text are marked unavailable and skipped on later runs.

    Progress is saved after every paper, so the run resumes where it left off
    and is safe to leave running unattended (e.g. overnight):

        caffeinate -i poetry run cite-hustle download   # macOS: prevent sleep

    Examples:
        cite-hustle download                      # all pending papers
        cite-hustle download --limit 50
        cite-hustle download --no-headless --limit 5   # watch the browser
    """
    repo = ctx.obj["repo"]

    if not use_selenium:
        click.echo("ℹ️  The HTTP download path was removed; using the browser downloader.\n")

    articles = repo.get_articles_with_ssrn_urls(
        limit=limit, downloaded=False, include_unavailable=retry_unavailable
    )
    if articles.empty:
        click.echo("✓ No papers pending PDF download")
        return

    download_list = [
        {"doi": row["doi"], "ssrn_url": row["ssrn_url"]}
        for _, row in articles.iterrows()
        if row.get("ssrn_url")
    ]
    if not download_list:
        click.echo("✗ No valid SSRN URLs found")
        return

    click.echo(f"📄 Downloading {len(download_list)} PDFs from SSRN")
    click.echo(f"Storage: {settings.pdf_storage_dir}")
    click.echo(
        f"Browser: {'Headless (not recommended)' if headless else 'Visible'} | "
        f"base delay: {delay}s\n"
    )

    downloader = SeleniumPDFDownloader(
        storage_dir=settings.pdf_storage_dir, delay=delay, headless=headless
    )

    ssrn_urls = {item["doi"]: item["ssrn_url"] for item in download_list}

    def persist(result):
        """Update the DB after each paper so progress survives interruptions."""
        doi = result["doi"]
        if result["success"]:
            repo.update_pdf_info(
                doi=doi, pdf_url=None, pdf_file_path=result.get("filepath"), downloaded=True
            )
            repo.upsert_pdf_file(
                doi=doi,
                source="ssrn",
                source_url=ssrn_urls.get(doi),
                pdf_url=None,
                pdf_file_path=result.get("filepath"),
            )
            repo.log_processing(doi, "download_pdf", "success")
        elif result["status"] == "unavailable":
            repo.mark_pdf_unavailable(doi)
        else:
            repo.log_processing(doi, "download_pdf", "failed", result.get("error"))

    downloader.download_batch(download_list, on_result=persist)
    click.echo("\n✓ Download process complete")


@main.command("verify-pdfs")
@click.option("--limit", default=None, type=int, help="Limit number of PDFs to verify")
@click.option("--model", default=None, help="Ollama model for gray-zone cases")
@click.option("--no-llm", is_flag=True, help="Deterministic checks only (no LLM calls)")
@click.option("--rerun-uncertain", is_flag=True, help="Also re-check uncertain/unreadable PDFs")
@click.pass_context
def verify_pdfs(ctx, limit, model, no_llm, rerun_uncertain):
    """
    Verify downloaded PDFs against article metadata (title + authors).

    Deterministic fuzzy matching first; only ambiguous cases go to a small
    Ollama Cloud model. Mismatched PDFs are moved to pdfs/quarantine/ and the
    article becomes eligible for re-scraping or fallback resolution.
    """
    import os

    from cite_hustle.verifier import PDFVerifier

    repo = ctx.obj["repo"]

    statuses = ("pending", "uncertain", "unreadable") if rerun_uncertain else ("pending",)
    pending = repo.get_pdfs_pending_verification(limit=limit, statuses=statuses)
    if pending.empty:
        click.echo("✓ No PDFs pending verification")
        return

    use_llm = not no_llm
    if use_llm and not os.environ.get("OLLAMA_API_KEY"):
        click.echo("⚠️  OLLAMA_API_KEY not set; gray-zone PDFs will be left uncertain")

    verifier = PDFVerifier(
        repo=repo,
        quarantine_dir=settings.quarantine_dir,
        model=model or settings.pdf_verifier_model,
        gray_low=settings.verify_gray_zone_low,
        gray_high=settings.verify_gray_zone_high,
        use_llm=use_llm,
    )

    click.echo(f"🔍 Verifying {len(pending)} PDFs against article metadata\n")
    counts = verifier.verify_batch(pending)

    click.echo(
        f"\n✓ Verification complete: {counts['match']} match, "
        f"{counts['mismatch']} mismatch (quarantined), "
        f"{counts['uncertain']} uncertain, {counts['unreadable']} unreadable"
    )


@main.command()
@click.option("--top-journals", default=10, type=int, help="Number of top journals to show")
@click.option("--recent", default=10, type=int, help="Recent processing entries to show")
@click.option("--missing-by-journal", default=0, type=int, help="Show missing-abstracts bar chart")
@click.option("--bar-width", default=30, type=int, help="Width of missing-abstracts bars")
@click.pass_context
def dashboard(ctx, top_journals, recent, missing_by_journal, bar_width):
    """Show a dashboard-style overview of database contents."""
    repo = ctx.obj["repo"]
    stats = repo.get_statistics()
    missing_abstracts = repo.get_missing_abstract_count()
    openalex_enriched = repo.get_openalex_enriched_count()
    top = repo.get_top_journals(limit=top_journals)
    recent_rows = repo.get_recent_processing(limit=recent)
    missing_chart = None
    if missing_by_journal:
        missing_chart = repo.get_missing_abstracts_by_journal(limit=missing_by_journal)

    click.echo("\n" + "=" * 60)
    click.echo("📊 CITE-HUSTLE DASHBOARD")
    click.echo("=" * 60)

    click.echo(f"\n📚 Articles: {stats['total_articles']:,}")
    click.echo(f"🌐 SSRN abstracts: {stats['ssrn_scraped']:,}")
    click.echo(f"🧩 Missing abstracts: {missing_abstracts:,}")
    click.echo(f"🧠 OpenAlex enriched: {openalex_enriched:,}")
    click.echo(f"📄 PDFs downloaded: {stats['pdfs_downloaded']:,}")

    if stats["pending_ssrn_scrapes"] > 0:
        click.echo(f"\n⏳ Pending SSRN scrapes: {stats['pending_ssrn_scrapes']:,}")
    if stats["pending_pdf_downloads"] > 0:
        click.echo(f"⏳ Pending PDF downloads: {stats['pending_pdf_downloads']:,}")

    if top:
        click.echo("\n🏷️  Top journals:")
        for row in top:
            click.echo(f"  • {row['journal_name']}: {row['count']:,}")

    if missing_chart:
        click.echo("\n🧱 Missing abstracts by journal:")
        max_count = max(row["missing_count"] for row in missing_chart) if missing_chart else 0
        for row in missing_chart:
            journal = row["journal_name"]
            missing_count = row["missing_count"]
            total_count = row.get("total_count") or 0
            missing_pct = row.get("missing_pct") or 0.0
            bar_len = int((missing_count / max_count) * bar_width) if max_count else 0
            bar = "█" * bar_len
            click.echo(
                f"  • {journal}: {missing_count:,}/{total_count:,} ({missing_pct:.1%}) {bar}"
            )

    if recent_rows:
        click.echo("\n🕒 Recent processing:")
        for row in recent_rows:
            error = f" | {row['error_message']}" if row["error_message"] else ""
            click.echo(
                f"  • {row['processed_at']} | {row['stage']} | {row['status']} | {row['doi']}{error}"
            )

    click.echo("\n" + "=" * 60 + "\n")


@main.command()
@click.pass_context
def status(ctx):
    """Show database statistics and progress"""
    repo = ctx.obj["repo"]

    stats = repo.get_statistics()

    click.echo("\n" + "=" * 50)
    click.echo("📊 CITE-HUSTLE STATUS")
    click.echo("=" * 50)

    click.echo(f"\n📁 Database: {settings.db_path}")
    db_size = settings.db_path.stat().st_size / 1024 / 1024 if settings.db_path.exists() else 0
    click.echo(f"   Size: {db_size:.2f} MB")

    click.echo(f"\n📚 Articles: {stats['total_articles']:,}")

    if stats["by_year"]:
        click.echo("\n   Recent years:")
        for year_stat in stats["by_year"][:5]:
            click.echo(f"     {year_stat['year']}: {year_stat['count']:,} articles")

    click.echo(f"\n🌐 SSRN pages scraped: {stats['ssrn_scraped']:,}")
    click.echo(f"📄 PDFs downloaded: {stats['pdfs_downloaded']:,}")

    # Pending tasks
    if stats["pending_ssrn_scrapes"] > 0:
        click.echo(f"\n⏳ Pending SSRN scrapes: {stats['pending_ssrn_scrapes']:,}")

    if stats["pending_pdf_downloads"] > 0:
        click.echo(f"⏳ Pending PDF downloads: {stats['pending_pdf_downloads']:,}")

    if stats["pending_ssrn_scrapes"] == 0 and stats["pending_pdf_downloads"] == 0:
        click.echo("\n✓ All tasks complete!")

    click.echo("\n" + "=" * 50 + "\n")


@main.command()
@click.option("--limit", default=10, type=int, help="Number of articles to show")
@click.pass_context
def sample(ctx, limit):
    """Show a sample of articles in the database"""
    repo = ctx.obj["repo"]

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
@click.argument("query")
@click.option("--limit", default=20, type=int, help="Number of results")
@click.option("--author", is_flag=True, help="Search by author instead of title")
@click.pass_context
def search(ctx, query, limit, author):
    """
    Search articles by title or author using full-text search

    Examples:
        cite-hustle search "earnings management"
        cite-hustle search "Smith" --author
        cite-hustle search "accounting" --limit 50
    """
    repo = ctx.obj["repo"]

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
        if "score" in result and result["score"]:
            click.echo(f"   Relevance: {result['score']:.2f}")
        click.echo()


@main.command(name="rebuild-fts")
@click.pass_context
def rebuild_fts(ctx):
    """
    Rebuild full-text search indexes

    Use this command if search is not returning expected results.
    This rebuilds the FTS indexes to include all articles in the database.
    """
    db = ctx.obj["db"]
    repo = ctx.obj["repo"]

    click.echo("\n" + "=" * 60)
    click.echo("🔍 REBUILDING FULL-TEXT SEARCH INDEXES")
    click.echo("=" * 60)

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

    click.echo("\n" + "=" * 60)
    click.echo("✓ REBUILD COMPLETE")
    click.echo("=" * 60)
    click.echo("\nYou can now search with: poetry run cite-hustle search 'your query'\n")


if __name__ == "__main__":
    main(obj={})
