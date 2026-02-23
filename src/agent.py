"""Agentic RAG that can execute tools based on user queries."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.config import Settings
from src.mcp_servers.ansible_server import run_playbook
from src.providers.base import LLMProvider
from src.rag.retriever import DocumentRetriever

if TYPE_CHECKING:
    from src.mcp_client import MCPClientManager

logger = structlog.get_logger(__name__)


@dataclass
class AnsibleTool:
    """Definition of an ansible-based tool."""

    name: str
    playbook: str
    description: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnsibleTool":
        """Create an AnsibleTool from a dictionary."""
        return cls(
            name=data["name"],
            playbook=data["playbook"],
            description=data["description"],
        )


def load_ansible_tools(playbooks_dir: Path) -> list[AnsibleTool]:
    """Load ansible tools from the JSON config file."""
    config_path = playbooks_dir / "ansible_tools.json"

    if not config_path.exists():
        logger.warning("ansible_tools.json not found", path=str(config_path))
        return []

    try:
        with open(config_path) as f:
            data = json.load(f)

        tools = [AnsibleTool.from_dict(t) for t in data.get("tools", [])]
        logger.info("Loaded ansible tools", count=len(tools))
        return tools

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to load ansible_tools.json", error=str(e))
        return []


@dataclass
class AgentResult:
    """Result from an agentic query."""

    answer: str
    tool_used: str | None = None
    tool_output: str | None = None
    target_host: str | None = None


class SREAgent:
    """Agent that consults documentation and can execute diagnostic tools."""

    # Prompt for tool-based queries (no documentation noise)
    TOOL_PROMPT = """You are an SRE assistant. Use the appropriate tool to answer the question.

TOOLS:
{tools}

QUESTION: {question}
CONTEXT HOST: {target_host}

Pick the appropriate tool and return:
```json
{{"tool": "tool_name", "target": "hostname_from_question"}}
```"""

    # Prompt for documentation-based queries
    DOC_PROMPT = """You are an SRE assistant. Answer the question using the documentation below.

QUESTION: {question}

DOCUMENTATION:
{context}

