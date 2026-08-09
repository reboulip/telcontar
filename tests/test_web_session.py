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

from host.agent import AgentEvent, ApprovalResult, AskUserResult, CostApprovalResult
from host.web import session as web_session
from host.web.bridge import AgentBridge, QueryBridge
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


def test_create_defaults_to_organize_mode(tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    assert session.mode == "organize"
    web_session.close(session.run_id)


def test_create_accepts_query_mode(tmp_path: Path) -> None:
    session = web_session.create(tmp_path, mode="query")
    assert session.mode == "query"
    web_session.close(session.run_id)


# ── RunSession: transcript building (turns only, T5) ─────────────────────────────


def test_add_turn_appends_to_transcript_only(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.add_turn("telcontar", "hello")

    assert [item.text for item in session.transcript] == ["hello"]
    assert session.steps == []


def test_seq_numbers_are_monotonic(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.add_turn("telcontar", "one")
    session.add_turn("telcontar", "two")
    assert [item.seq for item in session.transcript] == [1, 2]


def test_seq_is_shared_between_transcript_and_steps(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.add_turn("telcontar", "one")
    session.open_step("list_dir", "list_dir(path='.')")
    session.add_turn("telcontar", "two")

    assert [t.seq for t in session.transcript] == [1, 3]
    assert [s.seq for s in session.steps] == [2]


# ── RunSession: internal steps (T6) ───────────────────────────────────────────────


def test_open_step_appends_a_running_step(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)

    step = session.open_step("list_dir", "list_dir(path='.')", {"path": "."})

    assert session.steps == [step]
    assert step.tool == "list_dir"
    assert step.summary == "list_dir(path='.')"
    assert step.args == {"path": "."}
    assert step.status == "running"
    assert step.detail == ""


def test_close_step_sets_status_and_detail(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    step = session.open_step("list_dir", "list_dir(path='.')", {"path": "."})

    session.close_step({"entries": []}, ok=True)

    assert step.status == "ok"
    assert '"path": "."' in step.detail
    assert '"entries": []' in step.detail


def test_close_step_marks_error_status(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")

    session.close_step({"error": "Plan rejected by user."}, ok=False)

    assert session.steps[0].status == "error"


def test_close_step_without_open_step_is_a_noop(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)

    session.close_step({"ok": True}, ok=True)  # must not raise

    assert session.steps == []


def test_open_step_without_closing_previous_leaves_it_running(tmp_path: Path) -> None:
    """A step that never gets a matching tool_result (e.g. the run errored
    out mid-call) stays 'running' forever — the correct visual, not a bug."""
    session = RunSession(run_id="x", target=tmp_path)
    first = session.open_step("list_dir", "list_dir(a)")
    session.open_step("list_dir", "list_dir(b)")

    assert first.status == "running"
    assert len(session.steps) == 2


def test_close_step_truncates_oversized_detail(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    session.open_step("read_file_batch", "read_file_batch(2 files)")

    session.close_step({"a.txt": "x" * 30_000}, ok=True)

    detail = session.steps[0].detail
    assert len(detail) <= web_session._MAX_STEP_DETAIL_CHARS + len("\n… (truncated)")
    assert detail.endswith("(truncated)")


# ── AgentBridge.on_event ──────────────────────────────────────────────────────────


def test_on_event_tool_call_sets_activity_and_opens_step(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(
        AgentEvent(
            "tool_call",
            "list_dir(path='.')",
            data={"tool": "list_dir", "args": {"path": "."}},
        )
    )

    assert session.transcript == []
    assert "Scanning the directory" in session.activity
    assert len(session.steps) == 1
    assert session.steps[0].tool == "list_dir"
    assert session.steps[0].summary == "list_dir(path='.')"
    assert session.steps[0].args == {"path": "."}
    assert session.steps[0].status == "running"


def test_on_event_tool_result_closes_the_open_step(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(path='.')", data={"tool": "list_dir"}))
    bridge.on_event(AgentEvent("tool_result", "{'entries': []}", data={"result": {"entries": []}}))

    assert session.steps[0].status == "ok"
    assert "entries" in session.steps[0].detail


def test_on_event_tool_result_with_error_key_marks_step_error(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(
        AgentEvent("tool_call", "execute_plan(plan_id='p1')", data={"tool": "execute_plan"})
    )
    bridge.on_event(
        AgentEvent(
            "tool_result",
            "rejected",
            data={"result": {"error": "Plan rejected by user."}},
        )
    )

    assert session.steps[0].status == "error"


def test_on_event_consecutive_same_narration_sets_activity_once(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(a)", data={"tool": "list_dir"}))
    first_activity = session.activity
    bridge.on_event(AgentEvent("tool_call", "list_dir(b)", data={"tool": "list_dir"}))

    # Narrator collapses repeats — activity is unchanged, but both calls still
    # opened their own step.
    assert session.activity == first_activity
    assert len(session.steps) == 2


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


async def test_on_ask_user_needed_creates_an_ask_pending_and_resolves_via_future(
    tmp_path: Path,
) -> None:
    """V12: ask_user is a persistent, request-scoped PendingRequest like
    approval/cost above, not a plain-text question read from the chat queue."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(
        bridge.on_ask_user_needed([{"text": "Group by?", "options": ["date", "workstream"]}])
    )
    await asyncio.sleep(0)
    assert "Group by?" in session.transcript[-1].text

    assert session.pending is not None
    assert session.pending.kind == "ask"
    assert session.pending.payload["questions"][0]["text"] == "Group by?"

    session.resolve_pending(AskUserResult(reply="by date", provided=True))
    result = await task

    assert result.provided is True
    assert result.reply == "by date"


async def test_on_ask_user_needed_posts_exactly_one_user_turn(tmp_path: Path) -> None:
    """Regression guard for the double-post-on-answer bug (V12): the old
    design read the reply from session.messages — the same queue _send()
    (host/web/main.py) already posts a "user" turn into — so the reply was
    posted twice."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_ask_user_needed([{"text": "Group by?"}]))
    await asyncio.sleep(0)

    session.resolve_pending(AskUserResult(reply="by date", provided=True))
    await task

    user_turns = [item for item in session.transcript if item.speaker == "user"]
    assert len(user_turns) == 1
    assert user_turns[0].text == "by date"


async def test_on_ask_user_needed_never_reads_the_message_queue(tmp_path: Path) -> None:
    """The double-post fix is structural: on_ask_user_needed must not touch
    session.messages at all — that queue is for mid-run chat steering, not
    for ask_user answers, once the dialog owns resolution."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_ask_user_needed([{"text": "Group by?"}]))
    await asyncio.sleep(0)

    session.messages.put_nowait("a stray chat message, unrelated to this ask")
    session.resolve_pending(AskUserResult(reply="by date", provided=True))
    result = await task

    assert result.reply == "by date"
    assert session.messages.qsize() == 1  # never consumed by on_ask_user_needed


async def test_on_ask_user_needed_skip_posts_a_placeholder_turn(tmp_path: Path) -> None:
    """The dialog's "Skip" path resolves provided=False — the transcript
    should reflect that rather than posting an empty string as if the user
    had typed nothing."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    task = asyncio.create_task(bridge.on_ask_user_needed([{"text": "Group by?"}]))
    await asyncio.sleep(0)

    session.resolve_pending(AskUserResult(reply="", provided=False))
    result = await task

    assert result.provided is False
    user_turns = [item for item in session.transcript if item.speaker == "user"]
    assert len(user_turns) == 1
    assert user_turns[0].text == "(skipped)"


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

    assert (
        web_session.set_sidebar_width(web_session.SIDEBAR_WIDTH_MAX + 100)
        == web_session.SIDEBAR_WIDTH_MAX
    )
    assert web_session.get_sidebar_width() == web_session.SIDEBAR_WIDTH_MAX


def test_set_sidebar_width_accepts_exact_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_session, "_sidebar_width", web_session.SIDEBAR_WIDTH_DEFAULT)

    assert (
        web_session.set_sidebar_width(web_session.SIDEBAR_WIDTH_MIN)
        == web_session.SIDEBAR_WIDTH_MIN
    )
    assert (
        web_session.set_sidebar_width(web_session.SIDEBAR_WIDTH_MAX)
        == web_session.SIDEBAR_WIDTH_MAX
    )


# ── RunSession: close_step return value, fs_revision (U4) ────────────────────


def test_close_step_returns_the_closed_step(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    opened = session.open_step("execute_plan", "execute_plan(plan_id='p1')")

    closed = session.close_step({"ok": True}, ok=True)

    assert closed is opened
    assert closed.status == "ok"


def test_close_step_without_open_step_returns_none(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)

    assert session.close_step({"ok": True}, ok=True) is None


def test_bump_fs_revision_increments(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    assert session.fs_revision == 0

    session.bump_fs_revision()
    session.bump_fs_revision()

    assert session.fs_revision == 2


# ── RunSession: request-scoped resolve_pending (U4) ──────────────────────────


def test_resolve_pending_with_matching_request_id_resolves(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    async def _run() -> ApprovalResult:
        task = asyncio.create_task(bridge.on_approval_needed("plan-1", {"ops": []}))
        await asyncio.sleep(0)
        request_id = session.pending.request_id  # type: ignore[union-attr]
        session.resolve_pending(ApprovalResult(approved=True), request_id=request_id)
        return await task

    result = asyncio.run(_run())
    assert result.approved is True


def test_resolve_pending_with_stale_request_id_is_ignored(tmp_path: Path) -> None:
    """A stale dialog (another tab, or one left over after a reload) must
    not be able to resolve a *different* pending request than the one it
    was actually shown — a mismatched request_id is silently ignored."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    async def _run() -> None:
        task = asyncio.create_task(bridge.on_approval_needed("plan-1", {"ops": []}))
        await asyncio.sleep(0)

        session.resolve_pending(ApprovalResult(approved=True), request_id="stale-id")
        assert session.pending is not None  # ignored — still pending

        session.resolve_pending(ApprovalResult(approved=False))  # no request_id — always applies
        result = await task
        assert result.approved is False

    asyncio.run(_run())


# ── AgentBridge: fs_revision bump on tree-mutating tool results (U4) ─────────


def test_tool_result_bumps_fs_revision_for_execute_plan(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "execute_plan(...)", data={"tool": "execute_plan"}))
    bridge.on_event(AgentEvent("tool_result", "", data={"result": {"moved": 3}}))

    assert session.fs_revision == 1


def test_tool_result_does_not_bump_fs_revision_for_read_only_tools(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(...)", data={"tool": "list_dir"}))
    bridge.on_event(AgentEvent("tool_result", "", data={"result": {"entries": []}}))

    assert session.fs_revision == 0


def test_tool_result_does_not_bump_fs_revision_on_error(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = AgentBridge(session)

    bridge.on_event(AgentEvent("tool_call", "execute_plan(...)", data={"tool": "execute_plan"}))
    bridge.on_event(AgentEvent("tool_result", "", data={"result": {"error": "boom"}}))

    assert session.fs_revision == 0


# ── QueryBridge (U7) ──────────────────────────────────────────────────────────


def test_query_bridge_has_no_approval_cost_ask_user_callbacks(tmp_path: Path) -> None:
    """Query mode is read-only by construction (QUERY_ALLOWED_TOOLS) — the
    absence of these callbacks is itself the safety property, not a gap."""
    bridge = QueryBridge(RunSession(run_id="x", target=tmp_path))

    assert not hasattr(bridge, "on_approval_needed")
    assert not hasattr(bridge, "on_cost_approval_needed")
    assert not hasattr(bridge, "on_ask_user_needed")


def test_query_bridge_on_event_tool_call_result_open_close_steps(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)

    bridge.on_event(AgentEvent("tool_call", "list_dir(...)", data={"tool": "list_dir", "args": {}}))
    assert session.steps[-1].status == "running"

    bridge.on_event(AgentEvent("tool_result", "", data={"result": {"entries": []}}))
    assert session.steps[-1].status == "ok"


def test_query_bridge_on_event_done_does_not_set_session_done(tmp_path: Path) -> None:
    """run_query_loop emits a "done" AgentEvent *and* returns the answer —
    the TUI's QueryScreen renders from the return value only (no "done" case
    in its on_event), and this must match: handling "done" here too would
    render every answer twice, and done/error are per-question here, not
    per-session."""
    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)

    bridge.on_event(AgentEvent("done", "the answer"))

    assert session.done is False
    assert session.transcript == []


def test_query_bridge_on_event_error_does_not_set_session_done(tmp_path: Path) -> None:
    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)

    bridge.on_event(AgentEvent("error", "boom"))

    assert session.done is False
    assert session.status == "Error"


async def test_query_bridge_run_renders_answer_from_return_value_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    async def fake_run_query_loop(**kwargs: object) -> tuple[str, list]:
        on_event = kwargs["on_event"]
        on_event(AgentEvent("done", "the answer"))  # type: ignore[operator]
        return "the answer", [{"role": "assistant"}]

    monkeypatch.setattr("host.agent.run_query_loop", fake_run_query_loop)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)
    task = bridge.start()

    session.messages.put_nowait("what's in here?")
    await asyncio.sleep(0.05)

    answers = [t.text for t in session.transcript if t.text == "the answer"]
    assert answers == ["the answer"]  # rendered exactly once, from the return value
    assert session.status == "Ready — ask a question."

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_query_bridge_run_threads_same_ledger_across_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    async def fake_run_query_loop(**kwargs: object) -> tuple[str, list]:
        seen_ledgers.append(kwargs["ledger"])
        return "answer", []

    monkeypatch.setattr("host.agent.run_query_loop", fake_run_query_loop)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)
    task = bridge.start()

    session.messages.put_nowait("first question")
    await asyncio.sleep(0.05)
    session.messages.put_nowait("second question")
    await asyncio.sleep(0.05)

    assert len(seen_ledgers) == 2
    assert seen_ledgers[0] is seen_ledgers[1]

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_query_bridge_run_reports_config_error_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> None:
        raise RuntimeError("no endpoint configured")

    monkeypatch.setattr("config.settings.load", _raise)

    session = RunSession(run_id="x", target=tmp_path)
    bridge = QueryBridge(session)

    await bridge.run()

    assert "Config error" in session.transcript[-1].text
    assert session.done is False  # query-mode sessions never set .done
