"""Document preview pane (X9) — mirrors host/web/corpus_view.py's detail
pane field-for-field, as a small reusable builder for the organize/run
screen. Deliberately duplicated rather than shared/refactored with
corpus_view.py's own detail pane this sprint (per the sprint's Resolved
questions: keep the two clusters' work disjoint); a later unification is a
follow-up, not this item.

Every registry value rendered here is LLM-derived output from
attacker-controllable documents: `ui.label` only — never
`ui.markdown`/`ui.html`/`ui.code` (same untrusted-content rule
corpus_view.py, dialogs.py's step-detail view, and V11's prompt inspection
all follow).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nicegui import ui


@dataclass
class DocPane:
    """Handle to one build_doc_pane() call's widgets."""

    placeholder: ui.label
    content: ui.column
    title: ui.label
    meta: ui.label
    summary: ui.label
    provenance: ui.label
    entities: ui.column

    def show(self, record: dict) -> None:
        """Populate and reveal the pane from a registry record dict — the
        same shape host/web/corpus.py's list_documents/find_by_path
        return."""
        self.placeholder.visible = False
        self.content.visible = True
        self.title.set_text(record.get("title") or "(untitled)")
        meta_bits = [
            record.get("type") or "—",
            record.get("date") or "—",
            record.get("status") or "active",
        ]
        self.meta.set_text(" · ".join(str(bit) for bit in meta_bits))
        self.summary.set_text(record.get("summary") or "(no summary recorded)")
        self.provenance.set_text(record.get("provenance") or "(no provenance recorded)")
        self.entities.clear()
        entities = record.get("entities") or []
        with self.entities:
            if entities:
                for entity in entities:
                    name = entity.get("name") or "(unknown)"
                    role = entity.get("role") or "other"
                    kind = entity.get("kind") or "?"
                    ui.label(f"{name} — {role} ({kind})").mark("doc-entity")
            else:
                ui.label("None recorded.").classes("text-caption")

    def show_unanalyzed(self, path: Path, meta_line: str) -> None:
        """A file selected in the sidebar with no registry record yet — the
        common case mid-run, before analysis reaches it. Filename +
        size/mtime only; no extraction on the render path."""
        self.placeholder.visible = False
        self.content.visible = True
        self.title.set_text(path.name)
        self.meta.set_text(meta_line)
        self.summary.set_text("Not analyzed yet.")
        self.provenance.set_text("")
        self.entities.clear()

    def clear(self) -> None:
        self.content.visible = False
        self.placeholder.visible = True


def build_doc_pane(*, marker_prefix: str = "doc") -> DocPane:
    """Build the preview pane's widgets in the current NiceGUI context and
    return a handle. ``marker_prefix`` defaults to "doc" (the organize/run
    screen's own markers); kept parameterized so a later shared refactor
    with corpus_view.py's `corpus-detail-*` markers stays a near-zero-diff
    change rather than a breaking rename."""
    placeholder = (
        ui.label("Select a file in the tree to preview it.")
        .classes("text-caption")
        .mark(f"{marker_prefix}-detail-placeholder")
    )
    content = ui.column().classes("w-full gap-1").mark(f"{marker_prefix}-detail-content")
    content.visible = False
    with content:
        title = ui.label().classes("text-h6").mark(f"{marker_prefix}-detail-title")
        meta = ui.label().classes("text-caption").mark(f"{marker_prefix}-detail-meta")
        ui.separator()
        ui.label("Summary").classes("text-subtitle2")
        summary = ui.label().style("white-space: pre-wrap").mark(f"{marker_prefix}-detail-summary")
        ui.label("Provenance").classes("text-subtitle2")
        provenance = (
            ui.label().style("white-space: pre-wrap").mark(f"{marker_prefix}-detail-provenance")
        )
        ui.label("Entities").classes("text-subtitle2")
        entities = ui.column().classes("w-full gap-0").mark(f"{marker_prefix}-detail-entities")

    return DocPane(
        placeholder=placeholder,
        content=content,
        title=title,
        meta=meta,
        summary=summary,
        provenance=provenance,
        entities=entities,
    )
