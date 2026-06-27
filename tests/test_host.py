"""Tests for host/agent.py — agent loop and plan approval gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock


from host.agent import AgentEvent, ApprovalResult, _extract_content, run_agent_loop

# ── Mock builders ─────────────────────────────────────────────────────────────


def _mcp_result(data: Any) -> MagicMock:
    """CallToolResult mock with one TextContent holding JSON-encoded data."""
    content = MagicMock()
    content.text = json.dumps(data)
    result = MagicMock()
    result.content = [content]
    return result


def _list_tools(names: list[str]) -> MagicMock:
    tools = []
    for name in names:
        t = MagicMock()
        t.name = name
        t.description = f"mock {name}"
        t.inputSchema = {"type": "object", "properties": {}}
        tools.append(t)
    r = MagicMock()
    r.tools = tools
    return r


def _text_response(text: str) -> MagicMock:
    """LLM response with no tool calls — signals agent is done."""
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = text
    msg.model_dump.return_value = {"role": "assistant", "content": text}
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _tool_response(name: str, args: dict, call_id: str = "tc1") -> MagicMock:
    """LLM response with a single tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None
    msg.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}],
    }
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _session(tool_names: list[str], call_results: dict[str, Any]) -> AsyncMock:
    s = AsyncMock()
    s.list_tools.return_value = _list_tools(tool_names)

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        return _mcp_result(call_results.get(name, {"ok": True}))

    s.call_tool.side_effect = _call
    return s


def _llm(*responses: MagicMock) -> AsyncMock:
    m = AsyncMock()
    m.chat.completions.create.side_effect = list(responses)
    return m


