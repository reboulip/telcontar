"""M2/M7 — path-confinement guard and allowlist-default-on wired into
server/main.py's tool handlers."""

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


class TestEffectiveAllowlistDirs:
    """M7: ALLOWLIST_DIRS defaults to the target directory instead of no
    restriction when the operator leaves it unset."""

    def test_defaults_to_target_dir_when_unset(self, tmp_path: Path) -> None:
        cfg = Settings(target_dir=tmp_path)
        assert cfg.effective_allowlist_dirs() == [tmp_path]

    def test_empty_when_neither_set(self) -> None:
        cfg = Settings(target_dir=None)
        assert cfg.effective_allowlist_dirs() == []

    def test_explicit_allowlist_always_wins_over_target_dir(self, tmp_path: Path) -> None:
        explicit = tmp_path / "explicit"
        cfg = Settings(target_dir=tmp_path, allowlist_dirs=[explicit])
        # No merging — the explicit list is returned as-is, target_dir is not added.
        assert cfg.effective_allowlist_dirs() == [explicit]


class TestGuardedHandlers:
    """Spot-check a read-only and a plan-building tool wrapper end-to-end."""

    def test_read_file_defaults_to_target_dir_when_allowlist_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        with pytest.raises(PermissionError):
            server_main.read_file(str(outside))

    def test_read_file_respects_narrower_explicit_allowlist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        allowed_subdir = target / "public"
        other_subdir = target / "private"
        allowed_subdir.mkdir(parents=True)
        other_subdir.mkdir()
        doc = allowed_subdir / "doc.txt"
        doc.write_text("hello")
        monkeypatch.setattr(
            server_main,
            "_get_settings",
            lambda: Settings(target_dir=target, allowlist_dirs=[allowed_subdir]),
        )

        # Inside target_dir (passes check_within_root) AND inside the explicit,
        # narrower allowlist — both gates pass, so this succeeds.
        server_main.read_file(str(doc))

        # A file elsewhere under target_dir, but outside the explicit allowlist,
        # is rejected — proving the explicit list isn't just ignored/widened
        # back out to the whole target_dir.
        (other_subdir / "secret.txt").write_text("x")
        with pytest.raises(PermissionError):
            server_main.read_file(str(other_subdir / "secret.txt"))

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

    def test_read_file_batch_isolates_out_of_root_path_as_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        inside = target / "doc.txt"
        inside.write_text("hello")
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        result = server_main.read_file_batch([str(inside), str(outside)])
        assert result[str(inside)] == "hello"
        assert isinstance(result[str(outside)], dict)
        assert "error" in result[str(outside)]

    def test_compute_checksum_batch_isolates_out_of_root_path_as_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        inside = target / "doc.txt"
        inside.write_bytes(b"hello")
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        result = server_main.compute_checksum_batch([str(inside), str(outside)])
        assert isinstance(result[str(inside)], str)
        assert isinstance(result[str(outside)], dict)
        assert "error" in result[str(outside)]

    def test_record_document_batch_raises_when_a_path_is_outside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside = tmp_path / "elsewhere" / "doc.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        with pytest.raises(PermissionError):
            server_main.record_document_batch(
                [
                    {
                        "checksum": "c1",
                        "path": str(outside),
                        "title": "t",
                        "type": "notes",
                        "summary": "s",
                        "provenance": "p",
                    }
                ]
            )

    def test_rehome_documents_raises_when_new_path_is_outside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        outside = tmp_path / "elsewhere" / "doc.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        monkeypatch.setattr(server_main, "_get_settings", lambda: Settings(target_dir=target))

        with pytest.raises(PermissionError):
            server_main.rehome_documents({"c1": str(outside)})

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
