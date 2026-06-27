"""Project event journal — a verb-led, dated narrative of project events.

Distinct from the undo journal (``server/journal.py``, which records reversible
file operations and drives ``undo_last``). The event journal is append-only JSONL
at ``events_path`` (default ``.organizer/events.jsonl``); each entry is one short,
verb-led sentence stamped with the date the event occurred. It feeds the
knowledge graph and the project synthesis.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Event:
    """One project event: a verb-led sentence and the date it occurred.

    ``date`` is the ISO ``YYYY-MM-DD`` the event happened (null if unknown);
    ``created_at`` is when the event was recorded (full ISO timestamp).
    """

    event_id: str
    sentence: str
    date: str | None
    created_at: str

    @classmethod
    def new(cls, sentence: str, date: str | None = None) -> "Event":
        return cls(
            event_id=str(uuid.uuid4()),
            sentence=sentence,
            date=date,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            event_id=d.get("event_id", str(uuid.uuid4())),
            sentence=d["sentence"],
            date=d.get("date"),
            created_at=d.get("created_at", ""),
        )


def append(events_path: Path, event: Event) -> None:
    """Append a single event to the journal; creates parent dirs if needed."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def all_events(events_path: Path) -> list[Event]:
    """Return all events in chronological (append) order; empty if no file."""
    if not events_path.is_file():
        return []
    return [
        Event.from_dict(json.loads(ln))
        for ln in events_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