def _settings(plans_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.llm_model = "gpt-5"
    cfg.plans_dir = plans_dir
    return cfg


async def _run(
    tmp_path: Path,
    *,
    tool_names: list[str],
    call_results: dict[str, Any],
    llm_responses: list[MagicMock],
    on_approval_needed: AsyncMock | None = None,
    on_event: Any = None,
    plans_dir: Path | None = None,
) -> str:
    if plans_dir is None:
        plans_dir = tmp_path
    return await run_agent_loop(
        target=tmp_path,
        settings=_settings(plans_dir),
        llm=_llm(*llm_responses),
        session=_session(tool_names, call_results),
        on_event=on_event or (lambda _: None),
        on_approval_needed=on_approval_needed or AsyncMock(return_value=ApprovalResult(True)),
    )


# ── Loop termination ──────────────────────────────────────────────────────────


async def test_terminates_on_text_response(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    result = await _run(
        tmp_path,
        tool_names=["list_dir"],
        call_results={},
        llm_responses=[_text_response("All done!")],
        on_event=events.append,
    )
    assert result == "All done!"
    assert any(e.kind == "done" and e.text == "All done!" for e in events)


# ── Regular tool forwarding ───────────────────────────────────────────────────


async def test_tool_call_forwarded_to_mcp_session(tmp_path: Path) -> None:
    s = _session(["list_dir"], {"list_dir": {"entries": []}})
    events: list[AgentEvent] = []

    await run_agent_loop(
        target=tmp_path,
        settings=_settings(tmp_path),
        llm=_llm(
            _tool_response("list_dir", {"path": str(tmp_path)}),
            _text_response("Found nothing."),
        ),
        session=s,
        on_event=events.append,
        on_approval_needed=AsyncMock(return_value=ApprovalResult(True)),
    )

    s.call_tool.assert_any_call("list_dir", {"path": str(tmp_path)})
    assert any("list_dir" in e.text for e in events if e.kind == "tool_call")


# ── execute_plan interception ─────────────────────────────────────────────────


async def test_execute_plan_triggers_approval_callback(tmp_path: Path) -> None:
    plan_data = {"plan_id": "abc", "ops": [], "state": "pending"}
    on_approval = AsyncMock(return_value=ApprovalResult(approved=True))

    s = AsyncMock()
    s.list_tools.return_value = _list_tools(["execute_plan", "get_plan", "approve_plan"])

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        return _mcp_result(plan_data if name == "get_plan" else {"ok": True})

    s.call_tool.side_effect = _call

    await run_agent_loop(
        target=tmp_path,
        settings=_settings(tmp_path),
        llm=_llm(_tool_response("execute_plan", {"plan_id": "abc"}), _text_response("Done.")),
        session=s,
        on_event=lambda _: None,
        on_approval_needed=on_approval,
    )

    on_approval.assert_called_once()
    assert on_approval.call_args[0][0] == "abc"


async def test_rejected_plan_sends_error_to_llm_and_skips_execution(tmp_path: Path) -> None:
    plan_data = {"plan_id": "xyz", "ops": [], "state": "pending"}
    on_approval = AsyncMock(return_value=ApprovalResult(approved=False))

    s = AsyncMock()
    s.list_tools.return_value = _list_tools(["execute_plan", "get_plan"])

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        return _mcp_result(plan_data if name == "get_plan" else {"ok": True})

    s.call_tool.side_effect = _call

    captured_messages: list[list[dict]] = []

    llm = AsyncMock()
    responses = [
        _tool_response("execute_plan", {"plan_id": "xyz"}),
        _text_response("Revised plan."),
    ]

    async def _create(**kwargs: Any) -> Any:
        captured_messages.append(list(kwargs.get("messages", [])))
        return responses.pop(0)

    llm.chat.completions.create.side_effect = _create

    await run_agent_loop(
        target=tmp_path,
        settings=_settings(tmp_path),
        llm=llm,
        session=s,
        on_event=lambda _: None,
        on_approval_needed=on_approval,
    )

    # approve_plan and execute_plan must NOT be called on the server
    called_tools = [c[0][0] for c in s.call_tool.call_args_list]
    assert "approve_plan" not in called_tools
    assert "execute_plan" not in called_tools

    # The rejection error must appear in the tool result fed back to the LLM
    all_msgs = [m for batch in captured_messages for m in batch]
    tool_msgs = [m for m in all_msgs if m.get("role") == "tool"]
    assert any("rejected" in m.get("content", "") for m in tool_msgs)


async def test_approved_plan_calls_approve_before_execute(tmp_path: Path) -> None:
    plan_data = {"plan_id": "plan1", "ops": [], "state": "pending"}
    call_order: list[str] = []

    s = AsyncMock()
    s.list_tools.return_value = _list_tools(["execute_plan", "get_plan", "approve_plan"])

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        call_order.append(name)
        return _mcp_result(plan_data if name == "get_plan" else {"ok": True})

    s.call_tool.side_effect = _call

    await run_agent_loop(
        target=tmp_path,
        settings=_settings(tmp_path),
        llm=_llm(_tool_response("execute_plan", {"plan_id": "plan1"}), _text_response("Done.")),
        session=s,
        on_event=lambda _: None,
        on_approval_needed=AsyncMock(return_value=ApprovalResult(approved=True)),
    )

    assert "approve_plan" in call_order
    assert "execute_plan" in call_order
    assert call_order.index("approve_plan") < call_order.index("execute_plan")


# ── Op removal ────────────────────────────────────────────────────────────────


async def test_deselected_ops_removed_from_plan_before_execution(tmp_path: Path) -> None:
    from server.plan import Plan, PlanOp
    from server.plan import save as save_plan

    plans_dir = tmp_path / ".organizer" / "plans"
    plans_dir.mkdir(parents=True)

    op_keep = PlanOp.new("rename", "/a/file.txt", "file_clean.txt")
    op_drop = PlanOp.new("move", "/a/other.txt", "/a/docs/")
    plan = Plan.new()
    plan.ops = [op_keep, op_drop]
    save_plan(plan, plans_dir)

    plan_data = plan.to_dict()

    s = AsyncMock()
    s.list_tools.return_value = _list_tools(["execute_plan", "get_plan", "approve_plan"])

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        return _mcp_result(plan_data if name == "get_plan" else {"ok": True})

    s.call_tool.side_effect = _call

    on_approval = AsyncMock(
        return_value=ApprovalResult(approved=True, removed_op_ids=[op_drop.op_id])
    )

    await run_agent_loop(
        target=tmp_path,
        settings=_settings(plans_dir),
        llm=_llm(
            _tool_response("execute_plan", {"plan_id": plan.plan_id}),
            _text_response("Done."),
        ),
        session=s,
        on_event=lambda _: None,
        on_approval_needed=on_approval,
    )

    saved = json.loads((plans_dir / f"{plan.plan_id}.json").read_text())
    remaining = {op["op_id"] for op in saved["ops"]}
    assert op_keep.op_id in remaining
    assert op_drop.op_id not in remaining


# ── _extract_content ──────────────────────────────────────────────────────────


def test_extract_content_json() -> None:
    c = MagicMock()
    c.text = '{"x": 42}'
    r = MagicMock()
    r.content = [c]
    assert _extract_content(r) == {"x": 42}


def test_extract_content_plain_text() -> None:
    c = MagicMock()
    c.text = "hello"
    r = MagicMock()
    r.content = [c]
    assert _extract_content(r) == "hello"


def test_extract_content_empty_content() -> None:
    r = MagicMock()
    r.content = []
    result = _extract_content(r)
    assert isinstance(result, str)


# ── Profile-driven system prompt ───────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_system_prompt_is_profile_driven() -> None:
    from config.settings import load

    from host.agent import _build_system_prompt

    prompt = _build_system_prompt(_PROJECT_ROOT, load())

    # analysis pass + memory tools are present
    assert "compute_checksum" in prompt
    assert "record_document" in prompt
    # the active profile's vocabulary is injected
    assert "releve_de_decision" in prompt
    # the author guardrail is stated
    assert "author" in prompt.lower()
    # analysis precedes organization
    assert prompt.index("compute_checksum") < prompt.index("create_plan")


def test_system_prompt_includes_synthesis_template() -> None:
    from config.settings import load

    from host.agent import _build_system_prompt

    prompt = _build_system_prompt(_PROJECT_ROOT, load())

    # the profile's synthesis template is injected
    assert "Project synthesis" in prompt
    assert "Synthèse du projet" in prompt
    # synthesis tools are referenced in the workflow
    assert "build_graph" in prompt
    assert "get_actors" in prompt
    assert "create_event" in prompt
    # synthesis comes after the organize step
    assert prompt.index("create_plan") < prompt.index("Project synthesis")


def test_system_prompt_falls_back_without_profile() -> None:
    from host.agent import _build_system_prompt

    # A MagicMock has no real profile/profiles_dir → profile load fails → fallback
    prompt = _build_system_prompt(_PROJECT_ROOT, MagicMock())

    assert "Never delete files" in prompt
    assert '"default" domain profile' in prompt
