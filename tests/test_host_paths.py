"""Direct unit tests for host/paths.py (extracted from host/app.py in Phase 18 S1).

These are pure-function tests with no Textual/Pilot dependency — moved here from
tests/test_app_ui.py (find_organizer_root) plus new coverage for the behaviours
previously exercised only indirectly through a Pilot test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from config import settings as settings_module
from host import paths as host_paths
from host.paths import (
    directory_overview,
    is_op_out_of_scope,
    resolve_graph_path,
    resolve_journal_path,
    resolve_registry_path,
    reveal_in_file_manager,
)


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the per-machine user config so Settings() can't pick up a
    developer's real ~/.telcontar/config.env — see tests/test_settings.py for
    the same pattern."""
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")


# ── find_organizer_root ────────────────────────────────────────────────────────


def test_find_organizer_root_finds_organizer_at_start(tmp_path: Path) -> None:
    from host.paths import find_organizer_root

    (tmp_path / ".organizer").mkdir()

    assert find_organizer_root(tmp_path) == tmp_path.resolve()


def test_find_organizer_root_walks_up_to_parent(tmp_path: Path) -> None:
    from host.paths import find_organizer_root

    (tmp_path / ".organizer").mkdir()
    sub = tmp_path / "docs" / "2024"
    sub.mkdir(parents=True)

    assert find_organizer_root(sub) == tmp_path.resolve()


def test_find_organizer_root_returns_none_when_absent(tmp_path: Path) -> None:
    from host.paths import find_organizer_root

    sub = tmp_path / "docs"
    sub.mkdir()

    assert find_organizer_root(sub) is None


# ── resolve_journal_path ────────────────────────────────────────────────────────


def test_resolve_journal_path_rebases_under_target(tmp_path: Path) -> None:
    target = tmp_path / "corpus"
    target.mkdir()

    assert resolve_journal_path(target) == target.resolve() / ".organizer" / "journal.jsonl"


def test_resolve_journal_path_absolute_override_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "corpus"
    target.mkdir()
    explicit_journal = tmp_path / "elsewhere" / "journal.jsonl"
    monkeypatch.setenv("JOURNAL_PATH", str(explicit_journal))

    assert resolve_journal_path(target) == explicit_journal


# ── resolve_registry_path / resolve_graph_path ──────────────────────────────────


def test_resolve_registry_path_rebases_under_target(tmp_path: Path) -> None:
    target = tmp_path / "corpus"
    target.mkdir()

    assert resolve_registry_path(target) == target.resolve() / ".organizer" / "registry.json"


def test_resolve_graph_path_rebases_under_target(tmp_path: Path) -> None:
    target = tmp_path / "corpus"
    target.mkdir()

    assert resolve_graph_path(target) == target.resolve() / ".organizer" / "graph.json"


# ── directory_overview ──────────────────────────────────────────────────────────


def test_directory_overview_counts_files_and_subfolders(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "b.pdf").write_text("x")
    (tmp_path / "sub" / "c.txt").write_text("x")

    overview = directory_overview(tmp_path)

    assert "3 file(s) across 1 subfolder(s)" in overview
    assert "2× .pdf" in overview
    assert "1× .txt" in overview


def test_directory_overview_excludes_organizer_and_quarantine_dirs(tmp_path: Path) -> None:
    (tmp_path / ".organizer").mkdir()
    (tmp_path / ".organizer" / "registry.json").write_text("{}")
    (tmp_path / "_quarantine").mkdir()
    (tmp_path / "_quarantine" / "dup.pdf").write_text("x")
    (tmp_path / "real.pdf").write_text("x")

    overview = directory_overview(tmp_path)

    assert "1 file(s) across 0 subfolder(s)" in overview


def test_directory_overview_truncates_at_max_entries(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")

    overview = directory_overview(tmp_path, max_entries=3)

    assert "3+ file(s)" in overview


# ── is_op_out_of_scope ───────────────────────────────────────────────────────────


def test_is_op_out_of_scope_false_when_src_inside_target(tmp_path: Path) -> None:
    op = {"src": str(tmp_path / "doc.pdf")}
    assert is_op_out_of_scope(op, tmp_path) is False


def test_is_op_out_of_scope_true_when_src_outside_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "elsewhere" / "secret.env"
    assert is_op_out_of_scope({"src": str(outside)}, target) is True


def test_is_op_out_of_scope_false_when_target_is_none() -> None:
    assert is_op_out_of_scope({"src": "/anywhere/doc.pdf"}, None) is False


def test_is_op_out_of_scope_false_when_src_missing(tmp_path: Path) -> None:
    assert is_op_out_of_scope({}, tmp_path) is False


def test_is_op_out_of_scope_false_when_src_empty(tmp_path: Path) -> None:
    assert is_op_out_of_scope({"src": ""}, tmp_path) is False


# ── reveal_in_file_manager (X5) ────────────────────────────────────────────


def test_reveal_in_file_manager_uses_explorer_select_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(host_paths.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))

    target = tmp_path / "plan_ops.json"
    assert reveal_in_file_manager(target) is True

    assert calls == [f'explorer /select,"{target}"']


def test_reveal_in_file_manager_uses_open_dash_r_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(host_paths.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))

    target = tmp_path / "plan_ops.json"
    assert reveal_in_file_manager(target) is True

    assert calls == [["open", "-R", str(target)]]


def test_reveal_in_file_manager_opens_parent_folder_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(host_paths.sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))

    target = tmp_path / "plan_ops.json"
    assert reveal_in_file_manager(target) is True

    assert calls == [["xdg-open", str(tmp_path)]]


def test_reveal_in_file_manager_never_raises_on_spawn_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(args: str) -> None:
        raise OSError("no such program")

    monkeypatch.setattr(host_paths.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", _boom)

    assert reveal_in_file_manager(tmp_path / "plan_ops.json") is False


def test_reveal_in_file_manager_never_waits_on_the_child_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """explorer.exe exits 1 even on success — the function must never
    inspect a return code, just fire-and-forget."""

    class _FakePopen:
        def __init__(self, args: str) -> None:
            self.args = args

        def wait(self) -> int:
            raise AssertionError("must never wait on the child process")

    monkeypatch.setattr(host_paths.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    assert reveal_in_file_manager(tmp_path / "plan_ops.json") is True
