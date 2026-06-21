"""Append-only undo journal (JSONL)."""
from __future__ import annotations

from pathlib import Path


def append(journal_path: Path, entry: dict) -> None:
    """Append a single operation record to the journal."""
    raise NotImplementedError


def last(journal_path: Path) -> dict | None:
    """Return the most recent journal entry without removing it."""
    raise NotImplementedError


def pop_last(journal_path: Path) -> dict | None:
    """Remove and return the most recent journal entry."""
    raise NotImplementedError
