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

import importlib.util
import os
import secrets
import socket
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import app, run, ui
from starlette.types import ASGIApp, Receive, Scope, Send

from host.agent import ApprovalResult, AskUserResult, CostApprovalResult
from host.paths import directory_overview, find_organizer_root
from host.web import corpus, journal
from host.web import security
from host.web import session as web_session
from host.web import steplog
from host.web import theme
from host.web.bridge import AgentBridge, QueryBridge
from host.web.corpus_view import build_corpus_view
from host.web.dialogs import (
    build_approval_dialog,
    build_ask_user_dialog,
    build_cost_dialog,
    build_journal_dialog,
)
from host.web.docpane import build_doc_pane
from host.web.query_view import build_query_view
from host.web.settings import build_settings_view
from host.web.shell import app_shell
from host.web.wizard import build_setup_wizard

# Native window/taskbar icon (V1) — only used when native mode is actually
# active; the browser favicon (theme.FAVICON_SVG, an inline SVG constant)
# is untouched and stays the favicon in browser mode. NiceGUI's `favicon=`
# kwarg is dual-purpose: in native mode, if it resolves to an existing local
# file, that file is also applied as the native window/taskbar icon (see
# nicegui.ui_run.run's `native_favicon` handling) — there is no separate
# "icon" kwarg.
_ICON_PATH = Path(__file__).resolve().parent / "assets" / "telcontar.ico"


@dataclass
class _RenderState:
    """Per-client render cursor for run_page — transcript/dialog/tree-refresh
    bookkeeping. The step log's own cursor lives in a separate
    steplog.StepLogState (shared with U6/U7's screens)."""

    thread_seq: int = 0
    shown_request_id: str | None = None
    fs_revision: int = 0
    last_preview_path: Path | None = None
    # #43: rendered activity-entry labels, in thread order — used to flip a
    # phase from ⏳ (ongoing) to ✔️ (done) the moment a NEWER phase starts
    # (or the run finishes), since activity entries render once via the
    # thread_seq cursor and are never re-rendered.
    activity_labels: list[ui.label] = field(default_factory=list)


def _start_run(target: Path) -> web_session.RunSession:
    return web_session.create(target)


def _native_window_args(url: str) -> dict:
    """The dict spread into pywebview's ``create_window(**window_args)``
    (X2) — split out as a pure function so its content is unit-testable on
    Linux CI, where native mode is never actually active (no `webview`).

    ``text_select=True`` is the PRIMARY fix for chat text not being
    selectable/copyable: pywebview's own default is `text_select=False`
    (``webview.window.Window.__init__``), which disables text selection for
    the whole native window regardless of any in-page CSS — independent of
    the sidebar-resize drag bug fixed separately in `shell.py`'s
    `_RESIZE_JS` below.
    """
    return {"url": url, "text_select": True}


def _load_preview(target: Path, path: Path) -> tuple[bool, dict | None, str]:
    """(is_file, record_or_None, meta_line) for the X9 document preview
    pane — runs off the event loop thread via run.io_bound: a stat or
    registry read on a possibly-network path must never block the UI.
    No extraction on this path — an unanalyzed file just gets its
    filesystem metadata, never its content read/parsed."""
    if not path.is_file():
        return False, None, ""
    record = corpus.find_by_path(target, path)
    if record is not None:
        return True, record, ""
    try:
        st = path.stat()
        modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        meta_line = f"{st.st_size:,} bytes · modified {modified}"
    except OSError:
        meta_line = ""
    return True, None, meta_line


