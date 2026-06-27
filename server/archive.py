"""Archived-documents journal — the "retirer de la mémoire" log.

Append-only JSONL at ``archive_path`` (default ``.organizer/archive.jsonl``)
recording each document withdrawn from active memory: when it was archived, why,
and where its file was moved. Distinct from the undo journal (reversible file
ops) and the event journal (project narrative). Archiving also flips the registry
record's ``status`` to ``archived`` and quarantines the file; that file move is
recorded in the undo journal so it stays reversible — this log is the durable
record of *why* a document left active memory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ArchiveEntry:
    """One archived document: identity, reason, where the file went, and when."""

    checksum: str
    title: str
    reason: str
    src: str
    dst: str | None
    archived_at: str

    @classmethod
    def new(
        cls, checksum: str, title: str, reason: str, src: str, dst: str | None
    ) -> "ArchiveEntry":
        return cls(
            checksum=checksum,
            title=title,
            reason=reason,
            src=src,
            dst=dst,
            archived_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def append(archive_path: Path, entry: ArchiveEntry) -> None:
    """Append a single archive entry to the log; creates parent dirs if needed."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def all_entries(archive_path: Path) -> list[dict]:
    """Return all archive-log entries in chronological order; empty if no file."""
    if not archive_path.is_file():
        return []
    return [
        json.loads(ln)
        for ln in archive_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
