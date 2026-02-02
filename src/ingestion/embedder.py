"""Document embedder for storing chunks in ChromaDB."""

from datetime import datetime, timezone
from pathlib import Path

import chromadb
import structlog
from chromadb.config import Settings as ChromaSettings

from src.ingestion.chunker import Chunk
from src.providers.base import EmbeddingProvider

logger = structlog.get_logger(__name__)

COLLECTION_NAME = "sre_docs"


class DocumentEmbedder:
    """Embeds document chunks and stores them in ChromaDB."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        chromadb_path: Path,
    ):
        """Initialize the document embedder.

        Args:
            embedding_provider: Provider for generating embeddings.
            chromadb_path: Path to ChromaDB persistence directory.
        """
        self._embedding_provider = embedding_provider
        self._chromadb_path = chromadb_path
        self._client: chromadb.PersistentClient | None = None
        self._collection: chromadb.Collection | None = None

    def _get_client(self) -> chromadb.PersistentClient:
        """Get or create the ChromaDB client."""
        if self._client is None:
            self._chromadb_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self._chromadb_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _get_collection(self) -> chromadb.Collection:
        """Get or create the document collection."""
        if self._collection is None:
            client = self._get_client()
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={
                    "description": "SRE documentation chunks",
                    "embedding_model": self._embedding_provider.model_id,
                },
            )
        return self._collection

    async def embed_chunks(
        self,
        chunks: list[Chunk],
        batch_size: int = 32,
    ) -> int:
        """Embed and store chunks in ChromaDB.

        Args:
            chunks: List of chunks to embed.
            batch_size: Number of chunks to process per batch.

        Returns:
            Number of chunks successfully embedded.
        """
        if not chunks:
            return 0

        collection = self._get_collection()
        embedded_count = 0
        timestamp = datetime.now(timezone.utc).isoformat()

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [chunk.content for chunk in batch]

            try:
                # Generate embeddings
                embedding_results = await self._embedding_provider.embed_batch(
                    texts, batch_size=batch_size
                )

                # Prepare data for ChromaDB
                ids = [chunk.chunk_id for chunk in batch]
                embeddings = [result.embedding for result in embedding_results]
                documents = texts
                metadatas = []

                for chunk in batch:
                    chunk.metadata.embedding_model = self._embedding_provider.model_id
                    chunk.metadata.ingested_at = timestamp
                    metadatas.append(chunk.to_chroma_metadata())

                # Upsert to ChromaDB
                collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                )

                embedded_count += len(batch)
                logger.debug(
                    "Embedded batch",
                    batch_num=i // batch_size + 1,
                    batch_size=len(batch),
                    total_embedded=embedded_count,
                )

            except Exception as e:
                logger.error(
                    "Failed to embed batch",
                    batch_num=i // batch_size + 1,
                    error=str(e),
                )
                raise

        logger.info("Embedded all chunks", count=embedded_count)
        return embedded_count

    def get_existing_hashes(self) -> dict[str, tuple[str, str]]:
        """Get content hashes and embedding models for existing documents.

        Returns:
            Dict mapping document_path to (content_hash, embedding_model).
        """
        collection = self._get_collection()

        # Get all unique documents
        results = collection.get(
            include=["metadatas"],
        )

        doc_hashes: dict[str, tuple[str, str]] = {}
        for metadata in results.get("metadatas", []):
            if metadata:
                path = metadata.get("document_path", "")
                content_hash = metadata.get("content_hash", "")
                embedding_model = metadata.get("embedding_model", "")
                if path and path not in doc_hashes:
                    doc_hashes[path] = (content_hash, embedding_model)

        return doc_hashes

    def delete_document_chunks(self, document_path: str) -> int:
        """Delete all chunks for a document.

        Args:
            document_path: Path of the document to delete.

        Returns:
            Number of chunks deleted.
        """
        collection = self._get_collection()

        # Find chunks for this document
        results = collection.get(
            where={"document_path": document_path},
            include=[],
        )

        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
            logger.info("Deleted document chunks", path=document_path, count=len(ids))

        return len(ids)

    def get_document_count(self) -> int:
        """Get total number of chunks in the collection."""
        collection = self._get_collection()
        return collection.count()

    def get_collection_info(self) -> dict[str, int | str]:
        """Get information about the collection."""
        collection = self._get_collection()
        metadata = collection.metadata or {}

        return {
            "name": collection.name,
            "count": collection.count(),
            "embedding_model": metadata.get("embedding_model", "unknown"),
        }

    def check_embedding_model_mismatch(self) -> bool:
        """Check if current embedding model differs from stored data.

        Returns:
            True if there's a mismatch (warning condition).
        """
        collection = self._get_collection()
        metadata = collection.metadata or {}
        stored_model = metadata.get("embedding_model", "")

        if stored_model and stored_model != self._embedding_provider.model_id:
            logger.warning(
                "Embedding model mismatch",
                stored=stored_model,
                current=self._embedding_provider.model_id,
            )
            return True
        return False
