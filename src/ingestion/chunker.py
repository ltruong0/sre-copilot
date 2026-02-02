"""Semantic chunker for markdown documents."""

import re
from dataclasses import dataclass, field

import structlog
import tiktoken

logger = structlog.get_logger(__name__)


@dataclass
class HeadingContext:
    """Tracks the heading hierarchy for a chunk."""

    h1: str = ""
    h2: str = ""
    h3: str = ""

    @property
    def breadcrumb(self) -> str:
        """Generate a breadcrumb string from headings."""
        parts = [h for h in [self.h1, self.h2, self.h3] if h]
        return " > ".join(parts)

    def copy(self) -> "HeadingContext":
        """Create a copy of this context."""
        return HeadingContext(h1=self.h1, h2=self.h2, h3=self.h3)


@dataclass
class ChunkMetadata:
    """Metadata for a document chunk."""

    document_path: str
    category: str
    h1: str
    h2: str
    h3: str
    breadcrumb: str
    tags: str  # Comma-separated
    content_hash: str
    embedding_model: str = ""
    ingested_at: str = ""


@dataclass
class Chunk:
    """A chunk of document content with metadata."""

    chunk_id: str
    content: str
    metadata: ChunkMetadata
    token_count: int = 0

    def to_chroma_metadata(self) -> dict[str, str]:
        """Convert to ChromaDB metadata format."""
        return {
            "chunk_id": self.chunk_id,
            "document_path": self.metadata.document_path,
            "category": self.metadata.category,
            "h1": self.metadata.h1,
            "h2": self.metadata.h2,
            "h3": self.metadata.h3,
            "breadcrumb": self.metadata.breadcrumb,
            "tags": self.metadata.tags,
            "content_hash": self.metadata.content_hash,
            "embedding_model": self.metadata.embedding_model,
            "ingested_at": self.metadata.ingested_at,
        }


@dataclass
class Section:
    """A section of a document with heading context."""

    content: str
    context: HeadingContext
    level: int  # Heading level (1, 2, 3, etc.)


