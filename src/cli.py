"""CLI for SRE Copilot."""

import asyncio
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src import __version__
from src.config import get_cleanup_provider, get_embedding_provider, get_llm_provider, get_settings

console = Console()
logger = structlog.get_logger(__name__)


def setup_logging(log_level: str, log_format: str) -> None:
    """Configure structlog."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.dev.ConsoleRenderer()
                if log_format == "console"
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@click.group()
@click.version_option(version=__version__)
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, debug: bool) -> None:
    """SRE Copilot - RAG-based documentation assistant."""
    ctx.ensure_object(dict)
    settings = get_settings()

    log_level = "DEBUG" if debug else settings.log_level
    setup_logging(log_level, settings.log_format.value)

    ctx.obj["settings"] = settings
    ctx.obj["debug"] = debug


@main.command()
@click.option("--docs-path", type=click.Path(exists=True), help="Path to documentation")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
@click.option("--full", is_flag=True, help="Force full re-ingestion")
@click.option("--llm-cleanup", is_flag=True, help="Use LLM to clean badly formatted sections")
@click.pass_context
def ingest(ctx: click.Context, docs_path: str | None, dry_run: bool, full: bool, llm_cleanup: bool) -> None:
    """Ingest documentation into the vector database."""
    settings = ctx.obj["settings"]

    if docs_path:
        settings.docs_path = Path(docs_path)

    console.print(f"[bold]Ingesting documents from:[/bold] {settings.docs_path}")

    if dry_run:
        asyncio.run(_ingest_dry_run(settings))
    else:
        asyncio.run(_ingest(settings, full=full, llm_cleanup=llm_cleanup))


async def _ingest_dry_run(settings) -> None:  # noqa: ANN001
    """Show what would be ingested without actually doing it."""
    from src.ingestion.chunker import Chunker
    from src.ingestion.cleaner import DocumentCleaner
    from src.ingestion.parser import DocumentParser

    parser = DocumentParser(settings.docs_path)
    cleaner = DocumentCleaner()
    chunker = Chunker(
        min_tokens=settings.chunk_min_tokens,
        max_tokens=settings.chunk_max_tokens,
        target_tokens=settings.chunk_target_tokens,
    )

    documents = parser.parse_all()

    if not documents:
        console.print("[yellow]No documents found[/yellow]")
        return

    table = Table(title="Documents to Ingest")
    table.add_column("Path", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Category", style="yellow")
    table.add_column("Chunks", style="magenta", justify="right")
    table.add_column("Tokens", style="blue", justify="right")

    total_chunks = 0
    total_tokens = 0

    for doc in documents:
        cleaned_content, _ = cleaner.clean(doc.content)
        chunks = chunker.chunk_document(
            content=cleaned_content,
            document_path=doc.relative_path,
            content_hash=doc.content_hash,
            category=doc.category,
            tags=doc.tags,
            title=doc.title,
        )

        chunk_count = len(chunks)
        token_count = sum(c.token_count for c in chunks)
        total_chunks += chunk_count
        total_tokens += token_count

        table.add_row(
            str(doc.path),
            doc.title[:40] + "..." if len(doc.title) > 40 else doc.title,
            doc.category,
            str(chunk_count),
            str(token_count),
        )

    console.print(table)
    console.print(f"\n[bold]Total:[/bold] {len(documents)} documents, {total_chunks} chunks, {total_tokens} tokens")


async def _ingest(settings, full: bool = False, llm_cleanup: bool = False) -> None:  # noqa: ANN001
    """Run the full ingestion pipeline."""
    from src.ingestion.chunker import Chunker
    from src.ingestion.cleaner import DocumentCleaner
    from src.ingestion.embedder import DocumentEmbedder
    from src.ingestion.parser import DocumentParser

    embedding_provider = get_embedding_provider(settings)

    # Check provider availability
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking embedding provider...", total=None)
        if not await embedding_provider.is_available():
            console.print("[red]Embedding provider is not available[/red]")
            return

    # Set up cleanup provider if LLM cleanup is enabled
    cleanup_provider = None
    if llm_cleanup:
        cleanup_provider = get_cleanup_provider(settings)
        console.print("[cyan]LLM cleanup enabled[/cyan]")

    parser = DocumentParser(settings.docs_path)
    cleaner = DocumentCleaner(cleanup_provider=cleanup_provider, enable_llm_cleanup=llm_cleanup)
    chunker = Chunker(
        min_tokens=settings.chunk_min_tokens,
        max_tokens=settings.chunk_max_tokens,
        target_tokens=settings.chunk_target_tokens,
    )
    embedder = DocumentEmbedder(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
    )

    # Check for embedding model mismatch
    if embedder.check_embedding_model_mismatch() and not full:
        console.print(
            "[yellow]Warning: Embedding model has changed. "
            "Consider running with --full to re-embed all documents.[/yellow]"
        )

    # Get existing document hashes for incremental ingestion
    existing_hashes = {} if full else embedder.get_existing_hashes()

    documents = parser.parse_all()
    if not documents:
        console.print("[yellow]No documents found[/yellow]")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing documents...", total=len(documents))

        chunks_to_embed = []
        skipped = 0
        updated = 0
        new = 0

        for doc in documents:
            existing = existing_hashes.get(doc.relative_path)

            if existing and existing[0] == doc.content_hash and not full:
                # Document unchanged, skip
                skipped += 1
                progress.advance(task)
                continue

            if existing:
                # Document changed, delete old chunks
                embedder.delete_document_chunks(doc.relative_path)
                updated += 1
            else:
                new += 1

            # Clean and chunk document
            if llm_cleanup:
                cleaned_content, _ = await cleaner.async_clean(doc.content)
            else:
                cleaned_content, _ = cleaner.clean(doc.content)
            chunks = chunker.chunk_document(
                content=cleaned_content,
                document_path=doc.relative_path,
                content_hash=doc.content_hash,
                category=doc.category,
                tags=doc.tags,
                title=doc.title,
            )
            chunks_to_embed.extend(chunks)
            progress.advance(task)

        # Remove orphaned documents
        current_paths = {doc.relative_path for doc in documents}
        orphaned = set(existing_hashes.keys()) - current_paths
        for path in orphaned:
            embedder.delete_document_chunks(path)
            console.print(f"[yellow]Removed orphaned document:[/yellow] {path}")

        # Embed chunks
        if chunks_to_embed:
            progress.add_task("Embedding chunks...", total=None)
            embedded = await embedder.embed_chunks(chunks_to_embed)
            console.print(f"\n[green]Embedded {embedded} chunks[/green]")

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  New documents: {new}")
    console.print(f"  Updated documents: {updated}")
    console.print(f"  Skipped (unchanged): {skipped}")
    console.print(f"  Removed orphans: {len(orphaned)}")
    console.print(f"  Total chunks in database: {embedder.get_document_count()}")


@main.command()
@click.argument("question")
@click.option("--top-k", type=int, default=5, help="Number of chunks to retrieve")
@click.pass_context
def query(ctx: click.Context, question: str, top_k: int) -> None:
    """Query the documentation."""
    settings = ctx.obj["settings"]
    asyncio.run(_query(settings, question, top_k))


async def _query(settings, question: str, top_k: int) -> None:  # noqa: ANN001
    """Run a RAG query."""
    from src.rag.generator import RAGGenerator
    from src.rag.retriever import DocumentRetriever

    embedding_provider = get_embedding_provider(settings)
    llm_provider = get_llm_provider(settings)

    retriever = DocumentRetriever(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
        top_k=top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )

    generator = RAGGenerator(
        llm_provider=llm_provider,
        retriever=retriever,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Searching documentation...", total=None)
        result = await generator.generate_answer(question)

    console.print("\n[bold]Answer:[/bold]")
    console.print(result.answer)

    if result.sources:
        console.print("\n[bold]Sources:[/bold]")
        for source in result.sources:
            console.print(f"  - {source.document_path} ({source.breadcrumb})")


@main.command()
@click.option("--host", default=None, help="Server host")
@click.option("--port", type=int, default=None, help="Server port")
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Start the MCP server."""
    settings = ctx.obj["settings"]

    server_host = host or settings.mcp_server_host
    server_port = port or settings.mcp_server_port

    console.print(f"[bold]Starting MCP server on {server_host}:{server_port}[/bold]")

    from src.mcp_servers.rag_server import run_server

    asyncio.run(run_server(settings))


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show system status."""
    settings = ctx.obj["settings"]
    asyncio.run(_status(settings))


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def clean(ctx: click.Context, yes: bool) -> None:
    """Remove all documents from the vector database."""
    settings = ctx.obj["settings"]

    from src.ingestion.embedder import DocumentEmbedder

    embedding_provider = get_embedding_provider(settings)
    embedder = DocumentEmbedder(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
    )

    info = embedder.get_collection_info()
    chunk_count = info["count"]

    if chunk_count == 0:
        console.print("[yellow]Database is already empty[/yellow]")
        return

    if not yes:
        console.print(f"[bold]This will delete {chunk_count} chunks from the database.[/bold]")
        if not click.confirm("Are you sure?"):
            console.print("[yellow]Aborted[/yellow]")
            return

    # Delete the collection and recreate it
    client = embedder._get_client()
    client.delete_collection("sre_docs")
    console.print(f"[green]Deleted {chunk_count} chunks from database[/green]")


async def _status(settings) -> None:  # noqa: ANN001
    """Show system status."""
    from src.ingestion.embedder import DocumentEmbedder

    console.print("[bold]SRE Copilot Status[/bold]\n")

    # Provider status
    console.print("[bold]Providers:[/bold]")
    console.print(f"  LLM Provider: {settings.llm_provider.value}")

    embedding_provider = get_embedding_provider(settings)
    llm_provider = get_llm_provider(settings)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking providers...", total=None)

        embedding_available = await embedding_provider.is_available()
        llm_available = await llm_provider.is_available()

    status_icon = "[green]\u2713[/green]" if embedding_available else "[red]\u2717[/red]"
    console.print(f"  Embedding ({embedding_provider.model_id}): {status_icon}")

    status_icon = "[green]\u2713[/green]" if llm_available else "[red]\u2717[/red]"
    console.print(f"  LLM ({llm_provider.model_id}): {status_icon}")

    # Database status
    console.print("\n[bold]Database:[/bold]")
    embedder = DocumentEmbedder(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
    )

    info = embedder.get_collection_info()
    console.print(f"  Path: {settings.chromadb_path}")
    console.print(f"  Collection: {info['name']}")
    console.print(f"  Chunks: {info['count']}")
    console.print(f"  Embedding model: {info['embedding_model']}")

    # Paths
    console.print("\n[bold]Paths:[/bold]")
    console.print(f"  Documentation: {settings.docs_path}")
    console.print(f"  ChromaDB: {settings.chromadb_path}")


@main.command()
@click.option("--docs-path", type=click.Path(exists=True), help="Path to documentation")
@click.option("--output-dir", type=click.Path(), help="Output directory for standardized docs")
@click.option("--dry-run", is_flag=True, help="Analyze only, don't write files")
@click.option("--file", type=click.Path(exists=True), help="Standardize a single file")
@click.pass_context
def standardize(
    ctx: click.Context,
    docs_path: str | None,
    output_dir: str | None,
    dry_run: bool,
    file: str | None,
) -> None:
    """Standardize documents using LLM for better RAG retrieval."""
    settings = ctx.obj["settings"]

    if docs_path:
        settings.docs_path = Path(docs_path)

    asyncio.run(_standardize(settings, output_dir, dry_run, file))


async def _standardize(
    settings,  # noqa: ANN001
    output_dir: str | None,
    dry_run: bool,
    single_file: str | None,
) -> None:
    """Run document standardization."""
    from src.ingestion.parser import DocumentParser
    from src.ingestion.standardizer import DocumentStandardizer, get_template_guide

    llm_provider = get_llm_provider(settings)

    # Check provider availability
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking LLM provider...", total=None)
        if not await llm_provider.is_available():
            console.print("[red]LLM provider is not available[/red]")
            return

    standardizer = DocumentStandardizer(llm_provider)

    # Handle single file
    if single_file:
        file_path = Path(single_file)
        content = file_path.read_text()

        console.print(f"[bold]Analyzing:[/bold] {file_path}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Standardizing document...", total=None)
            result = await standardizer.standardize(content, str(file_path))

        # Show analysis
        console.print(f"\n[bold]Title:[/bold] {result.title}")
        console.print(f"[bold]Category:[/bold] {result.category}")
        console.print(f"[bold]Tags:[/bold] {', '.join(result.tags)}")
        console.print(f"[bold]Summary:[/bold] {result.summary}")
        console.print(f"[bold]Modified:[/bold] {result.was_modified}")

        if dry_run:
            if result.was_modified:
                console.print("\n[bold]Standardized content:[/bold]")
                console.print(result.content)
        else:
            # Write output
            if output_dir:
                out_path = Path(output_dir) / file_path.name
                out_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                out_path = file_path

            out_path.write_text(result.content)
            console.print(f"\n[green]Written to:[/green] {out_path}")

        return

    # Process all documents
    parser = DocumentParser(settings.docs_path)
    documents = parser.parse_all()

    if not documents:
        console.print("[yellow]No documents found[/yellow]")
        return

    console.print(f"[bold]Found {len(documents)} documents to analyze[/bold]\n")

    # Set up output directory
    if output_dir:
        out_base = Path(output_dir)
        out_base.mkdir(parents=True, exist_ok=True)
    else:
        out_base = settings.docs_path

    results_table = Table(title="Standardization Results")
    results_table.add_column("Document", style="cyan")
    results_table.add_column("Category", style="yellow")
    results_table.add_column("Modified", style="green")
    results_table.add_column("Issues Fixed", style="magenta")

    modified_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing documents...", total=len(documents))

        for doc in documents:
            try:
                result = await standardizer.standardize(doc.content, doc.relative_path)

                results_table.add_row(
                    str(doc.path),
                    result.category,
                    "Yes" if result.was_modified else "No",
                    str(len(result.tags)) if result.was_modified else "-",
                )

                if result.was_modified:
                    modified_count += 1

                    if not dry_run:
                        out_path = out_base / doc.path
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(result.content)

            except Exception as e:
                logger.error("Failed to standardize", path=str(doc.path), error=str(e))
                results_table.add_row(str(doc.path), "error", "No", str(e)[:30])

            progress.advance(task)

    console.print(results_table)
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Total documents: {len(documents)}")
    console.print(f"  Modified: {modified_count}")

    if dry_run:
        console.print("\n[yellow]Dry run - no files were written[/yellow]")
    elif output_dir:
        console.print(f"\n[green]Standardized documents written to:[/green] {output_dir}")


@main.command("templates")
@click.pass_context
def show_templates(ctx: click.Context) -> None:
    """Show document templates for writing standardized docs."""
    from src.ingestion.standardizer import get_template_guide

    guide = get_template_guide()
    console.print(guide)


if __name__ == "__main__":
    main()
