"""Tests for the guards module."""

from __future__ import annotations

import pytest
from pathlib import Path

from server.guards import (
    check_allowlist,
    check_no_overwrite,
    check_not_quarantine_collision,
    check_within_root,
    is_quarantine_like_name,
    normalize_dir_name,
    safe_quarantine_path,
)


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


class TestCheckWithinRoot:
    def test_passes_when_path_inside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "target"
        root.mkdir()
        check_within_root(root / "file.txt", [root])  # no exception

    def test_raises_when_path_outside_all_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "target"
        root.mkdir()
        outside = tmp_path / "other" / "file.txt"
        with pytest.raises(PermissionError, match="outside the confined"):
            check_within_root(outside, [root])

    def test_passes_when_one_of_multiple_roots_matches(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        organizer = tmp_path / "target" / ".organizer"
        target.mkdir()
        organizer.mkdir()
        check_within_root(organizer / "registry.json", [target])  # nested, still inside

    def test_raises_for_absolute_path_outside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "target"
        root.mkdir()
        with pytest.raises(PermissionError):
            check_within_root(tmp_path / "elsewhere" / "secret.env", [root])

    def test_raises_for_dot_dot_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "target"
        root.mkdir()
        escaping = root / ".." / "outside.txt"
        with pytest.raises(PermissionError):
            check_within_root(escaping, [root])

    def test_empty_roots_raises(self, tmp_path: Path) -> None:
        # No roots configured means nothing is in scope — fail closed, not open.
        with pytest.raises(PermissionError):
            check_within_root(tmp_path / "file.txt", [])


class TestNormalizeDirName:
    def test_casefolds(self) -> None:
        assert normalize_dir_name("Quarantine") == normalize_dir_name("quarantine")

    def test_strips_accents(self) -> None:
        assert normalize_dir_name("Quarantäine") == normalize_dir_name("Quarantaine")

    def test_strips_ordering_prefix(self) -> None:
        assert normalize_dir_name("01_decisions") == normalize_dir_name("decisions")
        assert normalize_dir_name("2. decisions") == normalize_dir_name("decisions")

    def test_collapses_separator_runs(self) -> None:
        assert normalize_dir_name("a  -_.b") == "a_b"

    def test_strips_leading_trailing_separators(self) -> None:
        assert normalize_dir_name("_quarantine_") == "quarantine"


class TestIsQuarantineLikeName:
    def test_matches_configured_name_case_insensitively(self) -> None:
        assert is_quarantine_like_name("_Quarantine", "_quarantine")

    def test_matches_known_alias(self) -> None:
        assert is_quarantine_like_name("Quarantaine", "_quarantine")
        assert is_quarantine_like_name("Corbeille", "_quarantine")

    def test_does_not_match_unrelated_name(self) -> None:
        assert not is_quarantine_like_name("01_decisions", "_quarantine")

    def test_does_not_match_on_substring_containment(self) -> None:
        # "quarantaine_sanitaire" is a legitimate taxonomy folder in a real
        # corpus (public health documents) — must not be rejected just
        # because it contains the word.
        assert not is_quarantine_like_name("quarantaine_sanitaire", "_quarantine")

    def test_does_not_match_broader_taxonomy_words(self) -> None:
        # "archive"/"obsolete" are real taxonomy categories, deliberately
        # excluded from the alias list.
        assert not is_quarantine_like_name("archives", "_quarantine")
        assert not is_quarantine_like_name("obsolete", "_quarantine")


class TestCheckNotQuarantineCollision:
    def test_passes_for_unrelated_name(self, tmp_path: Path) -> None:
        qdir = tmp_path / "_quarantine"
        check_not_quarantine_collision(tmp_path / "01_decisions", qdir)  # no exception

    def test_raises_for_configured_name_case_insensitive(self, tmp_path: Path) -> None:
        qdir = tmp_path / "_quarantine"
        with pytest.raises(ValueError, match="server-managed quarantine"):
            check_not_quarantine_collision(tmp_path / "_Quarantine", qdir)

    def test_raises_for_known_alias(self, tmp_path: Path) -> None:
        qdir = tmp_path / "_quarantine"
        with pytest.raises(ValueError, match="server-managed quarantine"):
            check_not_quarantine_collision(tmp_path / "quarantaine", qdir)

    def test_raises_for_path_nested_inside_quarantine(self, tmp_path: Path) -> None:
        qdir = tmp_path / "_quarantine"
        with pytest.raises(ValueError, match="quarantine folder"):
            check_not_quarantine_collision(qdir / "drafts", qdir)

    def test_passes_for_legitimate_similar_name(self, tmp_path: Path) -> None:
        qdir = tmp_path / "_quarantine"
        check_not_quarantine_collision(tmp_path / "quarantaine_sanitaire", qdir)  # no exception
