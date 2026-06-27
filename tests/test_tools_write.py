"""Tests for write tools: move_file, rename_file, create_file, update_file."""

from __future__ import annotations

import pytest
from pathlib import Path

from server.tools import move_file, rename_file, create_file, update_file


class TestMoveFile:
    def test_moves_file_to_dest_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "a" / "file.txt"
        src.parent.mkdir()
        src.write_text("hello")
        dst_dir = tmp_path / "b"
        dst_dir.mkdir()
        result = move_file(str(src), str(dst_dir))
        assert not src.exists()
        assert (dst_dir / "file.txt").read_text() == "hello"
        assert result["moved"] == str(dst_dir / "file.txt")

    def test_raises_if_destination_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("src")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        (dst_dir / "file.txt").write_text("existing")
        with pytest.raises(FileExistsError):
            move_file(str(src), str(dst_dir))

    def test_raises_if_source_not_a_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            move_file(str(tmp_path / "missing.txt"), str(tmp_path))

    def test_raises_if_dest_dir_not_a_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            move_file(str(src), str(tmp_path / "nonexistent_dir"))


class TestRenameFile:
    def test_renames_file(self, tmp_path: Path) -> None:
        src = tmp_path / "old.txt"
        src.write_text("content")
        result = rename_file(str(src), "new.txt")
        assert not src.exists()
        assert (tmp_path / "new.txt").read_text() == "content"
        assert result["renamed"] == str(tmp_path / "new.txt")

    def test_raises_if_new_name_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        src.write_text("a")
        (tmp_path / "b.txt").write_text("b")
        with pytest.raises(FileExistsError):
            rename_file(str(src), "b.txt")

    def test_raises_if_source_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            rename_file(str(tmp_path / "missing.txt"), "other.txt")


class TestCreateFile:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        p = tmp_path / "new.txt"
        result = create_file(str(p), "hello")
        assert p.read_text() == "hello"
        assert result["created"] == str(p)

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "b" / "c.txt"
        create_file(str(p), "deep")
        assert p.read_text() == "deep"

    def test_raises_if_file_exists(self, tmp_path: Path) -> None:
        p = tmp_path / "existing.txt"
        p.write_text("old")
        with pytest.raises(FileExistsError):
            create_file(str(p), "new")
        assert p.read_text() == "old"


class TestUpdateFile:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        p = tmp_path / "new.txt"
        result = update_file(str(p), "content")
        assert p.read_text() == "content"
        assert result["updated"] == str(p)

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "existing.txt"
        p.write_text("old")
        update_file(str(p), "new")
        assert p.read_text() == "new"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "file.txt"
        update_file(str(p), "nested content")
        assert p.read_text() == "nested content"