class Chunker:
    """Semantic chunker that splits documents on heading boundaries."""

    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```", re.MULTILINE)
    TABLE_PATTERN = re.compile(r"^\|.*\|$(\n\|.*\|$)+", re.MULTILINE)

    def __init__(
        self,
        min_tokens: int = 100,
        max_tokens: int = 1000,
        target_tokens: int = 500,
        tokenizer_model: str = "cl100k_base",
    ):
        """Initialize the chunker.

        Args:
            min_tokens: Minimum tokens per chunk.
            max_tokens: Maximum tokens per chunk.
            target_tokens: Target tokens per chunk.
            tokenizer_model: Tiktoken model for counting tokens.
        """
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.target_tokens = target_tokens
        self._tokenizer = tiktoken.get_encoding(tokenizer_model)

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._tokenizer.encode(text))

    def chunk_document(
        self,
        content: str,
        document_path: str,
        content_hash: str,
        category: str = "general",
        tags: list[str] | None = None,
        title: str = "",
    ) -> list[Chunk]:
        """Chunk a document into semantic sections.

        Args:
            content: Document content.
            document_path: Path to the document.
            content_hash: Hash of the document content.
            category: Document category.
            tags: Document tags.
            title: Document title (used as H1 if no H1 found).

        Returns:
            List of Chunk objects.
        """
        tags = tags or []
        tags_str = ",".join(tags)

        # Split into sections by headings
        sections = self._split_into_sections(content, title)

        # Process sections into chunks
        chunks = []
        chunk_index = 0

        for section in sections:
            section_chunks = self._process_section(
                section=section,
                document_path=document_path,
                content_hash=content_hash,
                category=category,
                tags_str=tags_str,
                start_index=chunk_index,
            )
            chunks.extend(section_chunks)
            chunk_index += len(section_chunks)

        # Merge small chunks
        chunks = self._merge_small_chunks(chunks, document_path, content_hash)

        logger.debug(
            "Chunked document",
            path=document_path,
            sections=len(sections),
            chunks=len(chunks),
        )

        return chunks

    def _split_into_sections(self, content: str, title: str) -> list[Section]:
        """Split content into sections by headings."""
        sections: list[Section] = []
        context = HeadingContext(h1=title)

        # Find all heading positions
        heading_matches = list(self.HEADING_PATTERN.finditer(content))

        if not heading_matches:
            # No headings, treat entire content as one section
            return [Section(content=content, context=context.copy(), level=1)]

        # Process content before first heading
        first_heading_pos = heading_matches[0].start()
        if first_heading_pos > 0:
            intro = content[:first_heading_pos].strip()
            if intro:
                sections.append(Section(content=intro, context=context.copy(), level=0))

        # Process each heading and its content
        for i, match in enumerate(heading_matches):
            level = len(match.group(1))
            heading_text = match.group(2).strip()

            # Update context based on heading level
            if level == 1:
                context.h1 = heading_text
                context.h2 = ""
                context.h3 = ""
            elif level == 2:
                context.h2 = heading_text
                context.h3 = ""
            elif level == 3:
                context.h3 = heading_text

            # Get content until next heading
            start = match.end()
            if i + 1 < len(heading_matches):
                end = heading_matches[i + 1].start()
            else:
                end = len(content)

            section_content = content[start:end].strip()

            # Include heading in content for context
            full_content = f"{match.group(0)}\n\n{section_content}".strip()

            if full_content:
                sections.append(
                    Section(content=full_content, context=context.copy(), level=level)
                )

        return sections

    def _process_section(
        self,
        section: Section,
        document_path: str,
        content_hash: str,
        category: str,
        tags_str: str,
        start_index: int,
    ) -> list[Chunk]:
        """Process a section into one or more chunks."""
        token_count = self.count_tokens(section.content)

        if token_count <= self.max_tokens:
            # Section fits in one chunk
            chunk_id = f"{content_hash[:12]}_{start_index}"
            return [
                Chunk(
                    chunk_id=chunk_id,
                    content=section.content,
                    metadata=ChunkMetadata(
                        document_path=document_path,
                        category=category,
                        h1=section.context.h1,
                        h2=section.context.h2,
                        h3=section.context.h3,
                        breadcrumb=section.context.breadcrumb,
                        tags=tags_str,
                        content_hash=content_hash,
                    ),
                    token_count=token_count,
                )
            ]

        # Section is too large, need to split
        return self._split_large_section(
            section=section,
            document_path=document_path,
            content_hash=content_hash,
            category=category,
            tags_str=tags_str,
            start_index=start_index,
        )

    def _split_large_section(
        self,
        section: Section,
        document_path: str,
        content_hash: str,
        category: str,
        tags_str: str,
        start_index: int,
    ) -> list[Chunk]:
        """Split a large section into multiple chunks, preserving atomic units."""
        chunks: list[Chunk] = []
        content = section.content

        # Extract atomic units (code blocks, tables) that shouldn't be split
        atomic_units = self._extract_atomic_units(content)

        # Replace atomic units with placeholders
        placeholder_map: dict[str, str] = {}
        for i, unit in enumerate(atomic_units):
            placeholder = f"__ATOMIC_{i}__"
            placeholder_map[placeholder] = unit
            content = content.replace(unit, placeholder, 1)

        # Split remaining content by paragraphs
        paragraphs = self._split_by_paragraphs(content)

        # Reassemble into chunks
        current_chunk_parts: list[str] = []
        current_tokens = 0

        def flush_chunk() -> None:
            nonlocal current_chunk_parts, current_tokens
            if current_chunk_parts:
                chunk_content = "\n\n".join(current_chunk_parts)
                # Restore atomic units
                for placeholder, unit in placeholder_map.items():
                    chunk_content = chunk_content.replace(placeholder, unit)

                chunk_id = f"{content_hash[:12]}_{start_index + len(chunks)}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        content=chunk_content,
                        metadata=ChunkMetadata(
                            document_path=document_path,
                            category=category,
                            h1=section.context.h1,
                            h2=section.context.h2,
                            h3=section.context.h3,
                            breadcrumb=section.context.breadcrumb,
                            tags=tags_str,
                            content_hash=content_hash,
                        ),
                        token_count=self.count_tokens(chunk_content),
                    )
                )
                current_chunk_parts = []
                current_tokens = 0

        for para in paragraphs:
            # Restore any placeholders for token counting
            para_for_counting = para
            for placeholder, unit in placeholder_map.items():
                para_for_counting = para_for_counting.replace(placeholder, unit)
            para_tokens = self.count_tokens(para_for_counting)

            # If paragraph alone exceeds max, it's an atomic unit - keep it together
            if para_tokens > self.max_tokens:
                flush_chunk()
                current_chunk_parts = [para]
                current_tokens = para_tokens
                flush_chunk()
                continue

            # Check if adding this paragraph exceeds max
            if current_tokens + para_tokens > self.max_tokens:
                flush_chunk()

            current_chunk_parts.append(para)
            current_tokens += para_tokens

        flush_chunk()
        return chunks

    def _extract_atomic_units(self, content: str) -> list[str]:
        """Extract code blocks and tables that shouldn't be split."""
        units = []

        # Extract code blocks
        for match in self.CODE_BLOCK_PATTERN.finditer(content):
            units.append(match.group(0))

        # Extract tables
        for match in self.TABLE_PATTERN.finditer(content):
            units.append(match.group(0))

        return units

    def _split_by_paragraphs(self, content: str) -> list[str]:
        """Split content into paragraphs."""
        # Split on double newlines, preserving structure
        parts = re.split(r"\n\n+", content)
        return [p.strip() for p in parts if p.strip()]

    def _merge_small_chunks(
        self,
        chunks: list[Chunk],
        document_path: str,
        content_hash: str,
    ) -> list[Chunk]:
        """Merge chunks that are too small."""
        if len(chunks) <= 1:
            return chunks

        merged: list[Chunk] = []
        current: Chunk | None = None

        for chunk in chunks:
            if current is None:
                current = chunk
                continue

            # Check if we should merge
            combined_tokens = current.token_count + chunk.token_count

            # Only merge if both are small and combined is reasonable
            if (
                current.token_count < self.min_tokens
                and combined_tokens <= self.target_tokens
                and current.metadata.h2 == chunk.metadata.h2  # Same section
            ):
                # Merge chunks
                combined_content = f"{current.content}\n\n{chunk.content}"
                chunk_id = f"{content_hash[:12]}_{len(merged)}"
                current = Chunk(
                    chunk_id=chunk_id,
                    content=combined_content,
                    metadata=current.metadata,  # Keep first chunk's metadata
                    token_count=combined_tokens,
                )
            else:
                merged.append(current)
                current = chunk

        if current:
            merged.append(current)

        # Renumber chunk IDs
        for i, chunk in enumerate(merged):
            chunk.chunk_id = f"{content_hash[:12]}_{i}"

        return merged
