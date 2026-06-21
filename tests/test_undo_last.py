"""Tests for undo_last."""
from __future__ import annotations

from pathlib import Path

import pytest

from server.journal import append, last
from server.plan import Plan, PlanOp, save
from server.tools import undo_last


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "journal.jsonl"


def _journal_entry(op_type: str, src: str, dst: str, plan_id: str = "pid", op_id: str = "oid") -> dict:
    return {"op_type": op_type, "plan_id": plan_id, "op_id": op_id,
            "src": src, "dst": dst, "timestamp": "2026-01-01T00:00:00+00:00"}


class TestUndoLastEmpty:
    def test_returns_error_when_no_journal(self, plans_dir: Path, journal_path: Path) -> None:
        result = undo_last(journal_path, plans_dir)
        assert result["undone"] is None
        assert "No operations" in result["error"]

    def test_returns_error_when_journal_empty(self, plans_dir: Path, journal_path: Path) -> None:
        journal_path.parent.mkdir(parents=True)
        journal_path.write_text("", encoding="utf-8")
        result = undo_last(journal_path, plans_dir)
        assert result["undone"] is None


class TestUndoRename:
    def test_restores_renamed_file(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original = tmp_path / "original.txt"
        renamed = tmp_path / "renamed.txt"
        renamed.write_text("x")
        append(journal_path, _journal_entry("rename", str(original), "renamed.txt"))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is not None
        assert "error" not in result
        assert original.exists()
        assert not renamed.exists()

    def test_removes_journal_entry_on_success(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original = tmp_path / "old.txt"
        (tmp_path / "new.txt").write_text("x")
        append(journal_path, _journal_entry("rename", str(original), "new.txt"))

        undo_last(journal_path, plans_dir)

        assert last(journal_path) is None

    def test_does_not_remove_entry_if_current_missing(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original = tmp_path / "old.txt"
        # "renamed.txt" never created — simulates file already moved elsewhere
        append(journal_path, _journal_entry("rename", str(original), "renamed.txt"))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is None
        assert result["error"]
        assert last(journal_path) is not None  # entry preserved

    def test_does_not_overwrite_existing_file_at_target(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original = tmp_path / "old.txt"
        original.write_text("pre-existing")
        (tmp_path / "new.txt").write_text("x")
        append(journal_path, _journal_entry("rename", str(original), "new.txt"))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is None
        assert "exists" in result["error"].lower() or "already" in result["error"].lower()


class TestUndoMove:
    def test_restores_moved_file(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        original_path = src_dir / "file.txt"
        (dst_dir / "file.txt").write_text("x")
        append(journal_path, _journal_entry("move", str(original_path), str(dst_dir)))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is not None
        assert original_path.exists()
        assert not (dst_dir / "file.txt").exists()

    def test_removes_journal_entry_on_success(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        (dst_dir / "file.txt").write_text("x")
        append(journal_path, _journal_entry("move", str(src_dir / "file.txt"), str(dst_dir)))

        undo_last(journal_path, plans_dir)

        assert last(journal_path) is None

    def test_does_not_remove_entry_if_file_missing_from_dest(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        # file not in dst_dir — already gone
        append(journal_path, _journal_entry("move", str(src_dir / "file.txt"), str(dst_dir)))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is None
        assert last(journal_path) is not None


class TestUndoQuarantine:
    def test_restores_quarantined_file(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original_dir = tmp_path / "docs"
        original_dir.mkdir()
        q_dir = tmp_path / "_q"
        q_dir.mkdir()
        original = original_dir / "file.txt"
        quarantined = q_dir / "file.txt"
        quarantined.write_text("x")
        append(journal_path, _journal_entry("quarantine", str(original), str(quarantined)))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is not None
        assert original.exists()
        assert not quarantined.exists()

    def test_handles_suffixed_quarantine_name(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        original_dir = tmp_path / "docs"
        original_dir.mkdir()
        q_dir = tmp_path / "_q"
        q_dir.mkdir()
        original = original_dir / "file.txt"
        quarantined = q_dir / "file_1.txt"  # collision-suffixed name
        quarantined.write_text("x")
        append(journal_path, _journal_entry("quarantine", str(original), str(quarantined)))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is not None
        assert original.exists()

    def test_does_not_remove_entry_if_quarantine_file_missing(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        original = tmp_path / "docs" / "file.txt"
        quarantined = tmp_path / "_q" / "file.txt"
        append(journal_path, _journal_entry("quarantine", str(original), str(quarantined)))

        result = undo_last(journal_path, plans_dir)

        assert result["undone"] is None
        assert last(journal_path) is not None


class TestUndoHardStop:
    def test_hard_stop_entry_is_popped_and_noted(self, plans_dir: Path, journal_path: Path) -> None:
        append(journal_path, {"op_type": "hard_stop", "plan_id": "p", "timestamp": "t",
                              "failed_count": 4, "failed_ops": [], "reason": "too many failures"})
        result = undo_last(journal_path, plans_dir)
        assert result["undone"] == "hard_stop"
        assert last(journal_path) is None

    def test_underlying_op_undone_after_hard_stop_cleared(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        original = tmp_path / "old.txt"
        (tmp_path / "new.txt").write_text("x")
        append(journal_path, _journal_entry("rename", str(original), "new.txt"))
        append(journal_path, {"op_type": "hard_stop", "plan_id": "p", "timestamp": "t",
                              "failed_count": 4, "failed_ops": [], "reason": "too many"})

        undo_last(journal_path, plans_dir)  # pops hard_stop
        undo_last(journal_path, plans_dir)  # undoes rename

        assert original.exists()
