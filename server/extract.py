"""Text extraction from binary formats (PDF, Office) via markitdown."""
from __future__ import annotations

from pathlib import Path


def extract(path: Path, max_chars: int) -> str:
    """Return up to max_chars of plain text extracted from path."""
    raise NotImplementedError
