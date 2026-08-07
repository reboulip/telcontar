"""Tests for the NiceGUI web UI's rendering and wiring, using NiceGUI's
headless `user` fixture (host/web/main.py:1, `-p nicegui.testing.user_plugin`
in pyproject.toml's [tool.pytest.ini_options]).

This is the *only* place the web UI is driven through a real page render —
everything the underlying logic needs to prove (approval/cost future
resolution, ledger threading, step open/close, etc.) is already covered
NiceGUI-free in tests/test_web_session.py, tests/test_host_format.py, and
tests/test_host_paths.py. Tests here assert two things only: the page renders
what the session data says, and a click/interaction wires through to the
right session call.

Gotchas learned the hard way (read before adding more tests):

1. **Runpy double-module.** The `user` fixture executes host/web/main.py via
   `runpy.run_path(..., run_name="__main__")` — a *second, separate* module
   object from the one `import host.web.main` gives you elsewhere in the
   suite. Patches on `host.web.main.*` silently no-op: the runpy copy never
   sees them. Every test seam this UI needs therefore lives in
   host/web/session.py (or bridge.py/config.settings), which stays the one
   cached module both the test and the runpy copy of main.py actually share.
   Never patch `host.web.main.*` and expect it to take effect — patch
   `host.web.session.*` instead.

2. **`sys.modules['__main__']` gets popped after every web test.** NiceGUI's
   teardown (`nicegui/testing/general.py:nicegui_reset_globals`) walks every
   registered page function's `__module__`; runpy-executed pages report
   `"__main__"`, which doesn't start with `"tests."`, so the teardown deletes
   `sys.modules["__main__"]` (and its "parents", i.e. nothing else, since
   `"__main__"` has no dots). Left unguarded, the *first* test using the
   `user` fixture corrupts `sys.modules["__main__"]` for every test that
   runs after it in the same process — including unrelated non-web tests.
   The `_preserve_dunder_main` fixture below guards this; every test in this
   file depends on it via autouse.

3. **A closed `ui.dialog` is hidden, not removed** (NiceGUI's `Dialog.close()`
   just clears `.visible`). `ElementFilter(only_visible=True)` — what
   `should_see`/`should_not_see` use — checks `element.visible`, so this
   works correctly *if* the dialog code actually flips `.visible` on close.
   If a `should_not_see` assertion for a dialog button flakes true (still
   sees it after the dialog "closed"), check whether the dialog was properly
   closed vs. just abandoned.

4. **The 0.5s render-poll timer vs. the 0.3s default `should_see` retry
   budget.** `web_session.REFRESH_INTERVAL` exists specifically so tests can
   shrink it — see the `_fast_refresh` fixture below. Without it, assertions
   against anything the poll timer renders (transcript turns, step rows,
   status line) either time out or need `retries=10+` on every call.

5. **Marker convention:** give every interactive element a `.mark("...")` so
   later tests can target it precisely instead of matching on rendered text
   (which changes with copy edits). Use the TUI's existing widget names as
   markers (e.g. `approve-btn`, `refine-btn`, `cost-proceed`,
   `journal-undo`) so the web port is auditable against the TUI 1:1.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
from nicegui.testing import User

from host.web import session as web_session


@pytest.fixture(autouse=True)
def _preserve_dunder_main() -> Iterator[None]:
    """Guard against nicegui_reset_globals() popping sys.modules['__main__']
    after a web test — see gotcha #2 above. Must wrap every test that uses
    the `user` fixture, hence autouse in this file."""
    saved = sys.modules.get("__main__")
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["__main__"] = saved


@pytest.fixture(autouse=True)
def _fast_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the render-poll interval so should_see()'s ~0.3s retry budget
    doesn't race the real 0.5s cadence — see gotcha #4 above."""
    monkeypatch.setattr(web_session, "REFRESH_INTERVAL", 0.02)


@pytest.fixture(autouse=True)
def _reset_session_registry() -> Iterator[None]:
    """Web sessions are module-level global state (host/web/session.py) —
    clear it around every test so one test's run can't leak into another's."""
    web_session._SESSIONS.clear()
    web_session.set_default_target(None)
    try:
        yield
    finally:
        web_session._SESSIONS.clear()
        web_session.set_default_target(None)


