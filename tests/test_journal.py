"""Tests for the journal module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.journal import append, last, pop_last


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "journal.jsonl"


class TestAppend:
    def test_creates_file_and_dir(self, journal_path: Path) -> None:
        append(journal_path, {"op_type": "rename", "src": "/a", "dst": "b"})
        assert journal_path.is_file()

    def test_writes_valid_json_line(self, journal_path: Path) -> None:
        entry = {"op_type": "move", "src": "/a/f.txt", "dst": "/b/"}
        append(journal_path, entry)
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == entry

    def test_multiple_appends_grow_file(self, journal_path: Path) -> None:
        for i in range(3):
            append(journal_path, {"op_type": "rename", "n": i})
        lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 3

    def test_appends_to_existing_file(self, journal_path: Path) -> None:
        append(journal_path, {"op_type": "rename", "src": "/a", "dst": "b"})
        append(journal_path, {"op_type": "move", "src": "/b", "dst": "/c/"})
        lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 2
        assert json.loads(lines[1])["op_type"] == "move"

    def test_handles_unicode(self, journal_path: Path) -> None:
        append(journal_path, {"src": "/docs/résumé.pdf", "dst": "resume.pdf"})
        restored = json.loads(journal_path.read_text(encoding="utf-8").strip())
        assert restored["src"] == "/docs/résumé.pdf"


class TestLast:
    def test_returns_none_if_file_missing(self, journal_path: Path) -> None:
        assert last(journal_path) is None

    def test_returns_none_if_file_empty(self, journal_path: Path) -> None:
        journal_path.parent.mkdir(parents=True)
        journal_path.write_text("", encoding="utf-8")
        assert last(journal_path) is None

    def test_returns_last_entry(self, journal_path: Path) -> None:
        append(journal_path, {"n": 1})
        append(journal_path, {"n": 2})
        append(journal_path, {"n": 3})
        assert last(journal_path) == {"n": 3}

    def test_does_not_remove_entry(self, journal_path: Path) -> None:
        append(journal_path, {"n": 1})
        last(journal_path)
        lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 1

    def test_single_entry(self, journal_path: Path) -> None:
        entry = {"op_type": "quarantine", "src": "/a/b.txt"}
        append(journal_path, entry)
        assert last(journal_path) == entry


class TestPopLast:
    def test_returns_none_if_file_missing(self, journal_path: Path) -> None:
        assert pop_last(journal_path) is None

    def test_returns_none_if_file_empty(self, journal_path: Path) -> None:
        journal_path.parent.mkdir(parents=True)
        journal_path.write_text("", encoding="utf-8")
        assert pop_last(journal_path) is None

    def test_returns_and_removes_last_entry(self, journal_path: Path) -> None:
        append(journal_path, {"n": 1})
        append(journal_path, {"n": 2})
        result = pop_last(journal_path)
        assert result == {"n": 2}
        remaining = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(remaining) == 1
        assert json.loads(remaining[0]) == {"n": 1}

    def test_removes_only_entry_leaves_empty_file(self, journal_path: Path) -> None:
        append(journal_path, {"n": 1})
        result = pop_last(journal_path)
        assert result == {"n": 1}
        assert journal_path.read_text(encoding="utf-8") == ""

    def test_successive_pops(self, journal_path: Path) -> None:
        for i in range(3):
            append(journal_path, {"n": i})
        assert pop_last(journal_path) == {"n": 2}
        assert pop_last(journal_path) == {"n": 1}
        assert pop_last(journal_path) == {"n": 0}
        assert pop_last(journal_path) is None

    def test_last_then_pop_consistent(self, journal_path: Path) -> None:
        append(journal_path, {"op_type": "rename", "src": "/a", "dst": "b"})
        via_last = last(journal_path)
        via_pop = pop_last(journal_path)
        assert via_last == via_pop
