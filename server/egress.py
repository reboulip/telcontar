"""Egress log — an audit trail of document content sent to the LLM endpoint (S8).

Append-only JSONL at ``egress_path`` (default ``.organizer/egress.jsonl``);
each entry records one file whose content left the machine via a read/extract/
compare tool: which path, how many bytes of content were sent, which tool sent
it, and when. Distinct from the undo journal (reversible file ops), the event
journal (project narrative), and the archive log (withdrawn documents) — this
is purely a record of what an operator should assume the configured
``LLM_BASE_URL`` has seen.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class EgressEntry:
    """One file's content sent to the LLM endpoint."""

    path: str
    size_bytes: int
    tool: str
    timestamp: str

    @classmethod
    def new(cls, path: str, size_bytes: int, tool: str) -> "EgressEntry":
        return cls(
            path=path,
            size_bytes=size_bytes,
            tool=tool,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def append(egress_path: Path, entry: EgressEntry) -> None:
    """Append a single egress entry to the log; creates parent dirs if needed."""
    egress_path.parent.mkdir(parents=True, exist_ok=True)
    with egress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def all_entries(egress_path: Path) -> list[dict]:
    """Return all egress-log entries in chronological order; empty if no file."""
    if not egress_path.is_file():
        return []
    return [
        json.loads(ln) for ln in egress_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
