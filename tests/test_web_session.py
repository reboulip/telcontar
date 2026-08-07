"""Tests for host/web/session.py and host/web/bridge.py — no NiceGUI, no
browser: these two modules are deliberately framework-agnostic (see
bridge.py's module docstring), so they're testable in plain pytest.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from host.agent import AgentEvent, ApprovalResult, CostApprovalResult
from host.web import session as web_session
from host.web.bridge import AgentBridge
from host.web.session import RunSession

# ── RunSession: registry ────────────────────────────────────────────────────────


def test_create_get_close_roundtrip(tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    assert web_session.get(session.run_id) is session
    web_session.close(session.run_id)
    assert web_session.get(session.run_id) is None


def test_create_assigns_unique_run_ids(tmp_path: Path) -> None:
    a = web_session.create(tmp_path)
    b = web_session.create(tmp_path)
    assert a.run_id != b.run_id
    web_session.close(a.run_id)
    web_session.close(b.run_id)


def test_get_unknown_run_id_returns_none() -> None:
    assert web_session.get("does-not-exist") is None


# ── RunSession: transcript building ──────────────────────────────────────────────


def test_add_turn_closes_open_steps_group(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.append_step("a")
    session.append_step("b")
    session.add_turn("telcontar", "done")
    session.append_step("c")

    assert [item.kind for item in session.transcript] == ["steps", "turn", "steps"]
    assert session.transcript[0].text == "a\nb"
    assert session.transcript[2].text == "c"


def test_seq_numbers_are_monotonic(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.add_turn("telcontar", "one")
    session.add_turn("telcontar", "two")
    assert [item.seq for item in session.transcript] == [1, 2]


# ── AgentBridge.on_event ──────────────────────────────────────────────────────────


def test_on_event_tool_call_narrates_then_appends_step(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(path='.')", data={"tool": "list_dir"}))

    assert session.transcript[0].kind == "turn"
    assert "Scanning the directory" in session.transcript[0].text
    assert session.transcript[1].kind == "steps"
    assert "list_dir" in session.transcript[1].text


def test_on_event_consecutive_same_narration_collapses(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(a)", data={"tool": "list_dir"}))
    bridge.on_event(AgentEvent("tool_call", "list_dir(b)", data={"tool": "list_dir"}))

    turns = [item for item in session.transcript if item.kind == "turn"]
    assert len(turns) == 1


def test_on_event_progress_updates_session_progress(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("progress", "3 / 10", data={"analyzed": 3, "total": 10}))

    assert session.progress == {"analyzed": 3, "total": 10}


def test_on_event_done_sets_terminal_state(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("progress", "3 / 10", data={"analyzed": 3, "total": 10}))
    bridge.on_event(AgentEvent("done", "All done."))

    assert session.done is True
    assert session.status == "Done"
    assert session.progress == {}
    assert "All done." in session.transcript[-1].text


def test_on_event_error_sets_terminal_state(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("error", "boom"))

    assert session.done is True
    assert session.status == "Error"


# ── AgentBridge: approval / cost / ask_user callbacks ────────────────────────────


async def test_on_approval_needed_resolves_via_pending_future(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_approval_needed("plan-1", {"ops": [{"op_type": "move"}]}))
    await asyncio.sleep(0)
    assert session.pending is not None
    assert session.pending.kind == "approval"

    session.resolve_pending(ApprovalResult(approved=True))
    result = await task

    assert result.approved is True
    assert session.pending is None
    assert "Approved" in session.transcript[-1].text


async def test_on_approval_needed_reconnect_resolves_through_second_attach(
    tmp_path: Path,
) -> None:
    """Regression test for the Stage-0 spike's deadlock finding (ROADMAP.md
    Break 1): a pending approval must be resolvable by whatever client is
    currently attached to the session, not only the one that was there when
    the request was made — a reload discards the first "page" without
    resolving anything, and a second page load must still be able to answer
    the same pending request."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_approval_needed("plan-1", {"ops": []}))
    await asyncio.sleep(0)
    first_request_id = session.pending.request_id if session.pending else None

    # First "page" is discarded (a bare reload) without resolving anything —
    # the pending request must still be there, unresolved, for whoever loads next.
    assert session.pending is not None
    assert session.pending.request_id == first_request_id

    # A second "page load" re-attaches to the *same* pending request and
    # resolves it — this is the reconnect path, exercised with no NiceGUI at all.
    session.resolve_pending(ApprovalResult(approved=False, refinement="try again"))
    result = await task

    assert result.approved is False
    assert result.refinement == "try again"


async def test_resolve_pending_twice_is_a_noop(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_cost_approval_needed("est", {}))
    await asyncio.sleep(0)

    session.resolve_pending(CostApprovalResult(approved=True))
    session.resolve_pending(CostApprovalResult(approved=False))  # must not raise
    result = await task

    assert result.approved is True


