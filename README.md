# SRE Copilot

RAG-based documentation assistant with MCP servers for SRE teams. Ingests internal documentation, creates embeddings, and provides question answering via CLI or MCP protocol.

## Features

- **Document Ingestion**: Parse and chunk markdown documentation with semantic splitting
- **RAG Queries**: Answer questions using retrieved context from your docs
- **Ansible Integration**: Execute playbooks via natural language or CLI commands
- **Agentic CLI**: Intelligent routing between documentation queries and ansible actions
- **MCP Servers**: Integrate with Claude Desktop or other MCP clients (RAG + Ansible)
- **Swappable Providers**: Ollama (local) or IBM watsonx.ai (cloud)

## Installation

```bash
# Clone and enter directory
cd sre-copilot

# Set Python version (requires 3.11+)
pyenv local 3.11.14

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

## Configuration

Copy the example environment file and customize:

```bash
cp .env.example .env
```

Key settings in `.env`:

```bash
# LLM Provider (ollama or watsonx)
LLM_PROVIDER=ollama

# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=granite3.3:8b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_CLEANUP_MODEL=qwen2.5:7b

# CA certificate for self-signed SSL (optional)
OLLAMA_CA_CERT=./rootCA.pem

# Paths
DOCS_PATH=./docs
CHROMADB_PATH=./data/chromadb

# Ansible Configuration
ANSIBLE_PLAYBOOK_CMD=/path/to/venv/bin/ansible-playbook
ANSIBLE_PLAYBOOKS_DIR=./playbooks
ANSIBLE_INVENTORY=./inventory
ANSIBLE_TIMEOUT=300
```

### SSL Certificates (for self-signed certs)

If your Ollama server uses a self-signed certificate (e.g., behind Traefik):

```bash
# Copy your CA certificate to the project
cp /path/to/rootCA.pem ./rootCA.pem

# Set in .env
OLLAMA_CA_CERT=./rootCA.pem
```

### Required Ollama Models

Pull these models on your Ollama server:

```bash
# Embedding model (required for ingestion)
ollama pull nomic-embed-text

# LLM model (for generation)
ollama pull granite3.3:8b

# Cleanup model (optional, for badly formatted docs)
ollama pull qwen2.5:7b
```

## Usage

### Check Status

Verify providers and database connection:

```bash
sre-copilot status
```

### Ingest Documentation

Preview what would be ingested:

```bash
sre-copilot ingest --dry-run
```

Run full ingestion:

```bash
sre-copilot ingest
```

Force re-embedding of all documents:

```bash
sre-copilot ingest --full
```

Ingest from a custom path:

```bash
sre-copilot ingest --docs-path /path/to/docs
```

Use LLM to clean badly formatted sections:

```bash
sre-copilot ingest --llm-cleanup
```

This uses the cleanup model (e.g., `qwen2.5:7b`) to fix:
- Remaining HTML artifacts after rule-based cleaning
- Broken tables
- Malformed lists
- Garbled text from HTML conversion

### Clean Database

Remove all documents from the vector database:

```bash
sre-copilot clean
```

Skip confirmation prompt:

```bash
sre-copilot clean --yes
```

### Standardize Documents

Use LLM to restructure documents into a consistent format for better RAG retrieval:

```bash
# Analyze all docs (dry run)
sre-copilot standardize --dry-run

# Standardize and save to new directory
sre-copilot standardize --output-dir ./docs-standardized

# Standardize a single file
sre-copilot standardize --file docs/runbooks/my-runbook.md --dry-run
```

View standard templates for writing new docs:

```bash
sre-copilot templates
```

The standardizer will:
- Detect document type (runbook, troubleshooting, architecture, onboarding, policy)
- Analyze missing sections and quality issues
- Restructure content into the standard template
- Add consistent frontmatter (title, category, tags, summary)
- Preserve all original content

### Query Documentation

Ask questions about your docs (RAG only):

```bash
sre-copilot query "How do I troubleshoot a pod in CrashLoopBackOff?"
```

Retrieve more context:

```bash
sre-copilot query "What is the incident response process?" --top-k 10
```

### Agentic Queries (Ask)

Use natural language to query docs OR execute ansible playbooks:

```bash
# The agent decides whether to search docs or run ansible
sre-copilot ask "What vulnerabilities are on server1.example.com"
sre-copilot ask "Get system info for all hosts"
sre-copilot ask "How do I troubleshoot CrashLoopBackOff"  # Uses RAG

# Preview what would happen without executing
sre-copilot ask "Check security on prod-servers" --dry-run
```

### Ansible Commands

Direct CLI access to ansible playbooks:

```bash
# List available playbooks
sre-copilot ansible list

