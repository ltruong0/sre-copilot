"""Document standardizer using LLM to restructure docs for better RAG retrieval."""

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.providers.base import LLMProvider

logger = structlog.get_logger(__name__)


@dataclass
class StandardizedDocument:
    """A document that has been standardized by the LLM."""

    original_path: str
    content: str
    title: str
    category: str
    tags: list[str]
    summary: str
    was_modified: bool


# Template that defines the standard structure for all documents
STANDARD_TEMPLATE = """---
title: {title}
category: {category}
tags: {tags}
summary: {summary}
---

# {title}

## Overview

{overview}

{sections}

## Related Documents

{related}
"""

DOCUMENT_TYPES = {
    "runbook": {
        "description": "Step-by-step operational procedures for handling incidents or tasks",
        "required_sections": ["Overview", "Prerequisites", "Procedure", "Verification", "Rollback"],
        "template": '''## Overview

Brief description of what this runbook addresses and when to use it.

## Prerequisites

- Required access/permissions
- Required tools
- Dependencies

## Symptoms

- Observable indicators that this runbook applies

## Procedure

### Step 1: {step_title}

{step_content}

```bash
# Commands go here
```

**Expected output:** Description of what you should see.

### Step 2: ...

## Verification

How to verify the procedure was successful.

## Rollback

Steps to undo changes if something goes wrong.

## Related Documents

- Links to related runbooks or documentation
''',
    },
    "troubleshooting": {
        "description": "Guides for diagnosing and resolving specific issues",
        "required_sections": ["Overview", "Symptoms", "Diagnosis", "Resolution"],
        "template": '''## Overview

Brief description of the issue this guide addresses.

## Symptoms

- Symptom 1
- Symptom 2

## Diagnosis

### Check 1: {check_title}

```bash
# Diagnostic command
```

**What to look for:** Explanation.

### Check 2: ...

## Root Causes

| Cause | Indicators | Resolution |
|-------|------------|------------|
| Cause 1 | What you see | Link to fix |

## Resolution

### For Cause 1

Step-by-step resolution.

### For Cause 2

...

## Prevention

How to prevent this issue in the future.

## Related Documents

- Links to related guides
''',
    },
    "architecture": {
        "description": "Technical documentation about system design and components",
        "required_sections": ["Overview", "Components", "Data Flow"],
        "template": '''## Overview

High-level description of this system/component.

## Components

### Component 1

- **Purpose:** What it does
- **Technology:** What it's built with
- **Location:** Where it runs

### Component 2

...

## Data Flow

Description of how data moves through the system.

```
[Source] --> [Processing] --> [Destination]
```

## Configuration

Key configuration options and where to find them.

## Dependencies

| Dependency | Purpose | Impact if Unavailable |
|------------|---------|----------------------|
| Service X | Does Y | Result Z |

## Related Documents

- Links to related architecture docs
''',
    },
    "onboarding": {
        "description": "Guides for new team members or getting started with tools",
        "required_sections": ["Overview", "Prerequisites", "Steps"],
        "template": '''## Overview

What this guide covers and who it's for.

## Prerequisites

- [ ] Prerequisite 1
- [ ] Prerequisite 2

## Steps

### 1. {step_title}

Detailed instructions.

### 2. ...

## Verification

How to verify you've completed onboarding successfully.

## Next Steps

What to do/learn after completing this guide.

## Getting Help

Where to get help if you're stuck.

## Related Documents

- Links to related onboarding docs
''',
    },
    "policy": {
        "description": "Organizational policies and procedures",
        "required_sections": ["Overview", "Scope", "Policy", "Procedures"],
        "template": '''## Overview

What this policy covers and why it exists.

## Scope

Who and what this policy applies to.

## Policy

### {policy_section}

Clear policy statements.

## Procedures

### {procedure_name}

Step-by-step procedures for following this policy.

## Exceptions

How to request exceptions to this policy.

## Enforcement

How this policy is enforced and consequences of violations.

## Related Documents

- Links to related policies
''',
    },
}

ANALYSIS_PROMPT = '''Analyze this document and extract structured information.

Document content:
```
{content}
```

Respond in this exact format:
TITLE: <document title>
CATEGORY: <one of: runbook, troubleshooting, architecture, onboarding, policy, reference>
TAGS: <comma-separated relevant tags>
SUMMARY: <one sentence summary>
CURRENT_SECTIONS: <comma-separated list of current section headings>
MISSING_SECTIONS: <sections that should be added based on category>
QUALITY_ISSUES: <list any formatting or structure issues>
'''

RESTRUCTURE_PROMPT = '''Restructure this document to follow our standard {category} template.

IMPORTANT RULES:
1. Preserve ALL original information - do not remove any content
2. Reorganize content into the standard sections
3. Fix formatting issues (broken tables, inconsistent headings, etc.)
4. Add section headers where missing
5. Keep all code blocks intact
6. Keep all commands and technical details exactly as written
7. If a standard section has no relevant content, include it with "N/A" or "Not applicable"

Original document:
```
{content}
```

Standard template for {category} documents:
```
{template}
```

Current issues identified:
{issues}

Output ONLY the restructured markdown document, nothing else. Start with the YAML frontmatter.
'''


