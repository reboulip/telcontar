"""Direct unit tests for host/format.py and host/narration.py (extracted from
host/app.py in Phase 18 S1).

These are pure-function tests with no Textual/Pilot dependency — moved here from
tests/test_app_ui.py (fmt_op, render_target_layout) plus new coverage for the
behaviours previously exercised only indirectly through a Pilot test.
"""

from __future__ import annotations

from pathlib import Path

from host.format import (
    fmt_journal_entry,
    fmt_op,
    fmt_progress,
    plan_tree_diff,
    quarantine_reason,
    render_target_layout,
)

# ── fmt_op ────────────────────────────────────────────────────────────────────


def test_fmt_op_update_file_without_overwrite_has_no_flag() -> None:
    op = {"op_type": "update_file", "src": "/a/notes.md", "dst": "", "params": {"content": "x"}}
    assert fmt_op(op) == "UPDATE   notes.md"


def test_fmt_op_update_file_with_overwrite_shows_subtle_flag() -> None:
    op = {
        "op_type": "update_file",
        "src": "/a/notes.md",
        "dst": "",
        "params": {"content": "x", "overwrite": True},
    }
    formatted = fmt_op(op)
    assert "notes.md" in formatted
    assert "overwrite" in formatted


def test_fmt_op_in_scope_has_no_indicator(tmp_path: Path) -> None:
    op = {"op_type": "move", "src": str(tmp_path / "doc.pdf"), "dst": "/sorted", "op_id": "o1"}
    assert fmt_op(op, tmp_path) == "MOVE     doc.pdf  →  /sorted"


