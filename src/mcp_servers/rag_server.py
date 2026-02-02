"""MCP server for RAG-based documentation queries."""

import asyncio
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import Settings, get_embedding_provider, get_llm_provider
from src.ingestion.chunker import Chunker
from src.ingestion.cleaner import DocumentCleaner
from src.ingestion.embedder import DocumentEmbedder
from src.ingestion.parser import DocumentParser
from src.rag.generator import RAGGenerator
from src.rag.retriever import DocumentRetriever

logger = structlog.get_logger(__name__)


def create_server(settings: Settings) -> Server:
    """Create and configure the MCP server.

    Args:
        settings: Application settings.

    Returns:
        Configured MCP server.
    """
    server = Server("sre-copilot-rag")

    # Initialize providers
    embedding_provider = get_embedding_provider(settings)
    llm_provider = get_llm_provider(settings)

    # Initialize RAG components
    retriever = DocumentRetriever(
        embedding_provider=embedding_provider,
        chromadb_path=settings.chromadb_path,
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )
    generator = RAGGenerator(
        llm_provider=llm_provider,
        retriever=retriever,
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="query_docs",
                description="Query the SRE documentation to answer questions about OpenShift, Kubernetes, runbooks, and infrastructure operations.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to answer based on the documentation",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of document chunks to retrieve (default: 5)",
                            "default": 5,
                        },
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g., 'runbook', 'architecture')",
                        },
                    },
                    "required": ["question"],
                },
            ),
            Tool(
                name="list_sources",
                description="List all available documentation sources and categories.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="reingest",
                description="Trigger re-ingestion of documentation. Use 'full' mode to re-embed all documents.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "full": {
                            "type": "boolean",
                            "description": "If true, re-embed all documents regardless of changes",
                            "default": False,
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        logger.info("Tool called", tool=name, arguments=arguments)

        if name == "query_docs":
            return await _handle_query_docs(generator, arguments)
        elif name == "list_sources":
            return await _handle_list_sources(retriever)
        elif name == "reingest":
            return await _handle_reingest(settings, arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _handle_query_docs(
    generator: RAGGenerator, arguments: dict[str, Any]
) -> list[TextContent]:
    """Handle query_docs tool call."""
    question = arguments.get("question", "")
    top_k = arguments.get("top_k", 5)
    category = arguments.get("category")

    if not question:
        return [TextContent(type="text", text="Error: question is required")]

    try:
        result = await generator.generate_answer(
            question=question,
            top_k=top_k,
            category_filter=category,
        )

        # Format response with sources
        response_parts = [result.answer]

        if result.sources:
            response_parts.append("\n\n**Sources:**")
            seen_docs = set()
            for source in result.sources:
                doc_key = f"{source.document_path}:{source.breadcrumb}"
                if doc_key not in seen_docs:
                    seen_docs.add(doc_key)
                    response_parts.append(f"- {source.breadcrumb} ({source.document_path})")

        return [TextContent(type="text", text="\n".join(response_parts))]

    except Exception as e:
        logger.error("Query failed", error=str(e))
        return [TextContent(type="text", text=f"Error querying documentation: {e}")]


async def _handle_list_sources(retriever: DocumentRetriever) -> list[TextContent]:
    """Handle list_sources tool call."""
    try:
        categories = retriever.get_categories()
        documents = retriever.get_documents()

        response_parts = ["**Available Documentation Sources**\n"]

        if categories:
            response_parts.append("**Categories:**")
            for cat in categories:
                count = sum(1 for d in documents if d["category"] == cat)
                response_parts.append(f"- {cat}: {count} documents")

        response_parts.append(f"\n**Total Documents:** {len(documents)}")

        if documents:
            response_parts.append("\n**Documents:**")
            # Group by category
            by_category: dict[str, list[str]] = {}
            for doc in documents:
                cat = doc["category"]
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(doc["path"])

            for cat, paths in sorted(by_category.items()):
                response_parts.append(f"\n*{cat}:*")
                for path in paths[:10]:  # Limit to 10 per category
                    response_parts.append(f"  - {path}")
                if len(paths) > 10:
                    response_parts.append(f"  - ... and {len(paths) - 10} more")

        return [TextContent(type="text", text="\n".join(response_parts))]

    except Exception as e:
        logger.error("List sources failed", error=str(e))
        return [TextContent(type="text", text=f"Error listing sources: {e}")]


async def _handle_reingest(
    settings: Settings, arguments: dict[str, Any]
) -> list[TextContent]:
    """Handle reingest tool call."""
    full = arguments.get("full", False)

    try:
        embedding_provider = get_embedding_provider(settings)

        parser = DocumentParser(settings.docs_path)
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

        existing_hashes = {} if full else embedder.get_existing_hashes()
        documents = parser.parse_all()

        if not documents:
            return [TextContent(type="text", text="No documents found to ingest.")]

        chunks_to_embed = []
        stats = {"new": 0, "updated": 0, "skipped": 0, "orphaned": 0}

        for doc in documents:
            existing = existing_hashes.get(doc.relative_path)

            if existing and existing[0] == doc.content_hash and not full:
                stats["skipped"] += 1
                continue

            if existing:
                embedder.delete_document_chunks(doc.relative_path)
                stats["updated"] += 1
            else:
                stats["new"] += 1

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

        # Remove orphaned documents
        current_paths = {doc.relative_path for doc in documents}
        orphaned = set(existing_hashes.keys()) - current_paths
        for path in orphaned:
            embedder.delete_document_chunks(path)
            stats["orphaned"] += 1

        # Embed new/updated chunks
        if chunks_to_embed:
            await embedder.embed_chunks(chunks_to_embed)

        response = f"""**Ingestion Complete**

- New documents: {stats['new']}
- Updated documents: {stats['updated']}
- Skipped (unchanged): {stats['skipped']}
- Removed orphans: {stats['orphaned']}
- Total chunks embedded: {len(chunks_to_embed)}
- Total chunks in database: {embedder.get_document_count()}"""

        return [TextContent(type="text", text=response)]

    except Exception as e:
        logger.error("Reingest failed", error=str(e))
        return [TextContent(type="text", text=f"Error during ingestion: {e}")]


async def run_server(settings: Settings) -> None:
    """Run the MCP server.

    Args:
        settings: Application settings.
    """
    server = create_server(settings)

    async with stdio_server() as (read_stream, write_stream):
        logger.info("Starting MCP RAG server")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    from src.config import get_settings

    settings = get_settings()
    asyncio.run(run_server(settings))
