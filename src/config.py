"""Configuration management using Pydantic settings."""

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from src.providers.base import EmbeddingProvider, LLMProvider


class LLMProviderType(str, Enum):
    """Supported LLM provider types."""

    OLLAMA = "ollama"
    WATSONX = "watsonx"


class LogFormat(str, Enum):
    """Log output format options."""

    CONSOLE = "console"
    JSON = "json"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Provider selection
    llm_provider: LLMProviderType = Field(
        default=LLMProviderType.OLLAMA,
        description="LLM provider to use",
    )

    # Ollama settings
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama API base URL",
    )
    ollama_model: str = Field(
        default="granite4",
        description="Ollama model for generation",
    )
    ollama_embedding_model: str = Field(
        default="granite-embedding:278m",
        description="Ollama model for embeddings",
    )
    ollama_cleanup_model: str = Field(
        default="granite4:7b",
        description="Ollama model for cleanup tasks (smaller/faster)",
    )
    ollama_ca_cert: Path | None = Field(
        default=None,
        description="Path to CA certificate for Ollama server (for self-signed certs)",
    )

    # watsonx settings
    watsonx_api_key: str | None = Field(
        default=None,
        description="IBM watsonx.ai API key",
    )
    watsonx_project_id: str | None = Field(
        default=None,
        description="IBM watsonx.ai project ID",
    )
    watsonx_url: str = Field(
        default="https://us-south.ml.cloud.ibm.com",
        description="IBM watsonx.ai API URL",
    )
    watsonx_model: str = Field(
        default="ibm/granite-13b-chat-v2",
        description="watsonx model for generation",
    )
    watsonx_embedding_model: str = Field(
        default="ibm/slate-125m-english-rtrvr",
        description="watsonx model for embeddings",
    )

    # Document paths
    docs_path: Path = Field(
        default=Path("./docs"),
        description="Path to documentation directory",
    )
    chromadb_path: Path = Field(
        default=Path("./data/chromadb"),
        description="Path to ChromaDB persistence directory",
    )

    # Chunking settings
    chunk_min_tokens: int = Field(
        default=100,
        description="Minimum tokens per chunk",
    )
    chunk_max_tokens: int = Field(
        default=1000,
        description="Maximum tokens per chunk",
    )
    chunk_target_tokens: int = Field(
        default=500,
        description="Target tokens per chunk",
    )

    # RAG settings
    rag_top_k: int = Field(
        default=5,
        description="Number of chunks to retrieve",
    )
    rag_similarity_threshold: float = Field(
        default=0.3,
        description="Minimum similarity score for retrieval",
    )

    # MCP server settings
    mcp_server_host: str = Field(
        default="127.0.0.1",
        description="MCP server host",
    )
    mcp_server_port: int = Field(
        default=8080,
        description="MCP server port",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    log_format: LogFormat = Field(
        default=LogFormat.CONSOLE,
        description="Log output format",
    )

    # Feature flags
    enable_cleanup: bool = Field(
        default=True,
        description="Enable LLM-based cleanup of badly formatted sections",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def get_llm_provider(settings: Settings | None = None) -> "LLMProvider":
    """Factory function to get the configured LLM provider.

    Args:
        settings: Optional settings instance. Uses cached settings if not provided.

    Returns:
        Configured LLM provider instance.

    Raises:
        ValueError: If the configured provider is not supported.
    """
    if settings is None:
        settings = get_settings()

    if settings.llm_provider == LLMProviderType.OLLAMA:
        from src.providers.ollama_provider import OllamaLLMProvider

        return OllamaLLMProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            ca_cert=settings.ollama_ca_cert,
        )
    elif settings.llm_provider == LLMProviderType.WATSONX:
        from src.providers.watsonx_provider import WatsonxLLMProvider

        return WatsonxLLMProvider(
            api_key=settings.watsonx_api_key,
            project_id=settings.watsonx_project_id,
            url=settings.watsonx_url,
            model=settings.watsonx_model,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def get_embedding_provider(settings: Settings | None = None) -> "EmbeddingProvider":
    """Factory function to get the configured embedding provider.

    Args:
        settings: Optional settings instance. Uses cached settings if not provided.

    Returns:
        Configured embedding provider instance.

    Raises:
        ValueError: If the configured provider is not supported.
    """
    if settings is None:
        settings = get_settings()

    if settings.llm_provider == LLMProviderType.OLLAMA:
        from src.providers.ollama_provider import OllamaEmbeddingProvider

        return OllamaEmbeddingProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
            ca_cert=settings.ollama_ca_cert,
        )
    elif settings.llm_provider == LLMProviderType.WATSONX:
        from src.providers.watsonx_provider import WatsonxEmbeddingProvider

        return WatsonxEmbeddingProvider(
            api_key=settings.watsonx_api_key,
            project_id=settings.watsonx_project_id,
            url=settings.watsonx_url,
            model=settings.watsonx_embedding_model,
        )
    else:
        raise ValueError(f"Unsupported embedding provider: {settings.llm_provider}")


def get_cleanup_provider(settings: Settings | None = None) -> "LLMProvider":
    """Factory function to get the cleanup LLM provider (smaller/faster model).

    Args:
        settings: Optional settings instance. Uses cached settings if not provided.

    Returns:
        Configured LLM provider instance for cleanup tasks.
    """
    if settings is None:
        settings = get_settings()

    if settings.llm_provider == LLMProviderType.OLLAMA:
        from src.providers.ollama_provider import OllamaLLMProvider

        return OllamaLLMProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_cleanup_model,
            ca_cert=settings.ollama_ca_cert,
        )
    else:
        # For watsonx, use the same model for cleanup
        return get_llm_provider(settings)