Provide a helpful answer based on the documentation."""

    # Keywords that trigger documentation lookup instead of tools
    DOC_KEYWORDS = ["what is", "how to", "explain", "docs", "documentation", "tell me about", "describe", "overview"]

    def __init__(
        self,
        llm_provider: LLMProvider,
        retriever: DocumentRetriever,
        settings: Settings,
        mcp_client: "MCPClientManager | None" = None,
    ):
        """Initialize the agent."""
        self._llm_provider = llm_provider
        self._retriever = retriever
        self._settings = settings
        self._mcp_client = mcp_client

        # Load tools from JSON config
        tools = load_ansible_tools(settings.ansible_playbooks_dir)
        self._tools = {tool.name: tool for tool in tools}
        self._tools_list = tools

    def _extract_target_host(self, question: str) -> str | None:
        """Extract target host from a question, returns None if not found."""
        # Common words that aren't hostnames
        ignore_words = {"that", "this", "it", "the", "host", "server", "machine", "system", "all"}

        # Check for FQDN patterns (must have at least 2 dots)
        fqdn_match = re.search(r'[\w-]+\.[\w-]+\.[\w.-]+', question)
        if fqdn_match:
            return fqdn_match.group(0)

        # Check for "on <host>" or "for <host>" patterns
        match = re.search(r'\b(?:on|for)\s+([\w.-]+)', question)
        if match:
            candidate = match.group(1).lower()
            if candidate not in ignore_words:
                return match.group(1)

        return None

    def _build_tools_description(self) -> str:
        """Build a description of available tools."""
        lines = []

        # Ansible/runbook tools
        if self._tools_list:
            lines.append("Runbook Tools (for host diagnostics):")
            for tool in self._tools_list:
                lines.append(f"  - {tool.name}: {tool.description}")

        # External MCP tools
        if self._mcp_client:
            mcp_tools = self._mcp_client.get_all_tools()
            if mcp_tools:
                lines.append("\nExternal Tools (MCP):")
                for tool in mcp_tools:
                    lines.append(f"  - {tool.server_name}.{tool.name}: {tool.description}")

        return "\n".join(lines) if lines else "No tools available."

    def _build_context(self, chunks: list) -> str:
        """Build context string from retrieved chunks."""
        if not chunks:
            return "No relevant documentation found."

        context_parts = []
        for chunk in chunks:
            source = f"{chunk.document_path} ({chunk.breadcrumb})" if chunk.breadcrumb else chunk.document_path
            context_parts.append(f"[From {source}]\n{chunk.content}")

        return "\n\n---\n\n".join(context_parts)

    def _parse_response(self, text: str) -> tuple[str, dict[str, str] | None]:
        """Parse LLM response to extract answer and tool request.

        Returns:
            Tuple of (answer_text, tool_request) where tool_request is
            {"tool": "name", "target": "host"} or None.
        """
        # Try to find JSON block
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)

        if json_match:
            answer = text[:json_match.start()].strip()
            try:
                data = json.loads(json_match.group(1))
                # New format: {"tool": "name", "target": "host"}
                if "tool" in data and "target" in data:
                    return answer, {"tool": data["tool"], "target": data["target"]}
                return answer, None
            except json.JSONDecodeError:
                return text, None
        else:
            return text, None

    def _is_doc_query(self, question: str) -> bool:
        """Check if question should use documentation instead of tools."""
        question_lower = question.lower()
        return any(kw in question_lower for kw in self.DOC_KEYWORDS)

    async def query(
        self,
        question: str,
        context_host: str | None = None,
    ) -> AgentResult:
        """Process a query - routes to tools or documentation based on keywords.

        Args:
            question: The user's question or issue description.
            context_host: Host from previous conversation context (used if no new host mentioned).

        Returns:
            AgentResult with the answer and any tool output.
        """
        target_host = context_host

        # Check if this is a documentation query
        if self._is_doc_query(question):
            logger.info("Documentation query detected", question=question)
            chunks = await self._retriever.retrieve(question, top_k=self._settings.rag_top_k)
            context = self._build_context(chunks)

            prompt = self.DOC_PROMPT.format(
                question=question,
                context=context,
            )

            result = await self._llm_provider.generate(
                prompt=prompt,
                max_tokens=1500,
                temperature=0.3,
            )

            return AgentResult(
                answer=result.text,
                tool_used="rag",
                target_host=target_host,
            )

        # Tool query - no documentation noise
        logger.info("Tool query detected", question=question, context_host=target_host)
        prompt = self.TOOL_PROMPT.format(
            question=question,
            target_host=target_host or "none",
            tools=self._build_tools_description(),
        )

        result = await self._llm_provider.generate(
            prompt=prompt,
            max_tokens=500,
            temperature=0.1,
        )

        # Parse response for tool request
        answer, tool_request = self._parse_response(result.text)

        if tool_request:
            tool_name = tool_request["tool"]
            tool_target = tool_request["target"]

            # Use context host if target is generic
            if tool_target in ("host", "none", "", "hostname_from_question") and target_host:
                tool_target = target_host

            logger.info("Executing tool", tool=tool_name, target=tool_target)
            tool_result = await self.execute_tool(tool_name, tool_target)

            combined = f"{answer}\n\n{tool_result.answer}" if answer else tool_result.answer
            return AgentResult(
                answer=combined,
                tool_used=tool_name,
                tool_output=tool_result.tool_output,
                target_host=tool_target,
            )

        # Fallback if no tool was parsed
        return AgentResult(
            answer=answer or result.text,
            tool_used=None,
            target_host=target_host,
        )

    SUMMARIZE_PROMPT = """Summarize this tool output concisely.

Tool: {tool_name}
Target: {target_host}
Status: {status}

Raw Output:
{output}

Format the output based on the tool type:
- For diagnostic tools (ping, security, host_info): extract key metrics as a markdown table
- For inventory/data tools (netbox, search): list the found items with key attributes
- For knowledge-based tools (docs search): summarize the key points in bullet form and provide sources
- For other tools: present the key information clearly

Keep it concise. End with a short 2-3 sentence assessment.

