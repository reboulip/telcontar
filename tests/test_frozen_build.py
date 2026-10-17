"""Frozen (PyInstaller) build support — packaging/windows/telcontar.spec.

The bundle's real behaviour is checked by packaging/windows/smoke_test.py in the
release workflow. These tests cover the in-process seams that make it possible:
how the host picks the MCP server command, the hidden ``--mcp-server`` mode, and
stdio setup for a windowed (no-console) exe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from host import agent


class TestServerCommand:
    def test_source_run_uses_interpreter_dash_m(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)

        assert agent._server_command() == (sys.executable, ["-m", "server.main"])

    def test_frozen_run_uses_sibling_console_exe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "telcontar.exe"))

        command, args = agent._server_command()

        assert Path(command) == tmp_path / "telcontar-cli.exe"
        assert args == ["--mcp-server"]


class TestMcpServerMode:
    def test_flag_runs_the_server_and_never_the_web_ui(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from host.main import main

        ran: list[str] = []
        monkeypatch.setattr("server.main.main", lambda: ran.append("server"))
        monkeypatch.setattr("host.web.main.run_web", lambda *a, **k: ran.append("web"))
        monkeypatch.setattr(sys, "argv", ["telcontar-cli", "--mcp-server"])

        main()

        assert ran == ["server"]


class TestEnsureStdStreams:
    def test_noop_when_streams_exist(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from config import settings
        from host.main import _ensure_std_streams

        monkeypatch.setattr(settings, "_USER_CONFIG_DIR", tmp_path / "cfg")

        _ensure_std_streams()

        assert not (tmp_path / "cfg").exists()

    def test_windowed_exe_streams_go_to_a_log_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from config import settings
        from host.main import _ensure_std_streams

        monkeypatch.setattr(settings, "_USER_CONFIG_DIR", tmp_path / "cfg")
        for name in ("stdin", "stdout", "stderr"):
            monkeypatch.setattr(sys, name, None)

        _ensure_std_streams()
        try:
            assert sys.stdout is not None and sys.stderr is not None and sys.stdin is not None
            sys.stderr.write("boom\n")
            # uvicorn's log formatter calls this on the stream; None would crash it.
            assert sys.stderr.isatty() is False
            assert "boom" in (tmp_path / "cfg" / "telcontar.log").read_text(encoding="utf-8")
        finally:
            sys.stdout.close()  # stdout and stderr are one shared file object
            sys.stdin.close()
