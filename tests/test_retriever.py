"""Tests for the document retriever."""

from pathlib import Path

import pytest

from src.ingestion.chunker import Chunker
from src.ingestion.embedder import DocumentEmbedder
from src.providers.base import EmbeddingProvider
from src.rag.retriever import DocumentRetriever, RetrievedChunk


class TestRetrievedChunk:
    """Test cases for RetrievedChunk."""

    def test_from_chroma_result(self) -> None:
        """Test creation from ChromaDB result."""
        chunk = RetrievedChunk.from_chroma_result(
            id="chunk_1",
            document="Test content",
            metadata={
                "document_path": "test.md",
                "category": "runbook",
                "h1": "Title",
                "h2": "Section",
                "h3": "",
                "breadcrumb": "Title > Section",
                "tags": "k8s",
            },
            distance=0.5,
        )

        assert chunk.chunk_id == "chunk_1"
        assert chunk.content == "Test content"
        assert chunk.document_path == "test.md"
        assert chunk.category == "runbook"
        assert chunk.breadcrumb == "Title > Section"
        assert chunk.similarity_score > 0

    def test_similarity_conversion(self) -> None:
        """Test distance to similarity conversion."""
        # Lower distance = higher similarity
        chunk_close = RetrievedChunk.from_chroma_result(
            id="1", document="", metadata={}, distance=0.1
        )
        chunk_far = RetrievedChunk.from_chroma_result(
            id="2", document="", metadata={}, distance=1.0
        )

        assert chunk_close.similarity_score > chunk_far.similarity_score


@pytest.mark.asyncio
class TestDocumentRetriever:
    """Test cases for DocumentRetriever."""

    async def test_retrieve_with_embeddings(
        self,
        mock_embedding_provider: EmbeddingProvider,
        temp_chromadb_path: Path,
    ) -> None:
        """Test retrieving documents with embeddings."""
        # First, add some documents
        embedder = DocumentEmbedder(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        chunker = Chunker()
        chunks = chunker.chunk_document(
            content="""# Kubernetes Troubleshooting

## Pod Issues

When a pod crashes, check the logs first.

## Network Issues

For network problems, verify the service endpoints.
""",
            document_path="troubleshooting.md",
            content_hash="test123",
            category="runbook",
        )

        await embedder.embed_chunks(chunks)

        # Now retrieve
        retriever = DocumentRetriever(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
            top_k=5,
            similarity_threshold=0.0,  # Low threshold for mock embeddings
        )

        results = await retriever.retrieve("pod crashes")

        # With mock embeddings, we just verify retrieval works
        # Actual similarity may be low with hash-based mock embeddings
        assert len(results) > 0
        assert all(isinstance(r, RetrievedChunk) for r in results)

    async def test_retrieve_empty_collection(
        self,
        mock_embedding_provider: EmbeddingProvider,
        temp_chromadb_path: Path,
    ) -> None:
        """Test retrieval from empty collection."""
        # Initialize embedder to create collection
        embedder = DocumentEmbedder(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )
        # Access collection to create it
        embedder._get_collection()

        retriever = DocumentRetriever(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        results = await retriever.retrieve("anything")
        assert len(results) == 0

    async def test_retrieve_with_category_filter(
        self,
        mock_embedding_provider: EmbeddingProvider,
        temp_chromadb_path: Path,
    ) -> None:
        """Test retrieval with category filter."""
        embedder = DocumentEmbedder(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        chunker = Chunker()

        # Add runbook
        runbook_chunks = chunker.chunk_document(
            content="# Runbook\n\nRunbook content about pods.",
            document_path="runbook.md",
            content_hash="run123",
            category="runbook",
        )

        # Add architecture doc
        arch_chunks = chunker.chunk_document(
            content="# Architecture\n\nArchitecture content about pods.",
            document_path="arch.md",
            content_hash="arch123",
            category="architecture",
        )

        await embedder.embed_chunks(runbook_chunks + arch_chunks)

        retriever = DocumentRetriever(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
            similarity_threshold=0.1,
        )

        # Filter by runbook
        results = await retriever.retrieve("pods", category_filter="runbook")

        # All results should be from runbook category
        for result in results:
            assert result.category == "runbook"

    async def test_get_categories(
        self,
        mock_embedding_provider: EmbeddingProvider,
        temp_chromadb_path: Path,
    ) -> None:
        """Test getting unique categories."""
        embedder = DocumentEmbedder(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        chunker = Chunker()
        chunks1 = chunker.chunk_document(
            content="# Doc 1\n\nContent.",
            document_path="doc1.md",
            content_hash="hash1",
            category="runbook",
        )
        chunks2 = chunker.chunk_document(
            content="# Doc 2\n\nContent.",
            document_path="doc2.md",
            content_hash="hash2",
            category="architecture",
        )

        await embedder.embed_chunks(chunks1 + chunks2)

        retriever = DocumentRetriever(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        categories = retriever.get_categories()

        assert "runbook" in categories
        assert "architecture" in categories

    async def test_get_documents(
        self,
        mock_embedding_provider: EmbeddingProvider,
        temp_chromadb_path: Path,
    ) -> None:
        """Test getting unique documents."""
        embedder = DocumentEmbedder(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        chunker = Chunker()
        chunks = chunker.chunk_document(
            content="# Test\n\n## Section 1\n\nContent 1.\n\n## Section 2\n\nContent 2.",
            document_path="test.md",
            content_hash="hash1",
            category="runbook",
        )

        await embedder.embed_chunks(chunks)

        retriever = DocumentRetriever(
            embedding_provider=mock_embedding_provider,
            chromadb_path=temp_chromadb_path,
        )

        documents = retriever.get_documents()

        # Should have one unique document even with multiple chunks
        paths = [d["path"] for d in documents]
        assert "test.md" in paths
        assert len([p for p in paths if p == "test.md"]) == 1
