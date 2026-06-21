"""Async agent loop: MCP client + GPT-5 tool-calling loop.

Fully decoupled from Textual — callers pass async callbacks for events and
approval so this module can be exercised in plain pytest tests.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from openai import AsyncOpenAI

from config.settings import Settings
from server.plan import load as _load_plan
from server.plan import save as _save_plan

# ── Event types ───────────────────────────────────────────────────────────────

EventKind = Literal["thinking", "tool_call", "tool_result", "plan_ready", "done", "error"]


@dataclass
class AgentEvent:
    kind: EventKind
    text: str
    data: dict | None = None


EventCallback = Callable[[AgentEvent], None]


# ── Approval result ───────────────────────────────────────────────────────────

@dataclass
class ApprovalResult:
    approved: bool
    removed_op_ids: list[str] = field(default_factory=list)


ApprovalCallback = Callable[[str, dict], Awaitable[ApprovalResult]]

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a local directory organizer assistant. Analyze the target directory and
produce a clean, organized structure following these steps:

1. Call list_dir on the target to enumerate contents; recurse into subdirectories.
2. Read files (read_file / extract_text) to understand their purpose.
3. Create a plan with create_plan, then stage ops with propose_rename,
   propose_move, and propose_quarantine.
4. Call review_plan for a deduplication pass before execution.
5. Call execute_plan to apply the plan (the user reviews and approves first).
6. Call write_summary on the target directory to produce SUMMARY.md.
7. Respond with a final text summary (no tool calls) when fully done.

Safety rules — never break these:
- Never delete files. Quarantine only.
- Never overwrite existing files.
- Always call review_plan before execute_plan.
- If a hard stop occurs, explain what failed and offer to undo.\
"""

_MAX_TURNS = 50

# ── MCP session ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def mcp_session(project_root: Path) -> AsyncIterator[ClientSession]:
    """Launch the MCP server subprocess and yield an initialised session.

    The server inherits the host's environment so that pydantic-settings picks
    up the .env file located in the project root.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.main"],
        env=None,  # inherit environment (picks up .env via pydantic-settings)
        cwd=str(project_root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ── Public entry points ───────────────────────────────────────────────────────


async def run_agent(
    target: Path,
    settings: Settings,
    llm: AsyncOpenAI,
    on_event: EventCallback,
    on_approval_needed: ApprovalCallback,
) -> str:
    """Launch the MCP server and run the agent loop. Returns final summary text."""
    project_root = Path(__file__).resolve().parent.parent
    async with mcp_session(project_root) as session:
        return await run_agent_loop(
            target=target,
            settings=settings,
            llm=llm,
            session=session,
            on_event=on_event,
            on_approval_needed=on_approval_needed,
        )


async def run_agent_loop(
    target: Path,
    settings: Settings,
    llm: AsyncOpenAI,
    session: ClientSession,
    on_event: EventCallback,
    on_approval_needed: ApprovalCallback,
) -> str:
    """Run the GPT-5 tool-calling loop against an already-connected MCP session.

    Separated from run_agent so tests can inject a mock session directly.
    """
    # Discover tools from the MCP server
    tools_response = await session.list_tools()
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema
                if t.inputSchema
                else {"type": "object", "properties": {}},
            },
        }
        for t in tools_response.tools
    ]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Please organize the directory: {target}"},
    ]

    on_event(AgentEvent("thinking", f"Starting agent for {target}"))

    for _turn in range(_MAX_TURNS):
        on_event(AgentEvent("thinking", "Calling LLM…"))

        response = await llm.chat.completions.create(
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            tools=openai_tools,  # type: ignore[arg-type]
            tool_choice="auto",
        )

        choice = response.choices[0]
        messages.append(choice.message.model_dump(exclude_none=True))

        # No tool calls → agent is finished
        if not choice.message.tool_calls:
            final_text = choice.message.content or "Done."
            on_event(AgentEvent("done", final_text))
            return final_text

        for tool_call in choice.message.tool_calls:
            name = tool_call.function.name
            args: dict[str, Any] = json.loads(tool_call.function.arguments or "{}")

            on_event(AgentEvent("tool_call", f"{name}({_fmt_args(args)})"))

            result = await _dispatch(
                name=name,
                args=args,
                session=session,
                settings=settings,
                on_event=on_event,
                on_approval_needed=on_approval_needed,
            )

            on_event(AgentEvent("tool_result", _fmt_result(result)))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    on_event(AgentEvent("error", f"Reached maximum turns ({_MAX_TURNS}); stopping."))
    return f"Stopped: maximum turns ({_MAX_TURNS}) reached."


# ── Tool dispatch ─────────────────────────────────────────────────────────────


async def _dispatch(
    *,
    name: str,
    args: dict[str, Any],
    session: ClientSession,
    settings: Settings,
    on_event: EventCallback,
    on_approval_needed: ApprovalCallback,
) -> Any:
    if name == "execute_plan":
        return await _handle_execute_plan(
            args=args,
            session=session,
            settings=settings,
            on_event=on_event,
            on_approval_needed=on_approval_needed,
        )
    raw = await session.call_tool(name, args)
    return _extract_content(raw)


async def _handle_execute_plan(
    *,
    args: dict[str, Any],
    session: ClientSession,
    settings: Settings,
    on_event: EventCallback,
    on_approval_needed: ApprovalCallback,
) -> Any:
    plan_id = args.get("plan_id", "")

    # Fetch plan details for display
    plan_raw = await session.call_tool("get_plan", {"plan_id": plan_id})
    plan_data = _extract_content(plan_raw)

    on_event(AgentEvent("plan_ready", f"Plan {plan_id[:8]} ready for review", data=plan_data))

    approval = await on_approval_needed(plan_id, plan_data if isinstance(plan_data, dict) else {})

    if not approval.approved:
        return {"error": "Plan rejected by user. Revise and resubmit."}

    # Remove any ops the user deselected
    if approval.removed_op_ids:
        _patch_plan(plan_id, approval.removed_op_ids, settings.plans_dir)

    # Approve then execute on the server
    await session.call_tool("approve_plan", {"plan_id": plan_id})
    result_raw = await session.call_tool("execute_plan", {"plan_id": plan_id})
    return _extract_content(result_raw)


def _patch_plan(plan_id: str, removed_op_ids: list[str], plans_dir: Path) -> None:
    """Remove specific ops from the plan file before execution."""
    plan = _load_plan(plan_id, plans_dir)
    plan.ops = [op for op in plan.ops if op.op_id not in removed_op_ids]
    _save_plan(plan, plans_dir)


# ── Content extraction ────────────────────────────────────────────────────────


def _extract_content(result: Any) -> Any:
    """Pull structured data out of an MCP tool result object."""
    if hasattr(result, "content") and result.content:
        first = result.content[0]
        if hasattr(first, "text"):
            try:
                return json.loads(first.text)
            except (json.JSONDecodeError, TypeError):
                return first.text
    return str(result)


# ── Formatting helpers ────────────────────────────────────────────────────────


def _fmt_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    items = list(args.items())
    parts = [f"{k}={v!r}" for k, v in items[:2]]
    if len(items) > 2:
        parts.append("…")
    return ", ".join(parts)


def _fmt_result(result: Any) -> str:
    text = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
    return text[:140] + "…" if len(text) > 140 else text
