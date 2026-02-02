"""Tests for the document chunker."""

import pytest

from src.ingestion.chunker import Chunk, Chunker, HeadingContext


class TestHeadingContext:
    """Test cases for HeadingContext."""

    def test_breadcrumb_full(self) -> None:
        """Test breadcrumb with all headings."""
        ctx = HeadingContext(h1="Title", h2="Section", h3="Subsection")
        assert ctx.breadcrumb == "Title > Section > Subsection"

    def test_breadcrumb_partial(self) -> None:
        """Test breadcrumb with partial headings."""
        ctx = HeadingContext(h1="Title", h2="Section")
        assert ctx.breadcrumb == "Title > Section"

    def test_breadcrumb_empty(self) -> None:
        """Test breadcrumb with no headings."""
        ctx = HeadingContext()
        assert ctx.breadcrumb == ""

    def test_copy(self) -> None:
        """Test copying context."""
        ctx1 = HeadingContext(h1="Title", h2="Section")
        ctx2 = ctx1.copy()
        ctx2.h2 = "Different"

        assert ctx1.h2 == "Section"
        assert ctx2.h2 == "Different"


class TestChunker:
    """Test cases for Chunker."""

    def test_token_counting(self) -> None:
        """Test token counting."""
        chunker = Chunker()
        text = "This is a test sentence."
        count = chunker.count_tokens(text)
        assert count > 0
        assert count < 20

    def test_chunk_simple_document(self) -> None:
        """Test chunking a simple document."""
        chunker = Chunker(max_tokens=500)
        content = """# Title

This is the introduction.

## Section 1

Content for section 1.

## Section 2

Content for section 2.
"""
        chunks = chunker.chunk_document(
            content=content,
            document_path="test.md",
            content_hash="abc123",
            title="Title",
        )

        assert len(chunks) >= 1
        assert all(isinstance(c, Chunk) for c in chunks)
        assert all(c.metadata.document_path == "test.md" for c in chunks)

    def test_heading_context_tracking(self) -> None:
        """Test that heading context is properly tracked."""
        chunker = Chunker(max_tokens=100)  # Small enough to create separate chunks
        content = """# Main Title

## Section A

Content A with some more text to ensure we have enough tokens.

### Subsection A1

Content A1 with additional text to make this section substantial enough.

## Section B

Content B with more text here as well.
"""
        chunks = chunker.chunk_document(
            content=content,
            document_path="test.md",
            content_hash="abc123",
            title="Main Title",
        )

        # Find chunk with h3 heading
        h3_chunks = [c for c in chunks if c.metadata.h3]
        if h3_chunks:
            chunk = h3_chunks[0]
            assert chunk.metadata.h1 == "Main Title"
            assert chunk.metadata.h2 == "Section A"
            assert chunk.metadata.h3 == "Subsection A1"
        else:
            # If sections were merged, verify h2 is tracked
            a_chunks = [c for c in chunks if "Section A" in c.content]
            assert len(a_chunks) > 0
            assert a_chunks[0].metadata.h2 == "Section A"

    def test_preserve_code_blocks(self) -> None:
        """Test that code blocks are not split."""
        chunker = Chunker(max_tokens=50)  # Very small to force splitting
        content = """# Test

```python
def very_long_function():
    # This is a long function
    # with many lines
    # that should not be split
    pass
```

Some text after.
"""
        chunks = chunker.chunk_document(
            content=content,
            document_path="test.md",
            content_hash="abc123",
        )

        # Find the chunk with the code block
        code_chunks = [c for c in chunks if "```python" in c.content]
        assert len(code_chunks) >= 1

        # Code block should be complete
        for chunk in code_chunks:
            if "```python" in chunk.content:
                assert "```" in chunk.content[chunk.content.find("```python") + 10 :]

    def test_chunk_metadata(self) -> None:
        """Test that chunk metadata is correct."""
        chunker = Chunker()
        chunks = chunker.chunk_document(
            content="# Test\n\nContent here.",
            document_path="docs/runbook.md",
            content_hash="abc123def456",
            category="runbook",
            tags=["k8s", "troubleshooting"],
            title="Test Title",
        )

        assert len(chunks) >= 1
        chunk = chunks[0]

        assert chunk.metadata.document_path == "docs/runbook.md"
        assert chunk.metadata.category == "runbook"
        assert chunk.metadata.tags == "k8s,troubleshooting"
        assert chunk.metadata.content_hash == "abc123def456"

    def test_chunk_id_generation(self) -> None:
        """Test that chunk IDs are unique and deterministic."""
        chunker = Chunker()
        content = """# Test

## Section 1

Content 1.

## Section 2

Content 2.
"""
        chunks = chunker.chunk_document(
            content=content,
            document_path="test.md",
            content_hash="abc123",
        )

        ids = [c.chunk_id for c in chunks]

        # All IDs should be unique
        assert len(ids) == len(set(ids))

        # IDs should start with hash prefix
        for chunk_id in ids:
            assert chunk_id.startswith("abc123")

    def test_large_section_splitting(self, sample_markdown: str) -> None:
        """Test splitting of large sections."""
        chunker = Chunker(max_tokens=200, target_tokens=100)
        chunks = chunker.chunk_document(
            content=sample_markdown,
            document_path="test.md",
            content_hash="abc123",
        )

        # Should create multiple chunks
        assert len(chunks) > 1

        # Each chunk should be under max tokens
        for chunk in chunks:
            assert chunk.token_count <= 200 or "```" in chunk.content  # Code blocks may exceed

    def test_merge_small_chunks(self) -> None:
        """Test merging of very small chunks."""
        chunker = Chunker(min_tokens=50, target_tokens=200, max_tokens=500)
        content = """# Test

## Section

A.

B.

C.

D.
"""
        chunks = chunker.chunk_document(
            content=content,
            document_path="test.md",
            content_hash="abc123",
        )

        # Small paragraphs should be merged
        # The exact number depends on implementation, but should be reasonable
        assert len(chunks) <= 3

    def test_chroma_metadata_conversion(self) -> None:
        """Test conversion to ChromaDB metadata format."""
        chunker = Chunker()
        chunks = chunker.chunk_document(
            content="# Test\n\nContent.",
            document_path="test.md",
            content_hash="abc123",
            category="runbook",
        )

        metadata = chunks[0].to_chroma_metadata()

        assert isinstance(metadata, dict)
        assert "chunk_id" in metadata
        assert "document_path" in metadata
        assert "category" in metadata
        assert "breadcrumb" in metadata
        assert "content_hash" in metadata
        assert metadata["category"] == "runbook"
