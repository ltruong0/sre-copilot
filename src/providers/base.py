"""Abstract base classes for LLM and embedding providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EmbeddingResult:
    """Result from an embedding operation."""

    embedding: list[float]
    model: str
    token_count: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Result from a generation operation."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class EmbeddingProvider(ABC):
    """Abstract interface for embedding providers."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the model identifier used for embeddings."""
        ...

    @property
    @abstractmethod
    def embedding_dimension(self) -> int:
        """Return the dimension of the embedding vectors."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a single text string.

        Args:
            text: The text to embed.

        Returns:
            EmbeddingResult containing the embedding vector and metadata.
        """
        ...

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], batch_size: int = 32
    ) -> list[EmbeddingResult]:
        """Embed multiple text strings.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts to process in each batch.

        Returns:
            List of EmbeddingResult objects.
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the embedding provider is available.

        Returns:
            True if the provider is accessible and ready.
        """
        ...


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the model identifier used for generation."""
        ...

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> GenerationResult:
        """Generate text from a prompt.

        Args:
            prompt: The user prompt to respond to.
            system_prompt: Optional system prompt to set context.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0-1).
            stop_sequences: Optional sequences that stop generation.

        Returns:
            GenerationResult containing the generated text and metadata.
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the LLM provider is available.

        Returns:
            True if the provider is accessible and ready.
        """
        ...
