"""Agentic RAG that can execute tools based on user queries."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from src.config import Settings
from src.mcp_servers.ansible_server import run_playbook
from src.providers.base import LLMProvider
from src.rag.retriever import DocumentRetriever

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
    suggested_tools: list[dict[str, str]] | None = None
    target_host: str | None = None


class SREAgent:
    """Agent that consults documentation and can execute diagnostic tools."""

    SYSTEM_PROMPT = """You are an SRE assistant. Answer the user's question directly and concisely.

CONTEXT (use only if relevant to the question):
{context}

AVAILABLE DIAGNOSTIC TOOLS:
{tools}

USER QUESTION: {question}
TARGET HOST: {target_host}

INSTRUCTIONS:
- Be concise. Answer in 2-4 sentences unless more detail is needed.
- IGNORE context that is not relevant to the user's question.
- For informational questions, just answer - do NOT mention tools.
- ONLY suggest tools when the user has a problem to diagnose AND mentions a host.
- If suggesting tools, add this JSON at the very end:
```json
{{"suggested_tools": [{{"tool": "name", "target": "host", "reason": "why"}}]}}
```"""

    def __init__(
        self,
        llm_provider: LLMProvider,
        retriever: DocumentRetriever,
        settings: Settings,
    ):
        """Initialize the agent."""
        self._llm_provider = llm_provider
        self._retriever = retriever
        self._settings = settings

        # Load tools from JSON config
        tools = load_ansible_tools(settings.ansible_playbooks_dir)
        self._tools = {tool.name: tool for tool in tools}
        self._tools_list = tools

    def _extract_target_host(self, question: str) -> str | None:
        """Extract target host from a question, returns None if not found."""
        # Check for FQDN patterns
        fqdn_match = re.search(r'[\w.-]+\.[\w.-]+\.[\w.-]+', question)
        if fqdn_match:
            return fqdn_match.group(0)

        # Check for "on <host>" or "for <host>" patterns
        match = re.search(r'\b(?:on|for)\s+([\w.-]+)', question)
        if match:
            return match.group(1)

        return None

    def _build_tools_description(self) -> str:
        """Build a description of available tools."""
        lines = []
        for tool in self._tools_list:
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines) if lines else "No diagnostic tools available."

    def _build_context(self, chunks: list) -> str:
        """Build context string from retrieved chunks."""
        if not chunks:
            return "No relevant documentation found."

        context_parts = []
        for chunk in chunks:
            source = f"{chunk.document_path} ({chunk.breadcrumb})" if chunk.breadcrumb else chunk.document_path
            context_parts.append(f"[From {source}]\n{chunk.content}")

        return "\n\n---\n\n".join(context_parts)

    def _parse_response(self, text: str) -> tuple[str, list[dict[str, str]] | None]:
        """Parse LLM response to extract answer and suggested tools."""
        # Try to find JSON block at the end
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)

        if json_match:
            answer = text[:json_match.start()].strip()
            try:
                data = json.loads(json_match.group(1))
                suggested = data.get("suggested_tools", [])
                return answer, suggested if suggested else None
            except json.JSONDecodeError:
                return text, None
        else:
            return text, None

    async def query(
        self,
        question: str,
        auto_execute: bool = False,
        context_host: str | None = None,
    ) -> AgentResult:
        """Process a query by consulting docs and suggesting/executing tools.

        Args:
            question: The user's question or issue description.
            auto_execute: If True, automatically execute suggested tools.
            context_host: Host from previous conversation context (used if no new host mentioned).

        Returns:
            AgentResult with the answer and any tool suggestions/executions.
        """
        # Extract target host if mentioned, otherwise use context
        target_host = self._extract_target_host(question) or context_host
        logger.info("Processing query", target_host=target_host)

        # Retrieve relevant documentation
        chunks = await self._retriever.retrieve(question, top_k=self._settings.rag_top_k)
        context = self._build_context(chunks)

        # Build and send prompt to LLM
        prompt = self.SYSTEM_PROMPT.format(
            context=context,
            tools=self._build_tools_description(),
            question=question,
            target_host=target_host or "not specified",
        )

        result = await self._llm_provider.generate(
            prompt=prompt,
            max_tokens=1500,
            temperature=0.3,
        )

        # Parse response
        answer, suggested_tools = self._parse_response(result.text)

        # If auto_execute and tools were suggested, run them
        if auto_execute and suggested_tools:
            return await self._execute_tools(answer, suggested_tools)

        # Format suggested tools for display and ensure they have the target
        if suggested_tools:
            # Ensure each suggestion has the target host
            for suggestion in suggested_tools:
                if not suggestion.get("target") or suggestion.get("target") == "host":
                    suggestion["target"] = target_host or "all"

            answer += "\n\n**I can run these diagnostics for you:**\n"
            for suggestion in suggested_tools:
                tool_name = suggestion.get("tool", "").replace("_", " ")
                reason = suggestion.get("reason", "")
                answer += f"- {tool_name}: {reason}\n"
            answer += "\nJust say 'yes' to run all, or mention which ones you want (e.g., 'ping it', 'check security')."

        return AgentResult(
            answer=answer,
            tool_used="rag",
            suggested_tools=suggested_tools,
            target_host=target_host,
        )

    PARSE_TOOL_REQUEST_PROMPT = """These tools were suggested to the user:
{suggested_tools}

