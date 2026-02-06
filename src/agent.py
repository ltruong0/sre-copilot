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
from src.rag.generator import RAGGenerator, RAGResult
from src.rag.retriever import DocumentRetriever

logger = structlog.get_logger(__name__)


@dataclass
class AnsibleTool:
    """Definition of an ansible-based tool."""

    name: str
    playbook: str
    description: str
    keywords: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnsibleTool":
        """Create an AnsibleTool from a dictionary."""
        return cls(
            name=data["name"],
            playbook=data["playbook"],
            description=data["description"],
            keywords=data.get("keywords", []),
        )


def load_ansible_tools(playbooks_dir: Path) -> list[AnsibleTool]:
    """Load ansible tools from the JSON config file.

    Args:
        playbooks_dir: Path to the playbooks directory.

    Returns:
        List of AnsibleTool definitions.
    """
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
    rag_result: RAGResult | None = None


class SREAgent:
    """Agent that can answer questions and execute ansible playbooks."""

    TOOL_DECISION_PROMPT = """You are an SRE assistant. Decide which tool to use based on the user's request.

AVAILABLE TOOLS:
{tools}

AVAILABLE HOSTS:
- dev-truong.fringe.ibm.com
- all (all hosts)
- local (localhost)

RULES:
- If the user wants to PERFORM AN ACTION on a host (check, scan, get info, run), pick the appropriate tool
- If the user asks a QUESTION about documentation or procedures (how to, what is, explain), use "rag"
- Extract the target host from the request, default to "all" if not specified

USER REQUEST: {question}

Respond with ONLY a JSON object:
{{
    "tool": "<tool_name>",
    "reason": "brief explanation",
    "params": {{"target_hosts": "<hostname_or_group>"}}
}}

For "rag" tool, params should be {{}}."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        retriever: DocumentRetriever,
        settings: Settings,
    ):
        """Initialize the agent.

        Args:
            llm_provider: Provider for LLM calls.
            retriever: Document retriever for RAG.
            settings: Application settings.
        """
        self._llm_provider = llm_provider
        self._retriever = retriever
        self._settings = settings
        self._rag_generator = RAGGenerator(llm_provider, retriever)

        # Load tools from JSON config
        tools = load_ansible_tools(settings.ansible_playbooks_dir)
        self._tools = {tool.name: tool for tool in tools}
        self._tools_list = tools

    def _extract_target_host(self, question: str) -> str:
        """Extract target host from a question."""
        # Check for known hosts
        known_hosts = ["dev-truong.fringe.ibm.com", "localhost", "all"]
        for host in known_hosts:
            if host in question:
                return host

        # Check for "on <host>" or "for <host>" patterns
        match = re.search(r'\b(?:on|for)\s+(\S+)', question)
        if match:
            return match.group(1)

        return "all"

    def _match_tool_by_keywords(self, question: str) -> AnsibleTool | None:
        """Match a tool based on keywords in the question."""
        q_lower = question.lower()

        for tool in self._tools_list:
            if any(kw in q_lower for kw in tool.keywords):
                return tool

        return None

    def _build_tools_description(self) -> str:
        """Build a description of available tools for the LLM prompt."""
        lines = []
        for tool in self._tools_list:
            lines.append(f'- "{tool.name}": {tool.description}')
        lines.append('- "rag": Search documentation to answer questions about procedures, troubleshooting, and how-to guides.')
        return "\n".join(lines)

    async def query(
        self,
        question: str,
        auto_execute: bool = True,
    ) -> AgentResult:
        """Process a query, deciding whether to use RAG or execute a tool.

        Args:
            question: The user's question or command.
            auto_execute: If True, automatically execute tools. If False, just return the plan.

        Returns:
            AgentResult with the answer and any tool execution details.
        """
        # Try keyword matching first (fast path)
        matched_tool = self._match_tool_by_keywords(question)

        if matched_tool:
            target_host = self._extract_target_host(question)
            logger.info("Matched tool by keywords", tool=matched_tool.name, host=target_host)
            decision = {
                "tool": matched_tool.name,
                "reason": f"Matched keywords for: {matched_tool.description}",
                "params": {"target_hosts": target_host}
            }
        else:
            # Fall back to LLM decision
            tools_desc = self._build_tools_description()

            decision_prompt = self.TOOL_DECISION_PROMPT.format(
                tools=tools_desc,
                question=question,
            )

            result = await self._llm_provider.generate(
                prompt=decision_prompt,
                max_tokens=500,
                temperature=0.1,
            )

            try:
                decision = self._parse_json_response(result.text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse tool decision, falling back to RAG", error=str(e))
                decision = {"tool": "rag", "reason": "Parse error, defaulting to RAG", "params": {}}

        tool_name = decision.get("tool", "rag")
        params = decision.get("params", {})
        reason = decision.get("reason", "")

        logger.info("Agent decision", tool=tool_name, reason=reason, params=params)

        # Handle RAG tool
        if tool_name == "rag":
            rag_result = await self._rag_generator.generate_answer(question)
            return AgentResult(
                answer=rag_result.answer,
                tool_used="rag",
                rag_result=rag_result,
            )

        # Handle registered ansible tools
        if tool_name in self._tools:
            tool = self._tools[tool_name]
            target_hosts = params.get("target_hosts", "all")

            if not auto_execute:
                return AgentResult(
                    answer=f"I would run: {tool.description}\nTarget: {target_hosts}\n\nReason: {reason}",
                    tool_used=f"{tool_name} (not executed)",
                )

            playbook_path = self._settings.ansible_playbooks_dir / tool.playbook
            if not playbook_path.exists():
                return AgentResult(
                    answer=f"Playbook not found at {playbook_path}",
                    tool_used=tool_name,
                )

            extra_vars = {"target_hosts": target_hosts}
            success, stdout, stderr = run_playbook(
                playbook_path, extra_vars, self._settings, check_mode=False
            )

            if success:
                answer = f"{tool.description}\n\nTarget: {target_hosts}\nStatus: Success\n\n**Output:**\n```\n{stdout}\n```"
            else:
                answer = f"{tool.description}\n\nTarget: {target_hosts}\nStatus: Failed\n\n**Error:**\n```\n{stderr}\n```\n\n**Output:**\n```\n{stdout}\n```"

            return AgentResult(
                answer=answer,
                tool_used=tool_name,
                tool_output=stdout or stderr,
            )

        # Unknown tool, fall back to RAG
        rag_result = await self._rag_generator.generate_answer(question)
        return AgentResult(
            answer=rag_result.answer,
            tool_used="rag",
            rag_result=rag_result,
        )

    def _parse_json_response(self, text: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        # Try to extract JSON from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

        # Try to find raw JSON
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))

        # Try parsing the whole thing
        return json.loads(text.strip())
