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
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import app, run, ui

from host.agent import ApprovalResult, CostApprovalResult
from host.format import fmt_op
from host.paths import directory_overview
from host.web import session as web_session
from host.web import theme
from host.web.bridge import AgentBridge
from host.web.settings import build_settings_view
from host.web.shell import app_shell
from host.web.wizard import build_setup_wizard


@dataclass
class _RenderState:
    """Per-client render cursor for run_page. ``step_rows`` holds each
    rendered step's (row, label) elements, keyed by seq, so a step that was
    "running" when first rendered can have its glyph/summary updated in
    place once it closes — StepRecords mutate after creation, unlike
    TranscriptItems."""

    turn_seq: int = 0
    step_seq: int = 0
    shown_request_id: str | None = None
    step_rows: dict[int, tuple[ui.row, ui.label]] = field(default_factory=dict)


def _start_run(target: Path) -> web_session.RunSession:
    return web_session.create(target)


@ui.page("/")
async def index_page() -> None:
    from config.settings import is_configured

    with app_shell() as shell:
        if not is_configured():
            ui.navigate.to("/setup")
            return

        default_target = web_session.get_default_target()
        if default_target is not None:
            session = _start_run(default_target)
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


@ui.page("/setup")
async def setup_page() -> None:
    with app_shell():
        await build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))


@ui.page("/settings")
async def settings_page() -> None:
    with app_shell():
        await build_settings_view(on_done=lambda: ui.navigate.back())


@ui.page("/run/{run_id}")
async def run_page(run_id: str) -> None:
    session = web_session.get(run_id)

    with app_shell(target=session.target if session is not None else None) as shell:
        if session is None:
            ui.label(
                "Run not found — it may have finished and been cleared, or the link is wrong."
            ).classes("text-negative")
            return

        # Dynamic per-request title — ui.page(title=...) is bound at
        # decoration time and can't see the target directory, which is only
        # known once the URL's run_id resolves to a session (T7).
        ui.page_title(theme.window_title(session.target))

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
            conversation_column = ui.column().classes("w-full")
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

            # Internal-step log strip (T5/T6) — pinned at the bottom, always
            # visible, distinct from the conversation above: telcontar's own
            # tool activity never renders as a chat bubble. activity_label is
            # the "what's happening right now" line; log_column is the
            # scrolling one-line-per-step history, each with a toggle that
            # opens the full payload in shell's right-side detail drawer.
            ui.separator()
            activity_label = ui.label().classes("text-xs text-grey-6 q-px-sm")
            log_column = (
                ui.column()
                .classes("w-full overflow-auto q-px-sm q-gutter-none")
                .style("max-height: 25vh; min-height: 25vh;")
            )

        render_state = _RenderState()

        def _render_turn(item: web_session.TranscriptItem) -> None:
            with conversation_column:
                ui.chat_message(item.text, name=item.speaker, sent=item.speaker == "user")

        _STEP_GLYPHS = {"running": "▶", "ok": "·", "error": "✗"}

        def _fmt_step_line(step: web_session.StepRecord) -> str:
            return f"{_STEP_GLYPHS[step.status]} {step.summary}"

        def _render_step_row(step: web_session.StepRecord) -> tuple[ui.row, ui.label]:
            with log_column:
                row = ui.row().classes("w-full items-center q-gutter-xs no-wrap")
                with row:
                    label = ui.label(_fmt_step_line(step)).classes(
                        "whitespace-pre font-mono text-xs ellipsis flex-grow"
                    )
                    ui.button(
                        icon="code",
                        on_click=lambda: shell.show_detail(
                            step.summary, step.detail or "(pending…)"
                        ),
                    ).props("flat dense size=xs")
            return row, label

        _MAX_LOG_ROWS = 500

        def _prune_log() -> None:
            while len(render_state.step_rows) > _MAX_LOG_ROWS:
                oldest_seq = next(iter(render_state.step_rows))
                row, _label = render_state.step_rows.pop(oldest_seq)
                row.delete()

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
                if item.seq > render_state.turn_seq:
                    _render_turn(item)
                    render_state.turn_seq = item.seq

            activity_label.set_text(session.activity)
            for step in session.steps:
                if step.seq > render_state.step_seq:
                    render_state.step_rows[step.seq] = _render_step_row(step)
                    render_state.step_seq = step.seq
                    _prune_log()
                else:
                    entry = render_state.step_rows.get(step.seq)
                    if entry is not None:
                        entry[1].set_text(_fmt_step_line(step))

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

        ui.timer(web_session.REFRESH_INTERVAL, _refresh)


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
    web_session.set_default_target(target)

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

    # Visual identity (T8) — applied globally, once, here: never a per-page
    # ui.colors() call, which would silently override this and re-fragment
    # the identity across routes. app.add_static_files() serves the
    # vendored Cinzel woff2 (if present — theme.css()'s @font-face degrades
    # to the fallback stack otherwise) at the URL theme.font_face_css()
    # references.
    app.colors(**theme.PALETTE)
    if theme.FONT_DIR.is_dir():
        app.add_static_files(theme.FONT_URL_PATH, theme.FONT_DIR)
    ui.add_css(theme.css(), shared=True)

    port = _pick_port()
    # reload=False is load-bearing, not a style choice: with reload=True,
    # uvicorn forces a SelectorEventLoop on Windows (its `use_subprocess`
    # flag), where asyncio.create_subprocess_exec raises NotImplementedError
    # — mcp_session's server subprocess launch would be dead on Windows.
    # Never bind 0.0.0.0 either: it triggers a Windows Firewall prompt and
    # would expose the approval gate on the LAN. dark=True is load-bearing
    # too: Quasar only honours the dark/dark_page palette tokens above in
    # dark mode.
    ui.run(
        host="127.0.0.1",
        port=port,
        show=True,
        reload=False,
        title=theme.window_title(),
        dark=True,
        favicon=theme.FAVICON_SVG,
    )


# NiceGUI's headless `user` test fixture (pyproject.toml's `main_file` ini)
# runpy-executes this file with __name__ == "__main__", the same convention
# a NiceGUI "main file" script uses when run directly. Under
# nicegui.testing.user_simulation, ui.run() detects the simulation and
# returns immediately after registering its run config — it never binds a
# port or opens a browser (see nicegui.ui_run.run's `is_user_simulation()`
# branch) — so this guard is inert outside tests (host/web/main.py is always
# imported, never executed as a script, in normal use) and safe inside them.
if __name__ in {"__main__", "__mp_main__"}:
    run_web()