The user responded: "{request}"

Which tools should be run? Return a JSON array of tool names.
- If user agrees (yes/ok/sure/run them/etc), return ALL suggested tools
- If user mentions specific ones (ping, security, info, etc), return only those
- If unclear, return empty array

JSON array:"""

    SUMMARIZE_PROMPT = """Extract key metrics from this diagnostic output and format as a markdown table.

Tool: {tool_name}
Target: {target_host}
Status: {status}

Raw Output:
{output}

Output technical info in a table or structured format to provide clear and concise info.
Extract relevant metrics based on the tool type:
- For host info: hostname, OS, CPU, memory, disk usage, network IP, uptime, load average
- For ping: reachability status, response time if available
- For security: open ports, failed logins, package updates needed, suspicious processes

Assessment: [one sentence]

Output:"""

    async def parse_tool_request(
        self, request: str, suggested_tools: list[dict[str, str]]
    ) -> list[str]:
        """Parse a user request to identify which tools to run.

        Args:
            request: The user's natural language request.
            suggested_tools: The tools that were suggested to the user.

        Returns:
            List of tool names to run.
        """
        if not suggested_tools:
            return []

        tools_desc = "\n".join(
            f"- {t.get('tool')}: {t.get('reason', '')}" for t in suggested_tools
        )

        prompt = self.PARSE_TOOL_REQUEST_PROMPT.format(
            suggested_tools=tools_desc,
            request=request,
        )

        try:
            result = await self._llm_provider.generate(
                prompt=prompt,
                max_tokens=100,
                temperature=0.1,
            )

            # Parse JSON array from response
            text = result.text.strip()
            # Handle markdown code blocks
            if "```" in text:
                match = re.search(r'\[.*?\]', text, re.DOTALL)
                if match:
                    text = match.group(0)

            tools = json.loads(text)
            # Filter to only valid tools
            valid_tools = [t for t in tools if t in self._tools]
            logger.info("Parsed tool request", request=request, tools=valid_tools)
            return valid_tools

        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse tool request", error=str(e))
            return []

    async def execute_tool(
        self, tool_name: str, target_host: str, summarize: bool = True
    ) -> AgentResult:
        """Execute a specific tool on a target host.

        Args:
            tool_name: Name of the tool to execute.
            target_host: Target host or host group.
            summarize: If True, summarize the output. If False, show raw output.

        Returns:
            AgentResult with the tool output.
        """
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

    async def _execute_tools(
        self, base_answer: str, suggested_tools: list[dict[str, str]]
    ) -> AgentResult:
        """Execute all suggested tools and combine results."""
        results = [base_answer, "\n\n**Diagnostic Results:**\n"]

        for suggestion in suggested_tools:
            tool_name = suggestion.get("tool", "")
            target = suggestion.get("target", "all")

            if tool_name in self._tools:
                result = await self.execute_tool(tool_name, target)
                results.append(f"\n### {tool_name}\n{result.answer}")

        return AgentResult(
            answer="\n".join(results),
            tool_used="multiple",
            tool_output=None,
        )
