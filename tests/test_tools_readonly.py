"""Tests for read-only server tools: list_dir, read_file, extract_text."""
from __future__ import annotations

import pytest
from pathlib import Path

from server.tools import list_dir, read_file, extract_text


class TestListDir:
    def test_returns_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        result = list_dir(str(tmp_path))
        names = {e["name"] for e in result["entries"]}
        assert "a.txt" in names
        assert "sub" in names

    def test_entry_has_required_fields(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("x")
        result = list_dir(str(tmp_path))
        entry = next(e for e in result["entries"] if e["name"] == "f.txt")
        assert entry["type"] == "file"
        assert isinstance(entry["size"], int)
        assert isinstance(entry["mtime"], float)

    def test_dirs_sorted_before_files(self, tmp_path: Path) -> None:
        (tmp_path / "z_file.txt").write_text("x")
        (tmp_path / "a_dir").mkdir()
        result = list_dir(str(tmp_path))
        types = [e["type"] for e in result["entries"]]
        assert types.index("dir") < types.index("file")

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = list_dir(str(tmp_path))
        assert result["entries"] == []

    def test_raises_for_file(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            list_dir(str(f))

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a directory"):
            list_dir(str(tmp_path / "missing"))


class TestReadFile:
    def test_reads_content(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        assert read_file(str(f), 100) == "hello world"

    def test_truncates_at_max_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("a" * 200)
        result = read_file(str(f), 10)
        assert result.startswith("a" * 10)
        assert "[... content truncated ...]" in result

    def test_no_truncation_indicator_when_fits(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("short")
        result = read_file(str(f), 100)
        assert "truncated" not in result

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_file(str(tmp_path), 100)

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_file(str(tmp_path / "missing.txt"), 100)

    def test_handles_non_utf8_gracefully(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.txt"
        f.write_bytes(b"\xff\xfe hello")
        result = read_file(str(f), 1000)
        assert isinstance(result, str)


class TestExtractText:
    def test_extracts_plain_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "note.txt"
        f.write_text("plain text content")
        result = extract_text(str(f), 1000)
        assert "plain text content" in result

    def test_truncates_at_max_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("x" * 500)
        result = extract_text(str(f), 10)
        assert "[... content truncated ...]" in result

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            extract_text(str(tmp_path), 100)

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            extract_text(str(tmp_path / "missing.pdf"), 100)
