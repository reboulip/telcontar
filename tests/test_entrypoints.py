"""F4 — verify the packaged console-script entry points resolve and run.

`telcontar` (host web UI) and `telcontar-server` (MCP stdio server) both block when
invoked bare, so these tests exercise them non-blockingly:

- the ``[project.scripts]`` wiring is resolved through the *installed* package
  metadata and asserted to load to a callable (proves packaging is correct);
- invoking each ``main()`` with ``--help`` / ``--version`` exits cleanly (0) in a
  subprocess with a timeout, proving the entry point starts and its argument
  handling works without launching the blocking stdio server.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

# Cold subprocess imports (mcp, markitdown) can be slow; be generous.
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


# ── U10: bare launch routes to the NiceGUI web UI ──────────────────────────────


class TestWebFlagRouting:
    """In-process (not subprocess) so run_web can be mocked out — actually
    starting the web server isn't something a unit test should do.

    Fakes accept ``**kwargs`` (not just ``target=None``) because host.main's
    bare/`--target` call now always also passes ``native=`` (V1) — an
    explicit-signature fake with no **kwargs would raise TypeError on that
    extra keyword.
    """

    def test_bare_launch_routes_to_run_web(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[Path | None] = []
        monkeypatch.setattr(
            "host.web.main.run_web", lambda target=None, **kwargs: calls.append(target)
        )
        monkeypatch.setattr(sys, "argv", ["telcontar"])

        main()

        assert calls == [None]

    def test_bare_launch_with_target_passes_it_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from host.main import main

        calls: list[Path | None] = []
        monkeypatch.setattr(
            "host.web.main.run_web", lambda target=None, **kwargs: calls.append(target)
        )
        monkeypatch.setattr(sys, "argv", ["telcontar", "--target", "/tmp/some-dir"])

        main()

        assert calls == [Path("/tmp/some-dir")]


# ── V1: --browser is the escape hatch from the native-window default ──────────


class TestBrowserFlagRouting:
    """In-process, same rationale as TestWebFlagRouting above."""

    def test_bare_launch_defaults_to_native_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[object] = []
        monkeypatch.setattr(
            "host.web.main.run_web",
            lambda target=None, **kwargs: calls.append(kwargs.get("native")),
        )
        monkeypatch.setattr(sys, "argv", ["telcontar"])

        main()

        assert calls == [True]

    def test_browser_flag_disables_native_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[object] = []
        monkeypatch.setattr(
            "host.web.main.run_web",
            lambda target=None, **kwargs: calls.append(kwargs.get("native")),
        )
        monkeypatch.setattr(sys, "argv", ["telcontar", "--browser"])

        main()

        assert calls == [False]

    def test_browser_flag_combines_with_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[tuple[Path | None, object]] = []
        monkeypatch.setattr(
            "host.web.main.run_web",
            lambda target=None, **kwargs: calls.append((target, kwargs.get("native"))),
        )
        monkeypatch.setattr(sys, "argv", ["telcontar", "--target", "/tmp/some-dir", "--browser"])

        main()

        assert calls == [(Path("/tmp/some-dir"), False)]


# ── AA2: --auto-class routes to the headless CLI, never the web UI ────────────


class TestAutoClassRouting:
    def test_help_lists_auto_class_flags(self) -> None:
        r = _run_main("host.main", "telcontar", "--help")
        assert "--auto-class" in r.stdout
        assert "--dry-run" in r.stdout

    def test_auto_class_routes_to_cli_with_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        calls: list[tuple[Path, bool]] = []

        def _fake_cli(target: Path, *, dry_run: bool = False) -> int:
            calls.append((target, dry_run))
            return 1

        monkeypatch.setattr("host.autoclass.run_auto_class_cli", _fake_cli)
        monkeypatch.setattr(
            "host.web.main.run_web", lambda **kwargs: pytest.fail("web UI must not start")
        )
        monkeypatch.setattr(
            sys, "argv", ["telcontar", "--auto-class", "--target", "/x", "--dry-run"]
        )

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
        assert calls == [(Path("/x"), True)]

    def test_auto_class_requires_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        monkeypatch.setattr(sys, "argv", ["telcontar", "--auto-class"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_dry_run_alone_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from host.main import main

        monkeypatch.setattr(sys, "argv", ["telcontar", "--dry-run"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_autoclass_module_does_not_import_web_ui(self) -> None:
        code = (
            "import sys, host.autoclass; "
            "sys.exit(1 if any(m.startswith(('nicegui', 'host.web')) for m in sys.modules) else 0)"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=_TIMEOUT
        )
        assert r.returncode == 0, r.stderr
