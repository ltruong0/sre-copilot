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
    import logging

    # Set the minimum log level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.filter_by_level,
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
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure root logger level
    logging.basicConfig(format="%(message)s", level=numeric_level)


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

        # Remove orphaned documents only if doing a full re-ingest
        # (prevents accidentally deleting docs when ingesting from a different path)
        orphaned = set()
        if full:
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
@click.option("--top-k", type=int, default=None, help="Number of chunks to retrieve (default: RAG_TOP_K)")
@click.option("--path", type=str, default=None, help="Filter results by document path prefix (e.g., 'sre/netbox-enterprise/')")
@click.pass_context
def query(ctx: click.Context, question: str, top_k: int | None, path: str | None) -> None:
    """Query the documentation (RAG only, no tool execution)."""
    settings = ctx.obj["settings"]
    top_k_val = top_k if top_k is not None else settings.rag_top_k
    asyncio.run(_query(settings, question, top_k_val, path))


@main.command()
@click.argument("question", required=False)
@click.pass_context
def ask(ctx: click.Context, question: str | None) -> None:
    """Ask a question - consults docs and runs diagnostic tools.

    Run without arguments to start an interactive chat session.
    Or provide a question for a single query.

    The LLM decides whether to use documentation or run diagnostic tools.

    Examples:
        sre-copilot ask                                              # Interactive mode
        sre-copilot ask "My app on dev-truong.fringe.ibm.com isn't working"
        sre-copilot ask "Is dev-truong reachable?"
    """
    settings = ctx.obj["settings"]
    if question:
        asyncio.run(_ask_single(settings, question))
    else:
        asyncio.run(_ask_interactive(settings))