class DocumentStandardizer:
    """Standardizes documents using LLM for better RAG retrieval."""

    def __init__(self, llm_provider: LLMProvider):
        """Initialize the standardizer.

        Args:
            llm_provider: LLM provider for document analysis and restructuring.
        """
        self._llm = llm_provider

    async def analyze_document(self, content: str) -> dict[str, str | list[str]]:
        """Analyze a document to extract metadata and identify issues.

        Args:
            content: Document content.

        Returns:
            Dict with title, category, tags, summary, and identified issues.
        """
        prompt = ANALYSIS_PROMPT.format(content=content[:8000])  # Limit content size

        result = await self._llm.generate(
            prompt=prompt,
            system_prompt="You are a technical documentation analyst. Extract information precisely.",
            max_tokens=1000,
            temperature=0.1,
        )

        # Parse the response
        analysis = {
            "title": "",
            "category": "reference",
            "tags": [],
            "summary": "",
            "current_sections": [],
            "missing_sections": [],
            "quality_issues": [],
        }

        for line in result.text.strip().split("\n"):
            if line.startswith("TITLE:"):
                analysis["title"] = line.replace("TITLE:", "").strip()
            elif line.startswith("CATEGORY:"):
                cat = line.replace("CATEGORY:", "").strip().lower()
                if cat in DOCUMENT_TYPES:
                    analysis["category"] = cat
            elif line.startswith("TAGS:"):
                tags = line.replace("TAGS:", "").strip()
                analysis["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            elif line.startswith("SUMMARY:"):
                analysis["summary"] = line.replace("SUMMARY:", "").strip()
            elif line.startswith("CURRENT_SECTIONS:"):
                sections = line.replace("CURRENT_SECTIONS:", "").strip()
                analysis["current_sections"] = [s.strip() for s in sections.split(",") if s.strip()]
            elif line.startswith("MISSING_SECTIONS:"):
                sections = line.replace("MISSING_SECTIONS:", "").strip()
                analysis["missing_sections"] = [s.strip() for s in sections.split(",") if s.strip()]
            elif line.startswith("QUALITY_ISSUES:"):
                issues = line.replace("QUALITY_ISSUES:", "").strip()
                analysis["quality_issues"] = [i.strip() for i in issues.split(",") if i.strip()]

        logger.debug(
            "Analyzed document",
            title=analysis["title"],
            category=analysis["category"],
            tags=analysis["tags"],
        )

        return analysis

    async def standardize(
        self,
        content: str,
        original_path: str,
        force_category: str | None = None,
    ) -> StandardizedDocument:
        """Standardize a document using LLM.

        Args:
            content: Original document content.
            original_path: Path to the original document.
            force_category: Force a specific category instead of auto-detecting.

        Returns:
            StandardizedDocument with restructured content.
        """
        # First analyze the document
        analysis = await self.analyze_document(content)

        category = force_category or analysis["category"]
        doc_type = DOCUMENT_TYPES.get(category, DOCUMENT_TYPES["runbook"])

        # Check if restructuring is needed
        needs_restructure = (
            len(analysis["missing_sections"]) > 0
            or len(analysis["quality_issues"]) > 0
        )

        if not needs_restructure:
            logger.info("Document already well-structured", path=original_path)
            return StandardizedDocument(
                original_path=original_path,
                content=content,
                title=analysis["title"],
                category=category,
                tags=analysis["tags"],
                summary=analysis["summary"],
                was_modified=False,
            )

        # Restructure the document
        issues_text = "\n".join(
            [f"- Missing section: {s}" for s in analysis["missing_sections"]]
            + [f"- Quality issue: {i}" for i in analysis["quality_issues"]]
        )

        prompt = RESTRUCTURE_PROMPT.format(
            category=category,
            content=content,
            template=doc_type["template"],
            issues=issues_text or "None identified",
        )

        result = await self._llm.generate(
            prompt=prompt,
            system_prompt="You are a technical writer. Restructure documents while preserving all information.",
            max_tokens=4000,
            temperature=0.2,
        )

        standardized_content = result.text.strip()

        # Ensure frontmatter is present
        if not standardized_content.startswith("---"):
            frontmatter = f"""---
title: {analysis['title']}
category: {category}
tags: {', '.join(analysis['tags'])}
summary: {analysis['summary']}
---

"""
            standardized_content = frontmatter + standardized_content

        logger.info(
            "Standardized document",
            path=original_path,
            category=category,
            issues_fixed=len(analysis["missing_sections"]) + len(analysis["quality_issues"]),
        )

        return StandardizedDocument(
            original_path=original_path,
            content=standardized_content,
            title=analysis["title"],
            category=category,
            tags=analysis["tags"],
            summary=analysis["summary"],
            was_modified=True,
        )

    async def standardize_batch(
        self,
        documents: list[tuple[str, str]],  # List of (path, content) tuples
    ) -> list[StandardizedDocument]:
        """Standardize multiple documents.

        Args:
            documents: List of (path, content) tuples.

        Returns:
            List of StandardizedDocument objects.
        """
        results = []
        for path, content in documents:
            try:
                result = await self.standardize(content, path)
                results.append(result)
            except Exception as e:
                logger.error("Failed to standardize document", path=path, error=str(e))
                # Return original on failure
                results.append(
                    StandardizedDocument(
                        original_path=path,
                        content=content,
                        title=Path(path).stem,
                        category="reference",
                        tags=[],
                        summary="",
                        was_modified=False,
                    )
                )
        return results


def get_template_guide() -> str:
    """Get a guide for writing standardized documents.

    Returns:
        Markdown guide with templates for each document type.
    """
    guide = """# Document Standards Guide

This guide describes the standard structure for each document type.
Following these templates improves searchability and consistency.

"""
    for doc_type, info in DOCUMENT_TYPES.items():
        guide += f"""## {doc_type.title()} Documents

{info['description']}

**Required sections:** {', '.join(info['required_sections'])}

### Template

```markdown
{info['template']}
```

---

"""
    return guide
