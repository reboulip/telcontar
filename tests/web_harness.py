"""Shared NiceGUI web-UI test fixtures (V11).

Extracted from tests/test_web_ui.py's top-of-file fixtures so a NEW test
module can reuse them without duplicating the gotchas they guard against —
see that file's module docstring (gotchas #1, #2, #4) for the full
explanation of *why* each of these exists. tests/test_web_ui.py keeps its own
copies untouched; import from here instead of re-deriving them, e.g.:

    from tests.web_harness import (  # noqa: F401
        _fast_refresh,
        _preserve_dunder_main,
        _reset_session_registry,
    )

This module is not itself a `test_*.py` file, so pytest never collects it
directly — fixtures only take effect once imported into a module pytest does
collect.
"""

from __future__ import annotations

import sys
from typing import Iterator

import pytest

from host.web import session as web_session


@pytest.fixture(autouse=True)
def _preserve_dunder_main() -> Iterator[None]:
    """Guard against nicegui_reset_globals() popping sys.modules['__main__']
    after a web test. NiceGUI's teardown walks every registered page
    function's `__module__`; the `user` fixture executes host/web/main.py via
    `runpy.run_path(..., run_name="__main__")`, so registered pages report
    `"__main__"` — which doesn't start with `"tests."`, so the teardown
    deletes `sys.modules["__main__"]`. Left unguarded, the *first* test using
    the `user` fixture corrupts `sys.modules["__main__"]` for every test that
    runs after it in the same process, web or not. Must wrap every test that
    uses the `user` fixture, hence autouse."""
    saved = sys.modules.get("__main__")
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["__main__"] = saved


@pytest.fixture(autouse=True)
def _fast_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the render-poll interval so should_see()'s ~0.3s retry budget
    doesn't race the real 0.5s cadence (web_session.REFRESH_INTERVAL exists
    specifically so tests can shrink it). Also shrinks the corpus browser's
    poll (X10) for the same reason — both are read once at mount time (a
    ui.timer's interval argument), so this must run before user.open()."""
    monkeypatch.setattr(web_session, "REFRESH_INTERVAL", 0.02)
    monkeypatch.setattr(web_session, "CORPUS_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(web_session, "GRAPH_POLL_INTERVAL", 0.02)


@pytest.fixture(autouse=True)
def _reset_session_registry() -> Iterator[None]:
    """Web sessions are module-level global state (host/web/session.py) —
    clear it around every test so one test's run can't leak into another's."""
    web_session._SESSIONS.clear()
    web_session.set_default_target(None)
    web_session.set_start_dir(None)
    try:
        yield
    finally:
        web_session._SESSIONS.clear()
        web_session.set_default_target(None)
        web_session.set_start_dir(None)