async def _ask_single(settings, question: str) -> None:  # noqa: ANN001
    """Run a single agentic query."""
    from src.agent import SREAgent
    from src.rag.retriever import DocumentRetriever

    embedding_provider = get_embedding_provider(settings)
    llm_provider = get_llm_provider(settings)

    retriever = DocumentRetriever(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )

    agent = SREAgent(
        llm_provider=llm_provider,
        retriever=retriever,
        settings=settings,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Processing...", total=None)
        result = await agent.query(question)

    console.print(result.answer)


def _render_user_message(text: str) -> None:
    """Render a user message as a right-aligned panel."""
    from rich.align import Align
    from rich.panel import Panel

    # Calculate width based on text length, with min/max bounds
    width = min(max(len(text) + 6, 30), 70)
    panel = Panel(
        text,
        title="You",
        title_align="right",
        border_style="blue",
        width=width,
    )
    console.print(Align.right(panel))


def _render_agent_message(text: str) -> None:
    """Render an agent message as a left-aligned panel."""
    from rich.markdown import Markdown
    from rich.panel import Panel

    panel = Panel(
        Markdown(text),
        title="Agent",
        title_align="left",
        border_style="green",
        width=min(console.width - 10, 80),
    )
    console.print(panel)


def _render_tool_output(tool_name: str, target: str, text: str) -> None:
    """Render tool output as a styled panel."""
    from rich.markdown import Markdown
    from rich.panel import Panel

    panel = Panel(
        Markdown(text),
        title=f"{tool_name} → {target}",
        title_align="left",
        border_style="cyan",
        width=min(console.width - 10, 80),
    )
    console.print(panel)


async def _load_docs_interactive(settings, embedding_provider, path_str: str) -> None:  # noqa: ANN001
    """Load documents from a path during interactive session."""
    from src.ingestion.chunker import Chunker
    from src.ingestion.cleaner import DocumentCleaner
    from src.ingestion.embedder import DocumentEmbedder
    from src.ingestion.parser import DocumentParser

    doc_path = Path(path_str).expanduser().resolve()

    if not doc_path.exists():
        console.print(f"[red]Path not found: {doc_path}[/red]")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Loading docs from {doc_path.name}...", total=None)

        parser = DocumentParser(doc_path)
        cleaner = DocumentCleaner()
        chunker = Chunker(
            min_tokens=settings.chunk_min_tokens,
            max_tokens=settings.chunk_max_tokens,
            target_tokens=settings.chunk_target_tokens,
        )
        embedder = DocumentEmbedder(
            embedding_provider=embedding_provider,
            chromadb_path=settings.chromadb_path,
        )

        documents = parser.parse_all()
        if not documents:
            console.print(f"[yellow]No documents found in {doc_path}[/yellow]")
            return

        chunks_to_embed = []
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
            chunks_to_embed.extend(chunks)

        if chunks_to_embed:
            embedded = await embedder.embed_chunks(chunks_to_embed)
            console.print(f"[green]Loaded {len(documents)} docs ({embedded} chunks)[/green]")
        else:
            console.print("[yellow]No chunks generated[/yellow]")


async def _ask_interactive(settings) -> None:  # noqa: ANN001
    """Run an interactive chat session with the agent."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from rich.panel import Panel

    from src.agent import SREAgent
    from src.mcp_client import MCPClientManager
    from src.rag.retriever import DocumentRetriever

    embedding_provider = get_embedding_provider(settings)
    llm_provider = get_llm_provider(settings)

    retriever = DocumentRetriever(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )

    # Initialize MCP client for external tools
    mcp_client = None
    if settings.mcp_servers_config.exists():
        mcp_client = MCPClientManager(settings.mcp_servers_config)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Connecting to MCP servers...", total=None)
            try:
                await mcp_client.connect_all()
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to connect to MCP servers: {e}[/yellow]")
                mcp_client = None

    agent = SREAgent(
        llm_provider=llm_provider,
        retriever=retriever,
        settings=settings,
        mcp_client=mcp_client,
    )

    # Build tools summary for welcome banner
    tools_summary = []

    # Internal tools (ansible playbooks)
    if agent._tools_list:
        internal_names = [t.name for t in agent._tools_list]
        tools_summary.append(f"[cyan]Runbooks:[/cyan] {', '.join(internal_names)}")

    # External MCP tools
    if mcp_client:
        for server_name in mcp_client._servers:
            mcp_tools = [t for t in mcp_client.get_all_tools() if t.server_name == server_name]
            if mcp_tools:
                tool_names = [t.name for t in mcp_tools]
                tools_summary.append(f"[magenta]{server_name}:[/magenta] {', '.join(tool_names)}")

    tools_display = "\n".join(tools_summary) if tools_summary else "[dim]No tools loaded[/dim]"

    # Welcome banner
    console.print(Panel(
        "[bold]SRE Copilot[/bold]\n\n"
        "Ask questions about your documentation or diagnose host issues.\n\n"
        f"[bold]Tools:[/bold] {tools_display}\n"
        "[bold]Commands:[/bold] [cyan]/load <path>[/cyan] [cyan]/help[/cyan] [cyan]exit[/cyan]",
        border_style="bright_black",
        width=min(console.width - 10, 70),
    ))
    console.print()

    session: PromptSession = PromptSession(history=InMemoryHistory())
    context_host: str | None = None  # Track target host across conversation

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: session.prompt("› ")
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        # Handle /load command to ingest docs from a path
        if user_input.startswith("/load "):
            path_arg = user_input[6:].strip()
            if path_arg:
                await _load_docs_interactive(settings, embedding_provider, path_arg)
            else:
                console.print("[yellow]Usage: /load <path>[/yellow]")
            continue

        # Handle /help command
        if user_input.lower() in ("/help", "help"):
            console.print("[bold]Commands:[/bold]")
            console.print("  /load <path>  - Ingest documents from a file or directory")
            console.print("  /help         - Show this help")
            console.print("  exit, quit, q - Exit the session")
            console.print("\n[bold]Query Tips:[/bold]")
            console.print("  • Use 'what is', 'explain', 'how to' for documentation lookups")
            console.print("  • Other questions will try to use diagnostic tools")
            continue

        # Show user message
        _render_user_message(user_input)
        console.print()

        # Query the agent - LLM decides whether to use tools or docs
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Thinking...", total=None)
            result = await agent.query(user_input, context_host=context_host)

        # If a tool was used, show as tool output; otherwise show as agent message
        if result.tool_used and result.tool_used != "rag":
            _render_tool_output(result.tool_used, result.target_host or "all", result.answer)
        else:
            _render_agent_message(result.answer)
        console.print()

        # Update context host for future queries
        if result.target_host:
            context_host = result.target_host

    # Cleanup MCP connections
    if mcp_client:
        await mcp_client.disconnect_all()


async def _query(settings, question: str, top_k: int, path_filter: str | None) -> None:  # noqa: ANN001
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
        result = await generator.generate_answer(question, path_filter=path_filter)

    console.print("\n[bold]Answer:[/bold]")
    console.print(result.answer)

    if result.sources:
        console.print("\n[bold]Sources:[/bold]")
        for source in result.sources:
            console.print(f"  - {source.document_path} ({source.breadcrumb})")


@main.command()
@click.option("--host", default=None, help="Server host")
@click.option("--port", type=int, default=None, help="Server port")
@click.option(
    "--server",
    type=click.Choice(["rag", "ansible"]),
    default="rag",
    help="Which MCP server to run (rag or ansible)",
)
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None, server: str) -> None:
    """Start the MCP server."""
    settings = ctx.obj["settings"]

    server_host = host or settings.mcp_server_host
    server_port = port or settings.mcp_server_port

    if server == "ansible":
        console.print("[bold]Starting Ansible MCP server[/bold]")
        from src.mcp_servers.ansible_server import run_server
    else:
        console.print(f"[bold]Starting RAG MCP server on {server_host}:{server_port}[/bold]")
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


# Ansible command group
@main.group()
@click.pass_context
def ansible(ctx: click.Context) -> None:
    """Ansible playbook operations."""
    pass


@ansible.command("list")
@click.pass_context
def ansible_list(ctx: click.Context) -> None:
    """List available Ansible playbooks."""
    settings = ctx.obj["settings"]

    from src.mcp_servers.ansible_server import _get_available_playbooks

    playbooks = _get_available_playbooks(settings)

    if not playbooks:
        console.print(f"[yellow]No playbooks found in {settings.ansible_playbooks_dir}[/yellow]")
        return

    console.print(f"[bold]Available Playbooks[/bold] ({settings.ansible_playbooks_dir})\n")
    for playbook in playbooks:
        console.print(f"  - {playbook}")


@ansible.command("run")
@click.argument("playbook")
@click.option("--extra-vars", "-e", multiple=True, help="Extra variables (key=value)")
@click.option("--check", is_flag=True, help="Run in check mode (dry run)")
@click.pass_context
def ansible_run(ctx: click.Context, playbook: str, extra_vars: tuple, check: bool) -> None:
    """Run an Ansible playbook.

    Example: sre-copilot ansible run check_security_vulnerabilities.yml -e target_hosts=all
    """
    settings = ctx.obj["settings"]

    from src.mcp_servers.ansible_server import _get_available_playbooks, run_playbook

    # Validate playbook exists
    playbook_path = settings.ansible_playbooks_dir / playbook
    if not playbook_path.exists():
        available = _get_available_playbooks(settings)
        console.print(f"[red]Playbook '{playbook}' not found[/red]")
        if available:
            console.print("\n[bold]Available playbooks:[/bold]")
            for p in available:
                console.print(f"  - {p}")
        return

    # Parse extra vars
    parsed_vars = {}
    for var in extra_vars:
        if "=" in var:
            key, value = var.split("=", 1)
            parsed_vars[key] = value
        else:
            console.print(f"[red]Invalid extra var format: {var}. Use key=value[/red]")
            return

    mode_str = "CHECK MODE (dry run)" if check else "EXECUTE MODE"
    console.print(f"[bold]Running playbook:[/bold] {playbook}")
    console.print(f"[bold]Mode:[/bold] {mode_str}")
    if parsed_vars:
        console.print(f"[bold]Extra vars:[/bold] {parsed_vars}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Running playbook...", total=None)
        success, stdout, stderr = run_playbook(playbook_path, parsed_vars, settings, check)

    if success:
        console.print("[green]✓ Playbook completed successfully[/green]\n")
        if stdout:
            console.print("[bold]Output:[/bold]")
            console.print(stdout)
    else:
        console.print("[red]✗ Playbook failed[/red]\n")
        if stderr:
            console.print("[bold]Error:[/bold]")
            console.print(stderr)
        if stdout:
            console.print("\n[bold]Output:[/bold]")
            console.print(stdout)


@ansible.command("check-security")
@click.argument("target_hosts")
@click.option("--check", is_flag=True, help="Run in check mode (dry run)")
@click.pass_context
def ansible_check_security(ctx: click.Context, target_hosts: str, check: bool) -> None:
    """Run security vulnerability check on target hosts.

    Example: sre-copilot ansible check-security all
    Example: sre-copilot ansible check-security webservers --check
    """
    settings = ctx.obj["settings"]

    from src.mcp_servers.ansible_server import run_playbook

    playbook_path = settings.ansible_playbooks_dir / "check_security_vulnerabilities.yml"

    if not playbook_path.exists():
        console.print(f"[red]Security check playbook not found at {playbook_path}[/red]")
        return

    extra_vars = {"target_hosts": target_hosts}
    mode_str = "CHECK MODE (dry run)" if check else "EXECUTE MODE"

    console.print("[bold]Security Vulnerability Check[/bold]")
    console.print(f"[bold]Target hosts:[/bold] {target_hosts}")
    console.print(f"[bold]Mode:[/bold] {mode_str}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Running security check...", total=None)
        success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, check)

    if success:
        console.print("[green]✓ Security check completed successfully[/green]\n")
        if stdout:
            console.print("[bold]Output:[/bold]")
            console.print(stdout)
    else:
        console.print("[red]✗ Security check failed[/red]\n")
        if stderr:
            console.print("[bold]Error:[/bold]")
            console.print(stderr)
        if stdout:
            console.print("\n[bold]Output:[/bold]")
            console.print(stdout)


@ansible.command("host-info")
@click.argument("target_hosts")
@click.pass_context
def ansible_host_info(ctx: click.Context, target_hosts: str) -> None:
    """Get system information from target hosts (OS, CPU, RAM, disk).

    Example: sre-copilot ansible host-info dev-truong.fringe.ibm.com
    Example: sre-copilot ansible host-info all
    """
    settings = ctx.obj["settings"]

    from src.mcp_servers.ansible_server import run_playbook

    playbook_path = settings.ansible_playbooks_dir / "get_host_info.yml"

    if not playbook_path.exists():
        console.print(f"[red]Host info playbook not found at {playbook_path}[/red]")
        return

    extra_vars = {"target_hosts": target_hosts}

    console.print("[bold]Get Host Information[/bold]")
    console.print(f"[bold]Target hosts:[/bold] {target_hosts}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Gathering host information...", total=None)
        success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, False)

    if success:
        console.print("[green]✓ Host information gathered successfully[/green]\n")
        if stdout:
            console.print("[bold]Output:[/bold]")
            console.print(stdout)
    else:
        console.print("[red]✗ Failed to get host information[/red]\n")
        if stderr:
            console.print("[bold]Error:[/bold]")
            console.print(stderr)
        if stdout:
            console.print("\n[bold]Output:[/bold]")
            console.print(stdout)


@ansible.command("ping")
@click.argument("target_hosts")
@click.pass_context
def ansible_ping(ctx: click.Context, target_hosts: str) -> None:
    """Check if target hosts are reachable.

    Example: sre-copilot ansible ping dev-truong.fringe.ibm.com
    Example: sre-copilot ansible ping all
    """
    settings = ctx.obj["settings"]

    from src.mcp_servers.ansible_server import run_playbook

    playbook_path = settings.ansible_playbooks_dir / "ping_host.yml"

    if not playbook_path.exists():
        console.print(f"[red]Ping playbook not found at {playbook_path}[/red]")
        return

    extra_vars = {"target_hosts": target_hosts}

    console.print("[bold]Ping Host[/bold]")
    console.print(f"[bold]Target hosts:[/bold] {target_hosts}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking connectivity...", total=None)
        success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, False)

    if success:
        console.print("[green]✓ Host is reachable[/green]\n")
        if stdout:
            console.print("[bold]Output:[/bold]")
            console.print(stdout)
    else:
        console.print("[red]✗ Host unreachable or check failed[/red]\n")
        if stderr:
            console.print("[bold]Error:[/bold]")
            console.print(stderr)
        if stdout:
            console.print("\n[bold]Output:[/bold]")
            console.print(stdout)


if __name__ == "__main__":
    main()
