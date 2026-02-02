"""Pytest configuration and fixtures."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.providers.base import EmbeddingProvider, EmbeddingResult, GenerationResult, LLMProvider


@pytest.fixture
def sample_markdown() -> str:
    """Sample markdown document for testing."""
    return """---
title: Test Runbook
category: runbook
tags: kubernetes, troubleshooting
---

# Pod Troubleshooting Guide

This guide covers common pod issues in Kubernetes.

## CrashLoopBackOff

When a pod enters CrashLoopBackOff, follow these steps:

### Check Pod Logs

```bash
kubectl logs <pod-name> -n <namespace>
```

### Check Events

```bash
kubectl describe pod <pod-name> -n <namespace>
```

## ImagePullBackOff

This error occurs when Kubernetes cannot pull the container image.

### Verify Image Name

Ensure the image name and tag are correct:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
spec:
  containers:
  - name: test
    image: registry.example.com/app:v1.0
```

## Resource Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| OOMKilled | Pod restarts | Increase memory limit |
| CPU Throttling | Slow response | Increase CPU limit |
"""


@pytest.fixture
def sample_markdown_with_html() -> str:
    """Sample markdown with HTML artifacts."""
    return """# Test Document

<br/>
This has some <br> line breaks.

<div class="admonition warning">
This is a warning message.
</div>

Check out <a href="https://example.com">this link</a> for more info.

```
oc get pods -n test
```
"""


@pytest.fixture
def temp_docs_dir(tmp_path: Path) -> Path:
    """Create a temporary documentation directory."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    # Create test documents
    runbook_dir = docs_dir / "runbooks"
    runbook_dir.mkdir()

    (runbook_dir / "pod-troubleshooting.md").write_text("""---
title: Pod Troubleshooting
category: runbook
---

# Pod Troubleshooting

## Overview

This runbook covers pod issues.

## CrashLoopBackOff

Check logs first.
""")

    arch_dir = docs_dir / "architecture"
    arch_dir.mkdir()

    (arch_dir / "overview.md").write_text("""# Architecture Overview

## Components

The system has three main components.

### API Server

Handles HTTP requests.

### Worker

Processes background jobs.
""")

    return docs_dir


@pytest.fixture
def mock_embedding_provider() -> EmbeddingProvider:
    """Create a mock embedding provider."""

    class MockEmbeddingProvider(EmbeddingProvider):
        def __init__(self):
            self._model = "mock-embedding"
            self._dimension = 384

        @property
        def model_id(self) -> str:
            return self._model

        @property
        def embedding_dimension(self) -> int:
            return self._dimension

        async def embed(self, text: str) -> EmbeddingResult:
            # Generate a deterministic embedding based on text hash
            import hashlib

            hash_bytes = hashlib.md5(text.encode()).digest()
            embedding = [float(b) / 255.0 for b in hash_bytes] * 24  # 384 dims
            return EmbeddingResult(
                embedding=embedding,
                model=self._model,
                token_count=len(text.split()),
            )

        async def embed_batch(
            self, texts: list[str], batch_size: int = 32
        ) -> list[EmbeddingResult]:
            return [await self.embed(text) for text in texts]

        async def is_available(self) -> bool:
            return True

    return MockEmbeddingProvider()


@pytest.fixture
def mock_llm_provider() -> LLMProvider:
    """Create a mock LLM provider."""

    class MockLLMProvider(LLMProvider):
        def __init__(self):
            self._model = "mock-llm"

        @property
        def model_id(self) -> str:
            return self._model

        async def generate(
            self,
            prompt: str,
            system_prompt: str = "",
            max_tokens: int = 1024,
            temperature: float = 0.7,
            stop_sequences: list[str] | None = None,
        ) -> GenerationResult:
            # Return a mock response
            return GenerationResult(
                text="This is a mock response based on the provided context.",
                model=self._model,
                prompt_tokens=len(prompt.split()),
                completion_tokens=10,
                total_tokens=len(prompt.split()) + 10,
            )

        async def is_available(self) -> bool:
            return True

    return MockLLMProvider()


@pytest.fixture
def temp_chromadb_path(tmp_path: Path) -> Path:
    """Create a temporary ChromaDB path."""
    chromadb_path = tmp_path / "chromadb"
    chromadb_path.mkdir()
    return chromadb_path
