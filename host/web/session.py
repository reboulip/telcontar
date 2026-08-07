"""Per-run web session state — no NiceGUI import.

Framework-agnostic on purpose: unit-testable in plain pytest, and this is the
data a page (host/web/main.py) polls and mutates, not the module that decides
how anything is drawn. A page reload creates a new NiceGUI client but reuses
the *same* RunSession (looked up by run_id from the URL) — this is what makes
reconnect work: a pending approval/cost future outlives any one client.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from host.narration import Narrator

TranscriptKind = Literal["turn", "steps"]


@dataclass
class TranscriptItem:
    seq: int
    kind: TranscriptKind
    speaker: str
    text: str
    lines: list[str] = field(default_factory=list)


PendingKind = Literal["approval", "cost"]


@dataclass
class PendingRequest:
    request_id: str
    kind: PendingKind
    payload: dict
    future: asyncio.Future


@dataclass
class RunSession:
    run_id: str
    target: Path
    transcript: list[TranscriptItem] = field(default_factory=list)
    status: str = "Initialising…"
    tokens: str = ""
    progress: dict = field(default_factory=dict)
    done: bool = False
    started: bool = False
    error: str | None = None
    pending: PendingRequest | None = None
    messages: asyncio.Queue = field(default_factory=asyncio.Queue)
    history: list[dict] | None = None
    narrator: Narrator = field(default_factory=Narrator)
    task: asyncio.Task | None = None
    _steps_item: TranscriptItem | None = field(default=None, repr=False)
    _seq: int = field(default=0, repr=False)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add_turn(self, speaker: str, text: str) -> None:
        """Append a speaker-tagged turn, closing any open internal-steps group —
        mirrors OrganizerScreen._add_turn."""
        self._steps_item = None
        self.transcript.append(TranscriptItem(self._next_seq(), "turn", speaker, text))

    def append_step(self, line: str) -> None:
        """Append a raw tool line to the current internal-steps group, opening
        one if none is open — mirrors OrganizerScreen._append_step."""
        if self._steps_item is None:
            self._steps_item = TranscriptItem(self._next_seq(), "steps", "telcontar", "", [])
            self.transcript.append(self._steps_item)
        self._steps_item.lines.append(line)
        self._steps_item.text = "\n".join(self._steps_item.lines)

    def new_pending(self, kind: PendingKind, payload: dict) -> PendingRequest:
        request = PendingRequest(
            request_id=secrets.token_urlsafe(8),
            kind=kind,
            payload=payload,
            future=asyncio.get_running_loop().create_future(),
        )
        self.pending = request
        return request

    def resolve_pending(self, result: object) -> None:
        """Resolve the current pending request's future, if any — safe to call
        more than once (e.g. a stale client retrying a click)."""
        if self.pending is not None and not self.pending.future.done():
            self.pending.future.set_result(result)
        self.pending = None


_SESSIONS: dict[str, RunSession] = {}


def create(target: Path) -> RunSession:
    run_id = secrets.token_urlsafe(16)
    session = RunSession(run_id=run_id, target=target)
    _SESSIONS[run_id] = session
    return session


def get(run_id: str) -> RunSession | None:
    return _SESSIONS.get(run_id)


def close(run_id: str) -> None:
    _SESSIONS.pop(run_id, None)


def all_sessions() -> list[RunSession]:
    return list(_SESSIONS.values())


# ── Sidebar width (T4) ───────────────────────────────────────────────────────
#
# One in-memory preference for the process's lifetime rather than a field on
# RunSession: it needs to apply on the picker route too, where no RunSession
# exists yet, and telcontar is a single-user local tool — there's no other
# viewer whose preference it could clobber.

SIDEBAR_WIDTH_DEFAULT = 380
SIDEBAR_WIDTH_MIN = 240
SIDEBAR_WIDTH_MAX = 720

_sidebar_width = SIDEBAR_WIDTH_DEFAULT


def get_sidebar_width() -> int:
    return _sidebar_width


def set_sidebar_width(width: int) -> int:
    """Clamp ``width`` to [SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX], persist it,
    and return the clamped value actually stored."""
    global _sidebar_width
    _sidebar_width = max(SIDEBAR_WIDTH_MIN, min(SIDEBAR_WIDTH_MAX, width))
    return _sidebar_width