def test_fmt_op_out_of_scope_shows_subtle_indicator(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "elsewhere" / "secret.env"
    op = {"op_type": "rename", "src": str(outside), "dst": "renamed.env", "op_id": "o1"}
    formatted = fmt_op(op, target)
    assert "secret.env" in formatted
    assert "outside target" in formatted


def test_fmt_op_no_target_has_no_indicator() -> None:
    op = {"op_type": "rename", "src": "/anywhere/doc.pdf", "dst": "new.pdf", "op_id": "o1"}
    assert "outside target" not in fmt_op(op, None)


def test_fmt_op_markup_false_strips_rich_tags() -> None:
    op = {
        "op_type": "update_file",
        "src": "/a/notes.md",
        "dst": "",
        "params": {"content": "x", "overwrite": True},
    }
    formatted = fmt_op(op, markup=False)
    assert "[dim]" not in formatted
    assert "(overwrite)" in formatted


# ── quarantine_reason / fmt_op quarantine (V10) ─────────────────────────────────


def test_quarantine_reason_returns_stated_reason() -> None:
    op = {"op_type": "quarantine", "params": {"reason": "duplicate of report.pdf"}}
    assert quarantine_reason(op) == "duplicate of report.pdf"


def test_quarantine_reason_no_reason_given_when_blank() -> None:
    assert quarantine_reason({"op_type": "quarantine", "params": {"reason": ""}}) == (
        "no reason given"
    )


def test_quarantine_reason_no_reason_given_when_params_missing() -> None:
    assert quarantine_reason({"op_type": "quarantine"}) == "no reason given"


def test_quarantine_reason_caps_long_text() -> None:
    op = {"op_type": "quarantine", "params": {"reason": "x" * 200}}
    formatted = quarantine_reason(op)
    assert len(formatted) == 120
    assert formatted.endswith("…")


def test_fmt_op_quarantine_shows_reason() -> None:
    op = {
        "op_type": "quarantine",
        "src": "/in/junk.txt",
        "dst": "/t/_quarantine/junk.txt",
        "params": {"reason": "superseded by v2.txt"},
    }
    formatted = fmt_op(op)
    assert "junk.txt" in formatted
    assert "superseded by v2.txt" in formatted


def test_fmt_op_quarantine_shows_no_reason_given_when_blank() -> None:
    op = {"op_type": "quarantine", "src": "/in/junk.txt", "dst": "", "params": {"reason": ""}}
    assert "no reason given" in fmt_op(op)


def test_fmt_op_quarantine_markup_false_strips_rich_tags() -> None:
    op = {
        "op_type": "quarantine",
        "src": "/in/junk.txt",
        "dst": "",
        "params": {"reason": "duplicate"},
    }
    formatted = fmt_op(op, markup=False)
    assert "[dim]" not in formatted
    assert "duplicate" in formatted


# ── plan_tree_diff (V3) ──────────────────────────────────────────────────────────


def test_plan_tree_diff_empty_ops_returns_empty_everything() -> None:
    before, after, other = plan_tree_diff([])
    assert before == []
    assert after == []
    assert other == []


def test_plan_tree_diff_rename_shows_old_and_new_name() -> None:
    ops = [{"op_id": "o1", "op_type": "rename", "src": "/t/a.txt", "dst": "b.txt"}]
    before, after, other = plan_tree_diff(ops)
    assert other == []
    before_files = [line for line in before if line.op_id]
    after_files = [line for line in after if line.op_id]
    assert [(line.label, line.op_id) for line in before_files] == [("a.txt", "o1")]
    assert [(line.label, line.op_id) for line in after_files] == [("b.txt", "o1")]


def test_plan_tree_diff_move_places_file_under_dest_dir() -> None:
    ops = [{"op_id": "o1", "op_type": "move", "src": "/t/in/a.txt", "dst": "/t/sorted"}]
    _, after, other = plan_tree_diff(ops)
    assert other == []
    labels = [line.label for line in after]
    assert "sorted/" in labels
    assert any(line.label == "a.txt" and line.op_id == "o1" for line in after)


def test_plan_tree_diff_quarantine_with_dest_appears_in_both_trees() -> None:
    ops = [
        {
            "op_id": "o1",
            "op_type": "quarantine",
            "src": "/t/junk.txt",
            "dst": "/t/_quarantine/junk.txt",
            "params": {"reason": "duplicate"},
        }
    ]
    before, after, other = plan_tree_diff(ops)
    assert other == []
    assert any(line.op_id == "o1" and line.label == "junk.txt  — duplicate" for line in before)
    assert any(line.op_id == "o1" and line.label == "junk.txt  — duplicate" for line in after)


def test_plan_tree_diff_quarantine_without_dest_goes_to_other_ops() -> None:
    op = {"op_id": "o1", "op_type": "quarantine", "src": "/t/junk.txt", "dst": ""}
    before, after, other = plan_tree_diff([op])
    assert before == []
    assert after == []
    assert other == [op]


def test_plan_tree_diff_archive_document_without_dest_goes_to_other_ops() -> None:
    op = {"op_id": "o1", "op_type": "archive_document", "src": "/t/doc.pdf", "dst": ""}
    before, after, other = plan_tree_diff([op])
    assert before == []
    assert after == []
    assert other == [op]


def test_plan_tree_diff_create_file_appears_only_in_after_tree() -> None:
    ops = [{"op_id": "o1", "op_type": "create_file", "src": "/t/README.md", "dst": ""}]
    before, after, other = plan_tree_diff(ops)
    assert other == []
    assert before == []
    assert any(line.op_id == "o1" and line.label == "README.md" for line in after)


def test_plan_tree_diff_update_file_create_dir_compress_quarantine_are_other_ops() -> None:
    ops = [
        {"op_id": "o1", "op_type": "update_file", "src": "/t/a.txt", "dst": ""},
        {"op_id": "o2", "op_type": "create_dir", "src": "/t/new_folder", "dst": ""},
        {"op_id": "o3", "op_type": "compress_quarantine", "src": "/t/_quarantine", "dst": ""},
    ]
    before, after, other = plan_tree_diff(ops)
    assert before == []
    assert after == []
    assert [op["op_id"] for op in other] == ["o1", "o2", "o3"]


def test_plan_tree_diff_every_op_id_appears_exactly_once_across_all_three() -> None:
    ops = [
        {"op_id": "o1", "op_type": "rename", "src": "/t/a.txt", "dst": "b.txt"},
        {"op_id": "o2", "op_type": "move", "src": "/t/c.txt", "dst": "/t/sorted"},
        {"op_id": "o3", "op_type": "quarantine", "src": "/t/d.txt", "dst": "/t/_q/d.txt"},
        {"op_id": "o4", "op_type": "quarantine", "src": "/t/e.txt", "dst": ""},
        {"op_id": "o5", "op_type": "create_file", "src": "/t/new.md", "dst": ""},
        {"op_id": "o6", "op_type": "update_file", "src": "/t/f.txt", "dst": ""},
        {"op_id": "o7", "op_type": "create_dir", "src": "/t/g", "dst": ""},
        {
            "op_id": "o8",
            "op_type": "archive_document",
            "src": "/t/h.pdf",
            "dst": "/t/_q/h.pdf",
        },
    ]
    before, after, other = plan_tree_diff(ops)
    seen_op_ids = (
        [line.op_id for line in before if line.op_id]
        + [line.op_id for line in after if line.op_id]
        + [op["op_id"] for op in other]
    )
    # o1/o2/o3/o8 have a genuine before AND after location (rename, move,
    # quarantine-with-dest, archive-with-dest) — they appear in both trees,
    # count 2. o4/o6/o7 (other-only) and o5 (create_file, after-only) appear
    # exactly once.
    from collections import Counter

    counts = Counter(seen_op_ids)
    assert set(counts) == {f"o{i}" for i in range(1, 9)}
    for op_id in ("o1", "o2", "o3", "o8"):
        assert counts[op_id] == 2
    for op_id in ("o4", "o5", "o6", "o7"):
        assert counts[op_id] == 1


def test_plan_tree_diff_groups_multiple_files_under_shared_folder() -> None:
    ops = [
        {"op_id": "o1", "op_type": "move", "src": "/t/in/a.txt", "dst": "/t/sorted/2024"},
        {"op_id": "o2", "op_type": "move", "src": "/t/in/b.txt", "dst": "/t/sorted/2024"},
    ]
    _, after, _ = plan_tree_diff(ops)
    file_lines = [line for line in after if line.op_id]
    assert {line.label for line in file_lines} == {"a.txt", "b.txt"}
    # both files sit at the same depth, under the same "2024/" folder line
    folder_lines = [line for line in after if line.label == "2024/"]
    assert len(folder_lines) == 1
    assert all(line.depth == folder_lines[0].depth + 1 for line in file_lines)


def test_plan_tree_diff_annotates_after_tree_folders_with_notes() -> None:
    ops = [{"op_id": "o1", "op_type": "move", "src": "/t/in/a.txt", "dst": "/t/sorted/2024"}]
    notes = {"2024": "This year's invoices"}
    before, after, _ = plan_tree_diff(ops, folder_notes=notes)
    assert not any("invoices" in line.label for line in before)
    assert any("This year's invoices" in line.label for line in after)


def test_plan_tree_diff_marks_out_of_scope_ops_on_both_tree_sides(tmp_path: Path) -> None:
    """S4/M4 regression guard: the (outside target) advisory marker must
    survive V3's before/after tree, not just the "Other operations"
    fallback list that still goes through fmt_op."""
    target = tmp_path / "target"
    target.mkdir()
    outside = tmp_path / "elsewhere" / "secret.env"
    ops = [{"op_id": "o1", "op_type": "rename", "src": str(outside), "dst": "renamed.env"}]
    before, after, _ = plan_tree_diff(ops, target=target)
    assert any(line.op_id == "o1" and "outside target" in line.label for line in before)
    assert any(line.op_id == "o1" and "outside target" in line.label for line in after)


def test_plan_tree_diff_in_scope_ops_have_no_marker(tmp_path: Path) -> None:
    target = tmp_path
    ops = [{"op_id": "o1", "op_type": "move", "src": str(tmp_path / "a.txt"), "dst": "/sorted"}]
    before, after, _ = plan_tree_diff(ops, target=target)
    assert not any("outside target" in line.label for line in before)
    assert not any("outside target" in line.label for line in after)


def test_plan_tree_diff_no_target_means_no_marker() -> None:
    ops = [{"op_id": "o1", "op_type": "rename", "src": "/anywhere/doc.pdf", "dst": "new.pdf"}]
    before, after, _ = plan_tree_diff(ops, target=None)
    assert not any("outside target" in line.label for line in before)
    assert not any("outside target" in line.label for line in after)


def test_plan_tree_diff_quarantine_reason_survives_on_tree_nodes() -> None:
    """V10 regression guard: a quarantine op with a resolved destination
    lands on a tree node (not "Other operations"), and its stated reason
    must still be visible there, not silently dropped."""
    ops = [
        {
            "op_id": "o1",
            "op_type": "quarantine",
            "src": "/t/junk.txt",
            "dst": "/t/_q/junk.txt",
            "params": {"reason": "duplicate of report_v2.pdf"},
        }
    ]
    before, after, other = plan_tree_diff(ops)
    assert other == []
    assert any(line.op_id == "o1" and "duplicate of report_v2.pdf" in line.label for line in before)
    assert any(line.op_id == "o1" and "duplicate of report_v2.pdf" in line.label for line in after)


def test_plan_tree_diff_quarantine_reason_falls_back_to_no_reason_given() -> None:
    ops = [
        {
            "op_id": "o1",
            "op_type": "quarantine",
            "src": "/t/junk.txt",
            "dst": "/t/_q/junk.txt",
            "params": {"reason": ""},
        }
    ]
    _, after, _ = plan_tree_diff(ops)
    assert any(line.op_id == "o1" and "no reason given" in line.label for line in after)


def test_plan_tree_diff_handles_cross_drive_paths_without_raising(tmp_path: Path) -> None:
    # os.path.commonpath raises ValueError across drives on Windows —
    # render_target_layout already guards this; plan_tree_diff must too.
    ops = [
        {"op_id": "o1", "op_type": "move", "src": "C:/a/one.txt", "dst": "C:/sorted"},
        {"op_id": "o2", "op_type": "move", "src": "D:/b/two.txt", "dst": "D:/sorted"},
    ]
    _, after, _ = plan_tree_diff(ops)  # must not raise
    assert {line.label for line in after if line.op_id} == {"one.txt", "two.txt"}


# ── render_target_layout ────────────────────────────────────────────────────────


def test_render_target_layout_builds_tree_with_notes() -> None:
    ops = [
        {"op_type": "move", "src": "/in/a.pdf", "dst": "/t/01_decisions", "op_id": "o1"},
        {"op_type": "move", "src": "/in/b.pdf", "dst": "/t/02_copil", "op_id": "o2"},
        {"op_type": "quarantine", "src": "/in/c.pdf", "dst": "/t/_quarantine/c.pdf", "op_id": "o3"},
    ]
    notes = {"01_decisions": "Formal decision records", "_quarantine": "Duplicates"}
    text = "\n".join(render_target_layout(ops, notes))
    assert "01_decisions/" in text
    assert "Formal decision records" in text
    assert "_quarantine/" in text
    assert "Duplicates" in text
    # A target folder without a note still appears in the tree (bare node).
    assert "02_copil/" in text


def test_render_target_layout_empty_without_folder_ops() -> None:
    # Rename-only plans move nothing into a folder, so there's no target tree.
    ops = [{"op_type": "rename", "src": "/in/a.pdf", "dst": "a_clean.pdf", "op_id": "o1"}]
    assert render_target_layout(ops, {}) == []


# ── fmt_journal_entry ────────────────────────────────────────────────────────────


def test_fmt_journal_entry_normal_entry() -> None:
    entry = {"timestamp": "2026-01-01T00:00:00", "op_type": "move", "src": "/a/b.pdf", "dst": "/c"}
    formatted = fmt_journal_entry(entry)
    assert "2026-01-01T00:00:00" in formatted
    assert "move" in formatted
    assert "/a/b.pdf" in formatted
    assert "→  /c" in formatted


def test_fmt_journal_entry_hard_stop_is_multiline() -> None:
    entry = {
        "op_type": "hard_stop",
        "timestamp": "2026-01-01T00:00:00",
        "reason": "disk full",
        "failed_ops": [
            {"op_type": "move", "src": "/a.pdf", "error": "no space"},
            {"op_type": "rename", "src": "/b.pdf", "error": "no space"},
        ],
    }
    formatted = fmt_journal_entry(entry)
    lines = formatted.split("\n")
    assert len(lines) == 3
    assert "HARD STOP" in lines[0]
    assert "disk full" in lines[0]
    assert "✗ move" in lines[1]
    assert "✗ rename" in lines[2]


def test_fmt_journal_entry_missing_keys_default_to_placeholder() -> None:
    formatted = fmt_journal_entry({})
    assert "?" in formatted


def test_fmt_journal_entry_markup_false_strips_rich_tags() -> None:
    entry = {"timestamp": "t", "op_type": "move", "src": "/a.pdf"}
    formatted = fmt_journal_entry(entry, markup=False)
    assert "[dim]" not in formatted

    hard_stop = {
        "op_type": "hard_stop",
        "timestamp": "t",
        "reason": "disk full",
        "failed_ops": [{"op_type": "move", "src": "/a.pdf", "error": "no space"}],
    }
    formatted_hard_stop = fmt_journal_entry(hard_stop, markup=False)
    assert "[bold red]" not in formatted_hard_stop
    assert "[red]" not in formatted_hard_stop


# ── fmt_progress ─────────────────────────────────────────────────────────────


def test_fmt_progress_basic_counts_only() -> None:
    assert fmt_progress({"analyzed": 3, "total": 10, "current": []}) == "3/10"


def test_fmt_progress_single_current_file_appended() -> None:
    progress = {"analyzed": 3, "total": 10, "current": ["report.pdf"]}
    assert fmt_progress(progress) == "3/10 — report.pdf"


def test_fmt_progress_multiple_current_files_shows_count_suffix() -> None:
    progress = {"analyzed": 3, "total": 10, "current": ["a.pdf", "b.pdf", "c.pdf"]}
    assert fmt_progress(progress) == "3/10 — a.pdf +2"


def test_fmt_progress_missing_current_key_defaults_to_no_suffix() -> None:
    # Older/other progress events (e.g. run_prepass's) don't carry "current".
    assert fmt_progress({"analyzed": 1, "total": 2}) == "1/2"


def test_fmt_progress_missing_keys_default_to_zero() -> None:
    assert fmt_progress({}) == "0/0"


# ── Narrator (narration collapse rule) ───────────────────────────────────────────


def test_narrator_collapses_consecutive_same_phrase() -> None:
    from host.narration import Narrator

    narrator = Narrator()
    assert narrator.narrate("list_dir") == "Scanning the directory…"
    # Consecutive calls mapping to the same phrase collapse to a single narration.
    assert narrator.narrate("list_dir") is None
    assert narrator.narrate("list_dir") is None


def test_narrator_unknown_tool_returns_none_without_resetting_state() -> None:
    from host.narration import Narrator

    narrator = Narrator()
    assert narrator.narrate("list_dir") == "Scanning the directory…"
    # An unrecognized tool yields no phrase and must not clear the last-seen one.
    assert narrator.narrate("some_unmapped_tool") is None
    # So a following list_dir call still collapses into the same macro-task.
    assert narrator.narrate("list_dir") is None


def test_narrator_different_phrase_emits_new_narration() -> None:
    from host.narration import Narrator

    narrator = Narrator()
    assert narrator.narrate("list_dir") == "Scanning the directory…"
    assert narrator.narrate("read_file") == "Reading documents…"