@ui.page("/")
async def index_page() -> None:
    from config.settings import is_configured

    # Y3: app_shell's on_select is wired to the tree at mount time (below,
    # before the ui.label it needs to clear even exists), so the callback
    # goes through this mutable cell instead of a direct closure over
    # `error_label` — the cell is filled in once the label is actually
    # created, further down in this same function.
    error_label_cell: dict[str, ui.label] = {}

    def _on_select(_path: Path) -> None:
        label = error_label_cell.get("el")
        if label is not None:
            label.set_text("")

    with app_shell(on_select=_on_select) as shell:
        if not is_configured():
            ui.navigate.to("/setup")
            return

        default_target = web_session.get_default_target()
        if default_target is not None:
            session = _start_run(default_target)
            ui.navigate.to(f"/run/{session.run_id}")
            return

        ui.label("telcontar").classes("text-h5")
        ui.label("Pick a directory in the sidebar, then organize or query it:")
        error_label = ui.label().classes("text-negative").mark("startup-error")
        error_label_cell["el"] = error_label

        def _organize() -> None:
            if shell.selected is None or not shell.selected.is_dir():
                error_label.set_text("Please choose a folder to organize.")
                return
            session = _start_run(shell.selected)
            ui.navigate.to(f"/run/{session.run_id}")

        def _query() -> None:
            if shell.selected is None or not shell.selected.is_dir():
                error_label.set_text("Please choose a folder to query.")
                return
            organizer_root = find_organizer_root(shell.selected)
            if organizer_root is None:
                error_label.set_text(
                    f"No analyzed corpus found in {shell.selected} or any parent folder. "
                    "Run Organize first."
                )
                return
            session = web_session.create(organizer_root, mode="query")
            ui.navigate.to(f"/query/{session.run_id}")

        with ui.row().classes("mt-2"):
            ui.button("Use selected directory", on_click=_organize, color="primary").mark(
                "btn-startup-organize"
            )
            ui.button("Query", on_click=_query, color="positive").mark("btn-startup-query")


@ui.page("/setup")
async def setup_page() -> None:
    with app_shell(nav=False):
        await build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))


@ui.page("/settings")
async def settings_page() -> None:
    with app_shell(active="settings"):
        await build_settings_view(on_done=lambda: ui.navigate.back())


