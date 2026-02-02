"""Tests for the document cleaner."""

import pytest

from src.ingestion.cleaner import DocumentCleaner


class TestDocumentCleaner:
    """Test cases for DocumentCleaner."""

    def test_remove_br_tags(self) -> None:
        """Test removal of <br> and <br/> tags."""
        cleaner = DocumentCleaner()
        content = "Line 1<br>Line 2<br/>Line 3"

        cleaned, stats = cleaner.clean(content)

        assert "<br>" not in cleaned
        assert "<br/>" not in cleaned
        assert stats.html_tags_removed >= 2

    def test_convert_html_links(self) -> None:
        """Test conversion of HTML links to markdown."""
        cleaner = DocumentCleaner()
        content = 'Check <a href="https://example.com">this link</a> for info.'

        cleaned, stats = cleaner.clean(content)

        assert "[this link](https://example.com)" in cleaned
        assert "<a " not in cleaned
        assert stats.links_converted == 1

    def test_convert_admonitions(self) -> None:
        """Test conversion of admonition divs to blockquotes."""
        cleaner = DocumentCleaner()
        content = """<div class="admonition warning">
This is a warning message.
</div>"""

        cleaned, stats = cleaner.clean(content)

        assert "> **Warning**:" in cleaned
        assert "<div" not in cleaned
        assert stats.admonitions_converted == 1

    def test_normalize_heading_levels(self) -> None:
        """Test normalization of heading hierarchy."""
        cleaner = DocumentCleaner()
        content = """# Title

### Subsection

Content here.
"""

        cleaned, stats = cleaner.clean(content)

        # H3 after H1 should become H2
        assert "## Subsection" in cleaned
        assert "### Subsection" not in cleaned
        assert stats.headings_normalized == 1

    def test_label_unlabeled_code_blocks(self) -> None:
        """Test adding language labels to code blocks."""
        cleaner = DocumentCleaner()
        content = """Here's a command:

```
kubectl get pods
```
"""

        cleaned, stats = cleaner.clean(content)

        assert "```bash" in cleaned
        assert stats.code_blocks_labeled == 1

    def test_infer_yaml_code_block(self) -> None:
        """Test inference of YAML code blocks."""
        cleaner = DocumentCleaner()
        content = """Example manifest:

```
apiVersion: v1
kind: Pod
metadata:
  name: test
```
"""

        cleaned, stats = cleaner.clean(content)

        assert "```yaml" in cleaned

    def test_preserve_labeled_code_blocks(self) -> None:
        """Test that already labeled code blocks are preserved."""
        cleaner = DocumentCleaner()
        content = """Example:

```python
print("hello")
```
"""

        cleaned, stats = cleaner.clean(content)

        assert "```python" in cleaned
        assert stats.code_blocks_labeled == 0

    def test_remove_generic_html_tags(self) -> None:
        """Test removal of generic HTML tags."""
        cleaner = DocumentCleaner()
        content = "<div>Content</div> and <span>more</span> text"

        cleaned, stats = cleaner.clean(content)

        assert "<div>" not in cleaned
        assert "<span>" not in cleaned
        assert "</div>" not in cleaned
        assert "Content" in cleaned

    def test_normalize_whitespace(self) -> None:
        """Test whitespace normalization."""
        cleaner = DocumentCleaner()
        content = """Line 1




Line 2"""

        cleaned, _ = cleaner.clean(content)

        # Should not have more than 2 consecutive blank lines
        assert "\n\n\n\n" not in cleaned

    def test_full_cleaning_pipeline(self, sample_markdown_with_html: str) -> None:
        """Test the full cleaning pipeline."""
        cleaner = DocumentCleaner()

        cleaned, stats = cleaner.clean(sample_markdown_with_html)

        # All HTML should be cleaned
        assert "<br" not in cleaned
        assert "<div" not in cleaned
        assert "<a " not in cleaned

        # Links should be converted
        assert "[this link](https://example.com)" in cleaned

        # Code block should be labeled
        assert "```bash" in cleaned

        # Stats should reflect work done
        assert stats.html_tags_removed > 0
        assert stats.links_converted == 1
