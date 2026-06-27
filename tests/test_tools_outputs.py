"""Tests for E1 write_index, E2 write_summary, and E3 naming conventions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.tools import write_index, write_summary
from server import journal as _journal


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tree(root: Path) -> None:
    """Create a small fixture directory tree."""
    (root / "docs").mkdir()
    (root / "docs" / "report.pdf").write_bytes(b"x" * 1024)
    (root / "docs" / "notes.txt").write_text("some notes", encoding="utf-8")
    (root / "images").mkdir()
    (root / "images" / "photo.jpg").write_bytes(b"y" * 2048)
    (root / "readme.md").write_text("# Hello", encoding="utf-8")


# ── E1: write_index ───────────────────────────────────────────────────────────

class TestWriteIndex:
    def test_creates_index_md_and_manifest_json(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        result = write_index(str(tmp_path), journal_path)
        assert (tmp_path / "INDEX.md").exists()
        assert (tmp_path / "manifest.json").exists()
        assert result["index"] == str(tmp_path / "INDEX.md")
        assert result["manifest"] == str(tmp_path / "manifest.json")

    def test_index_md_contains_tree_entries(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        content = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        assert "docs/" in content
        assert "report.pdf" in content
        assert "notes.txt" in content
        assert "photo.jpg" in content
        assert "readme.md" in content

    def test_index_md_excludes_output_files(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        content = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        # The tree section should not list INDEX.md or manifest.json themselves
        tree_section = content.split("## Changes")[0]
        assert "INDEX.md" not in tree_section
        assert "manifest.json" not in tree_section
        assert "SUMMARY.md" not in tree_section

    def test_manifest_json_is_valid_and_has_files(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert "generated" in data
        assert "target" in data
        assert "files" in data
        assert "dirs" in data
        assert "journal_summary" in data
        file_names = [Path(f["path"]).name for f in data["files"]]
        assert "report.pdf" in file_names
        assert "notes.txt" in file_names
        assert "photo.jpg" in file_names

    def test_manifest_excludes_output_files(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        file_names = [Path(f["path"]).name for f in data["files"]]
        assert "INDEX.md" not in file_names
        assert "manifest.json" not in file_names

    def test_changelog_reflects_journal_entries(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        _journal.append(journal_path, {"op_type": "rename", "src": "a", "dst": "b"})
        _journal.append(journal_path, {"op_type": "rename", "src": "c", "dst": "d"})
        _journal.append(journal_path, {"op_type": "quarantine", "src": "e", "dst": "q/e"})
        write_index(str(tmp_path), journal_path)
        content = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        assert "2 rename" in content
        assert "1 quarantine" in content
        assert "Total operations" in content

    def test_changelog_shows_no_operations_when_journal_empty(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        content = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        assert "No operations recorded" in content

    def test_hard_stop_entries_excluded_from_counts(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        _journal.append(journal_path, {"op_type": "move", "src": "a", "dst": "b/"})
        _journal.append(journal_path, {"op_type": "hard_stop", "reason": "too many failures"})
        write_index(str(tmp_path), journal_path)
        data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert "hard_stop" not in data["journal_summary"]["by_type"]
        assert data["journal_summary"]["total_ops"] == 1

    def test_raises_for_non_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x")
        journal_path = tmp_path / "journal.jsonl"
        with pytest.raises(ValueError, match="Not a directory"):
            write_index(str(f), journal_path)

    def test_idempotent_second_call_overwrites(self, tmp_path: Path) -> None:
        _make_tree(tmp_path)
        journal_path = tmp_path / ".organizer" / "journal.jsonl"
        write_index(str(tmp_path), journal_path)
        _journal.append(journal_path, {"op_type": "rename", "src": "x", "dst": "y"})
        write_index(str(tmp_path), journal_path)
        content = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
        assert "1 rename" in content


# ── E2: write_summary ─────────────────────────────────────────────────────────

class TestWriteSummary:
    def test_creates_summary_md(self, tmp_path: Path) -> None:
        result = write_summary(str(tmp_path), "This is the summary.")
        assert (tmp_path / "SUMMARY.md").exists()
        assert result["written"] == str(tmp_path / "SUMMARY.md")

    def test_content_is_written_verbatim(self, tmp_path: Path) -> None:
        prose = "# Summary\n\nThe directory contained 42 files.\n"
        write_summary(str(tmp_path), prose)
        assert (tmp_path / "SUMMARY.md").read_text(encoding="utf-8") == prose

    def test_overwrites_existing_summary(self, tmp_path: Path) -> None:
        (tmp_path / "SUMMARY.md").write_text("old content", encoding="utf-8")
        write_summary(str(tmp_path), "new content")
        assert (tmp_path / "SUMMARY.md").read_text(encoding="utf-8") == "new content"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        write_summary(str(nested), "hi")
        assert (nested / "SUMMARY.md").read_text(encoding="utf-8") == "hi"


# ── E3: naming conventions ────────────────────────────────────────────────────

class TestNamingConventions:
    def test_default_when_file_missing(self, tmp_path: Path) -> None:
        from host.agent import _load_naming_conventions
        result = _load_naming_conventions(tmp_path, None)
        assert "snake_case" in result or "underscore" in result.lower()

    def test_loads_custom_file(self, tmp_path: Path) -> None:
        from host.agent import _load_naming_conventions
        naming_path = tmp_path / ".organizer" / "NAMING.md"
        naming_path.parent.mkdir(parents=True)
        naming_path.write_text("## My Rules\n- Use CamelCase\n", encoding="utf-8")
        result = _load_naming_conventions(tmp_path, None)
        assert "CamelCase" in result

    def test_falls_back_to_default_for_empty_file(self, tmp_path: Path) -> None:
        from host.agent import _load_naming_conventions
        naming_path = tmp_path / ".organizer" / "NAMING.md"
        naming_path.parent.mkdir(parents=True)
        naming_path.write_text("   \n", encoding="utf-8")
        result = _load_naming_conventions(tmp_path, None)
        assert "underscore" in result.lower() or "snake_case" in result

    def test_system_prompt_contains_naming_section(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from host.agent import _build_system_prompt
        prompt = _build_system_prompt(tmp_path, MagicMock())
        assert "naming" in prompt.lower() or "rename" in prompt.lower()
        assert "write_index" in prompt
        assert "write_summary" in prompt