@ui.page("/run/{run_id}")
async def run_page(run_id: str) -> None:
    session = web_session.get(run_id)

    with app_shell(
        target=session.target if session is not None else None,
        session=session,
        active="conversation",
    ) as shell:
        if session is None:
            ui.label(
                "Run not found — it may have finished and been cleared, or the link is wrong."
            ).classes("text-negative")
            return
        run_session: web_session.RunSession = session  # non-None local so closures below type-check

        # Dynamic per-request title — ui.page(title=...) is bound at
        # decoration time and can't see the target directory, which is only
        # known once the URL's run_id resolves to a session (T7).
        ui.page_title(theme.window_title(session.target))

        # X1: highlight the run's root in the sidebar tree from mount —
        # `reload_tree()` only ever replaces the `nodes` prop, never
        # `selected`, so this survives every subsequent tree poll.
        shell.tree.select(str(session.target))

        # Journal toolbar affordance (U6) — placed above starter_column so
        # it's usable before a run even starts (TUI parity: `j` works from
        # mount). Count refreshes below whenever fs_revision changes. Reads
        # the journal synchronously — see build_journal_dialog's docstring
        # for why this feature doesn't go through run.io_bound.
        def _open_journal() -> None:
            build_journal_dialog(session).open()

        journal_button = (
            ui.button(
                f"Journal ({len(journal.load_entries(session.target))})",
                on_click=_open_journal,
                icon="history",
            )
            .props("flat dense")
            .mark("btn-open-journal")
        )

        # X1: the target directory stays visible in the main content area
        # for the whole run, not just before it starts — plain label, not
        # clickable (X5's reveal-in-explorer helper is a different item;
        # wiring this to it would make X1 depend on a not-yet-built piece
        # for a nicety).
        ui.label(str(session.target)).classes("text-caption ellipsis").mark(
            "run-target-path"
        ).tooltip(str(session.target))

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
            # X9: document preview pane, reusing the corpus browser's (V5)
            # detail-pane approach — triggered by polling shell.selected in
            # _refresh() below, never by wiring through app_shell()/the
            # sidebar tree directly (keeps this out of host/web/shell.py,
            # which already has heavy contention this sprint).
            with ui.row().classes("w-full no-wrap items-start gap-4"):
                with ui.column().classes("w-2/3"):
                    conversation_column = ui.column().classes("w-full")
                with ui.column().classes("w-1/3").mark("doc-preview"):
                    doc_pane = build_doc_pane()
            status_label = ui.label()
            # V14/#40: a row, not a bare bar. The integer-percent label lives
            # ON the bar itself (absolute-center inside q-linear-progress),
            # replacing Quasar's default raw-float value label — show_value=False
            # is what kills the "0.16949…" that NiceGUI otherwise binds inside
            # the bar, and our own label is the human-readable "17%". The bar's
            # height comes from `size` (default 20px with the built-in label);
            # with show_value=False the default size collapses to 4px, so a
            # non-default size keeps the on-bar label legible.
            with ui.row().classes("w-full items-center gap-2").mark("progress-row") as progress_row:
                progress_bar = ui.linear_progress(value=0.0, show_value=False, size="16px").classes(
                    "flex-grow"
                )
                with progress_bar:
                    progress_percent_label = (
                        ui.label().classes("absolute-center text-white").mark("progress-percent")
                    )
                progress_current_label = ui.label().classes("text-caption").mark("progress-current")
            progress_row.visible = False
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

            # TUI parity: OrganizerScreen's "g" binding, gated on _done.
            # X11: reuse an existing query session for this target rather
            # than minting a fresh one (and its own MCP subprocess) on every
            # click — same reuse the nav shell's Query tab uses.
            def _query_corpus() -> None:
                query_session = web_session.find_by_target(
                    session.target, mode="query"
                ) or web_session.create(session.target, mode="query")
                ui.navigate.to(f"/query/{query_session.run_id}")

            query_button = (
                ui.button("Query this corpus", on_click=_query_corpus, icon="chat")
                .props("flat dense")
                .mark("btn-query-corpus")
            )
            query_button.visible = session.done

            # V5: same session — corpus_page only reads session.target, so
            # there's no reason to spin up a second RunSession just to browse.
            def _browse_corpus() -> None:
                ui.navigate.to(f"/corpus/{session.run_id}")

            corpus_button = (
                ui.button("Browse corpus", on_click=_browse_corpus, icon="table_chart")
                .props("flat dense")
                .mark("btn-browse-corpus")
            )
            corpus_button.visible = session.done

            # Internal-step log strip (T6) — pinned at the bottom, always
            # visible, distinct from the conversation above: telcontar's own
            # tool activity never renders as a chat bubble. It's the
            # finer-grained scrolling one-line-per-tool-call history, each
            # with a toggle that opens the full payload in shell's
            # left-sidebar detail section (V13b). The macro-phase history
            # (V16: "Reading documents…", "Planning changes…") used to live
            # in its own column here; X3 interleaves it into
            # conversation_column instead, chronologically with the turns.
            ui.separator()
            log_column = (
                ui.column()
                .classes("w-full overflow-auto q-px-sm q-gutter-none")
                .style("max-height: 25vh; min-height: 25vh;")
            )

        render_state = _RenderState()
        step_log_state = steplog.StepLogState()

        def _render_turn(item: web_session.TranscriptItem) -> None:
            # V13a: `sent=` already picks the right side, but NiceGUI's
            # `.nicegui-column` CSS (`align-items: flex-start`) shrink-wraps
            # every chat-message to its content width, hiding that alignment
            # — `.classes("w-full")` on the message itself is the fix.
            # bg-color/text-color are genuine QChatMessage props (Quasar
            # renders them as `text-<color>` utility classes under the hood,
            # relying on `.q-message-text { background: currentColor }`)
            # resolved against theme.PALETTE via run_web()'s app.colors().
            is_user = item.speaker == "user"
            bubble_props = (
                "bg-color=secondary text-color=dark"
                if is_user
                else "bg-color=primary text-color=dark"
            )
            with conversation_column:
                ui.chat_message(item.text, name=item.speaker, sent=is_user).classes("w-full").props(
                    bubble_props
                )

        def _flip_activity_done() -> None:
            # Rewrite the newest rendered activity label from ⏳ to ✔️. Called
            # when a NEWER phase begins (so the just-previous one is no longer
            # ongoing) and when the run finishes (so the final phase reads as
            # done, not stuck spinning).
            if render_state.activity_labels:
                newest = render_state.activity_labels[-1]
                if newest.text.startswith("⏳"):
                    newest.set_text("✔️" + newest.text[1:])

        def _render_activity(entry: web_session.ActivityEntry) -> None:
            # #43: a phase renders ⏳ while it is the newest activity entry
            # (nothing has superseded it yet) and flips to ✔️ the moment a
            # newer phase starts or the run finishes — _flip_activity_done
            # rewrites already-rendered labels. Left-aligned (telcontar's own
            # side of the thread), small and grey so it reads as status, not a
            # chat turn. A "done" run renders every phase ✔️ from the start
            # (a page reload mid-run correctly shows only the final phase ⏳).
            with conversation_column:
                label = (
                    ui.label(f"{'⏳' if not session.done else '✔️'} {entry.text}")
                    .classes("text-xs text-grey-6")
                    .mark("activity-entry")
                )
            if render_state.activity_labels:
                _flip_activity_done()
            render_state.activity_labels.append(label)

        def _render_thread_item(
            item: web_session.TranscriptItem | web_session.ActivityEntry,
        ) -> None:
            # X3: activity/stage entries interleave chronologically with
            # conversation turns (both share session._seq) instead of
            # rendering in a separate column.
            if isinstance(item, web_session.ActivityEntry):
                _render_activity(item)
            else:
                _render_turn(item)

        def _show_pending_dialog() -> None:
            pending = session.pending
            if pending is None or pending.request_id == render_state.shown_request_id:
                return
            render_state.shown_request_id = pending.request_id

            match pending.kind:
                case "approval":
                    build_approval_dialog(session, pending).open()
                case "cost":
                    build_cost_dialog(session, pending).open()
                case "ask":
                    build_ask_user_dialog(session, pending).open()

        async def _refresh() -> None:
            for item in session.thread():
                if item.seq > render_state.thread_seq:
                    _render_thread_item(item)
                    render_state.thread_seq = item.seq

            # #43: the final phase must read as done once the run finishes.
            # The "done"/"error" turn is already in the thread; flip the
            # newest activity label (still ⏳ from when it was live) to ✔️.
            if session.done and render_state.activity_labels:
                _flip_activity_done()

            steplog.sync_steps(log_column, shell, step_log_state, session.steps)

            line = session.status
            if session.tokens:
                line = f"{session.status}   ·   ⬍ {session.tokens}"
            status_label.set_text(line)

            total = session.progress.get("total", 0)
            if total:
                progress_row.visible = True
                fraction = session.progress.get("analyzed", 0) / total
                progress_bar.set_value(fraction)
                progress_percent_label.set_text(f"{round(fraction * 100)}%")
                current = session.progress.get("current") or []
                if current:
                    extra = f" +{len(current) - 1}" if len(current) > 1 else ""
                    progress_current_label.set_text(f"{current[0]}{extra}")
                else:
                    progress_current_label.set_text("")
            else:
                progress_row.visible = False

            _show_pending_dialog()
            chat_input.enabled = session.started
            if session.started:
                starter_column.visible = False
                main_column.visible = True
            query_button.visible = session.done
            corpus_button.visible = session.done

            # Sidebar tree + journal count refresh (U4/U6) — only when a
            # tree-mutating tool (or an undo) actually changed something
            # since the last tick, never on every 0.5s poll. reload_tree()
            # (V7) is the one writer for tree.props["nodes"] — it also backs
            # the manual refresh button and the periodic tree poll, and
            # already reads expansion state from the tree's own Quasar
            # `expanded` prop so a refresh doesn't collapse whatever the user
            # had open.
            if session.fs_revision != render_state.fs_revision:
                render_state.fs_revision = session.fs_revision
                await shell.reload_tree()
                journal_button.set_text(f"Journal ({len(journal.load_entries(session.target))})")

            # X9: document preview pane — only stat/look up when the sidebar
            # selection actually changed since the last tick, never on
            # every 0.5s poll.
            if shell.selected != render_state.last_preview_path:
                render_state.last_preview_path = shell.selected
                if shell.selected is None:
                    doc_pane.clear()
                else:
                    preview = await run.io_bound(_load_preview, run_session.target, shell.selected)
                    if (
                        preview is None
                    ):  # io_bound returns None on cancellation/shutdown (NiceGUI interim shape)
                        doc_pane.clear()
                    else:
                        is_file, record, meta_line = preview
                        if not is_file:
                            doc_pane.clear()
                        elif record is not None:
                            doc_pane.show(record)
                        else:
                            doc_pane.show_unanalyzed(shell.selected, meta_line)

        ui.timer(web_session.REFRESH_INTERVAL, _refresh)


