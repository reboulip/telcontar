"""Tests for server/memory.py — the .organizer/memory.md file format (Z5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.memory import append_block, contains_note, read_memory, render_block, truncate_to

# ── read_memory ────────────────────────────────────────────────────────────────


def test_read_memory_returns_empty_string_for_missing_file(tmp_path: Path) -> None:
    assert read_memory(tmp_path / "memory.md", max_chars=1000) == ""


def test_read_memory_returns_full_text_under_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    path.write_text("# telcontar memory\n\n- note one\n", encoding="utf-8")

    assert read_memory(path, max_chars=1000) == "# telcontar memory\n\n- note one\n"


def test_read_memory_head_truncates_over_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    path.write_text("x" * 50, encoding="utf-8")

    result = read_memory(path, max_chars=10)

    assert result == "x" * 10 + "\n\n[... content truncated ...]"


# ── render_block ──────────────────────────────────────────────────────────────


def test_render_block_first_write_includes_header() -> None:
    block = render_block("always quarantine drafts", first_write=True)

    assert block.startswith("# telcontar memory\n\n")
    assert "always quarantine drafts" in block
    assert "(telcontar)" in block


def test_render_block_subsequent_write_has_no_header() -> None:
    block = render_block("another note", first_write=False)

    assert "# telcontar memory" not in block
    assert block.startswith("- [")
    assert "another note" in block


# ── append_block / truncate_to (undo round-trip) ──────────────────────────────


def test_append_block_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / ".organizer" / "memory.md"

    append_block(path, render_block("first note", first_write=True))

    assert path.is_file()


def test_append_block_returns_offset_and_bytes_written(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"

    block = render_block("first note", first_write=True)
    offset, written = append_block(path, block)

    assert offset == 0
    assert written == len(block.encode("utf-8"))
    assert path.stat().st_size == written


def test_append_block_second_call_offset_matches_first_files_size(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    append_block(path, render_block("first note", first_write=True))
    size_after_first = path.stat().st_size

    offset, written = append_block(path, render_block("second note", first_write=False))

    assert offset == size_after_first
    assert path.stat().st_size == size_after_first + written


def test_append_block_prepends_newline_if_file_does_not_end_in_one(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    path.write_text("no trailing newline", encoding="utf-8")
    offset_before = path.stat().st_size

    offset, written = append_block(path, "- [2026-01-01] (telcontar) a note\n")

    assert offset == offset_before
    content = path.read_text(encoding="utf-8")
    assert content == "no trailing newline\n- [2026-01-01] (telcontar) a note\n"
    # The prepended newline is counted in bytes_written, so undo truncates
    # back to exactly the pre-append state.
    assert written == len("\n- [2026-01-01] (telcontar) a note\n")


def test_truncate_to_reverts_an_append(tmp_path: Path) -> None:
    path = tmp_path / "memory.md"
    offset, _ = append_block(path, render_block("first note", first_write=True))
    append_block(path, render_block("second note", first_write=False))

    truncate_to(path, offset)

    assert path.stat().st_size == offset
    assert "second note" not in path.read_text(encoding="utf-8")


def test_append_block_on_oserror_truncates_back_to_offset_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried execute_plan attempt must never duplicate a partial write."""
    path = tmp_path / "memory.md"
    append_block(path, render_block("first note", first_write=True))
    size_before = path.stat().st_size

    real_open = Path.open

    def _boom(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if mode == "ab":
            raise OSError("disk full")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _boom)

    with pytest.raises(OSError):
        append_block(path, render_block("second note", first_write=False))

    assert path.stat().st_size == size_before


# ── contains_note ─────────────────────────────────────────────────────────────


def test_contains_note_exact_match() -> None:
    assert contains_note(
        "- [2026-01-01] (telcontar) keep invoices by year\n", "keep invoices by year"
    )


def test_contains_note_is_case_and_whitespace_insensitive() -> None:
    text = "- [2026-01-01] (telcontar) Keep   Invoices by Year\n"
    assert contains_note(text, "keep invoices by year")


def test_contains_note_false_when_absent() -> None:
    text = "- [2026-01-01] (telcontar) something else entirely\n"
    assert not contains_note(text, "keep invoices by year")
