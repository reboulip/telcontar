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


PendingKind = Literal["approval", "cost", "ask"]


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
    # "organize" (default) or "query" (U7) — one session type/registry for
    # both rather than a parallel QuerySession: query mode needs the exact
    # same add_turn/open_step/close_step/status/tokens primitives, and a
    # second dataclass would duplicate all of it. pending/progress simply
    # stay unused for query sessions.
    mode: Literal["organize", "query"] = "organize"
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
    # Bumped whenever a tree-mutating tool result closes (U4) — the sidebar
    # tree refresh and (U6) the journal strip both key their refresh off
    # this one counter rather than rebuilding on every render tick.
    fs_revision: int = 0
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

    def has_open_step(self) -> bool:
        """True while a tool call is still running (U6): undo must be
        blocked in this state — server.journal.pop_last rewrites the whole
        journal file while the MCP server subprocess may be appending to
        it, and racing them can silently drop audit records."""
        return self._open_step is not None

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

    def close_step(self, result: object, *, ok: bool) -> StepRecord | None:
        """Close the currently-open step (if any) with its tool result and
        return the closed StepRecord (None if none was open) — the caller
        (AgentBridge) uses ``.tool`` to decide whether this call mutated the
        tree and the sidebar/journal refresh counter should bump (U4).

        The detail payload pairs the call's args with its result — useful for
        seeing what was actually asked for, not just what came back — pretty-
        printed and capped at `_MAX_STEP_DETAIL_CHARS` (a batch read/extract
        result can be megabytes of document text; this is a display cap, not
        the egress cap `MAX_SNIPPET_CHARS` already enforces upstream).
        """
        step = self._open_step
        if step is None:
            return None
        payload = {"args": step.args, "result": result}
        detail = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        if len(detail) > _MAX_STEP_DETAIL_CHARS:
            detail = detail[:_MAX_STEP_DETAIL_CHARS] + "\n… (truncated)"
        step.status = "ok" if ok else "error"
        step.detail = detail
        self._open_step = None
        return step

    def bump_fs_revision(self) -> None:
        """Signal that the target directory's contents changed — consumed by
        the sidebar tree refresh (U4) and the journal strip refresh (U6)."""
        self.fs_revision += 1

    def new_pending(self, kind: PendingKind, payload: dict) -> PendingRequest:
        request = PendingRequest(
            request_id=secrets.token_urlsafe(8),
            kind=kind,
            payload=payload,
            future=asyncio.get_running_loop().create_future(),
        )
        self.pending = request
        return request

    def resolve_pending(self, result: object, *, request_id: str | None = None) -> None:
        """Resolve the current pending request's future, if any — safe to
        call more than once (e.g. a stale client retrying a click).

        ``request_id``, when given, must match the *current* pending
        request's id or the call is ignored — a stale dialog (another
        browser tab, or one left over after a reload) can otherwise resolve
        a request it was never shown, silently approving/rejecting the
        wrong plan. Optional so existing callers (the app-shutdown hook,
        which has no dialog and just wants to reject whatever is pending)
        keep working unchanged.
        """
        if self.pending is None or self.pending.future.done():
            return
        if request_id is not None and request_id != self.pending.request_id:
            return
        self.pending.future.set_result(result)
        self.pending = None


_SESSIONS: dict[str, RunSession] = {}


def create(target: Path, *, mode: Literal["organize", "query"] = "organize") -> RunSession:
    run_id = secrets.token_urlsafe(16)
    session = RunSession(run_id=run_id, target=target, mode=mode)
    _SESSIONS[run_id] = session
    return session


def get(run_id: str) -> RunSession | None:
    return _SESSIONS.get(run_id)


def close(run_id: str) -> None:
    _SESSIONS.pop(run_id, None)


def all_sessions() -> list[RunSession]:
    return list(_SESSIONS.values())


# ── Default target (T7 / U9 test-seam) ───────────────────────────────────────
#
# Set by run_web() before ui.run() starts; read by the landing page so a
# `telcontar --target DIR` launch skips straight to a run instead of
# showing the directory browser. None means "show the browser". Lives here
# rather than in host/web/main.py so it's patchable from tests: the NiceGUI
# `user` fixture runpy-executes main.py as a second, separate module object
# (run_name="__main__"), so a patch on host.web.main.* never reaches the page
# code that fixture drives — host.web.session stays the one cached module
# both the test and the runpy copy of main.py actually share.

_default_target: Path | None = None


def set_default_target(target: Path | None) -> None:
    global _default_target
    _default_target = target


def get_default_target() -> Path | None:
    return _default_target


# ── Render refresh interval (U9 test-seam) ──────────────────────────────────
#
# Read by run_page's ui.timer at page-build time instead of a hardcoded
# literal, so tests can shrink it: the `user` fixture's should_see() retries
# 3x over ~0.3s total, which races the real UI's 0.5s poll cadence and either
# times out or forces every assertion to raise its retry count.

REFRESH_INTERVAL: float = 0.5


# ── Sidebar tree poll interval (V7 test-seam) ───────────────────────────────
#
# Read by app_shell's ui.timer at mount time instead of a hardcoded literal —
# same test-seam reasoning as REFRESH_INTERVAL above. rebuild_nodes recurses
# over every expanded directory, so this is deliberately much coarser than
# REFRESH_INTERVAL: a live tree poll is a nice-to-have, not something that
# needs sub-second latency, and polling faster buys nothing a human would
# notice while risking a real I/O cliff on a deeply-expanded corpus.

TREE_POLL_INTERVAL: float = 5.0


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
