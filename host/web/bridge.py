"""AgentEvent -> RunSession state, the three awaited callbacks, and the run
driver — no NiceGUI import, so this is unit-testable in plain pytest against
a real host.agent.run_agent_loop. Also QueryBridge (U7), the same shape for
query-mode sessions, driving run_query_loop instead.

Deliberately kept NiceGUI-free: it only mutates the framework-agnostic
RunSession/TranscriptItem data. host/web/main.py is the only module that
turns this state into DOM elements — this is what keeps a page reload safe
(see RunSession's docstring) and lets this module be tested without a
browser, an event loop trick, or a running NiceGUI server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from host.agent import (
    AgentEvent,
    ApprovalResult,
    AskUserResult,
    CostApprovalResult,
)
from host.format import fmt_exc
from host.web.session import RunSession

# Tools whose successful result changes what's on disk under the target
# directory — closing one of these bumps RunSession.fs_revision (U4), which
# drives both the sidebar tree refresh and (U6) the journal strip refresh.
_TREE_MUTATING_TOOLS = frozenset(
    {"execute_plan", "write_index", "write_summary", "write_folder_readme"}
)


class AgentBridge:
    """Bound callbacks for one RunSession, mirroring
    host.app.OrganizerScreen's on_event/on_approval_needed/on_ask_user_needed/
    on_cost_approval_needed, plus the run driver those callbacks are wired
    into (OrganizerScreen._agent_worker's counterpart)."""

    def __init__(self, session: RunSession) -> None:
        self.session = session

    def on_event(self, event: AgentEvent) -> None:
        session = self.session
        match event.kind:
            case "thinking":
                session.status = event.text
            case "tool_call":
                # Narration becomes the log zone's "current activity" line
                # (T5) — never a chat turn; that was the "telcontar talking
                # to itself in bubbles" T5 was written to fix.
                tool = (event.data or {}).get("tool", "")
                phrase = session.narrator.narrate(tool)
                if phrase:
                    session.activity = phrase
                args = (event.data or {}).get("args") or {}
                session.open_step(tool, event.text, args)
            case "tool_result":
                result = (event.data or {}).get("result")
                ok = not (isinstance(result, dict) and "error" in result)
                step = session.close_step(result, ok=ok)
                if step is not None and ok and step.tool in _TREE_MUTATING_TOOLS:
                    session.bump_fs_revision()
            case "plan_ready":
                session.status = "Waiting for plan approval…"
            case "ask_user":
                session.status = "Awaiting your reply…"
            case "cost_estimate":
                session.status = "Awaiting cost approval…"
            case "progress":
                session.progress = event.data or {}
            case "tokens":
                session.tokens = event.text
            case "done":
                session.add_turn("telcontar", f"✓ Done\n{event.text}")
                session.status = "Done"
                session.progress = {}
                session.done = True
            case "warning":
                # Non-terminal — e.g. a single analysis batch failed and was
                # skipped, but the run continues. Never touch status/progress
                # or session.done here.
                session.add_turn("telcontar", f"⚠ {event.text}")
            case "error":
                session.add_turn("telcontar", f"✗ {event.text}")
                session.status = "Error"
                session.progress = {}
                session.done = True

    async def on_approval_needed(self, plan_id: str, plan_data: dict) -> ApprovalResult:
        session = self.session
        session.add_turn(
            "telcontar",
            f"Plan ready for review ({len(plan_data.get('ops', []))} op(s)) — awaiting approval…",
        )
        pending = session.new_pending("approval", {"plan_id": plan_id, "plan_data": plan_data})
        result: ApprovalResult = await pending.future
        if result.approved:
            removed = len(result.removed_op_ids)
            session.add_turn(
                "user", "Approved" + (f"  ({removed} op(s) removed)" if removed else "")
            )
        elif result.refinement:
            session.add_turn("user", f"Refine: {result.refinement}")
        else:
            session.add_turn("user", "Rejected — sending feedback to agent")
        return result

    async def on_cost_approval_needed(self, summary: str, data: dict) -> CostApprovalResult:
        session = self.session
        session.add_turn("telcontar", f"Cost estimate: {summary}")
        pending = session.new_pending("cost", {"summary": summary, "data": data})
        result: CostApprovalResult = await pending.future
        session.add_turn("user", "Proceed" if result.approved else "Cancelled")
        return result

    async def on_ask_user_needed(self, questions: list[dict]) -> AskUserResult:
        """Structured ask_user dialog (V12) — a persistent, request-scoped
        PendingRequest like on_approval_needed/on_cost_approval_needed above,
        not the old plain-text-in-transcript-then-read-the-chat-queue design.

        This is also the fix for the double-post-on-answer bug: the old
        version read the reply from `session.messages` — the same queue
        `_send()` (host/web/main.py) already posts the user's typed text
        into as a "user" transcript turn, so the reply got posted twice.
        Awaiting the pending's future exclusively means this method never
        touches `session.messages` at all, so there is only one place left
        (right below) that can post the reply as a turn.
        """
        session = self.session
        summary = "; ".join(q.get("text", "") for q in questions)
        session.add_turn("telcontar", f"I have a question for you: {summary}")
        pending = session.new_pending("ask", {"questions": questions})
        result: AskUserResult = await pending.future
        session.add_turn("user", result.reply if result.provided else "(skipped)")
        return result

    def start(self, instructions: str | None = None) -> asyncio.Task:
        """Kick off the run as a detached task owned by the RunSession, not by
        any NiceGUI client — the task must keep running across a page
        reload/close, per the reconnect design."""
        self.session.started = True
        task = asyncio.create_task(self.run(instructions))
        self.session.task = task
        return task

    async def run(self, instructions: str | None = None) -> None:
        """Drive one full organize run against self.session: settings load
        through the run_agent_loop continuation loop. A near-verbatim port of
        OrganizerScreen._agent_worker onto a plain asyncio.Task. ``instructions``
        is the user's optional pre-analysis steering text (L3), only meaningful
        on the first call."""
        from config.settings import load as load_settings
        from host.agent import _TokenLedger, mcp_session, run_agent_loop
        from host.llm import make_client

        session = self.session
        try:
            settings = load_settings().for_target(session.target)
        except Exception as exc:
            session.add_turn("telcontar", f"Config error: {fmt_exc(exc)}")
            session.status = "Error — check settings"
            session.done = True
            return

        llm = make_client(settings)
        # One ledger for the whole session's lifetime (R1, GH #27) — threaded
        # through every run_agent_loop call below, initial and follow-up
        # alike, so running token totals persist across chat turns instead of
        # resetting on each call.
        ledger = _TokenLedger.new(settings)
        project_root = Path(__file__).resolve().parent.parent.parent

        try:
            async with mcp_session(project_root, target=session.target) as mcp:
                _summary, session.history = await run_agent_loop(
                    target=session.target,
                    settings=settings,
                    llm=llm,
                    session=mcp,
                    on_event=self.on_event,
                    on_approval_needed=self.on_approval_needed,
                    on_ask_user_needed=self.on_ask_user_needed,
                    on_cost_approval_needed=self.on_cost_approval_needed,
                    project_root=project_root,
                    instructions=instructions,
                    message_queue=session.messages,
                    ledger=ledger,
                )

                # A run_agent_loop call only returns once the agent has fully
                # finished AND no chat message was waiting at that instant —
                # any message that arrives after that point resumes the same
                # session via history/message (O7), still with the queue
                # wired in so a later continuation stays just as live.
                while True:
                    message = await session.messages.get()
                    _summary, session.history = await run_agent_loop(
                        target=session.target,
                        settings=settings,
                        llm=llm,
                        session=mcp,
                        on_event=self.on_event,
                        on_approval_needed=self.on_approval_needed,
                        on_ask_user_needed=self.on_ask_user_needed,
                        on_cost_approval_needed=self.on_cost_approval_needed,
                        project_root=project_root,
                        history=session.history,
                        message=message,
                        message_queue=session.messages,
                        ledger=ledger,
                    )
        except Exception as exc:
            session.add_turn("telcontar", f"Agent error: {fmt_exc(exc)}")
            session.status = "Error"
            session.done = True


class QueryBridge:
    """Bound callback + run driver for one query-mode RunSession, mirroring
    AgentBridge but driving host.agent.run_query_loop instead of
    run_agent_loop (U7). Query mode is read-only by construction
    (QUERY_ALLOWED_TOOLS) — there is no on_approval_needed/
    on_cost_approval_needed/on_ask_user_needed here; their absence is
    itself the safety property, not a gap to fill in later.
    """

    def __init__(self, session: RunSession) -> None:
        self.session = session

    def on_event(self, event: AgentEvent) -> None:
        session = self.session
        match event.kind:
            case "thinking":
                session.status = event.text
            case "tool_call":
                tool = (event.data or {}).get("tool", "")
                args = (event.data or {}).get("args") or {}
                session.open_step(tool, event.text, args)
            case "tool_result":
                result = (event.data or {}).get("result")
                ok = not (isinstance(result, dict) and "error" in result)
                session.close_step(result, ok=ok)
            case "tokens":
                session.tokens = event.text
            case "warning":
                session.add_turn("telcontar", f"⚠ {event.text}")
            case "error":
                session.add_turn("telcontar", f"✗ {event.text}")
                session.status = "Error"
            # "done" is deliberately not handled: run_query_loop both emits
            # it and returns the answer text, and the TUI's QueryScreen
            # renders from the return value only (host/app.py's QueryScreen
            # on_event has no "done" case either) — handling it here too
            # would render every answer twice. done/error here are also
            # per-question, not per-session: never set session.done.

    def start(self) -> asyncio.Task:
        """Kick off the query worker immediately (TUI parity: QueryScreen
        starts its worker in on_mount, no explicit "start" button)."""
        self.session.started = True
        task = asyncio.create_task(self.run())
        self.session.task = task
        return task

    async def run(self) -> None:
        """Near-verbatim port of QueryScreen._query_worker onto a plain
        asyncio.Task: one MCP session and one _TokenLedger for the whole
        chat, threading history across questions for multi-turn context."""
        from config.settings import load as load_settings
        from host.agent import _TokenLedger, mcp_session, run_query_loop
        from host.llm import make_client

        session = self.session
        try:
            settings = load_settings().for_target(session.target)
        except Exception as exc:
            session.add_turn("telcontar", f"Config error: {fmt_exc(exc)}")
            session.status = "Error — check settings"
            return

        llm = make_client(settings)
        ledger = _TokenLedger.new(settings)
        project_root = Path(__file__).resolve().parent.parent.parent

        try:
            async with mcp_session(project_root, target=session.target) as mcp:
                session.status = "Ready — ask a question."
                while True:
                    question = await session.messages.get()
                    session.status = "Thinking…"
                    answer, session.history = await run_query_loop(
                        question=question,
                        settings=settings,
                        llm=llm,
                        session=mcp,
                        on_event=self.on_event,
                        history=session.history,
                        project_root=project_root,
                        ledger=ledger,
                    )
                    session.add_turn("telcontar", answer)
                    session.status = "Ready — ask a question."
        except Exception as exc:
            session.add_turn("telcontar", f"Query error: {fmt_exc(exc)}")
            session.status = "Error"