@ui.page("/query/{run_id}")
async def query_page(run_id: str) -> None:
    session = web_session.get(run_id)

    with app_shell(target=session.target if session is not None else None, active="query") as shell:
        if session is None:
            ui.label(
                "Run not found — it may have finished and been cleared, or the link is wrong."
            ).classes("text-negative")
            return

        ui.page_title(theme.window_title(session.target))

        if not session.started:
            QueryBridge(session).start()

        build_query_view(shell, session)


@ui.page("/corpus/{run_id}")
async def corpus_page(run_id: str) -> None:
    session = web_session.get(run_id)

    with app_shell(
        target=session.target if session is not None else None,
        session=session,
        active="corpus",
    ):
        if session is None:
            ui.label(
                "Run not found — it may have finished and been cleared, or the link is wrong."
            ).classes("text-negative")
            return

        ui.page_title(theme.window_title(session.target))
        await build_corpus_view(session)


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


_DENY_BODY = (
    b"telcontar: this local server requires the launch token -- "
    b"close this window and relaunch telcontar."
)


def _header(headers: dict[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name)
    return value.decode("latin-1") if value is not None else None


class _AuthMiddleware:
    """Pure-ASGI (V2) — not ``BaseHTTPMiddleware``: that base class only
    forwards ``http``-type scopes, and NiceGUI's socket.io upgrade at
    ``/_nicegui_ws/`` is a ``websocket``-type scope that must be covered
    too, or the auth check would only ever apply to the initial page load
    and never to the connection actually driving the UI.

    Registered on ``app`` only when NOT running under NiceGUI's headless
    test fixture (see run_web()) — that fixture drives the app through a
    raw ASGI transport with no token/cookie and a ``Host: test`` header,
    so an unconditionally-registered middleware would 403 every test in
    tests/test_web_ui.py.
    """

    def __init__(self, asgi_app: ASGIApp) -> None:
        self._app = asgi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        decision = security.authorize(
            host_header=_header(headers, b"host"),
            origin_header=_header(headers, b"origin"),
            cookie_header=_header(headers, b"cookie"),
            query_string=(scope.get("query_string") or b"").decode("latin-1"),
        )

        if not decision.allowed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": _DENY_BODY})
            return

        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                # The approval gate is the highest-trust screen in the
                # product; these two headers close the one browser-borne
                # attack a valid token doesn't stop (framing the app inside
                # an attacker page that guesses the port).
                extra = [
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"frame-ancestors 'none'"),
                ]
                if decision.set_cookie:
                    cookie = security.build_cookie_header()
                    extra.append((b"set-cookie", cookie.encode("latin-1")))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self._app(scope, receive, _send_wrapper)


