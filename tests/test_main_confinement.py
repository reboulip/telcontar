"""M2 — path-confinement guard wired into server/main.py's tool handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from server import main as server_main


class TestConfinementRoots:
    def test_includes_target_dir_when_set(self, tmp_path: Path) -> None:
        cfg = Settings(target_dir=tmp_path)
        roots = server_main._confinement_roots(cfg)
        assert tmp_path in roots

    def test_always_includes_cwd(self, tmp_path: Path) -> None:
        cfg = Settings(target_dir=tmp_path)
        roots = server_main._confinement_roots(cfg)
        assert Path.cwd() in roots

    def test_omits_target_dir_when_none(self) -> None:
        cfg = Settings(target_dir=None)
        roots = server_main._confinement_roots(cfg)
        assert roots == [Path.cwd()]


class TestCheckWithinRootIntegration:
    def test_passes_for_path_inside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = Settings(target_dir=tmp_path)
        server_main._check_within_root(str(tmp_path / "doc.txt"), cfg)  # no exception

    def test_raises_for_path_outside_target_and_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside = tmp_path / "elsewhere" / "secret.env"
        cfg = Settings(target_dir=target)
        with pytest.raises(PermissionError):
            server_main._check_within_root(str(outside), cfg)


class TestGuardedHandlers:
    """Spot-check a read-only and a plan-building tool wrapper end-to-end."""

    def test_list_dir_raises_outside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        with pytest.raises(PermissionError):
            server_main.list_dir(str(outside))

    def test_list_dir_passes_inside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        result = server_main.list_dir(str(target))
        assert result["path"] == str(target)

    def test_propose_move_raises_when_source_outside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside_file = tmp_path / "elsewhere" / "doc.txt"
        outside_file.parent.mkdir()
        outside_file.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        with pytest.raises(PermissionError):
            server_main.propose_move(str(outside_file), str(target), "some-plan-id")
