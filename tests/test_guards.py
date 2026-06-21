"""Tests for the guards module."""
from __future__ import annotations

import pytest
from pathlib import Path

from server.guards import check_no_overwrite, safe_quarantine_path, check_allowlist


class TestCheckNoOverwrite:
    def test_raises_if_file_exists(self, tmp_path: Path) -> None:
        f = tmp_path / "existing.txt"
        f.write_text("x")
        with pytest.raises(FileExistsError, match="already exists"):
            check_no_overwrite(f)

    def test_passes_if_file_missing(self, tmp_path: Path) -> None:
        check_no_overwrite(tmp_path / "new.txt")  # no exception

    def test_raises_if_dir_exists(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        with pytest.raises(FileExistsError):
            check_no_overwrite(d)


class TestSafeQuarantinePath:
    def test_returns_direct_path_when_no_collision(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        qdir = tmp_path / "q"
        qdir.mkdir()
        result = safe_quarantine_path(src, qdir)
        assert result == qdir / "file.txt"

    def test_adds_suffix_on_collision(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        qdir = tmp_path / "q"
        qdir.mkdir()
        (qdir / "file.txt").write_text("existing")
        result = safe_quarantine_path(src, qdir)
        assert result == qdir / "file_1.txt"

    def test_increments_suffix_until_free(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        qdir = tmp_path / "q"
        qdir.mkdir()
        (qdir / "file.txt").write_text("x")
        (qdir / "file_1.txt").write_text("x")
        (qdir / "file_2.txt").write_text("x")
        result = safe_quarantine_path(src, qdir)
        assert result == qdir / "file_3.txt"

    def test_handles_file_without_extension(self, tmp_path: Path) -> None:
        src = tmp_path / "Makefile"
        qdir = tmp_path / "q"
        qdir.mkdir()
        (qdir / "Makefile").write_text("x")
        result = safe_quarantine_path(src, qdir)
        assert result == qdir / "Makefile_1"


class TestCheckAllowlist:
    def test_passes_when_allowlist_empty(self, tmp_path: Path) -> None:
        check_allowlist(tmp_path / "file.txt", [])  # no exception

    def test_passes_when_path_inside_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        check_allowlist(allowed / "file.txt", [allowed])  # no exception

    def test_raises_when_path_outside_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "other" / "file.txt"
        with pytest.raises(PermissionError, match="not within an allowed"):
            check_allowlist(outside, [allowed])

    def test_passes_when_one_of_multiple_allowed_matches(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        check_allowlist(b / "file.txt", [a, b])  # no exception