async def test_pending_future_never_auto_resolves(tmp_path: Path) -> None:
    """A disconnected/absent client must never auto-resolve a pending
    approval — silent auto-approval would be a safety hole. Blocking forever
    is the correct failure mode until something explicitly resolves it."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_approval_needed("plan-1", {"ops": []}))
    await asyncio.sleep(0)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
    assert session.pending is not None

    session.resolve_pending(ApprovalResult(approved=True))
    await task  # let the task finish cleanly


async def test_on_ask_user_needed_reads_reply_from_message_queue(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(
        bridge.on_ask_user_needed([{"text": "Group by?", "options": ["date", "workstream"]}])
    )
    await asyncio.sleep(0)
    assert "Group by?" in session.transcript[-1].text

    session.messages.put_nowait("by date")
    result = await task

    assert result.provided is True
    assert result.reply == "by date"


# ── AgentBridge.run: settings/ledger/queue threading ─────────────────────────────


async def test_run_threads_same_ledger_and_queue_across_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 (GH #27) regression, ported to the web driver: a follow-up chat
    message must reuse the same _TokenLedger and message_queue as the initial
    call, or token totals reset and mid-run chat breaks."""
    from contextlib import asynccontextmanager

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    @asynccontextmanager
    async def fake_mcp_session(
        project_root: Path, target: Path | None = None
    ) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)

    seen_ledgers: list[object] = []
    seen_queues: list[object] = []

    async def fake_run_agent_loop(**kwargs: object) -> tuple[str, list]:
        seen_ledgers.append(kwargs["ledger"])
        seen_queues.append(kwargs["message_queue"])
        on_event = kwargs["on_event"]
        if kwargs.get("history") is None:
            on_event(AgentEvent("done", "first"))  # type: ignore[operator]
            return "first", [{"role": "assistant"}]
        on_event(AgentEvent("done", "second"))  # type: ignore[operator]
        return "second", [*kwargs["history"], {"role": "assistant"}]  # type: ignore[misc]

    monkeypatch.setattr("host.agent.run_agent_loop", fake_run_agent_loop)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)
    task = bridge.start()

    await asyncio.sleep(0.05)
    assert not task.done()  # parked on session.messages.get() for a continuation
    assert session.status == "Done"

    # The driver's continuation loop never returns on its own (by design — it
    # waits indefinitely for the *next* chat message, same as the TUI's
    # worker) — so we check state after the continuation runs, then cancel
    # the still-running task ourselves rather than awaiting it to finish.
    session.messages.put_nowait("follow up")
    await asyncio.sleep(0.05)

    assert len(seen_ledgers) == 2
    assert seen_ledgers[0] is seen_ledgers[1]
    assert len(seen_queues) == 2
    assert seen_queues[0] is seen_queues[1] is session.messages

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_start_passes_instructions_only_on_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S5: the starter pane's steering-instructions box must reach the first
    run_agent_loop call (L3) but never a continuation — same contract as the
    TUI's OrganizerScreen._agent_worker(instructions=...)."""
    from contextlib import asynccontextmanager

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    @asynccontextmanager
    async def fake_mcp_session(
        project_root: Path, target: Path | None = None
    ) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)

    seen_instructions: list[object] = []

    async def fake_run_agent_loop(**kwargs: object) -> tuple[str, list]:
        seen_instructions.append(kwargs.get("instructions"))
        on_event = kwargs["on_event"]
        if kwargs.get("history") is None:
            on_event(AgentEvent("done", "first"))  # type: ignore[operator]
            return "first", [{"role": "assistant"}]
        on_event(AgentEvent("done", "second"))  # type: ignore[operator]
        return "second", [*kwargs["history"], {"role": "assistant"}]  # type: ignore[misc]

    monkeypatch.setattr("host.agent.run_agent_loop", fake_run_agent_loop)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)
    task = bridge.start(instructions="group by workstream")

    await asyncio.sleep(0.05)
    session.messages.put_nowait("follow up")
    await asyncio.sleep(0.05)

    assert seen_instructions == ["group by workstream", None]

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_run_reports_config_error_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> None:
        raise RuntimeError("no endpoint configured")

    monkeypatch.setattr("config.settings.load", _raise)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    await bridge.run()

    assert session.done is True
    assert "Config error" in session.transcript[-1].text


# ── Sidebar width (T4) ───────────────────────────────────────────────────────


def test_get_sidebar_width_defaults_to_380(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert web_session.get_sidebar_width() == 380


def test_set_sidebar_width_persists_within_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert web_session.set_sidebar_width(500) == 500
    assert web_session.get_sidebar_width() == 500


def test_set_sidebar_width_clamps_below_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert web_session.set_sidebar_width(100) == web_session.SIDEBAR_WIDTH_MIN
    assert web_session.get_sidebar_width() == web_session.SIDEBAR_WIDTH_MIN


def test_set_sidebar_width_clamps_above_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert web_session.set_sidebar_width(900) == web_session.SIDEBAR_WIDTH_MAX
    assert web_session.get_sidebar_width() == web_session.SIDEBAR_WIDTH_MAX


def test_set_sidebar_width_accepts_exact_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert web_session.set_sidebar_width(web_session.SIDEBAR_WIDTH_MIN) == 240
    assert web_session.set_sidebar_width(web_session.SIDEBAR_WIDTH_MAX) == 720
