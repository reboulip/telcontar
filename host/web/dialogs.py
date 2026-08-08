"""Approval (U4) / cost estimate (U5) / journal (U6) dialog builders.

The approval and cost dialogs are one per PendingRequest kind, both
`.props("persistent")`: no backdrop-click or Esc dismissal. The original
inline dialog (host/web/main.py, before U4) was a plain `ui.dialog()` —
closeable without resolving its future, which leaves `RunSession.pending`
set and the run silently deadlocked with zero visible symptom (the same
failure mode ROADMAP.md's Break 1 spike found and fixed for the reload
path; this closes the same door for the dialog-dismissal path). Every
resolution therefore goes through an explicit button, and every resolution
is request-scoped (`session.resolve_pending(result,
request_id=pending.request_id)`) so a stale dialog left over in another tab
or after a reload can't resolve a different, newer pending request.

The journal dialog (U6) is different in kind: it isn't resolving a
PendingRequest — nothing is waiting on a future — so a normal, dismissible
`ui.dialog()` is fine there; Esc/backdrop-close just closes the viewer.
"""

from __future__ import annotations

from nicegui import ui

from host.agent import ApprovalResult, CostApprovalResult
from host.format import fmt_journal_entry, fmt_op, render_target_layout
from host.web import journal
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
            "text-h6 tc-display"
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


def build_journal_dialog(session: RunSession) -> ui.dialog:
    """Journal viewer + undo (U6). Not tied to a PendingRequest — nothing
    is waiting on a future — so a normal dismissible dialog is used here,
    unlike the approval/cost gates above; Esc/backdrop-close is safe.

    ``journal.load_entries``/``journal.do_undo`` are called directly
    (synchronously), not via ``run.io_bound`` — deliberately, unlike every
    other blocking-I/O call site in host/web/. Both read/write a single
    small JSONL file and only ever run on an explicit, rare user click (not
    the poll timer), so a brief synchronous stall is imperceptible — unlike
    S5's motivating cases (a full directory walk, a Windows keyring
    round-trip that can take seconds). Wrapping either in `run.io_bound` (or
    plain `asyncio.to_thread`) breaks under NiceGUI's headless test harness
    specifically: an executor-callback continuation invoked from inside a
    click handler on a dialog opened from *another* dialog's handler
    (`background_tasks.create_or_defer`-dispatched, one level removed from
    the page's own top-level event dispatch) never resumes — confirmed by
    direct experiment, not yet filed upstream. A real browser session has
    no such issue; this is a test-harness limitation, not a correctness
    concern for this fast, rare operation.

    The confirm step is a separate, sibling dialog built once up front
    (``confirm_dialog``), not nested inside ``body``'s refreshable, so its
    buttons are bound once at construction time rather than re-bound on
    every ``body.refresh()``.
    """
    dialog = ui.dialog()
    state = {"status": ""}

    @ui.refreshable
    def body() -> None:
        entries = journal.load_entries(session.target)
        with ui.column().classes("max-h-96 overflow-auto w-full"):
            if entries:
                for entry in entries:
                    ui.label(fmt_journal_entry(entry, markup=False)).classes(
                        "whitespace-pre-wrap font-mono text-xs"
                    )
            else:
                ui.label("No operations recorded yet.").classes("text-grey")

        if state["status"]:
            ui.label(state["status"]).mark("journal-status")

        if session.has_open_step():
            ui.label("Cannot undo while an operation is in progress.").classes(
                "text-caption text-grey"
            ).mark("journal-busy")
        else:
            ui.button("Undo last operation", on_click=confirm_dialog.open, color="negative").mark(
                "journal-undo"
            )

    confirm_dialog = ui.dialog()
    with confirm_dialog, ui.card():
        ui.label("Undo the last operation?").mark("journal-confirm-prompt")

        def _confirm() -> None:
            result = journal.do_undo(session.target)
            confirm_dialog.close()
            if result.get("undone") is not None:
                state["status"] = "Undone the last operation."
                session.bump_fs_revision()
            else:
                state["status"] = f"Undo failed: {result.get('error', 'unknown error')}"
            body.refresh()

        with ui.row():
            ui.button("Confirm undo", on_click=_confirm, color="negative").mark(
                "journal-undo-confirm"
            )
            ui.button("Cancel", on_click=confirm_dialog.close).mark("journal-undo-cancel")

    with dialog, ui.card().classes("w-full max-w-2xl"):
        ui.label("Operation journal").classes("text-h6")
        body()
        ui.button("Close", on_click=dialog.close).mark("journal-close")

    return dialog
