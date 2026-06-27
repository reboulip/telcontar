"""Text extraction from binary formats (PDF, Office) via markitdown."""

from __future__ import annotations

from pathlib import Path

from markitdown import MarkItDown

_md = MarkItDown()


def extract(path: Path, max_chars: int) -> str:
    """Return up to max_chars of plain text extracted from path."""
    result = _md.convert(str(path))
    text = result.text_content
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text
