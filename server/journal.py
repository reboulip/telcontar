"""Append-only undo journal (JSONL)."""
from __future__ import annotations

import json
from pathlib import Path


def append(journal_path: Path, entry: dict) -> None:
    """Append a single operation record to the journal; creates parent dirs if needed."""
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def last(journal_path: Path) -> dict | None:
    """Return the most recent journal entry without removing it; None if empty."""
    if not journal_path.is_file():
        return None
    lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def all_entries(journal_path: Path) -> list[dict]:
    """Return all journal entries in chronological order."""
    if not journal_path.is_file():
        return []
    return [
        json.loads(ln)
        for ln in journal_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def pop_last(journal_path: Path) -> dict | None:
    """Remove and return the most recent journal entry; None if empty."""
    if not journal_path.is_file():
        return None
    lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    entry = json.loads(lines[-1])
    remainder = lines[:-1]
    journal_path.write_text(
        ("\n".join(remainder) + "\n") if remainder else "",
        encoding="utf-8",
    )
    return entry
