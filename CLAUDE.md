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
    └── ansible_server.py # Ansible playbook execution (parses mcp_meta from playbooks)

playbooks/              # Ansible playbooks with embedded mcp_meta
├── check_security_vulnerabilities.yml  # Security scan (non-destructive)
├── get_host_info.yml                   # System info (non-destructive)
├── patch_vulnerabilities.yml           # Apply patches (destructive)
└── ping_host.yml                       # Connectivity check (non-destructive)
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
Add a `# mcp_meta:` comment block at the top of your playbook - no separate config needed:
```yaml
# mcp_meta:
#   name: my_tool
#   description: What this tool does
#   destructive: false
#   keywords: [keyword1, keyword2]
#   vars:
#     target_hosts: {type: string, required: true, description: "Host or group"}
---
- name: My Playbook
  hosts: "{{ target_hosts }}"
  ...
```

**Fields:**
- `name` (required): Tool name used in MCP and CLI
- `description` (required): What the tool does
- `destructive` (optional, default: false): If true, requires `confirm=true` to execute (otherwise runs in check mode)
- `keywords` (optional): Array of keywords for agent routing
- `vars` (optional): Input variables with type, required flag, and description

The tool will be automatically available in:
- CLI: `sre-copilot ansible list` shows all tools with metadata
- CLI agent: `sre-copilot ask` routes queries based on keywords
- MCP ansible server: Exposes tools with proper input schemas

### Agent Architecture
The agent (`src/agent.py`) orchestrates between tools:
1. Parses `# mcp_meta:` from playbooks to discover tools
2. Routes queries based on keywords or LLM decision
3. Executes matched tool or uses RAG for documentation questions
4. Destructive tools run in check mode first unless explicitly confirmed
