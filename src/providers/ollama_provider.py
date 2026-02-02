"""Ollama provider implementation for LLM and embeddings."""

from pathlib import Path

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.providers.base import (
    EmbeddingProvider,
    EmbeddingResult,
    GenerationResult,
    LLMProvider,
)

logger = structlog.get_logger(__name__)


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using Ollama API."""

    # Known embedding dimensions for common models
    KNOWN_DIMENSIONS = {
        "granite-embedding:278m": 768,
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
    }

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "granite-embedding:278m",
        timeout: float = 60.0,
        ca_cert: Path | None = None,
    ):
        """Initialize the Ollama embedding provider.

        Args:
            base_url: Base URL for the Ollama API.
            model: Model name to use for embeddings.
            timeout: Request timeout in seconds.
            ca_cert: Path to CA certificate for SSL verification.
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._ca_cert = ca_cert
        self._client: httpx.AsyncClient | None = None
        self._dimension: int | None = self.KNOWN_DIMENSIONS.get(model)

    @property
    def model_id(self) -> str:
        """Return the model identifier."""
        return self._model

    @property
    def embedding_dimension(self) -> int:
        """Return the embedding dimension."""
        if self._dimension is None:
            # Default to 768 if unknown, will be updated on first embed
            return 768
        return self._dimension

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            # Use CA cert if provided, otherwise use default verification
            verify = str(self._ca_cert) if self._ca_cert else True
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                verify=verify,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a single text string.

        Args:
            text: The text to embed.

        Returns:
            EmbeddingResult containing the embedding vector.
        """
        client = await self._get_client()

        response = await client.post(
            "/api/embed",
            json={
                "model": self._model,
                "input": text,
            },
        )
        response.raise_for_status()
        data = response.json()

        # Ollama returns embeddings in a list
        embedding = data.get("embeddings", [[]])[0]

        # Update dimension if we didn't know it
        if self._dimension is None and embedding:
            self._dimension = len(embedding)

        return EmbeddingResult(
            embedding=embedding,
            model=self._model,
            token_count=data.get("prompt_eval_count", 0),
        )

    async def embed_batch(
        self, texts: list[str], batch_size: int = 32
    ) -> list[EmbeddingResult]:
        """Embed multiple texts.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts per batch.

        Returns:
            List of EmbeddingResult objects.
        """
        results = []
        client = await self._get_client()

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            # Ollama's embed endpoint supports multiple inputs
            response = await client.post(
                "/api/embed",
                json={
                    "model": self._model,
                    "input": batch,
                },
            )
            response.raise_for_status()
            data = response.json()

            embeddings = data.get("embeddings", [])

            # Update dimension if needed
            if self._dimension is None and embeddings and embeddings[0]:
                self._dimension = len(embeddings[0])

            for embedding in embeddings:
                results.append(
                    EmbeddingResult(
                        embedding=embedding,
                        model=self._model,
                    )
                )

            logger.debug(
                "Embedded batch",
                batch_size=len(batch),
                total_processed=len(results),
            )

        return results

    async def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            return response.status_code == 200
        except Exception as e:
            logger.warning("Ollama not available", error=str(e))
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


class OllamaLLMProvider(LLMProvider):
    """LLM provider using Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "granite4",
        timeout: float = 120.0,
        ca_cert: Path | None = None,
    ):
        """Initialize the Ollama LLM provider.

        Args:
            base_url: Base URL for the Ollama API.
            model: Model name to use for generation.
            timeout: Request timeout in seconds.
            ca_cert: Path to CA certificate for SSL verification.
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._ca_cert = ca_cert
        self._client: httpx.AsyncClient | None = None

    @property
    def model_id(self) -> str:
        """Return the model identifier."""
        return self._model

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            # Use CA cert if provided, otherwise use default verification
            verify = str(self._ca_cert) if self._ca_cert else True
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                verify=verify,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
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
            prompt: The user prompt.
            system_prompt: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            stop_sequences: Sequences that stop generation.

        Returns:
            GenerationResult with generated text.
        """
        client = await self._get_client()

        # Build messages for chat endpoint
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if stop_sequences:
            options["stop"] = stop_sequences

        response = await client.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": messages,
                "options": options,
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()

        message = data.get("message", {})
        text = message.get("content", "")

        return GenerationResult(
            text=text,
            model=self._model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            finish_reason=data.get("done_reason", ""),
        )

    async def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            if response.status_code != 200:
                return False

            # Check if our model is available
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            # Match model name (with or without tag)
            model_base = self._model.split(":")[0]
            return any(model_base in m for m in models)
        except Exception as e:
            logger.warning("Ollama not available", error=str(e))
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
