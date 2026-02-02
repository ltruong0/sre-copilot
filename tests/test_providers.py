"""Tests for LLM and embedding providers."""

import pytest

from src.providers.base import EmbeddingProvider, EmbeddingResult, GenerationResult, LLMProvider


class TestEmbeddingResult:
    """Test cases for EmbeddingResult."""

    def test_creation(self) -> None:
        """Test creating an EmbeddingResult."""
        result = EmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            model="test-model",
            token_count=5,
        )

        assert result.embedding == [0.1, 0.2, 0.3]
        assert result.model == "test-model"
        assert result.token_count == 5
        assert result.metadata == {}

    def test_with_metadata(self) -> None:
        """Test EmbeddingResult with metadata."""
        result = EmbeddingResult(
            embedding=[0.1],
            model="test",
            metadata={"key": "value"},
        )

        assert result.metadata["key"] == "value"


class TestGenerationResult:
    """Test cases for GenerationResult."""

    def test_creation(self) -> None:
        """Test creating a GenerationResult."""
        result = GenerationResult(
            text="Hello, world!",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
        )

        assert result.text == "Hello, world!"
        assert result.model == "test-model"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.total_tokens == 15
        assert result.finish_reason == "stop"

    def test_defaults(self) -> None:
        """Test GenerationResult defaults."""
        result = GenerationResult(text="test", model="model")

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.finish_reason == ""
        assert result.metadata == {}


@pytest.mark.asyncio
class TestMockEmbeddingProvider:
    """Test cases for the mock embedding provider."""

    async def test_embed_single(self, mock_embedding_provider: EmbeddingProvider) -> None:
        """Test embedding a single text."""
        result = await mock_embedding_provider.embed("test text")

        assert isinstance(result, EmbeddingResult)
        assert len(result.embedding) == 384  # Mock dimension
        assert result.model == "mock-embedding"

    async def test_embed_batch(self, mock_embedding_provider: EmbeddingProvider) -> None:
        """Test embedding multiple texts."""
        texts = ["text 1", "text 2", "text 3"]
        results = await mock_embedding_provider.embed_batch(texts)

        assert len(results) == 3
        assert all(isinstance(r, EmbeddingResult) for r in results)

    async def test_is_available(self, mock_embedding_provider: EmbeddingProvider) -> None:
        """Test availability check."""
        available = await mock_embedding_provider.is_available()
        assert available is True

    async def test_model_id(self, mock_embedding_provider: EmbeddingProvider) -> None:
        """Test model ID property."""
        assert mock_embedding_provider.model_id == "mock-embedding"

    async def test_embedding_dimension(
        self, mock_embedding_provider: EmbeddingProvider
    ) -> None:
        """Test embedding dimension property."""
        assert mock_embedding_provider.embedding_dimension == 384


@pytest.mark.asyncio
class TestMockLLMProvider:
    """Test cases for the mock LLM provider."""

    async def test_generate(self, mock_llm_provider: LLMProvider) -> None:
        """Test generating text."""
        result = await mock_llm_provider.generate("What is Kubernetes?")

        assert isinstance(result, GenerationResult)
        assert len(result.text) > 0
        assert result.model == "mock-llm"

    async def test_generate_with_system_prompt(
        self, mock_llm_provider: LLMProvider
    ) -> None:
        """Test generating with system prompt."""
        result = await mock_llm_provider.generate(
            prompt="What is K8s?",
            system_prompt="You are an SRE expert.",
        )

        assert isinstance(result, GenerationResult)

    async def test_is_available(self, mock_llm_provider: LLMProvider) -> None:
        """Test availability check."""
        available = await mock_llm_provider.is_available()
        assert available is True

    async def test_model_id(self, mock_llm_provider: LLMProvider) -> None:
        """Test model ID property."""
        assert mock_llm_provider.model_id == "mock-llm"