# ── Seam smoke test ──────────────────────────────────────────────────────────
#
# Confirms the test seam described in gotcha #1 actually works before any
# later wave builds on it: a patch on host.web.session.* must reach the page
# code the `user` fixture drives, even though that page code runs as a
# runpy-executed copy of host/web/main.py.


async def test_default_target_seam_is_patchable_from_session_module(
    user: User, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []
    original_create = web_session.create

    def spy_create(target: Path) -> web_session.RunSession:
        calls.append(target)
        return original_create(target)

    monkeypatch.setattr(web_session, "get_default_target", lambda: tmp_path)
    monkeypatch.setattr(web_session, "create", spy_create)

    await user.open("/")

    assert calls == [tmp_path]


async def test_landing_page_shows_picker_prompt_with_no_default_target(user: User) -> None:
    await user.open("/")

    await user.should_see("Pick a directory in the sidebar")


# ── Setup wizard (U2) ────────────────────────────────────────────────────────


async def test_index_redirects_to_setup_when_not_configured(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.settings.is_configured", lambda: False)

    await user.open("/")

    await user.should_see("Welcome!")


async def test_wizard_full_flow_saves_and_reaches_done_step(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[dict] = []

    def fake_save_user_config(updates: dict, allow_plaintext_fallback: bool = False) -> None:
        saved.append(dict(updates))

    monkeypatch.setattr("config.settings.save_user_config", fake_save_user_config)

    await user.open("/setup")
    user.find(marker="btn-welcome-next").click()
    await user.should_see(marker="btn-svc-compatible")

    user.find(marker="btn-svc-compatible").click()
    await user.should_see(marker="input-url")

    user.find(marker="input-url").type("https://example.com")
    user.find(marker="input-key").type("sk-test")
    user.find(marker="input-model").type("gpt-5")
    user.find(marker="btn-api-next").click()
    await user.should_see(marker="select-profile")

    user.find(marker="btn-profile-next").click()
    await user.should_see("You're all set!")

    assert saved == [
        {
            "llm_base_url": "https://example.com",
            "llm_api_key": "sk-test",
            "llm_model": "gpt-5",
            "profile": "is_it_project",
        }
    ]


async def test_wizard_api_step_validates_url_before_key_before_model(user: User) -> None:
    await user.open("/setup")
    user.find(marker="btn-welcome-next").click()
    await user.should_see(marker="btn-svc-compatible")
    user.find(marker="btn-svc-compatible").click()
    await user.should_see(marker="btn-api-next")

    # Every field blank — the URL error must win (frozen order/string, ports
    # the TUI's SetupScreen validation verbatim).
    user.find(marker="btn-api-next").click()
    await user.should_see("Please enter the web address of your AI service.")


async def test_wizard_warns_before_plaintext_key_fallback(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.settings import PlaintextKeyFallbackNeeded

    calls: list[bool] = []

    def flaky_save_user_config(updates: dict, allow_plaintext_fallback: bool = False) -> None:
        calls.append(allow_plaintext_fallback)
        if not allow_plaintext_fallback:
            raise PlaintextKeyFallbackNeeded("keyring unavailable")

    monkeypatch.setattr("config.settings.save_user_config", flaky_save_user_config)

    await user.open("/setup")
    user.find(marker="btn-welcome-next").click()
    await user.should_see(marker="btn-svc-compatible")
    user.find(marker="btn-svc-compatible").click()
    await user.should_see(marker="input-url")
    user.find(marker="input-url").type("https://example.com")
    user.find(marker="input-key").type("sk-test")
    user.find(marker="input-model").type("gpt-5")
    user.find(marker="btn-api-next").click()
    await user.should_see(marker="btn-profile-next")

    user.find(marker="btn-profile-next").click()
    await user.should_see('"Save & continue →"')
    await user.should_not_see("You're all set!")

    # Second press, now with the warning acknowledged — must succeed.
    user.find(marker="btn-profile-next").click()
    await user.should_see("You're all set!")

    assert calls == [False, True]
