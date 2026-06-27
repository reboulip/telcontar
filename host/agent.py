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
from server.profile import Profile, load_profile

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

_SYSTEM_PROMPT_TEMPLATE = """\
You are telcontar, a local document-intelligence assistant. You turn a messy
directory of documents into structured knowledge and a clean, organized tree,
using the "{profile_name}" domain profile. Work in this order:

A. ANALYZE each meaningful document and record it in the memory registry:
   1. Read its content with read_file or extract_text (for PDF/Office).
   2. Call compute_checksum to obtain its unique content id.
   3. Derive its metadata and call record_document(checksum, path, title, type,
      summary, provenance, date, entities):
{extraction_rules}
   4. Use find_duplicates and find_modified_documents to spot duplicates and
      newer versions before deciding what to keep or quarantine.

B. ORGANIZE the tree:
   5. Design a relevant target taxonomy — a small, readable folder tree for THIS
      corpus. Reason from the document types and themes you actually found (e.g.
      group by document type, by workstream, or by phase); prefer a shallow tree
      with clearly named folders over deep nesting, and do not create folders for
      categories the corpus does not contain. Create each folder with
      create_dir(path) (idempotent and collision-safe).
   6. Create a plan with create_plan, then stage ops: propose_rename to apply the
      naming convention, propose_move to file each document into its folder in the
      taxonomy, and propose_quarantine for useless or duplicate documents (never
      delete them).
   7. Call review_plan for a deduplication pass.
   8. Call execute_plan to apply the plan (the user reviews and approves first).
      Registry paths are reconciled automatically as files move. After execution,
      you MAY call compress_quarantine to losslessly archive the quarantined files
      and reclaim space (reversible via undo_last); skip it if nothing was quarantined.

C. SYNTHESIZE:
   9. Record key project events as you go with create_event(sentence, date): one
      short, verb-led, dated sentence per milestone (e.g. a decision, a delivery).
   10. Call build_graph to project the registry and events into the knowledge graph,
      then get_actors for the ranked main actors and list_events for the timeline.
   11. Call write_index on the target directory to produce INDEX.md and manifest.json,
      reflecting the organized taxonomy.
   12. Compose the project synthesis as Markdown from the registry (list_documents /
      get_registry), the events (list_events), the graph (get_graph) and the actors
      (get_actors), following the "Project synthesis" template below. Persist it with
      write_summary(path=<target_dir>, content=<your markdown>). Never invent facts
      not present in the data.
   13. For each meaningful folder of the organized tree, compose a short README and
      persist it with write_folder_readme(path=<folder>, content=<your markdown>):
      one or two paragraphs naming what the folder holds and its role in the
      arborescence, drawn from the documents you recorded there. Skip trivial or
      empty folders; never invent contents.
   14. Respond with a final text summary (no tool calls) when fully done.

Safety rules — never break these:
- Never delete files. Quarantine only.
- Never overwrite existing files.
- Always call review_plan before execute_plan.
- If a hard stop occurs, explain what failed and offer to undo.

{types_section}{naming_section}{synthesis_section}\
"""

_DEFAULT_NAMING_CONVENTIONS = """\
## File-naming conventions

When proposing renames, follow these rules:
- Use lowercase letters and underscores (snake_case).
- Replace spaces and hyphens with underscores.
- Prefix dates in ISO format: YYYY-MM-DD (e.g. 2024-01-15_report.pdf).
- Remove or transliterate special characters (accents, punctuation).
- Keep the original file extension unchanged.
- Drop redundant suffixes such as "final", "copy", "v2" when a date is present.
- Avoid leading numbers unless they represent a meaningful sequence.
"""


def _try_load_profile(project_root: Path, settings: Settings) -> Profile | None:
    """Load the active profile, or return None if it cannot be resolved."""
    try:
        profiles_dir = Path(settings.profiles_dir)
        if not profiles_dir.is_absolute():
            profiles_dir = project_root / profiles_dir
        return load_profile(str(settings.profile), profiles_dir)
    except Exception:
        return None


