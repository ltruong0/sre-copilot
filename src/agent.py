"""Agentic RAG that can execute tools based on user queries."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.config import Settings
from src.mcp_servers.ansible_server import run_playbook, _get_available_playbooks
from src.providers.base import LLMProvider
from src.rag.generator import RAGGenerator, RAGResult
from src.rag.retriever import DocumentRetriever

logger = structlog.get_logger(__name__)


@dataclass
class AgentResult:
    """Result from an agentic query."""

    answer: str
    tool_used: str | None = None
    tool_output: str | None = None
    rag_result: RAGResult | None = None


class SREAgent:
    """Agent that can answer questions and execute ansible playbooks."""

    TOOL_DECISION_PROMPT = """You are an SRE assistant that decides which tool to use.

TOOLS AVAILABLE:
1. "check_security" - Run security/vulnerability scan on hosts. USE THIS when user asks to:
   - Check vulnerabilities
   - Run security check/scan
   - Find security issues
   - Scan for CVEs
   - Any security-related ACTION on a host

2. "run_playbook" - Run a specific ansible playbook. USE THIS when user explicitly mentions running a playbook.

3. "rag" - Search documentation for answers. USE THIS ONLY for questions about HOW to do something, not for ACTIONS.

AVAILABLE HOSTS:
- dev-truong.fringe.ibm.com
- all (all hosts)
- local (localhost)

AVAILABLE PLAYBOOKS:
{playbooks}

IMPORTANT RULES:
- If the user mentions a HOST and wants to CHECK/SCAN/RUN something, use "check_security" or "run_playbook"
- If the user asks a QUESTION about documentation/procedures, use "rag"
- When in doubt about security checks, use "check_security"

USER REQUEST: {question}

Respond with ONLY a JSON object:
{{
    "tool": "check_security" | "run_playbook" | "rag",
    "reason": "brief explanation",
    "params": {{"target_hosts": "hostname_or_group"}} or {{"playbook": "name.yml", "extra_vars": {{}}}} or {{}}
}}"""

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

    def _detect_security_intent(self, question: str) -> tuple[bool, str | None]:
        """Detect if the question is asking for a security check action.

        Returns:
            Tuple of (is_security_action, target_host_if_found)
        """
        q_lower = question.lower()

        # Keywords that indicate a security action
        action_keywords = [
            "check security", "security check", "vulnerability", "vulnerabilities",
            "scan", "cve", "run security", "check for security", "security scan",
        ]

        is_action = any(kw in q_lower for kw in action_keywords)

        if not is_action:
            return False, None

        # Try to extract host
        # Check for known hosts
        known_hosts = ["dev-truong.fringe.ibm.com", "localhost", "all"]
        for host in known_hosts:
            if host in question:
                return True, host

        # Check for "on <host>" pattern
        on_match = re.search(r'\bon\s+(\S+)', question)
        if on_match:
            return True, on_match.group(1)

        return True, "all"

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
        # Quick pattern check for obvious security actions
        is_security_action, detected_host = self._detect_security_intent(question)
        if is_security_action and detected_host:
            logger.info("Detected security action via pattern matching", host=detected_host)
            decision = {
                "tool": "check_security",
                "reason": "User requested security/vulnerability check",
                "params": {"target_hosts": detected_host}
            }
        else:
            # Get available playbooks for context
            playbooks = _get_available_playbooks(self._settings)
            playbook_list = "\n".join(f"- {p}" for p in playbooks) if playbooks else "- No playbooks available"

            # Ask LLM to decide on tool usage
            decision_prompt = self.TOOL_DECISION_PROMPT.format(
                playbooks=playbook_list,
                question=question,
            )

            result = await self._llm_provider.generate(
                prompt=decision_prompt,
                max_tokens=500,
                temperature=0.1,
            )

            # Parse the decision
            try:
                decision = self._parse_json_response(result.text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse tool decision, falling back to RAG", error=str(e))
                decision = {"tool": "rag", "reason": "Parse error, defaulting to RAG", "params": {}}

        tool = decision.get("tool", "rag")
        params = decision.get("params", {})
        reason = decision.get("reason", "")

        logger.info("Agent decision", tool=tool, reason=reason, params=params)

        if tool == "rag":
            # Use RAG to answer
            rag_result = await self._rag_generator.generate_answer(question)
            return AgentResult(
                answer=rag_result.answer,
                tool_used="rag",
                rag_result=rag_result,
            )

        elif tool == "check_security":
            target_hosts = params.get("target_hosts", "all")

            if not auto_execute:
                return AgentResult(
                    answer=f"I would run a security vulnerability check on: {target_hosts}\n\nReason: {reason}",
                    tool_used="check_security (not executed)",
                )

            # Execute security check
            playbook_path = self._settings.ansible_playbooks_dir / "check_security_vulnerabilities.yml"
            if not playbook_path.exists():
                return AgentResult(
                    answer=f"Security check playbook not found at {playbook_path}",
                    tool_used="check_security",
                )

            extra_vars = {"target_hosts": target_hosts}
            success, stdout, stderr = run_playbook(
                playbook_path, extra_vars, self._settings, check_mode=False
            )

            if success:
                answer = f"Security vulnerability check completed on {target_hosts}.\n\n**Results:**\n```\n{stdout}\n```"
            else:
                answer = f"Security check failed on {target_hosts}.\n\n**Error:**\n```\n{stderr}\n```\n\n**Output:**\n```\n{stdout}\n```"

            return AgentResult(
                answer=answer,
                tool_used="check_security",
                tool_output=stdout or stderr,
            )

        elif tool == "run_playbook":
            playbook = params.get("playbook", "")
            extra_vars = params.get("extra_vars", {})

            if not playbook:
                return AgentResult(
                    answer="No playbook specified in the request.",
                    tool_used="run_playbook",
                )

            playbook_path = self._settings.ansible_playbooks_dir / playbook
            if not playbook_path.exists():
                available = _get_available_playbooks(self._settings)
                return AgentResult(
                    answer=f"Playbook '{playbook}' not found.\n\nAvailable playbooks:\n"
                    + "\n".join(f"- {p}" for p in available),
                    tool_used="run_playbook",
                )

            if not auto_execute:
                return AgentResult(
                    answer=f"I would run playbook: {playbook}\nWith variables: {extra_vars}\n\nReason: {reason}",
                    tool_used="run_playbook (not executed)",
                )

            success, stdout, stderr = run_playbook(
                playbook_path, extra_vars, self._settings, check_mode=False
            )

            if success:
                answer = f"Playbook '{playbook}' completed successfully.\n\n**Output:**\n```\n{stdout}\n```"
            else:
                answer = f"Playbook '{playbook}' failed.\n\n**Error:**\n```\n{stderr}\n```\n\n**Output:**\n```\n{stdout}\n```"

            return AgentResult(
                answer=answer,
                tool_used="run_playbook",
                tool_output=stdout or stderr,
            )

        else:
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
