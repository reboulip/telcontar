"""Query view (U7) — read-only NL Q&A over an already-analyzed corpus.

Reuses the same conversation-column + step-log-strip idiom run_page
(organize mode) already established (T5/T6) rather than the TUI
QueryScreen's side-by-side RichLog split — Phase 20 is parity with a
cleaner surface, not a redesign, and the log strip already IS the web UI's
"tool timeline". No approval/cost dialog wiring here at all: query mode is
read-only by construction (QUERY_ALLOWED_TOOLS), so there is nothing to
gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from host.web import session as web_session
from host.web import steplog
from host.web.shell import Shell


@dataclass
class _RenderState:
    turn_seq: int = 0


def build_query_view(shell: Shell, session: web_session.RunSession) -> None:
    conversation_column = ui.column().classes("w-full")
    status_label = ui.label()
    with ui.row().classes("w-full items-center"):
        question_input = (
            ui.input("Ask a question about this corpus…").classes("flex-grow").mark("query-input")
        )

        def _send() -> None:
            text = question_input.value.strip()
            if not text:
                return
            question_input.value = ""
            session.add_turn("user", text)
            session.messages.put_nowait(text)

        question_input.on("keydown.enter", lambda _: _send())
        ui.button("Ask", on_click=_send).mark("btn-query-ask")

    ui.separator()
    log_column = (
        ui.column()
        .classes("w-full overflow-auto q-px-sm q-gutter-none")
        .style("max-height: 40vh; min-height: 40vh;")
    )

    render_state = _RenderState()
    step_log_state = steplog.StepLogState()

    def _render_turn(item: web_session.TranscriptItem) -> None:
        # V13a: alignment + bubble colour — see host/web/main.py's
        # _render_turn (same idiom, duplicated rather than shared, matching
        # this pair's existing duplication) for why `.classes("w-full")` and
        # bg-color/text-color are the right fix.
        is_user = item.speaker == "user"
        bubble_props = (
            "bg-color=secondary text-color=dark" if is_user else "bg-color=primary text-color=dark"
        )
        with conversation_column:
            ui.chat_message(item.text, name=item.speaker, sent=is_user).classes("w-full").props(
                bubble_props
            )

    def _refresh() -> None:
        for item in session.transcript:
            if item.seq > render_state.turn_seq:
                _render_turn(item)
                render_state.turn_seq = item.seq

        steplog.sync_steps(log_column, shell, step_log_state, session.steps)

        line = session.status
        if session.tokens:
            line = f"{session.status}   ·   ⬍ {session.tokens}"
        status_label.set_text(line)

    ui.timer(web_session.REFRESH_INTERVAL, _refresh)
