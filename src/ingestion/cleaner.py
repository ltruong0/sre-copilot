"""Document cleaner for normalizing markdown formatting."""

import re
from dataclasses import dataclass

import structlog

from src.providers.base import LLMProvider

logger = structlog.get_logger(__name__)


@dataclass
class CleaningStats:
    """Statistics from a cleaning operation."""

    html_tags_removed: int = 0
    admonitions_converted: int = 0
    links_converted: int = 0
    headings_normalized: int = 0
    code_blocks_labeled: int = 0
    llm_cleanups: int = 0


class DocumentCleaner:
    """Cleans and normalizes markdown documents."""

    # HTML tag patterns
    HTML_BR_PATTERN = re.compile(r"<br\s*/?>", re.IGNORECASE)
    HTML_DIV_PATTERN = re.compile(
        r'<div[^>]*class=["\']?admonition[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    HTML_LINK_PATTERN = re.compile(r'<a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE)
    HTML_GENERIC_PATTERN = re.compile(r"</?(?:div|span|p|table|tr|td|th|thead|tbody)[^>]*>", re.IGNORECASE)

    # Admonition type extraction
    ADMONITION_TYPE_PATTERN = re.compile(r'class=["\'][^"\']*\b(note|warning|tip|important|caution|danger)\b', re.IGNORECASE)

    # Code block patterns
    CODE_BLOCK_PATTERN = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

    # Heading patterns
    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    # Content patterns for code language inference
    KUBECTL_PATTERNS = ["kubectl", "oc ", "oc\n", "openshift"]
    YAML_PATTERNS = ["apiVersion:", "kind:", "metadata:", "spec:"]
    JSON_PATTERNS = ["{", "["]
    BASH_PATTERNS = ["$", "#!/", "sudo", "apt", "yum", "dnf", "brew"]

    def __init__(self, cleanup_provider: LLMProvider | None = None, enable_llm_cleanup: bool = True):
        """Initialize the document cleaner.

        Args:
            cleanup_provider: Optional LLM provider for cleaning badly formatted sections.
            enable_llm_cleanup: Whether to use LLM for cleanup tasks.
        """
        self._cleanup_provider = cleanup_provider
        self._enable_llm_cleanup = enable_llm_cleanup and cleanup_provider is not None

    def clean(self, content: str) -> tuple[str, CleaningStats]:
        """Clean and normalize a markdown document.

        Args:
            content: Raw markdown content.

        Returns:
            Tuple of (cleaned content, cleaning statistics).
        """
        stats = CleaningStats()

        # Apply cleaning rules in order
        content = self._remove_br_tags(content, stats)
        content = self._convert_admonitions(content, stats)
        content = self._convert_links(content, stats)
        content = self._remove_generic_html(content, stats)
        content = self._normalize_headings(content, stats)
        content = self._label_code_blocks(content, stats)
        content = self._normalize_whitespace(content)

        logger.debug(
            "Cleaned document",
            html_removed=stats.html_tags_removed,
            admonitions=stats.admonitions_converted,
            links=stats.links_converted,
            headings=stats.headings_normalized,
            code_blocks=stats.code_blocks_labeled,
        )

        return content, stats

    async def async_clean(self, content: str) -> tuple[str, CleaningStats]:
        """Clean document with optional LLM cleanup for problematic sections.

        Args:
            content: Raw markdown content.

        Returns:
            Tuple of (cleaned content, cleaning statistics).
        """
        # First apply rule-based cleaning
        content, stats = self.clean(content)

        # If LLM cleanup is enabled, process sections that still look problematic
        if self._enable_llm_cleanup and self._cleanup_provider:
            content = await self._llm_cleanup_pass(content, stats)

        return content, stats

    async def _llm_cleanup_pass(self, content: str, stats: CleaningStats) -> str:
        """Run LLM cleanup on sections that still have issues.

        Args:
            content: Content after rule-based cleaning.
            stats: Stats to update.

        Returns:
            Content with LLM-cleaned sections.
        """
        # Detect problematic sections (still has HTML-like artifacts, broken formatting)
        problem_patterns = [
            r'<[^>]+>',  # Remaining HTML tags
            r'\|[^\n]*\|[^\n]*\n[^\|]',  # Broken tables
        ]

        import re
        needs_cleanup = any(re.search(p, content) for p in problem_patterns)

        if not needs_cleanup:
            return content

        # Clean the entire document with LLM
        cleaned = await self.clean_with_llm(content, content)
        if cleaned != content:
            stats.llm_cleanups += 1
            logger.info("LLM cleaned problematic content")

        return cleaned

    async def clean_with_llm(self, content: str, section: str) -> str:
        """Use LLM to clean a badly formatted section.

        Args:
            content: Full document content for context.
            section: The specific section to clean.

        Returns:
            Cleaned section text.
        """
        if not self._enable_llm_cleanup or not self._cleanup_provider:
            return section

        system_prompt = """You are a documentation formatting assistant.
Your task is to clean up badly formatted markdown sections while preserving all information.
Only output the cleaned markdown, nothing else."""

        prompt = f"""Clean up this markdown section. Fix formatting issues like:
- Broken tables
- Malformed lists
- Inconsistent heading levels
- Garbled text from HTML conversion

Section to clean:
```
{section}
```

Output only the cleaned markdown:"""

        try:
            result = await self._cleanup_provider.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=2000,
                temperature=0.1,
            )
            return result.text.strip()
        except Exception as e:
            logger.warning("LLM cleanup failed", error=str(e))
            return section

    def _remove_br_tags(self, content: str, stats: CleaningStats) -> str:
        """Remove or convert <br> tags."""
        matches = self.HTML_BR_PATTERN.findall(content)
        stats.html_tags_removed += len(matches)
        return self.HTML_BR_PATTERN.sub("\n", content)

    def _convert_admonitions(self, content: str, stats: CleaningStats) -> str:
        """Convert HTML admonitions to markdown blockquotes."""

        def replace_admonition(match: re.Match[str]) -> str:
            full_match = match.group(0)
            inner_content = match.group(1).strip()

            # Try to extract admonition type
            type_match = self.ADMONITION_TYPE_PATTERN.search(full_match)
            adm_type = type_match.group(1).capitalize() if type_match else "Note"

            # Convert to blockquote format
            lines = inner_content.split("\n")
            quoted_lines = [f"> {line}" for line in lines]
            stats.admonitions_converted += 1

            return f"> **{adm_type}**: {quoted_lines[0][2:]}\n" + "\n".join(quoted_lines[1:])

        return self.HTML_DIV_PATTERN.sub(replace_admonition, content)

    def _convert_links(self, content: str, stats: CleaningStats) -> str:
        """Convert HTML links to markdown links."""

        def replace_link(match: re.Match[str]) -> str:
            href = match.group(1)
            text = match.group(2)
            stats.links_converted += 1
            return f"[{text}]({href})"

        return self.HTML_LINK_PATTERN.sub(replace_link, content)

    def _remove_generic_html(self, content: str, stats: CleaningStats) -> str:
        """Remove generic HTML tags."""
        matches = self.HTML_GENERIC_PATTERN.findall(content)
        stats.html_tags_removed += len(matches)
        return self.HTML_GENERIC_PATTERN.sub("", content)

    def _normalize_headings(self, content: str, stats: CleaningStats) -> str:
        """Normalize heading hierarchy (H1 followed by H3 -> H1, H2, H3)."""
        lines = content.split("\n")
        result_lines = []
        last_level = 0

        for line in lines:
            match = self.HEADING_PATTERN.match(line)
            if match:
                hashes = match.group(1)
                text = match.group(2)
                current_level = len(hashes)

                # If we skip a level, normalize
                if current_level > last_level + 1 and last_level > 0:
                    new_level = last_level + 1
                    line = "#" * new_level + " " + text
                    stats.headings_normalized += 1
                    current_level = new_level

                last_level = current_level

            result_lines.append(line)

        return "\n".join(result_lines)

    def _label_code_blocks(self, content: str, stats: CleaningStats) -> str:
        """Add language labels to unlabeled code blocks."""

        def replace_code_block(match: re.Match[str]) -> str:
            lang = match.group(1)
            code = match.group(2)

            if lang:
                return match.group(0)  # Already labeled

            # Infer language from content
            inferred_lang = self._infer_code_language(code)
            if inferred_lang:
                stats.code_blocks_labeled += 1
                return f"```{inferred_lang}\n{code}```"

            return match.group(0)

        return self.CODE_BLOCK_PATTERN.sub(replace_code_block, content)

    def _infer_code_language(self, code: str) -> str | None:
        """Infer programming language from code content."""
        code_lower = code.lower()

        # Check for kubectl/oc commands
        for pattern in self.KUBECTL_PATTERNS:
            if pattern in code_lower:
                return "bash"

        # Check for YAML
        for pattern in self.YAML_PATTERNS:
            if pattern in code:
                return "yaml"

        # Check for JSON (but not if it looks like a shell prompt)
        if code.strip().startswith("{") or code.strip().startswith("["):
            return "json"

        # Check for bash/shell
        for pattern in self.BASH_PATTERNS:
            if pattern in code_lower:
                return "bash"

        return None

    def _normalize_whitespace(self, content: str) -> str:
        """Normalize whitespace in the document."""
        # Remove trailing whitespace from lines
        lines = [line.rstrip() for line in content.split("\n")]

        # Collapse multiple blank lines into two
        result = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= 2:
                    result.append(line)
            else:
                blank_count = 0
                result.append(line)

        return "\n".join(result).strip() + "\n"
