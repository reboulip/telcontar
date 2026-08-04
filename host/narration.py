"""Tool-call narration vocabulary, shared by the Textual and web UIs.

Extracted from host/app.py (Phase 18 S1) with zero behaviour change.
"""

from __future__ import annotations

# Friendly, plain-language narration for the conversation pane (F10). Maps each
# MCP tool to the macro-task it belongs to; consecutive calls in the same macro
# task collapse to a single line so the pane reads as progress, not a call log.
TOOL_NARRATION: dict[str, str] = {
    "list_dir": "Scanning the directory…",
    "walk_tree": "Exploring nested folders…",
    "read_file": "Reading documents…",
    "extract_text": "Reading documents…",
    "read_file_batch": "Reading documents…",
    "extract_text_batch": "Reading documents…",
    "compute_checksum": "Computing checksums…",
    "compute_checksum_batch": "Computing checksums…",
    "lookup_documents": "Looking up documents in memory…",
    "record_document": "Recording documents in memory…",
    "record_document_batch": "Recording documents in memory…",
    "find_duplicates": "Checking for duplicates…",
    "find_modified_documents": "Checking for newer versions…",
    "compare_documents": "Comparing documents…",
    "create_plan": "Planning changes…",
    "propose_rename": "Planning changes…",
    "propose_move": "Planning changes…",
    "propose_quarantine": "Planning changes…",
    "propose_create_file": "Planning changes…",
    "propose_update_file": "Planning changes…",
    "propose_create_dir": "Planning changes…",
    "propose_archive_document": "Planning changes…",
    "propose_compress_quarantine": "Planning changes…",
    "review_plan": "Reviewing the plan…",
    "set_plan_rationale": "Summarizing the plan…",
    "set_plan_folder_notes": "Describing the target folders…",
    "execute_plan": "Applying the plan…",
    "create_event": "Recording project events…",
    "build_graph": "Building the knowledge graph…",
    "get_graph": "Building the knowledge graph…",
    "get_actors": "Identifying the main actors…",
    "list_events": "Reviewing the timeline…",
    "write_index": "Writing the index…",
    "write_summary": "Writing the summary…",
    "write_folder_readme": "Describing folders…",
    "ask_user": "Asking you a question…",
}


class Narrator:
    """Collapses consecutive same-phrase narrations into a single line (F10).

    An unknown tool (not in ``TOOL_NARRATION``) yields no phrase and leaves the
    last-seen phrase untouched — so a `list_dir` -> unknown-tool -> `list_dir`
    sequence still collapses to a single narration, not two.
    """

    def __init__(self) -> None:
        self._last = ""

    def narrate(self, tool: str) -> str | None:
        phrase = TOOL_NARRATION.get(tool)
        if phrase and phrase != self._last:
            self._last = phrase
            return phrase
        return None
