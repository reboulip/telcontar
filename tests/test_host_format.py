"""Direct unit tests for host/format.py and host/narration.py (extracted from
host/app.py in Phase 18 S1).

These are pure-function tests with no Textual/Pilot dependency — moved here from
tests/test_app_ui.py (fmt_op, render_target_layout) plus new coverage for the
behaviours previously exercised only indirectly through a Pilot test.
"""

from __future__ import annotations

from pathlib import Path

from host.format import fmt_journal_entry, fmt_op, fmt_progress, render_target_layout

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
