"""Document retriever for semantic search."""

from dataclasses import dataclass
from pathlib import Path

import chromadb
import structlog
from chromadb.config import Settings as ChromaSettings

from src.providers.base import EmbeddingProvider

logger = structlog.get_logger(__name__)

COLLECTION_NAME = "sre_docs"


@dataclass
class RetrievedChunk:
    """A retrieved chunk with relevance information."""

    chunk_id: str
    content: str
    document_path: str
    category: str
    h1: str
    h2: str
    h3: str
    breadcrumb: str
    tags: str
    similarity_score: float

    @classmethod
    def from_chroma_result(
        cls,
        id: str,  # noqa: A002
        document: str,
        metadata: dict[str, str],
        distance: float,
    ) -> "RetrievedChunk":
        """Create from ChromaDB query result.

        Args:
            id: Chunk ID.
            document: Chunk content.
            metadata: Chunk metadata.
            distance: Distance score from ChromaDB.

        Returns:
            RetrievedChunk instance.
        """
        # ChromaDB returns L2 distance, convert to similarity
        # For normalized embeddings, similarity = 1 - (distance^2 / 2)
        # Simpler approximation: similarity = 1 / (1 + distance)
        similarity = 1 / (1 + distance)

        return cls(
            chunk_id=id,
            content=document,
            document_path=metadata.get("document_path", ""),
            category=metadata.get("category", ""),
            h1=metadata.get("h1", ""),
            h2=metadata.get("h2", ""),
            h3=metadata.get("h3", ""),
            breadcrumb=metadata.get("breadcrumb", ""),
            tags=metadata.get("tags", ""),
            similarity_score=similarity,
        )


class DocumentRetriever:
    """Retrieves relevant document chunks using semantic search."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        chromadb_path: Path,
        top_k: int = 5,
        similarity_threshold: float = 0.5,
    ):
        """Initialize the document retriever.

        Args:
            embedding_provider: Provider for generating query embeddings.
            chromadb_path: Path to ChromaDB persistence directory.
            top_k: Maximum number of chunks to retrieve.
            similarity_threshold: Minimum similarity score for results.
        """
        self._embedding_provider = embedding_provider
        self._chromadb_path = chromadb_path
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold
        self._client: chromadb.PersistentClient | None = None
        self._collection: chromadb.Collection | None = None

    def _get_client(self) -> chromadb.PersistentClient:
        """Get or create the ChromaDB client."""
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self._chromadb_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _get_collection(self) -> chromadb.Collection:
        """Get the document collection."""
        if self._collection is None:
            client = self._get_client()
            self._collection = client.get_collection(name=COLLECTION_NAME)
        return self._collection

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        category_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve relevant chunks for a query.

        Args:
            query: The search query.
            top_k: Override default top_k.
            category_filter: Optional category to filter by.

        Returns:
            List of retrieved chunks sorted by relevance.
        """
        top_k = top_k or self._top_k

        # Generate query embedding
        embedding_result = await self._embedding_provider.embed(query)
        query_embedding = embedding_result.embedding

        # Build ChromaDB query
        collection = self._get_collection()

        where_filter = None
        if category_filter:
            where_filter = {"category": category_filter}

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        # Process results
        chunks = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i, chunk_id in enumerate(ids):
            chunk = RetrievedChunk.from_chroma_result(
                id=chunk_id,
                document=documents[i],
                metadata=metadatas[i],
                distance=distances[i],
            )

            # Apply similarity threshold
            if chunk.similarity_score >= self._similarity_threshold:
                chunks.append(chunk)

        logger.debug(
            "Retrieved chunks",
            query=query[:50],
            total_results=len(ids),
            above_threshold=len(chunks),
        )

        return chunks

    async def retrieve_by_document(self, document_path: str) -> list[RetrievedChunk]:
        """Retrieve all chunks for a specific document.

        Args:
            document_path: Path of the document.

        Returns:
            List of chunks from the document.
        """
        collection = self._get_collection()

        results = collection.get(
            where={"document_path": document_path},
            include=["documents", "metadatas"],
        )

        chunks = []
        ids = results.get("ids", [])
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])

        for i, chunk_id in enumerate(ids):
            # Use a high similarity score since we're fetching directly
            chunk = RetrievedChunk(
                chunk_id=chunk_id,
                content=documents[i],
                document_path=metadatas[i].get("document_path", ""),
                category=metadatas[i].get("category", ""),
                h1=metadatas[i].get("h1", ""),
                h2=metadatas[i].get("h2", ""),
                h3=metadatas[i].get("h3", ""),
                breadcrumb=metadatas[i].get("breadcrumb", ""),
                tags=metadatas[i].get("tags", ""),
                similarity_score=1.0,
            )
            chunks.append(chunk)

        return chunks

    def get_categories(self) -> list[str]:
        """Get all unique categories in the collection.

        Returns:
            List of category names.
        """
        collection = self._get_collection()
        results = collection.get(include=["metadatas"])

        categories = set()
        for metadata in results.get("metadatas", []):
            if metadata and "category" in metadata:
                categories.add(metadata["category"])

        return sorted(categories)

    def get_documents(self) -> list[dict[str, str]]:
        """Get all unique documents in the collection.

        Returns:
            List of dicts with document_path and category.
        """
        collection = self._get_collection()
        results = collection.get(include=["metadatas"])

        documents: dict[str, str] = {}
        for metadata in results.get("metadatas", []):
            if metadata:
                path = metadata.get("document_path", "")
                category = metadata.get("category", "")
                if path and path not in documents:
                    documents[path] = category

        return [{"path": path, "category": cat} for path, cat in sorted(documents.items())]