def run_web(target: Path | None = None, *, native: bool = True) -> None:
    """Launch the NiceGUI web UI. Blocks until the server stops.

    ``native`` (default True — "one command, one window") opens a native
    pywebview window instead of the system browser; host/main.py's
    ``--browser`` flag is the escape hatch that passes False. Actual native
    availability is re-checked here rather than trusted blindly: pywebview
    is a Windows-only dependency (see pyproject.toml's platform marker), and
    even on Windows it may not be installed in a given environment. If
    native was requested but isn't usable, this falls back to the browser
    with a stderr warning instead of hard-exiting — NiceGUI's own
    native-mode path calls sys.exit(1) on a missing `webview`, which would
    be unacceptable now that this is the default entrypoint (U10).
    """
    web_session.set_default_target(target)

    effective_native = (
        native and sys.platform == "win32" and importlib.util.find_spec("webview") is not None
    )
    if native and not effective_native:
        print(
            "Native window mode isn't available here (pywebview not installed, or not "
            "on Windows) — falling back to the browser. Pass --browser to skip this "
            "check next time.",
            file=sys.stderr,
        )

    @app.on_shutdown
    def _reject_pending_on_shutdown() -> None:
        for session in web_session.all_sessions():
            if session.pending is not None:
                result: ApprovalResult | CostApprovalResult | AskUserResult
                match session.pending.kind:
                    case "approval":
                        result = ApprovalResult(approved=False)
                    case "cost":
                        result = CostApprovalResult(approved=False)
                    case "ask":
                        result = AskUserResult(reply="", provided=False)
                session.resolve_pending(result)
            # U7: every organize/query session leaves its MCP server
            # subprocess running for the process's lifetime (nothing ever
            # calls web_session.close() today) — a real lifecycle is future
            # work, but at minimum don't leave the driving task itself
            # running past shutdown.
            if session.task is not None:
                session.task.cancel()

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

    # V2 — local-server hardening. 127.0.0.1-only binding (below) keeps the
    # server off the LAN, but any other local process can still reach a
    # loopback port; the token/Origin/Host checks in host/web/security.py
    # are the defense-in-depth layer for that. Skipped only under NiceGUI's
    # headless test fixture, which drives the app through a raw ASGI
    # transport with no token and a `Host: test` header — see
    # _AuthMiddleware's docstring for why an unconditional registration
    # would break every test in tests/test_web_ui.py.
    token = security.new_token()
    security.configure(token, port)
    if os.environ.get("NICEGUI_USER_SIMULATION") != "true":
        app.add_middleware(_AuthMiddleware)

    if effective_native:
        # Picked up by native_mode._open_window(), which spreads
        # app.native.window_args after its own default `url` key — an
        # explicit `url` here wins. Must be set before ui.run() spawns the
        # native-window process (window_args needs to survive the pickle).
        app.native.window_args.update(
            _native_window_args(f"http://127.0.0.1:{port}/?token={token}")
        )

    # reload=False is load-bearing, not a style choice: with reload=True,
    # uvicorn forces a SelectorEventLoop on Windows (its `use_subprocess`
    # flag), where asyncio.create_subprocess_exec raises NotImplementedError
    # — mcp_session's server subprocess launch would be dead on Windows.
    # Never bind 0.0.0.0 either: it triggers a Windows Firewall prompt and
    # would expose the approval gate on the LAN. dark=True is load-bearing
    # too: Quasar only honours the dark/dark_page palette tokens above in
    # dark mode. storage_secret is a fresh value per launch (V2) — this app
    # uses no app.storage.* today, so this is enablement + defence-in-depth,
    # not a fix for an existing read.
    ui.run(
        host="127.0.0.1",
        port=port,
        show=False if effective_native else f"/?token={token}",
        reload=False,
        title=theme.window_title(),
        dark=True,
        favicon=str(_ICON_PATH) if effective_native and _ICON_PATH.is_file() else theme.FAVICON_SVG,
        native=effective_native,
        window_size=(1280, 860) if effective_native else None,
        storage_secret=secrets.token_urlsafe(32),
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
