"""NiceGUI entrypoint — the only module in host/web that imports nicegui.

Pages are registered at import time (decorators below); nothing binds a port
or starts a server until run_web() is called. That keeps `import
host.web.main` side-effect-free and safe to import in tests.

Rendering model: on_event (host/web/bridge.py) only ever mutates the
framework-agnostic RunSession/TranscriptItem data — it never touches a
NiceGUI element, and runs from a plain asyncio.Task with no client of its
own. Each connected browser tab instead polls that data with its own
`ui.timer`, created during this page's own build so NiceGUI automatically
scopes its callback to this tab's client. A page reload starts a fresh timer
with a fresh render cursor at 0, replaying the whole transcript, and re-shows
whatever is in `session.pending` — this is what makes a reload re-attach to
an in-flight approval/cost dialog instead of orphaning it (see
RunSession's docstring; validated against a real run in the Stage 0 spike
referenced in ROADMAP.md's Break 1).

Every route mounts through `host.web.shell.app_shell` (T2) — a persistent
left-sidebar frame — including the early-return branches below, so the
sidebar stays visible while the user browses or waits.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from nicegui import app, run, ui

from host.agent import ApprovalResult, CostApprovalResult
from host.format import fmt_op
from host.paths import directory_overview
from host.web import session as web_session
from host.web.bridge import AgentBridge
from host.web.shell import app_shell

# Set by run_web() before ui.run() starts; read by the landing page so a
# `telcontar --web --target DIR` launch skips straight to a run instead of
# showing the directory browser. None means "show the browser".
_default_target: Path | None = None


@dataclass
class _RenderState:
    """Per-client render cursor for run_page — plain instance attributes
    (rather than a dict) so mypy can track the two different value types."""

    seq: int = 0
    shown_request_id: str | None = None


def _start_run(target: Path) -> web_session.RunSession:
    return web_session.create(target)


@ui.page("/")
async def index_page() -> None:
    from config.settings import is_configured

    with app_shell() as shell:
        if not is_configured():
            ui.label("telcontar isn't configured yet").classes("text-h5")
            ui.label(
                "Run `telcontar` once (the Textual TUI) to complete first-time setup, "
                "then reload this page."
            )
            return

        if _default_target is not None:
            session = _start_run(_default_target)
            ui.navigate.to(f"/run/{session.run_id}")
            return

        ui.label("telcontar").classes("text-h5")
        ui.label("Pick a directory in the sidebar, then start organizing:")

        def _select() -> None:
            if shell.selected is None or not shell.selected.is_dir():
                return
            session = _start_run(shell.selected)
            ui.navigate.to(f"/run/{session.run_id}")

        ui.button("Use selected directory", on_click=_select, color="primary").classes("mt-2")


@ui.page("/run/{run_id}")
async def run_page(run_id: str) -> None:
    session = web_session.get(run_id)

    with app_shell(target=session.target if session is not None else None):
        if session is None:
            ui.label(
                "Run not found — it may have finished and been cleared, or the link is wrong."
            ).classes("text-negative")
            return

        starter_column = ui.column().classes("w-full")
        main_column = ui.column().classes("w-full")
        starter_column.visible = not session.started
        main_column.visible = session.started

        with starter_column:
            ui.label("Here's what I found").classes("text-h6")
            overview_label = ui.label("Scanning…").classes("whitespace-pre font-mono text-sm")
            instructions_input = ui.input(
                "Steering instructions (optional) — e.g. "
                '"group by workstream", "don\'t quarantine drafts"'
            ).classes("w-full")

            def _start() -> None:
                text = instructions_input.value.strip()
                AgentBridge(session).start(instructions=text or None)
                starter_column.visible = False
                main_column.visible = True

            ui.button("Start organizing", on_click=_start, color="primary")

        if not session.started:
            overview_label.set_text(await run.io_bound(directory_overview, session.target) or "")

        with main_column:
            transcript_column = ui.column().classes("w-full")
            status_label = ui.label()
            progress_bar = ui.linear_progress(value=0.0)
            progress_bar.visible = False
            with ui.row().classes("w-full items-center"):
                chat_input = ui.input("Chat anytime…").classes("flex-grow")

                def _send() -> None:
                    text = chat_input.value.strip()
                    if not text:
                        return
                    chat_input.value = ""
                    session.add_turn("user", text)
                    session.messages.put_nowait(text)

                chat_input.on("keydown.enter", lambda _: _send())
                ui.button("Send", on_click=_send)

        render_state = _RenderState()

        def _render_item(item: web_session.TranscriptItem) -> None:
            with transcript_column:
                if item.kind == "turn":
                    ui.chat_message(item.text, name=item.speaker, sent=item.speaker == "user")
                else:
                    with ui.expansion("internal steps").classes("w-full"):
                        ui.label(item.text).classes("whitespace-pre font-mono text-xs")

        def _show_pending_dialog() -> None:
            pending = session.pending
            if pending is None or pending.request_id == render_state.shown_request_id:
                return
            render_state.shown_request_id = pending.request_id

            dialog = ui.dialog()
            with dialog, ui.card():
                if pending.kind == "approval":
                    plan_data = pending.payload["plan_data"]
                    ops = plan_data.get("ops", [])
                    ui.label(f"Plan ready for review — {len(ops)} op(s)")
                    with ui.column().classes("max-h-64 overflow-auto"):
                        for op in ops:
                            ui.label(fmt_op(op, session.target, markup=False))
                    refine_input = ui.input("Refine instead of approving (optional)").classes(
                        "w-full"
                    )

                    def _approve() -> None:
                        session.resolve_pending(ApprovalResult(approved=True))
                        dialog.close()

                    def _reject() -> None:
                        text = refine_input.value.strip()
                        result = (
                            ApprovalResult(approved=False, refinement=text)
                            if text
                            else ApprovalResult(approved=False)
                        )
                        session.resolve_pending(result)
                        dialog.close()

                    with ui.row():
                        ui.button("Approve", on_click=_approve, color="positive")
                        ui.button("Reject / Refine", on_click=_reject, color="negative")
                else:
                    ui.label(pending.payload["summary"])

                    def _proceed() -> None:
                        session.resolve_pending(CostApprovalResult(approved=True))
                        dialog.close()

                    def _cancel() -> None:
                        session.resolve_pending(CostApprovalResult(approved=False))
                        dialog.close()

                    with ui.row():
                        ui.button("Proceed", on_click=_proceed, color="positive")
                        ui.button("Cancel", on_click=_cancel, color="negative")
            dialog.open()

        def _refresh() -> None:
            for item in session.transcript:
                if item.seq > render_state.seq:
                    _render_item(item)
                    render_state.seq = item.seq

            line = session.status
            if session.tokens:
                line = f"{session.status}   ·   ⬍ {session.tokens}"
            status_label.set_text(line)

            total = session.progress.get("total", 0)
            if total:
                progress_bar.visible = True
                progress_bar.set_value(session.progress.get("analyzed", 0) / total)
            else:
                progress_bar.visible = False

            _show_pending_dialog()
            chat_input.enabled = session.started
            if session.started:
                starter_column.visible = False
                main_column.visible = True

        ui.timer(0.5, _refresh)


def _pick_port() -> int:
    """Bind an ephemeral port, release it, and hand the number to ui.run().

    Small TOCTOU window between releasing and ui.run() rebinding it — retried
    once on OSError, which is the practical mitigation for a single-user
    localhost tool (a persistent listener some other process holds onto is a
    real failure, not a race, and retrying once won't fix that either).
    """
    for _attempt in range(2):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise OSError("Could not find a free port on 127.0.0.1")


def run_web(target: Path | None = None) -> None:
    """Launch the NiceGUI web UI. Blocks until the server stops."""
    global _default_target
    _default_target = target

    @app.on_shutdown
    def _reject_pending_on_shutdown() -> None:
        for session in web_session.all_sessions():
            if session.pending is not None:
                result = (
                    ApprovalResult(approved=False)
                    if session.pending.kind == "approval"
                    else CostApprovalResult(approved=False)
                )
                session.resolve_pending(result)

    port = _pick_port()
    # reload=False is load-bearing, not a style choice: with reload=True,
    # uvicorn forces a SelectorEventLoop on Windows (its `use_subprocess`
    # flag), where asyncio.create_subprocess_exec raises NotImplementedError
    # — mcp_session's server subprocess launch would be dead on Windows.
    # Never bind 0.0.0.0 either: it triggers a Windows Firewall prompt and
    # would expose the approval gate on the LAN.
    ui.run(host="127.0.0.1", port=port, show=True, reload=False)
