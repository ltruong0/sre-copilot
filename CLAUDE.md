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
├── cli.py              # CLI entry point (query, ask, ansible, serve, status, clean)
├── config.py           # Pydantic settings, provider factories
├── agent.py            # Agentic orchestrator (routes queries to RAG or Ansible)
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
    └── ansible_server.py # Ansible playbook execution (loads from ansible_tools.json)

playbooks/              # Ansible playbooks and tool definitions
├── ansible_tools.json  # Tool registry (name, playbook, description, keywords)
├── check_security_vulnerabilities.yml
└── get_host_info.yml
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
sre-copilot query "question" # Query docs (RAG only)
sre-copilot ask "question"  # Agentic query (can run ansible or RAG)
sre-copilot clean           # Clear database

# Ansible commands
sre-copilot ansible list              # List playbooks
sre-copilot ansible check-security HOST  # Security scan
sre-copilot ansible host-info HOST    # Get system info
sre-copilot ansible run PLAYBOOK -e key=value  # Run any playbook

# MCP servers
sre-copilot serve --server rag      # Start RAG MCP server
sre-copilot serve --server ansible  # Start Ansible MCP server
```

## Configuration

Settings loaded from `.env` file via Pydantic. Key settings:
- `OLLAMA_BASE_URL` - Ollama API endpoint
- `OLLAMA_MODEL` - LLM model for generation
- `OLLAMA_EMBEDDING_MODEL` - Model for embeddings
- `OLLAMA_CA_CERT` - CA certificate for self-signed SSL
- `ANSIBLE_PLAYBOOK_CMD` - Path to ansible-playbook executable
- `ANSIBLE_PLAYBOOKS_DIR` - Directory containing playbooks (default: `./playbooks`)
- `ANSIBLE_INVENTORY` - Path to inventory file
- `ANSIBLE_TIMEOUT` - Playbook execution timeout in seconds

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

### New MCP Tool (RAG Server)
Add to `rag_server.py` in `list_tools()` and `call_tool()` handlers.

### New Ansible Tool
Add to `playbooks/ansible_tools.json` - no Python code changes needed:
```json
{
  "name": "my_tool",
  "playbook": "my_playbook.yml",
  "description": "What this tool does",
  "keywords": ["keyword1", "keyword2"]
}
```
The tool will be automatically available in:
- CLI agent (`sre-copilot ask`)
- MCP ansible server
- Keyword matching uses the `keywords` array to route natural language queries

### Agent Architecture
The agent (`src/agent.py`) orchestrates between tools:
1. Tries keyword matching from `ansible_tools.json` first (fast path)
2. Falls back to LLM decision if no keywords match
3. Executes matched tool or uses RAG for documentation questions
