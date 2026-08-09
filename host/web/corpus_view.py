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

from nicegui import run, ui
from nicegui.events import TableSelectionEventArguments

from host.web import corpus
from host.web.session import RunSession

_SUMMARY_PREVIEW_CHARS = 120

_COLUMNS = [
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


async def build_corpus_view(session: RunSession) -> None:
    records = await run.io_bound(corpus.list_documents, session.target)
    records_by_checksum = {rec["checksum"]: rec for rec in records}

    ui.label("Corpus browser").classes("text-h5")

    if not records:
        ui.label(
            "No analyzed documents yet — run Organize first, or check back once "
            "analysis has produced at least one record."
        ).classes("text-caption").mark("corpus-empty")
        return

    all_rows = [_to_row(rec) for rec in records]

    search_input = (
        ui.input("Search title, type, or summary…").classes("w-full").mark("corpus-search")
    )

    with ui.row().classes("w-full no-wrap items-start gap-4"):
        with ui.column().classes("w-2/3"):
            table = (
                ui.table(
                    columns=_COLUMNS,
                    rows=all_rows,
                    row_key="checksum",
                    selection="single",
                    pagination=10,
                )
                .classes("w-full")
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
        record = records_by_checksum.get(checksum)
        if record is None:
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

    def _on_select(e: TableSelectionEventArguments) -> None:
        if e.selection:
            _show_detail(e.selection[0]["checksum"])

    table.on_select(_on_select)

    def _apply_filter(value: str) -> None:
        needle = value.strip().lower()
        if not needle:
            table.rows = all_rows
            return
        matches = {
            rec["checksum"]
            for rec in records
            if needle in (rec.get("title") or "").lower()
            or needle in (rec.get("type") or "").lower()
            or needle in (rec.get("summary") or "").lower()
        }
        table.rows = [row for row in all_rows if row["checksum"] in matches]

    search_input.on_value_change(lambda e: _apply_filter(e.value or ""))
