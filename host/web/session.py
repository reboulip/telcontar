"""Per-run web session state — no NiceGUI import.

Framework-agnostic on purpose: unit-testable in plain pytest, and this is the
data a page (host/web/main.py) polls and mutates, not the module that decides
how anything is drawn. A page reload creates a new NiceGUI client but reuses
the *same* RunSession (looked up by run_id from the URL) — this is what makes
reconnect work: a pending approval/cost future outlives any one client.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from host.narration import Narrator

# ── Conversation (T5) ────────────────────────────────────────────────────────
#
# Turns only, now — genuine user<->telcontar exchanges (chat, ask_user,
# approval/cost outcomes, done/error). Tool activity lives in `steps` below
# instead of being interleaved here as a "steps"-kind item.


@dataclass
class TranscriptItem:
    seq: int
    speaker: str
    text: str


# ── Internal steps / log stream (T6) ─────────────────────────────────────────

StepStatus = Literal["running", "ok", "error"]

# Detail-payload cap: a read_file_batch/extract_text_batch result can be
# megabytes of document text — never hold or render that unbounded, even
# though it's only ever displayed, not executed.
_MAX_STEP_DETAIL_CHARS = 20_000


@dataclass
class StepRecord:
    seq: int
    tool: str
    summary: str
    args: dict = field(default_factory=dict)
    detail: str = ""
    status: StepStatus = "running"


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
    steps: list[StepRecord] = field(default_factory=list)
    activity: str = ""
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
    _open_step: StepRecord | None = field(default=None, repr=False)
    _seq: int = field(default=0, repr=False)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add_turn(self, speaker: str, text: str) -> None:
        """Append a speaker-tagged turn — a genuine user<->telcontar exchange,
        never telcontar's own tool activity (that's `open_step`/`close_step`
        below, rendered in the separate log zone, T5)."""
        self.transcript.append(TranscriptItem(self._next_seq(), speaker, text))

    def open_step(self, tool: str, summary: str, args: dict | None = None) -> StepRecord:
        """Start a new log-stream entry for one tool call (T6). Any
        previously-open step is left as-is — a step that never got closed
        (e.g. the run errored out mid-call) stays "running" forever, which is
        the correct visual, not a bug: it shows exactly where things stopped.
        """
        step = StepRecord(self._next_seq(), tool, summary, args=dict(args or {}))
        self.steps.append(step)
        self._open_step = step
        return step

    def close_step(self, result: object, *, ok: bool) -> None:
        """Close the currently-open step (if any) with its tool result.

        The detail payload pairs the call's args with its result — useful for
        seeing what was actually asked for, not just what came back — pretty-
        printed and capped at `_MAX_STEP_DETAIL_CHARS` (a batch read/extract
        result can be megabytes of document text; this is a display cap, not
        the egress cap `MAX_SNIPPET_CHARS` already enforces upstream).
        """
        step = self._open_step
        if step is None:
            return
        payload = {"args": step.args, "result": result}
        detail = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        if len(detail) > _MAX_STEP_DETAIL_CHARS:
            detail = detail[:_MAX_STEP_DETAIL_CHARS] + "\n… (truncated)"
        step.status = "ok" if ok else "error"
        step.detail = detail
        self._open_step = None

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
