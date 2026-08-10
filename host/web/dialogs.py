"""Approval (U4) / cost estimate (U5) / ask_user (V12) / journal (U6) dialog
builders.

The approval, cost, and ask_user dialogs are one per PendingRequest kind, all
`.props("persistent")`: no backdrop-click or Esc dismissal. The original
inline dialog (host/web/main.py, before U4) was a plain `ui.dialog()` —
closeable without resolving its future, which leaves `RunSession.pending`
set and the run silently deadlocked with zero visible symptom (the same
failure mode ROADMAP.md's Break 1 spike found and fixed for the reload
path; this closes the same door for the dialog-dismissal path). Every
resolution therefore goes through an explicit button, and every resolution
is request-scoped (`_make_resolver` below, wrapping
`session.resolve_pending(result, request_id=pending.request_id)`) so a stale
dialog left over in another tab or after a reload can't resolve a
different, newer pending request.

ask_user (V12) used to be a plain-text question rendered into the transcript,
answered by typing a chat reply — no dialog, no request-scoping. That path
also double-posted the user's reply as a transcript turn (`bridge.py`'s
`_send()` posted it once via the chat queue, `on_ask_user_needed` posted it
again after reading the queue). Moving it onto this module's persistent-
dialog / request-scoped-resolution pattern is what fixes both: the dialog
resolves through `_make_resolver` like the other two, and `on_ask_user_needed`
now awaits the pending's future exclusively — it never touches the chat
queue at all, so there is only one place left that can post the reply.

The journal dialog (U6) is different in kind: it isn't resolving a
PendingRequest — nothing is waiting on a future — so a normal, dismissible
`ui.dialog()` is fine there; Esc/backdrop-close just closes the viewer.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import ui

from host import paths as host_paths
from host.agent import ApprovalResult, AskUserResult, CostApprovalResult
from host.format import fmt_journal_entry, fmt_op, plan_tree_diff
from host.web import journal
from host.web.session import PendingRequest, RunSession


def _make_resolver(
    session: RunSession, pending: PendingRequest, dialog: ui.dialog
) -> Callable[[Any], None]:
    """Every PendingRequest-backed dialog resolves through this same shape:
    request-scoped resolution, then close. See this module's docstring."""

    def _resolve(result: Any) -> None:
        session.resolve_pending(result, request_id=pending.request_id)
        dialog.close()

    return _resolve


