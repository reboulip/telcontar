"""Approval (U4) / cost estimate (U5) dialog builders — one per
PendingRequest kind.

Both dialogs are `.props("persistent")`: no backdrop-click or Esc dismissal.
The original inline dialog (host/web/main.py, before U4) was a plain
`ui.dialog()` — closeable without resolving its future, which leaves
`RunSession.pending` set and the run silently deadlocked with zero visible
symptom (the same failure mode ROADMAP.md's Break 1 spike found and fixed
for the reload path; this closes the same door for the dialog-dismissal
path). Every resolution therefore goes through an explicit button, and every
resolution is request-scoped (`session.resolve_pending(result,
request_id=pending.request_id)`) so a stale dialog left over in another tab
or after a reload can't resolve a different, newer pending request.
"""

from __future__ import annotations

from nicegui import ui

from host.agent import ApprovalResult, CostApprovalResult
from host.format import fmt_op, render_target_layout
from host.web.session import PendingRequest, RunSession


def build_approval_dialog(session: RunSession, pending: PendingRequest) -> ui.dialog:
    plan_data = pending.payload["plan_data"]
    ops = plan_data.get("ops", [])
    rationale = (plan_data.get("rationale") or "").strip()
    folder_notes = plan_data.get("folder_notes") or {}
    layout_lines = render_target_layout(ops, folder_notes)
    ops_json_path = (plan_data.get("ops_json_path") or "").strip()

    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("w-full max-w-3xl"):
        ui.label(f"Plan Review · {pending.payload['plan_id'][:8]} · {len(ops)} op(s)").classes(
            "text-h6"
        )

        if rationale:
            ui.label("Model-generated rationale — not verified fact").classes(
                "text-caption text-grey"
            )
            ui.label(rationale).mark("plan-rationale")

        if layout_lines:
            ui.separator()
            ui.label("Target layout").classes("text-subtitle2")
            if folder_notes:
                ui.label("Folder notes are model-generated — not verified fact.").classes(
                    "text-caption text-grey"
                )
            with ui.column().classes("max-h-40 overflow-auto"):
                ui.label("\n".join(layout_lines)).classes("whitespace-pre font-mono text-xs").mark(
                    "target-layout"
                )

        ui.separator()
        checkboxes: dict[str, ui.checkbox] = {}
        with ui.column().classes("max-h-64 overflow-auto w-full"):
            if ops:
                for op in ops:
                    op_id = op.get("op_id", "")
                    checkboxes[op_id] = ui.checkbox(
                        fmt_op(op, session.target, markup=False), value=True
                    ).mark(f"op-{op_id}")
            else:
                ui.label("No operations in this plan.").classes("text-grey")

        if ops_json_path:
            ui.label(f"Full ops JSON: {ops_json_path}").classes("text-caption text-grey").mark(
                "ops-json-path"
            )

        ui.separator()
        ui.label(
            "Not quite right? Describe the changes and Refine (e.g. "
            '"merge the drafts into one folder", "don\'t quarantine the specs"):'
        ).classes("text-caption text-grey")
        refine_input = (
            ui.input(placeholder="Describe changes to make, then press Refine…")
            .classes("w-full")
            .mark("refine-input")
        )

        def _resolve(result: ApprovalResult) -> None:
            session.resolve_pending(result, request_id=pending.request_id)
            dialog.close()

        def _approve() -> None:
            removed = [op_id for op_id, cb in checkboxes.items() if not cb.value and op_id]
            _resolve(ApprovalResult(approved=True, removed_op_ids=removed))

        def _refine() -> None:
            text = refine_input.value.strip()
            if not text:
                return  # nothing to refine — keep the dialog open
            _resolve(ApprovalResult(approved=False, refinement=text))

        def _reject() -> None:
            _resolve(ApprovalResult(approved=False))

        with ui.row().classes("w-full justify-end"):
            ui.button("Approve", on_click=_approve, color="positive").mark("approve-btn")
            ui.button("Refine", on_click=_refine, color="primary").mark("refine-btn")
            ui.button("Reject", on_click=_reject, color="negative").mark("reject-btn")

    return dialog


def build_cost_dialog(session: RunSession, pending: PendingRequest) -> ui.dialog:
    """Faithful port of the TUI's CostEstimateModal (U5). Composes its text
    from the engine-side ``data`` dict (new/already_analyzed/estimated_tokens/
    batch_size) rather than the pre-rendered ``summary`` string — the
    ``data`` dict is the source of truth (matches the approval dialog's
    ``plan_data``-driven approach); ``summary`` is kept only as a fallback
    for a caller that passes no ``data`` (e.g. an empty dict in a test).
    """
    data = pending.payload.get("data") or {}

    dialog = ui.dialog().props("persistent")
    with dialog, ui.card():
        ui.label("Analyze this corpus?").classes("text-h6")

        if data:
            summary_text = (
                f"{data.get('new', 0)} new document(s) "
                f"({data.get('already_analyzed', 0)} already analyzed, skipped), "
                f"~{data.get('estimated_tokens', 0)} input tokens estimated, "
                f"batched in groups of {data.get('batch_size', 10)}."
            )
        else:
            summary_text = pending.payload["summary"]
        ui.label(summary_text).mark("cost-summary")

        ui.label(
            "A rough estimate from file sizes, not a real tokenization. "
            "Covers analysis only — organizing the corpus afterward adds more."
        ).classes("text-caption text-grey")

        def _resolve(result: CostApprovalResult) -> None:
            session.resolve_pending(result, request_id=pending.request_id)
            dialog.close()

        with ui.row():
            ui.button(
                "Proceed",
                on_click=lambda: _resolve(CostApprovalResult(approved=True)),
                color="positive",
            ).mark("cost-proceed")
            ui.button(
                "Cancel",
                on_click=lambda: _resolve(CostApprovalResult(approved=False)),
                color="negative",
            ).mark("cost-cancel")

    return dialog
