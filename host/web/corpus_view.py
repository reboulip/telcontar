"""Corpus browser view (V5) — a sortable, filterable table over the document
registry (host/web/corpus.py), with a detail pane for the full summary,
provenance, and entities of whichever row is selected. Merges the former V4
(document preview) into one screen.

Read-only, and reachable only from a document's own row — no MCP call, no
agent turn, no sidebar-tree wiring (that would need a new `on_preview` kwarg
on `app_shell()`, out of scope here per sprint-brief.md's V11/V5 decision).

Every registry value rendered here is LLM-derived output from
attacker-controllable documents: `ui.label`/`ui.table` row values only —
never `ui.markdown`/`ui.html`/`ui.code`, which would interpret it as markup
instead of displaying it as text (same rule V13b's step-detail view and
V11's prompt inspection already follow for untrusted content).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nicegui import run, ui
from nicegui.events import GenericEventArguments

from host.web import corpus
from host.web import session as web_session
from host.web.session import RunSession

_SUMMARY_PREVIEW_CHARS = 120

_COLUMNS: list[
    dict
] = [  # explicit annotation avoids mypy inferring list[object] from the mixed-value dict literal
    {"name": "title", "label": "Title", "field": "title", "align": "left", "sortable": True},
    {"name": "type", "label": "Type", "field": "type", "align": "left", "sortable": True},
    {"name": "date", "label": "Date", "field": "date", "align": "left", "sortable": True},
    {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
    {"name": "summary", "label": "Summary", "field": "summary", "align": "left"},
    {"name": "entities", "label": "Entities", "field": "entities", "align": "left"},
]


def _entities_preview(entities: list[dict]) -> str:
    if not entities:
        return ""
    names = [str(e.get("name") or "(unknown)") for e in entities[:3]]
    preview = ", ".join(names)
    if len(entities) > 3:
        preview += f" +{len(entities) - 3}"
    return preview


def _to_row(record: dict) -> dict:
    # ui.table forbids list-valued cells (crashes the browser) — entities
    # must be pre-flattened to a display string; the full list still lives
    # in the record dict for the detail pane below.
    summary = record.get("summary") or ""
    preview = (
        summary
        if len(summary) <= _SUMMARY_PREVIEW_CHARS
        else summary[:_SUMMARY_PREVIEW_CHARS] + "…"
    )
    return {
        "checksum": record["checksum"],
        "title": record.get("title") or "(untitled)",
        "type": record.get("type") or "",
        "date": record.get("date") or "",
        "status": record.get("status") or "active",
        "summary": preview,
        "entities": _entities_preview(record.get("entities") or []),
    }


@dataclass
class _CorpusViewState:
    """Mutable state for one build_corpus_view() call, held outside any
    widget. ``selected_checksum``/``current_filter`` let a later data reload
    (X10) restore what the user was looking at instead of resetting it;
    ``records``/``records_by_checksum``/``all_rows`` are the reloadable data
    itself (lifted out of local variables so `_reload` can replace them);
    ``reloading``/``last_mtime`` back the same re-entrancy-guard and
    skip-unchanged disciplines `Shell.reload_tree` already established."""

    selected_checksum: str | None = None
    current_filter: str = ""
    reloading: bool = False
    last_mtime: tuple[float, int] | None = None
    records: list[dict] = field(default_factory=list)
    records_by_checksum: dict[str, dict] = field(default_factory=dict)
    all_rows: list[dict] = field(default_factory=list)


async def build_corpus_view(session: RunSession) -> None:
    # `run.io_bound` returns None (not the callback's result) when the call
    # is cancelled or the app is stopping — `or []` treats that the same as
    # a genuinely empty registry, matching this file's existing
    # directory_overview-style call sites elsewhere in host/web/main.py.
    records = await run.io_bound(corpus.list_documents, session.target) or []
    last_mtime = await run.io_bound(corpus.registry_mtime, session.target)
    state = _CorpusViewState(
        records=records,
        records_by_checksum={rec["checksum"]: rec for rec in records},
        all_rows=[_to_row(rec) for rec in records],
        last_mtime=last_mtime,
    )

    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Corpus browser").classes("text-h5")
        ui.button(icon="refresh", on_click=lambda: _reload()).props("flat dense round").mark(
            "btn-corpus-refresh"
        ).tooltip("Refresh corpus")

    # X10: built unconditionally (not an early return) so a later poll can
    # fill the table in once analysis produces records — only this label's
    # visibility toggles on empty/non-empty.
    empty_label = (
        ui.label(
            "No analyzed documents yet — run Organize first, or check back once "
            "analysis has produced at least one record."
        )
        .classes("text-caption")
        .mark("corpus-empty")
    )
    empty_label.visible = not records

    search_input = (
        ui.input("Search title, type, or summary…").classes("w-full").mark("corpus-search")
    )

    with ui.row().classes("w-full no-wrap items-start gap-4"):
        with ui.column().classes("w-2/3"):
            table = (
                ui.table(
                    columns=_COLUMNS,
                    rows=state.all_rows,
                    row_key="checksum",
                    pagination=10,
                )
                .classes("w-full cursor-pointer")
                .mark("corpus-table")
            )

        with ui.column().classes("w-1/3 gap-1").mark("corpus-detail"):
            detail_placeholder = (
                ui.label("Select a row to see its details.")
                .classes("text-caption")
                .mark("corpus-detail-placeholder")
            )
            detail_content = ui.column().classes("w-full gap-1").mark("corpus-detail-content")
            detail_content.visible = False
            with detail_content:
                detail_title = ui.label().classes("text-h6").mark("corpus-detail-title")
                detail_meta = ui.label().classes("text-caption").mark("corpus-detail-meta")
                detail_location = (
                    ui.label().classes("text-caption text-grey").mark("corpus-detail-location")
                )
                ui.separator()
                ui.label("Summary").classes("text-subtitle2")
                detail_summary = (
                    ui.label().style("white-space: pre-wrap").mark("corpus-detail-summary")
                )
                ui.label("Provenance").classes("text-subtitle2")
                detail_provenance = (
                    ui.label().style("white-space: pre-wrap").mark("corpus-detail-provenance")
                )
                ui.label("Entities").classes("text-subtitle2")
                detail_entities = ui.column().classes("w-full gap-0").mark("corpus-detail-entities")

    def _show_detail(checksum: str) -> None:
        record = state.records_by_checksum.get(checksum)
        if record is None:
            # X10: the previously-selected document vanished from the
            # registry across a reload (e.g. quarantined) — collapse back
            # to the placeholder rather than show stale content.
            state.selected_checksum = None
            detail_content.visible = False
            detail_placeholder.visible = True
            return
        detail_placeholder.visible = False
        detail_content.visible = True
        detail_title.set_text(record.get("title") or "(untitled)")
        meta_bits = [
            record.get("type") or "—",
            record.get("date") or "—",
            record.get("status") or "active",
        ]
        detail_meta.set_text(" · ".join(str(bit) for bit in meta_bits))
        path_str = record.get("path") or ""
        location = path_str
        if path_str:
            try:
                location = str(Path(path_str).relative_to(session.target))
            except (ValueError, OSError):
                location = path_str
        detail_location.set_text(f"Location: {location}" if location else "")
        detail_summary.set_text(record.get("summary") or "(no summary recorded)")
        detail_provenance.set_text(record.get("provenance") or "(no provenance recorded)")
        detail_entities.clear()
        entities = record.get("entities") or []
        with detail_entities:
            if entities:
                for entity in entities:
                    name = entity.get("name") or "(unknown)"
                    role = entity.get("role") or "other"
                    kind = entity.get("kind") or "?"
                    ui.label(f"{name} — {role} ({kind})").mark("corpus-entity")
            else:
                ui.label("None recorded.").classes("text-caption")

    def _on_row_click(e: GenericEventArguments) -> None:
        # Quasar's row-click payload is [evt, row, index] — the DOM event
        # comes first, not the row; guard defensively rather than assume
        # the shape (X12).
        if not isinstance(e.args, list) or len(e.args) < 2:
            return
        row = e.args[1]
        if not isinstance(row, dict):
            return
        checksum = row.get("checksum")
        if not checksum:
            return
        state.selected_checksum = checksum
        _show_detail(checksum)

    table.on("rowClick", _on_row_click)

    def _apply_filter(value: str) -> None:
        state.current_filter = value
        needle = value.strip().lower()
        rows = (
            state.all_rows
            if not needle
            else [
                row
                for row in state.all_rows
                if row["checksum"]
                in {
                    rec["checksum"]
                    for rec in state.records
                    if needle in (rec.get("title") or "").lower()
                    or needle in (rec.get("type") or "").lower()
                    or needle in (rec.get("summary") or "").lower()
                }
            ]
        )
        if rows != table.rows:
            table.rows = rows

    search_input.on_value_change(lambda e: _apply_filter(e.value or ""))

    async def _reload() -> None:
        # Re-entrancy guard + skip-when-unchanged: the same two disciplines
        # Shell.reload_tree already established (host/web/shell.py) — a
        # ui.table.rows replacement re-renders and resets pagination/sort,
        # so "does not disturb the user" means not touching it at all when
        # nothing changed, not just being fast about it.
        if state.reloading:
            return
        state.reloading = True
        try:
            mtime = await run.io_bound(corpus.registry_mtime, session.target)
            if mtime is not None and mtime == state.last_mtime:
                return
            state.last_mtime = mtime
            records = await run.io_bound(corpus.list_documents, session.target)
            if records is None:
                return
            state.records = records
            state.records_by_checksum = {rec["checksum"]: rec for rec in records}
            state.all_rows = [_to_row(rec) for rec in records]
            empty_label.visible = not records
            _apply_filter(state.current_filter)
            if state.selected_checksum is not None:
                _show_detail(state.selected_checksum)
        finally:
            state.reloading = False

    ui.timer(web_session.CORPUS_POLL_INTERVAL, _reload)
