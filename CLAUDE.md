# SRE Copilot - Claude Instructions

## Project Overview

SRE Copilot is a RAG-based documentation assistant with MCP servers. It ingests markdown documentation, creates embeddings, and provides question answering for SRE teams.

## Tech Stack

- **Python 3.11+** with async/await
- **Pydantic** for settings and data validation
- **ChromaDB** for vector storage
- **httpx** for async HTTP (Ollama API)
- **Click + Rich** for CLI
- **MCP SDK** for Model Context Protocol servers
- **pytest + pytest-asyncio** for testing

## Project Structure

```
src/
├── cli.py              # CLI entry point (ingest, query, serve, status, clean)
├── config.py           # Pydantic settings, provider factories
├── providers/          # LLM/embedding provider abstractions
│   ├── base.py         # Abstract interfaces
│   ├── ollama_provider.py
│   └── watsonx_provider.py
├── ingestion/          # Document processing pipeline
│   ├── parser.py       # Markdown discovery and parsing
│   ├── cleaner.py      # HTML cleanup, heading normalization
│   ├── chunker.py      # Semantic chunking on H2/H3 boundaries
│   └── embedder.py     # ChromaDB storage
├── rag/                # Retrieval-augmented generation
│   ├── retriever.py    # Similarity search
│   └── generator.py    # Answer generation with sources
└── mcp_servers/        # MCP protocol implementations
    ├── rag_server.py   # Documentation queries
    └── ansible_server.py # Operations (stub)
```

## Key Commands

```bash
# Activate venv
source .venv/bin/activate

# Run tests
pytest tests/ -v

# CLI commands
sre-copilot status          # Check providers and database
sre-copilot ingest --dry-run # Preview ingestion
sre-copilot ingest          # Run ingestion
sre-copilot query "question" # Query docs
sre-copilot clean           # Clear database
```

## Configuration

Settings loaded from `.env` file via Pydantic. Key settings:
- `OLLAMA_BASE_URL` - Ollama API endpoint
- `OLLAMA_MODEL` - LLM model for generation
- `OLLAMA_EMBEDDING_MODEL` - Model for embeddings
- `OLLAMA_CA_CERT` - CA certificate for self-signed SSL

## Development Guidelines

- Use async/await for all I/O operations
- Provider classes must implement abstract base classes in `providers/base.py`
- ChromaDB collection name is `sre_docs` (defined in `embedder.py` and `retriever.py`)
- Chunks preserve heading context (h1, h2, h3) for better retrieval
- Tests use mock providers from `tests/conftest.py`

## Adding New Features

### New CLI Command
Add to `src/cli.py` using Click decorators:
```python
@main.command()
@click.pass_context
def mycommand(ctx: click.Context) -> None:
    settings = ctx.obj["settings"]
    # implementation
```

### New Provider
1. Create class implementing `LLMProvider` or `EmbeddingProvider` from `providers/base.py`
2. Add to factory functions in `config.py`
3. Add settings to `Settings` class

### New MCP Tool
Add to `rag_server.py` in `list_tools()` and `call_tool()` handlers.