def build_approval_dialog(session: RunSession, pending: PendingRequest) -> ui.dialog:
    """V3: the target-layout preview is an actual before/after file tree
    (host.format.plan_tree_diff) rather than render_target_layout's flat
    folder-only text block — every op still gets exactly one checkbox
    (`removed_op_ids` is the safety contract this preserves), living on its
    after-tree node for ops with a real destination, or in "Other
    operations" for the rest (create_dir, compress_quarantine, update_file,
    and any quarantine/archive_document with no destination). The modal is
    sized via an inline style, not a Tailwind max-w-* class: Quasar's own
    dialog CSS has higher specificity and a class swap alone would not
    visibly change anything.
    """
    plan_data = pending.payload["plan_data"]
    ops = plan_data.get("ops", [])
    rationale = (plan_data.get("rationale") or "").strip()
    folder_notes = plan_data.get("folder_notes") or {}
    ops_json_path = (plan_data.get("ops_json_path") or "").strip()
    before_lines, after_lines, other_ops = plan_tree_diff(ops, folder_notes, session.target)

    dialog = ui.dialog().props("persistent")
    with (
        dialog,
        ui.card().classes("w-full").style("width: 90vw; max-width: 1400px"),
    ):
        ui.label(f"Plan Review · {pending.payload['plan_id'][:8]} · {len(ops)} op(s)").classes(
            "text-h6 tc-display"
        )

        if rationale:
            ui.label("Model-generated rationale — not verified fact").classes(
                "text-caption text-grey"
            )
            ui.label(rationale).mark("plan-rationale")
        if folder_notes:
            ui.label("Folder notes are model-generated — not verified fact.").classes(
                "text-caption text-grey"
            )

        checkboxes: dict[str, ui.checkbox] = {}

        if before_lines or after_lines:
            ui.separator()
            with ui.row().classes("w-full gap-4 items-start"):
                with ui.column().classes("flex-1 max-h-96 overflow-auto gap-0"):
                    ui.label("Before").classes("text-subtitle2")
                    with ui.column().classes("gap-0").mark("before-tree"):
                        for line in before_lines:
                            ui.label(line.label).classes("whitespace-pre font-mono text-xs").style(
                                f"margin-left: {line.depth * 16}px"
                            )
                with ui.column().classes("flex-1 max-h-96 overflow-auto gap-0"):
                    ui.label("After").classes("text-subtitle2")
                    ui.label("Unchecking a box excludes that change from execution.").classes(
                        "text-caption text-grey"
                    ).mark("after-checkbox-hint")
                    with ui.column().classes("gap-0").mark("after-tree"):
                        for line in after_lines:
                            with (
                                ui.row()
                                .classes("items-center gap-1 no-wrap")
                                .style(f"margin-left: {line.depth * 16}px")
                            ):
                                if line.op_ids:
                                    # X4: a chained file (e.g. rename+move)
                                    # carries every op_id in the chain — one
                                    # checkbox, marked under each op_id, so
                                    # unchecking it excludes the whole chain
                                    # together rather than leaving the file
                                    # half-applied (user-confirmed X6 decision).
                                    cb = ui.checkbox(value=True).mark(
                                        *(f"op-{oid}" for oid in line.op_ids)
                                    )
                                    cb.tooltip(
                                        "Uncheck to skip this change"
                                        if len(line.op_ids) == 1
                                        else "Uncheck to skip all of this file's chained changes"
                                    )
                                    for oid in line.op_ids:
                                        checkboxes[oid] = cb
                                ui.label(line.label).classes("whitespace-pre font-mono text-xs")

        if other_ops:
            ui.separator()
            ui.label("Other operations").classes("text-subtitle2")
            ui.label("Unchecking a box excludes that change from execution.").classes(
                "text-caption text-grey"
            ).mark("after-checkbox-hint")
            with ui.column().classes("max-h-48 overflow-auto w-full"):
                for op in other_ops:
                    op_id = op.get("op_id", "")
                    checkboxes[op_id] = (
                        ui.checkbox(fmt_op(op, session.target, markup=False), value=True)
                        .mark(f"op-{op_id}")
                        .tooltip("Uncheck to skip this change")
                    )

        if not ops:
            ui.label("No operations in this plan.").classes("text-grey")

        if ops_json_path:
            ui.label(f"Full ops JSON: {ops_json_path}").classes("text-caption text-grey").mark(
                "ops-json-path"
            )

            def _reveal_ops_json() -> None:
                # X5: ops_json_path is host-authoritative (written by
                # host/agent.py's _write_ops_json, never model-controlled),
                # but this is the first place the product spawns a native OS
                # process from a UI click — pin it to the run's target
                # anyway, same spirit as is_op_out_of_scope's confinement
                # check above.
                try:
                    Path(ops_json_path).resolve().relative_to(session.target.resolve())
                except (ValueError, OSError):
                    return
                host_paths.reveal_in_file_manager(Path(ops_json_path))

            ui.button(
                "Reveal in file explorer", icon="folder_open", on_click=_reveal_ops_json
            ).props("flat dense").mark("reveal-ops-json")

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

        resolve = _make_resolver(session, pending, dialog)

        def _approve() -> None:
            removed = [op_id for op_id, cb in checkboxes.items() if not cb.value and op_id]
            resolve(ApprovalResult(approved=True, removed_op_ids=removed))

        def _refine() -> None:
            text = refine_input.value.strip()
            if not text:
                return  # nothing to refine — keep the dialog open
            resolve(ApprovalResult(approved=False, refinement=text))

        def _reject() -> None:
            resolve(ApprovalResult(approved=False))

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

        resolve = _make_resolver(session, pending, dialog)

        with ui.row():
            ui.button(
                "Proceed",
                on_click=lambda: resolve(CostApprovalResult(approved=True)),
                color="positive",
            ).mark("cost-proceed")
            ui.button(
                "Cancel",
                on_click=lambda: resolve(CostApprovalResult(approved=False)),
                color="negative",
            ).mark("cost-cancel")

    return dialog


def build_ask_user_dialog(session: RunSession, pending: PendingRequest) -> ui.dialog:
    """Structured ask_user modal (V12): radio-button options per question
    plus one free-text "additional comment" field, replacing the old
    plain-text question rendered into the transcript. Persistent like the
    other two PendingRequest dialogs above — a dismissible ask modal would
    reintroduce the exact deadlock this module's docstring describes.

    The reply sent back to the agent is composed here, in code, from the
    selected options and the comment — never a second LLM round-trip — in
    the fixed shape ``"<question> → <answer>"`` per question, plus an
    ``"Additional comment: <text>"`` line when non-empty. "Skip" resolves
    with ``AskUserResult(reply="", provided=False)``, which
    ``host/agent.py`` already treats as "proceed with your best judgement."
    """
    questions: list[dict] = pending.payload.get("questions") or []

    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("w-full max-w-2xl").mark("ask-dialog"):
        ui.label("telcontar has a question").classes("text-h6 tc-display")

        radios: dict[int, ui.radio] = {}
        with ui.column().classes("w-full gap-3"):
            for i, question in enumerate(questions):
                ui.label(question.get("text", "")).classes("text-body1")
                options = question.get("options") or []
                if options:
                    radios[i] = ui.radio(list(options)).props("inline").mark(f"ask-q{i}")

        comment_input = (
            ui.input("Additional comment (optional)").classes("w-full").mark("ask-comment")
        )

        resolve = _make_resolver(session, pending, dialog)

        def _submit() -> None:
            lines = [
                f"{question.get('text', '')} → {radios[i].value}"
                for i, question in enumerate(questions)
                if i in radios and radios[i].value
            ]
            comment = comment_input.value.strip()
            if comment:
                lines.append(f"Additional comment: {comment}")
            reply = "\n".join(lines)
            resolve(AskUserResult(reply=reply, provided=bool(reply)))

        def _skip() -> None:
            resolve(AskUserResult(reply="", provided=False))

        with ui.row().classes("w-full justify-end"):
            ui.button("Submit", on_click=_submit, color="positive").mark("ask-submit")
            ui.button("Skip — you decide", on_click=_skip, color="grey").mark("ask-skip")

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