def _build_extraction_rules(profile: Profile | None) -> str:
    if profile is None:
        type_ids = "the profile's document types"
        roles = "author, mentioned"
        cap = "a few"
    else:
        type_ids = ", ".join(profile.document_type_ids())
        roles = ", ".join(profile.entity_roles()) or "author, mentioned"
        cap = str(profile.salient_cap)
    return (
        "      - title: a clear, human-readable title (required).\n"
        f"      - type: exactly one of [{type_ids}] (required).\n"
        "      - summary: one paragraph capturing the content (required).\n"
        "      - provenance: why this document is here / its knowledge contribution (required).\n"
        "      - date: ISO YYYY-MM-DD if derivable from the document, else null (never guess).\n"
        f"      - entities: people/organisations as {{name, role, kind}}; roles from [{roles}].\n"
        '        Set an entity with role "author" ONLY if the author is explicitly named —\n'
        f"        never infer one. Keep the main actors to about {cap}."
    )


def _build_types_section(profile: Profile | None) -> str:
    if profile is None or not profile.document_types:
        return ""
    lines = ["## Document types\n"]
    for dt in profile.document_types:
        desc = f" — {dt.description}" if dt.description else ""
        lines.append(f"- `{dt.id}` ({dt.label}){desc}")
    return "\n".join(lines) + "\n\n"


def _build_synthesis_section(profile: Profile | None) -> str:
    """Render the profile's project-synthesis template into a prompt section."""
    if profile is None:
        return ""
    sections = profile.synthesis_sections
    instructions = profile.synthesis_instructions.strip()
    if not sections and not instructions:
        return ""
    title = profile.synthesis_title.strip() or "Project synthesis"
    lines = [
        "## Project synthesis",
        "",
        f'When composing SUMMARY.md, structure it as "{title}" with one Markdown',
        "section per item below, in this order:",
    ]
    for s in sections:
        lines.append(f"- {s}")
    if instructions:
        lines.append("")
        lines.append(instructions)
    return "\n" + "\n".join(lines) + "\n"


def _load_naming_conventions(project_root: Path, profile: Profile | None) -> str:
    naming_path = project_root / ".organizer" / "NAMING.md"
    if naming_path.is_file():
        text = naming_path.read_text(encoding="utf-8").strip()
        if text:
            return "## File-naming conventions\n\n" + text + "\n"
    if profile is not None and profile.naming_instructions.strip():
        return "## File-naming conventions\n\n" + profile.naming_instructions.strip() + "\n"
    return _DEFAULT_NAMING_CONVENTIONS


def _build_system_prompt(project_root: Path, settings: Settings) -> str:
    profile = _try_load_profile(project_root, settings)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        profile_name=profile.name if profile is not None else "default",
        extraction_rules=_build_extraction_rules(profile),
        types_section=_build_types_section(profile),
        naming_section=_load_naming_conventions(project_root, profile),
        synthesis_section=_build_synthesis_section(profile),
    )


# ── Query mode ──────────────────────────────────────────────────────────────

# Read-only allowlist for interactive query mode. Query mode answers natural
# language questions over the corpus and must never mutate it: no plan,
# execution, file-write, graph-build, event or archive tools are exposed.
# Keep this in sync when adding new read-only inspection tools to the server.
QUERY_ALLOWED_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "extract_text",
        "compute_checksum",
        "compare_documents",
        "get_document",
        "list_documents",
        "get_registry",
        "find_duplicates",
        "find_modified_documents",
        "list_events",
        "get_graph",
        "get_actors",
        "list_archived",
    }
)

_QUERY_SYSTEM_PROMPT_TEMPLATE = """\
You are telcontar, a local document-intelligence assistant, in QUERY mode for the
"{profile_name}" domain profile. The corpus has already been analyzed and recorded
in a persistent memory registry, an event journal and a knowledge graph. Your job
is to answer the user's questions about this corpus — nothing else.

You have READ-ONLY tools. Use them to gather facts before answering:
- list_documents / get_registry / get_document — the recorded documents and their
  metadata (title, type, date, summary, provenance, entities, status).
- list_events — the dated project timeline.
- get_graph / get_actors — the knowledge graph and the ranked main actors.
- find_duplicates / find_modified_documents — duplicate clusters and modified versions.
- list_archived — documents removed from active memory.
- list_dir / read_file / extract_text / compare_documents / compute_checksum — to
  inspect a specific file's content when the registry is not enough.

Rules:
- Answer ONLY from the data returned by these tools. Never invent facts, dates,
  authors or figures that the tools do not support. If the data does not answer the
  question, say so plainly.
- Cite specifics where helpful: document titles, dates, actor names, event sentences.
- You CANNOT modify the corpus. There are no rename/move/quarantine/write tools here;
  if the user asks to reorganize, explain that query mode is read-only.
- Be concise and answer in the language the user asks in.

{types_section}\
"""


