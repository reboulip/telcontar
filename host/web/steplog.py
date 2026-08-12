"""Shared internal-step log-strip rendering (T5/T6) — lifted out of
host/web/main.py's run_page so U6 (journal view) and U7 (query view) can
reuse the same "one compact line per step, toggle opens full detail in the
shell's drawer" idiom instead of re-deriving it per screen.

Still NiceGUI (renders ui.row/ui.label/ui.button), unlike session.py/tree.py
— only the pure formatting (`fmt_step_line`) has no framework dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import ui

from host.web.session import StepRecord
from host.web.shell import Shell

_STEP_GLYPHS = {"running": "▶", "ok": "·", "error": "✗"}

_MAX_LOG_ROWS = 500


@dataclass
class StepLogState:
    """Per-client render cursor for one screen's step log. ``step_rows``
    holds each rendered step's (row, label) elements, keyed by seq, so a
    step that was "running" when first rendered can have its glyph/summary
    updated in place once it closes — StepRecords mutate after creation,
    unlike TranscriptItems."""

    step_seq: int = 0
    step_rows: dict[int, tuple[ui.row, ui.label]] = field(default_factory=dict)


def fmt_step_line(step: StepRecord) -> str:
    return f"{_STEP_GLYPHS[step.status]} {step.summary}"


def render_step_row(
    log_column: ui.column, shell: Shell, step: StepRecord
) -> tuple[ui.row, ui.label]:
    with log_column:
        row = ui.row().classes("w-full items-center q-gutter-xs no-wrap")
        with row:
            label = ui.label(fmt_step_line(step)).classes(
                "whitespace-pre font-mono text-xs ellipsis flex-grow"
            )
            ui.button(
                icon="code",
                on_click=lambda: shell.show_detail(step.summary, step.detail or "(pending…)"),
            ).props("flat dense size=xs").mark(f"step-detail-{step.seq}")
    return row, label


def prune_log(state: StepLogState) -> None:
    while len(state.step_rows) > _MAX_LOG_ROWS:
        oldest_seq = next(iter(state.step_rows))
        row, _label = state.step_rows.pop(oldest_seq)
        row.delete()


def sync_steps(
    log_column: ui.column, shell: Shell, state: StepLogState, steps: list[StepRecord]
) -> None:
    """Render any steps newer than ``state.step_seq``, and refresh the text
    of already-rendered rows whose status/summary changed since (a step
    that was "running" is updated in place once it closes)."""
    for step in steps:
        if step.seq > state.step_seq:
            state.step_rows[step.seq] = render_step_row(log_column, shell, step)
            state.step_seq = step.seq
            prune_log(state)
        else:
            entry = state.step_rows.get(step.seq)
            if entry is not None:
                entry[1].set_text(fmt_step_line(step))
