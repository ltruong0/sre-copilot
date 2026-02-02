"""RAG generator for answering questions."""

from dataclasses import dataclass, field

import structlog

from src.providers.base import LLMProvider
from src.rag.retriever import DocumentRetriever, RetrievedChunk

logger = structlog.get_logger(__name__)


@dataclass
class RAGResult:
    """Result from a RAG query."""

    answer: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    query: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RAGGenerator:
    """Generates answers using retrieved context."""

    SYSTEM_PROMPT = """You are an SRE (Site Reliability Engineering) assistant specialized in OpenShift and Kubernetes infrastructure.

Your role is to help engineers with:
- Troubleshooting cluster issues
- Understanding runbooks and procedures
- Explaining architecture decisions
- Providing operational guidance

Guidelines:
1. Always cite your sources by referencing the document sections provided
2. If the context doesn't contain relevant information, explicitly say so
3. Be concise but thorough in your explanations
4. When discussing commands, include the exact syntax
5. Highlight any warnings or caveats mentioned in the documentation
6. If multiple approaches exist, present them with their trade-offs

When you don't have enough context to answer fully, acknowledge this and suggest what additional information might be helpful."""

    QUERY_TEMPLATE = """Based on the following documentation excerpts, answer the user's question.

## Documentation Context

{context}

## Question

{question}

## Instructions

1. Answer the question using the provided documentation context
2. Cite specific sources using the breadcrumb paths (e.g., "According to [Runbook > Pod Troubleshooting > CrashLoopBackOff]...")
3. If the context doesn't fully answer the question, acknowledge what's missing
4. Provide actionable steps when applicable

Answer:"""

    def __init__(
        self,
        llm_provider: LLMProvider,
        retriever: DocumentRetriever,
        max_context_tokens: int = 4000,
    ):
        """Initialize the RAG generator.

        Args:
            llm_provider: Provider for generating answers.
            retriever: Document retriever for context.
            max_context_tokens: Maximum tokens for context window.
        """
        self._llm_provider = llm_provider
        self._retriever = retriever
        self._max_context_tokens = max_context_tokens

    async def generate_answer(
        self,
        question: str,
        top_k: int | None = None,
        category_filter: str | None = None,
        path_filter: str | None = None,
    ) -> RAGResult:
        """Generate an answer for a question using RAG.

        Args:
            question: The user's question.
            top_k: Override number of chunks to retrieve.
            category_filter: Optional category filter.
            path_filter: Optional path prefix filter.

        Returns:
            RAGResult with answer and sources.
        """
        # Retrieve relevant chunks
        chunks = await self._retriever.retrieve(
            query=question,
            top_k=top_k,
            category_filter=category_filter,
            path_filter=path_filter,
        )

        if not chunks:
            return RAGResult(
                answer="I couldn't find any relevant documentation to answer your question. "
                "Please try rephrasing or check if the topic is covered in the documentation.",
                sources=[],
                query=question,
                model=self._llm_provider.model_id,
            )

        # Build context from chunks
        context = self._build_context(chunks)

        # Generate prompt
        prompt = self.QUERY_TEMPLATE.format(
            context=context,
            question=question,
        )

        # Generate answer
        result = await self._llm_provider.generate(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            max_tokens=2048,
            temperature=0.3,  # Lower temperature for more focused answers
        )

        logger.debug(
            "Generated answer",
            question=question[:50],
            sources=len(chunks),
            tokens=result.total_tokens,
        )

        return RAGResult(
            answer=result.text.strip(),
            sources=chunks,
            query=question,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

    def _build_context(self, chunks: list[RetrievedChunk]) -> str:
        """Build context string from retrieved chunks.

        Args:
            chunks: Retrieved chunks.

        Returns:
            Formatted context string.
        """
        context_parts = []

        for i, chunk in enumerate(chunks, 1):
            # Build source header
            source = chunk.breadcrumb or chunk.document_path
            category = f"[{chunk.category}]" if chunk.category else ""

            context_parts.append(
                f"### Source {i}: {source} {category}\n\n{chunk.content}"
            )

        return "\n\n---\n\n".join(context_parts)

    async def generate_with_custom_prompt(
        self,
        question: str,
        custom_system_prompt: str,
        top_k: int | None = None,
    ) -> RAGResult:
        """Generate an answer with a custom system prompt.

        Args:
            question: The user's question.
            custom_system_prompt: Custom system prompt to use.
            top_k: Override number of chunks to retrieve.

        Returns:
            RAGResult with answer and sources.
        """
        chunks = await self._retriever.retrieve(query=question, top_k=top_k)

        if not chunks:
            return RAGResult(
                answer="No relevant documentation found.",
                sources=[],
                query=question,
                model=self._llm_provider.model_id,
            )

        context = self._build_context(chunks)

        prompt = f"""Documentation context:

{context}

Question: {question}

Please answer based on the provided documentation."""

        result = await self._llm_provider.generate(
            prompt=prompt,
            system_prompt=custom_system_prompt,
            max_tokens=2048,
            temperature=0.3,
        )

        return RAGResult(
            answer=result.text.strip(),
            sources=chunks,
            query=question,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