# Run security vulnerability check
sre-copilot ansible check-security dev-server.example.com
sre-copilot ansible check-security all --check  # Dry run

# Get host information (OS, CPU, RAM, disk)
sre-copilot ansible host-info dev-server.example.com

# Run any playbook with custom variables
sre-copilot ansible run my_playbook.yml -e target_hosts=webservers -e some_var=value
```

### Adding New Ansible Tools

Tools are defined in `playbooks/ansible_tools.json`:

```json
{
  "tools": [
    {
      "name": "check_security",
      "playbook": "check_security_vulnerabilities.yml",
      "description": "Run security vulnerability scan on hosts",
      "keywords": ["security", "vulnerability", "cve", "scan"]
    },
    {
      "name": "get_host_info",
      "playbook": "get_host_info.yml",
      "description": "Get system info including OS, CPU, RAM, disk",
      "keywords": ["info", "system", "cpu", "ram", "disk", "memory"]
    }
  ]
}
```

To add a new tool:
1. Create the playbook in `playbooks/`
2. Add an entry to `playbooks/ansible_tools.json`
3. The tool is automatically available in CLI and MCP server

### Start MCP Server

Run the MCP server for integration with Claude Desktop:

```bash
# RAG server (documentation queries)
sre-copilot serve --server rag

# Ansible server (playbook execution)
sre-copilot serve --server ansible

# Or run directly
python -m src.mcp_servers.rag_server
python -m src.mcp_servers.ansible_server
```

### Debug Mode

Enable verbose logging:

```bash
sre-copilot --debug status
sre-copilot --debug ingest
```

## MCP Integration

Add to your Claude Desktop config (`~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sre-rag": {
      "command": "/path/to/sre-copilot/.venv/bin/sre-copilot",
      "args": ["serve", "--server", "rag"]
    },
    "sre-ansible": {
      "command": "/path/to/sre-copilot/.venv/bin/sre-copilot",
      "args": ["serve", "--server", "ansible"]
    }
  }
}
```

### Available MCP Tools

**RAG Server:**

| Tool | Description |
|------|-------------|
| `query_docs` | Query documentation with a question |
| `list_sources` | List all available documentation sources |
| `reingest` | Trigger re-ingestion of documentation |

**Ansible Server** (tools loaded from `ansible_tools.json`):

| Tool | Description |
|------|-------------|
| `check_security` | Run security vulnerability scan on hosts |
| `get_host_info` | Get system information (OS, CPU, RAM, disk) |
| `run_playbook` | Run any playbook with custom variables |
| `list_playbooks` | List all available playbooks |

## Development

### Run Tests

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ -v --cov=src --cov-report=term-missing
```

### Project Structure

```
sre-copilot/
├── src/
│   ├── cli.py              # CLI commands (query, ask, ansible, serve, etc.)
│   ├── config.py           # Settings and provider factory
│   ├── agent.py            # Agentic orchestrator (routes to RAG or Ansible)
│   ├── providers/          # LLM/embedding providers
│   │   ├── base.py         # Abstract interfaces
│   │   ├── ollama_provider.py
│   │   └── watsonx_provider.py
│   ├── ingestion/          # Document processing
│   │   ├── parser.py       # Markdown parsing
│   │   ├── cleaner.py      # HTML cleanup
│   │   ├── chunker.py      # Semantic chunking
│   │   └── embedder.py     # ChromaDB storage
│   ├── rag/                # Retrieval-augmented generation
│   │   ├── retriever.py    # Similarity search
│   │   └── generator.py    # Answer generation
│   └── mcp_servers/        # MCP protocol servers
│       ├── rag_server.py   # Documentation queries
│       └── ansible_server.py # Ansible playbook execution
├── playbooks/              # Ansible playbooks
│   ├── ansible_tools.json  # Tool definitions (name, playbook, keywords)
│   ├── check_security_vulnerabilities.yml
│   └── get_host_info.yml
├── inventory               # Ansible inventory file
├── tests/
├── docs/                   # Your documentation
├── data/                   # ChromaDB storage
└── .env                    # Configuration
```

## Troubleshooting

### SSL Certificate Errors

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
```

Set `OLLAMA_CA_CERT` to your CA certificate path in `.env`.

### Embedding Model 404

```
Client error '404 Not Found' for url '.../api/embed'
```

Pull the embedding model on your Ollama server:

```bash
ollama pull nomic-embed-text
```

### Model Not Found

Check available models:

```bash
ollama list
```

Update `OLLAMA_MODEL` in `.env` to match an available model.
