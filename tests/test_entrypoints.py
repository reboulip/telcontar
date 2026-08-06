"""F4 — verify the packaged console-script entry points resolve and run.

`telcontar` (host TUI) and `telcontar-server` (MCP stdio server) both block when
invoked bare, so these tests exercise them non-blockingly:

- the ``[project.scripts]`` wiring is resolved through the *installed* package
  metadata and asserted to load to a callable (proves packaging is correct);
- invoking each ``main()`` with ``--help`` / ``--version`` exits cleanly (0) in a
  subprocess with a timeout, proving the entry point starts and its argument
  handling works without launching the TUI or the blocking stdio server.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

# Cold subprocess imports (textual, mcp, markitdown) can be slow; be generous.
_TIMEOUT = 120

_EXPECTED = {"telcontar": "host.main:main", "telcontar-server": "server.main:main"}


def _console_scripts() -> dict:
    eps = entry_points(group="console_scripts")
    return {ep.name: ep for ep in eps if ep.name in _EXPECTED}


# ── Packaging wiring: [project.scripts] resolves end-to-end ────────────────────


class TestEntryPointWiring:
    def test_both_scripts_registered(self) -> None:
        assert set(_console_scripts()) == set(_EXPECTED)

    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_target_loads_to_callable(self, name: str) -> None:
        ep = _console_scripts()[name]
        assert ep.value == _EXPECTED[name]
        assert callable(ep.load())


# ── Runtime: each entry point starts and exits cleanly ────────────────────────


def _run_main(module: str, prog: str, *args: str) -> subprocess.CompletedProcess:
    """Invoke ``<module>:main`` in a fresh interpreter with the given argv."""
    code = f"import sys; from {module} import main; sys.argv = {[prog, *args]!r}; main()"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


@pytest.mark.parametrize(
    ("module", "prog"),
    [("host.main", "telcontar"), ("server.main", "telcontar-server")],
)
class TestEntryPointsRunAndExitClean:
    def test_help_exits_zero(self, module: str, prog: str) -> None:
        r = _run_main(module, prog, "--help")
        assert r.returncode == 0, r.stderr
        assert prog in r.stdout

    def test_version_exits_zero(self, module: str, prog: str) -> None:
        r = _run_main(module, prog, "--version")
        assert r.returncode == 0, r.stderr
        assert prog in r.stdout


# ── S6: --web routes to the NiceGUI entrypoint instead of the Textual TUI ────────


class TestWebFlagRouting:
    """In-process (not subprocess) so run_web can be mocked out — actually
    starting the web server isn't something a unit test should do."""

    def test_web_flag_routes_to_run_web(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[Path | None] = []
        monkeypatch.setattr("host.web.main.run_web", lambda target=None: calls.append(target))
        monkeypatch.setattr(sys, "argv", ["telcontar", "--web"])

        main()

        assert calls == [None]

    def test_web_flag_with_target_passes_it_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[Path | None] = []
        monkeypatch.setattr("host.web.main.run_web", lambda target=None: calls.append(target))
        monkeypatch.setattr(sys, "argv", ["telcontar", "--web", "--target", "/tmp/some-dir"])

        main()

        assert calls == [Path("/tmp/some-dir")]

    def test_no_web_flag_launches_the_tui_not_the_web_ui(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from host.main import main

        web_calls = []
        tui_calls = []
        monkeypatch.setattr("host.web.main.run_web", lambda target=None: web_calls.append(target))
        monkeypatch.setattr("host.app.OrganizerApp.run", lambda self: tui_calls.append(True))
        monkeypatch.setattr(sys, "argv", ["telcontar"])

        main()

        assert tui_calls == [True]
        assert web_calls == []
