"""Document parser for discovering and reading markdown files."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Document:
    """Represents a parsed markdown document."""

    path: Path
    content: str
    title: str
    content_hash: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    frontmatter: dict[str, str] = field(default_factory=dict)

    @property
    def relative_path(self) -> str:
        """Return the path as a string for storage."""
        return str(self.path)


class DocumentParser:
    """Discovers and parses markdown documents from a directory."""

    SUPPORTED_EXTENSIONS = {".md", ".markdown"}

    # Category inference from path components
    CATEGORY_PATTERNS = {
        "runbook": ["runbook", "runbooks", "playbook", "playbooks"],
        "architecture": ["architecture", "design", "arch"],
        "troubleshooting": ["troubleshoot", "troubleshooting", "debug"],
        "howto": ["howto", "how-to", "guide", "guides", "tutorial"],
        "reference": ["reference", "ref", "api"],
        "onboarding": ["onboarding", "onboard", "getting-started"],
    }

    def __init__(self, base_path: Path):
        """Initialize the document parser.

        Args:
            base_path: Base directory containing documentation.
        """
        self.base_path = base_path

    def discover(self) -> list[Path]:
        """Discover all markdown files in the base path.

        Returns:
            List of paths to markdown files.
        """
        if not self.base_path.exists():
            logger.warning("Documentation path does not exist", path=str(self.base_path))
            return []

        files = []
        for ext in self.SUPPORTED_EXTENSIONS:
            files.extend(self.base_path.rglob(f"*{ext}"))

        # Sort for consistent ordering
        files.sort()

        logger.info(
            "Discovered documents",
            count=len(files),
            base_path=str(self.base_path),
        )

        return files

    def parse(self, file_path: Path) -> Document:
        """Parse a single markdown file.

        Args:
            file_path: Path to the markdown file.

        Returns:
            Parsed Document object.
        """
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # Parse frontmatter if present
        post = frontmatter.loads(content)

        # Extract metadata from frontmatter
        fm_dict = dict(post.metadata) if post.metadata else {}
        title = fm_dict.get("title", self._extract_title(post.content, file_path))
        tags = fm_dict.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        category = fm_dict.get("category", self._infer_category(file_path))

        # Compute content hash for change detection
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Make path relative to base_path
        try:
            relative_path = file_path.relative_to(self.base_path)
        except ValueError:
            relative_path = file_path

        return Document(
            path=relative_path,
            content=post.content,  # Content without frontmatter
            title=title,
            content_hash=content_hash,
            category=category,
            tags=tags,
            frontmatter=fm_dict,
        )

    def parse_all(self) -> list[Document]:
        """Discover and parse all documents.

        Returns:
            List of parsed Document objects.
        """
        files = self.discover()
        documents = []

        for file_path in files:
            try:
                doc = self.parse(file_path)
                documents.append(doc)
                logger.debug(
                    "Parsed document",
                    path=str(doc.path),
                    title=doc.title,
                    category=doc.category,
                )
            except Exception as e:
                logger.error(
                    "Failed to parse document",
                    path=str(file_path),
                    error=str(e),
                )

        logger.info("Parsed all documents", count=len(documents))
        return documents

    def _extract_title(self, content: str, file_path: Path) -> str:
        """Extract title from content or filename.

        Args:
            content: Document content.
            file_path: Path to the file.

        Returns:
            Extracted or derived title.
        """
        # Look for first H1 heading
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()

        # Fall back to filename
        return file_path.stem.replace("-", " ").replace("_", " ").title()

    def _infer_category(self, file_path: Path) -> str:
        """Infer document category from file path.

        Args:
            file_path: Path to the file.

        Returns:
            Inferred category name.
        """
        path_lower = str(file_path).lower()

        for category, patterns in self.CATEGORY_PATTERNS.items():
            for pattern in patterns:
                if pattern in path_lower:
                    return category

        return "general"