def _build_query_system_prompt(project_root: Path, settings: Settings) -> str:
    profile = _try_load_profile(project_root, settings)
    return _QUERY_SYSTEM_PROMPT_TEMPLATE.format(
        profile_name=profile.name if profile is not None else "default",
        types_section=_build_types_section(profile),
    )


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


# ── Tool discovery ────────────────────────────────────────────────────────────


async def _discover_openai_tools(
    session: ClientSession, allowed: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    """List MCP tools and convert them to OpenAI function specs.

    When `allowed` is given, only tools whose name is in the set are exposed —
    used by query mode to hide every mutating tool from the model.
    """
    tools_response = await session.list_tools()
    return [
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
        if allowed is None or t.name in allowed
    ]


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
            project_root=project_root,
        )


async def run_agent_loop(
    target: Path,
    settings: Settings,
    llm: AsyncOpenAI,
    session: ClientSession,
    on_event: EventCallback,
    on_approval_needed: ApprovalCallback,
    project_root: Path | None = None,
) -> str:
    """Run the GPT-5 tool-calling loop against an already-connected MCP session.

    Separated from run_agent so tests can inject a mock session directly.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    # Discover tools from the MCP server
    openai_tools = await _discover_openai_tools(session)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(project_root, settings)},
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


async def run_query(
    question: str,
    settings: Settings,
    llm: AsyncOpenAI,
    on_event: EventCallback,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Answer one NL question over the corpus, launching a fresh MCP session.

    Convenience wrapper around `run_query_loop` for callers that do not manage a
    session themselves. For a multi-turn chat, keep a single session open and call
    `run_query_loop` directly instead (one server subprocess for the whole chat).
    """
    project_root = Path(__file__).resolve().parent.parent
    async with mcp_session(project_root) as session:
        return await run_query_loop(
            question=question,
            settings=settings,
            llm=llm,
            session=session,
            on_event=on_event,
            history=history,
            project_root=project_root,
        )


async def run_query_loop(
    *,
    question: str,
    settings: Settings,
    llm: AsyncOpenAI,
    session: ClientSession,
    on_event: EventCallback,
    history: list[dict[str, Any]] | None = None,
    project_root: Path | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Answer one NL question over the corpus using read-only tools only.

    `history` carries the conversation across questions: pass the list returned by
    a previous call back in to preserve multi-turn context. When None, a fresh
    history seeded with the query-mode system prompt is created. Returns the
    answer text and the updated history.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    # Read-only tool subset — query mode never mutates the corpus.
    openai_tools = await _discover_openai_tools(session, allowed=QUERY_ALLOWED_TOOLS)

    if history is None:
        history = [
            {"role": "system", "content": _build_query_system_prompt(project_root, settings)}
        ]
    messages = history
    messages.append({"role": "user", "content": question})

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

        # No tool calls → the model has produced its answer
        if not choice.message.tool_calls:
            answer = choice.message.content or "(no answer)"
            on_event(AgentEvent("done", answer))
            return answer, messages

        for tool_call in choice.message.tool_calls:
            name = tool_call.function.name
            args: dict[str, Any] = json.loads(tool_call.function.arguments or "{}")

            # Defense in depth: the model can only see allowed tools, but never
            # forward a mutating call even if it hallucinates one.
            if name not in QUERY_ALLOWED_TOOLS:
                result: Any = {"error": f"Tool {name!r} is not available in query mode."}
            else:
                on_event(AgentEvent("tool_call", f"{name}({_fmt_args(args)})"))
                raw = await session.call_tool(name, args)
                result = _extract_content(raw)
                on_event(AgentEvent("tool_result", _fmt_result(result)))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    on_event(AgentEvent("error", f"Reached maximum turns ({_MAX_TURNS}); stopping."))
    return f"Stopped: maximum turns ({_MAX_TURNS}) reached.", messages


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