Output:"""

    async def execute_tool(
        self, tool_name: str, target_host: str, summarize: bool = True, **kwargs: Any
    ) -> AgentResult:
        """Execute a specific tool on a target host.

        Args:
            tool_name: Name of the tool to execute.
            target_host: Target host or host group.
            summarize: If True, summarize the output. If False, show raw output.
            **kwargs: Additional arguments for MCP tools.

        Returns:
            AgentResult with the tool output.
        """
        # Check if it's an MCP tool (format: server.tool_name)
        if "." in tool_name and self._mcp_client:
            return await self._execute_mcp_tool(tool_name, target_host, summarize, **kwargs)

        # Try to find MCP tool by name (handles various naming formats)
        if self._mcp_client:
            for full_name, tool in self._mcp_client._tools.items():
                # Direct match on tool name (e.g., "netbox_search_objects")
                if tool_name == tool.name:
                    return await self._execute_mcp_tool(full_name, target_host, summarize, **kwargs)
                # Match "server_toolname" pattern (e.g., "netbox_search" -> "netbox.netbox_search")
                if "_" in tool_name:
                    server_name = full_name.split(".", 1)[0]
                    if tool_name == f"{server_name}_{tool.name}":
                        return await self._execute_mcp_tool(full_name, target_host, summarize, **kwargs)

        if tool_name not in self._tools:
            return AgentResult(
                answer=f"Unknown tool: {tool_name}. Available tools: {', '.join(self._tools.keys())}",
                tool_used=None,
            )

        tool = self._tools[tool_name]
        playbook_path = self._settings.ansible_playbooks_dir / tool.playbook

        if not playbook_path.exists():
            return AgentResult(
                answer=f"Playbook not found: {playbook_path}",
                tool_used=tool_name,
            )

        logger.info("Executing tool", tool=tool_name, target=target_host)

        extra_vars = {"target_hosts": target_host}
        success, stdout, stderr = run_playbook(
            playbook_path, extra_vars, self._settings, check_mode=False
        )

        status = "Success" if success else "Failed"
        output = stdout if success else f"{stderr}\n{stdout}"

        if summarize:
            # Use LLM to summarize the output as a table
            summary_prompt = self.SUMMARIZE_PROMPT.format(
                tool_name=tool_name,
                target_host=target_host,
                status=status,
                output=output[:3000],  # Limit output size for LLM
            )
            try:
                result = await self._llm_provider.generate(
                    prompt=summary_prompt,
                    max_tokens=500,
                    temperature=0.3,
                )
                answer = result.text.strip()
            except Exception as e:
                logger.warning("Failed to summarize, showing raw output", error=str(e))
                answer = f"{status}\n\n```\n{output}\n```"
        else:
            # Show raw output (debug mode)
            if success:
                answer = f"Status: ✓ Success\n\n**Output:**\n```\n{stdout}\n```"
            else:
                answer = f"Status: ✗ Failed\n\n**Error:**\n```\n{stderr}\n```\n\n**Output:**\n```\n{stdout}\n```"

        return AgentResult(
            answer=answer,
            tool_used=tool_name,
            tool_output=stdout or stderr,
        )

    async def _execute_mcp_tool(
        self, tool_name: str, target_host: str, summarize: bool = True, **kwargs: Any
    ) -> AgentResult:
        """Execute an external MCP tool.

        Args:
            tool_name: Full tool name (server.tool_name).
            target_host: Target host (may be used as argument).
            summarize: If True, summarize the output.
            **kwargs: Additional arguments for the tool.

        Returns:
            AgentResult with the tool output.
        """
        if not self._mcp_client:
            return AgentResult(
                answer="MCP client not configured",
                tool_used=None,
            )

        logger.info("Executing MCP tool", tool=tool_name, target=target_host)

        # Build arguments - include target_host using appropriate parameter name
        arguments = dict(kwargs)
        if target_host and target_host != "all":
            # Check tool's input schema to find the right parameter name
            tool_info = self._mcp_client._tools.get(tool_name)
            if tool_info and tool_info.input_schema:
                props = tool_info.input_schema.get("properties", {})
                # Use first matching parameter type
                if "query" in props:
                    arguments["query"] = target_host
                elif "search" in props:
                    arguments["search"] = target_host
                elif "name" in props:
                    arguments["name"] = target_host
                elif "host" in props:
                    arguments["host"] = target_host
                elif props:
                    # Use the first required parameter or first property
                    required = tool_info.input_schema.get("required", [])
                    param = required[0] if required else list(props.keys())[0]
                    arguments[param] = target_host
            else:
                # Fallback: use 'query' for search tools, 'host' otherwise
                if "search" in tool_name.lower():
                    arguments["query"] = target_host
                else:
                    arguments["host"] = target_host

        try:
            output = await self._mcp_client.call_tool(tool_name, arguments)

            if summarize:
                # Use LLM to summarize the output
                summary_prompt = self.SUMMARIZE_PROMPT.format(
                    tool_name=tool_name,
                    target_host=target_host,
                    status="Success",
                    output=output[:3000],
                )
                try:
                    result = await self._llm_provider.generate(
                        prompt=summary_prompt,
                        max_tokens=500,
                        temperature=0.3,
                    )
                    answer = result.text.strip()
                except Exception as e:
                    logger.warning("Failed to summarize MCP output", error=str(e))
                    answer = f"```\n{output}\n```"
            else:
                answer = f"```\n{output}\n```"

            return AgentResult(
                answer=answer,
                tool_used=tool_name,
                tool_output=output,
            )

        except Exception as e:
            logger.error("MCP tool execution failed", tool=tool_name, error=str(e))
            return AgentResult(
                answer=f"Error executing {tool_name}: {str(e)}",
                tool_used=tool_name,
            )
