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

3. **`Dialog.close()`/`.open()` set `.value` (the Quasar `model-value`
   prop), not `.visible`.** `ElementFilter(only_visible=True)` — what
   `should_see`/`should_not_see` use — walks `.visible` on the element and
   its ancestors, which a closed dialog never touches. A `should_not_see`
   assertion for a dialog's own content therefore **never works**, closed
   or not — it isn't a flake to chase, it's testing the wrong attribute.
   Assert the *functional* outcome instead (state that changed, a future
   that resolved, a file that did/didn't change) rather than the dialog's
   own visibility.

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

6. **A `run.io_bound`/`asyncio.to_thread` await inside a click handler
   defined on a dialog opened from *another* dialog's own click handler
   (a "second-order" `background_tasks.create_or_defer` dispatch, one level
   removed from the page's top-level event handling) never resumes in this
   test harness** — the executor-callback continuation is silently lost, no
   exception, no timeout, just a coroutine that never continues past the
   `await`. Confirmed by direct experiment (host/web/dialogs.py's
   `build_journal_dialog`). Not an issue in a real browser session — this is
   specific to the headless simulation. If a nested-dialog handler needs to
   do blocking I/O, call it synchronously instead (justified when the
   operation is fast and rare — never for anything on the poll-timer path).

7. **`user.find(...).elements` has no defined order.** `User._gather_elements`
   (`nicegui/testing/user.py`) wraps its `ElementFilter` result in `set(...)`
   before returning — confirmed by direct experiment (V16's activity-column
   entries came back permuted despite both the underlying `session` list and
   the actual DOM children being correctly ordered). Never assert a specific
   sequence straight off `.elements` when more than one element shares a
   marker/kind/content match; sort by `.id` first (NiceGUI's element id is
   assigned in creation order, so it's a reliable proxy) or compare as a set
   if order genuinely doesn't matter.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Iterator

import pytest
from nicegui import ui
from nicegui.testing import User

from host import paths as host_paths
from host.agent import AgentEvent
from host.web import session as web_session
from host.web import shell as web_shell
from host.web import theme
from server.registry import DocumentRecord, Registry, save


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
    doesn't race the real 0.5s cadence — see gotcha #4 above. Also shrinks
    V7's tree-poll interval (5s in production) for the same reason — both
    are read once at mount time (ui.timer's interval argument), so this must
    run before user.open()."""
    monkeypatch.setattr(web_session, "REFRESH_INTERVAL", 0.02)
    monkeypatch.setattr(web_session, "TREE_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(web_session, "CORPUS_POLL_INTERVAL", 0.02)


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


# ── Startup view (U1) ────────────────────────────────────────────────────────


async def test_startup_organize_button_shows_error_with_no_selection(user: User) -> None:
    await user.open("/")
    await user.should_see(marker="btn-startup-organize")

    user.find(marker="btn-startup-organize").click()

    await user.should_see("Please choose a folder to organize.")


async def test_startup_query_button_shows_error_with_no_selection(user: User) -> None:
    await user.open("/")
    await user.should_see(marker="btn-startup-query")

    user.find(marker="btn-startup-query").click()

    await user.should_see("Please choose a folder to query.")


async def test_startup_query_button_shows_error_when_no_corpus_found(
    user: User, tmp_path: Path
) -> None:
    await user.open("/")
    await user.should_see(marker="btn-startup-query")

    user.find(kind=ui.tree).trigger("update:selected", args=str(tmp_path))
    user.find(marker="btn-startup-query").click()

    await user.should_see("No analyzed corpus found")


async def test_startup_query_button_navigates_to_query_page_for_valid_corpus(
    user: User, tmp_path: Path
) -> None:
    (tmp_path / ".organizer").mkdir()

    await user.open("/")
    await user.should_see(marker="btn-startup-query")

    user.find(kind=ui.tree).trigger("update:selected", args=str(tmp_path))
    user.find(marker="btn-startup-query").click()

    # Just confirms navigation + session creation — QueryBridge.run() itself
    # (settings/MCP session/LLM) is covered separately in test_web_session.py
    # with fakes; letting it run for real here would try to spawn a real
    # MCP server subprocess.
    await user.should_see(marker="query-input")
    query_sessions = [s for s in web_session.all_sessions() if s.mode == "query"]
    assert len(query_sessions) == 1
    assert query_sessions[0].target == tmp_path.resolve()


async def test_startup_organize_button_navigates_for_valid_selection(
    user: User, tmp_path: Path
) -> None:
    await user.open("/")
    await user.should_see(marker="btn-startup-organize")

    user.find(kind=ui.tree).trigger("update:selected", args=str(tmp_path))
    user.find(marker="btn-startup-organize").click()

    await user.should_see("Here's what I found")
    organize_sessions = [s for s in web_session.all_sessions() if s.mode == "organize"]
    assert len(organize_sessions) == 1


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


# ── Settings view (U3) ───────────────────────────────────────────────────────
#
# read_user_config() reads ~/.telcontar/config.env — a real per-machine file
# that may hold real values from manual testing. Every settings test below
# needs a blank slate, so this section's tests always patch it explicitly
# except where a test overrides it with its own fixture values (see
# test_settings_prefills_from_saved_config).


@pytest.fixture(autouse=True)
def _blank_saved_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.settings.read_user_config", lambda: {})


async def test_settings_link_is_reachable_from_the_landing_page(user: User) -> None:
    await user.open("/")

    user.find(marker="btn-sidebar-settings").click()
    await user.should_see(marker="btn-settings-save")


async def test_settings_prefills_from_saved_config(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "config.settings.read_user_config",
        lambda: {"llm_base_url": "https://saved.example", "llm_model": "gpt-4"},
    )

    await user.open("/")
    user.find(marker="btn-sidebar-settings").click()
    await user.should_see(marker="input-url")

    assert user.find(marker="input-url").elements.pop().value == "https://saved.example"
    assert user.find(marker="input-model").elements.pop().value == "gpt-4"
    assert user.find(marker="input-key").elements.pop().value == ""


async def test_settings_blank_key_preserves_existing_key(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[dict] = []

    def fake_save_user_config(updates: dict, allow_plaintext_fallback: bool = False) -> None:
        saved.append(dict(updates))

    monkeypatch.setattr("config.settings.save_user_config", fake_save_user_config)

    await user.open("/")
    user.find(marker="btn-sidebar-settings").click()
    await user.should_see(marker="input-url")

    user.find(marker="input-url").type("https://example.com")
    # The model field defaults to "gpt-5" when unset — clear before typing,
    # since .type() appends rather than replaces.
    user.find(marker="input-model").clear().type("gpt-5")
    # API key left blank — must not appear in the saved dict at all.
    user.find(marker="btn-settings-save").click()
    await user.should_not_see(marker="btn-settings-save")

    assert saved == [
        {
            "llm_base_url": "https://example.com",
            "llm_model": "gpt-5",
            "profile": "is_it_project",
            "approval_mode": "always",
        }
    ]
    assert "llm_api_key" not in saved[0]


async def test_settings_validation_requires_url_before_model(user: User) -> None:
    await user.open("/")
    user.find(marker="btn-sidebar-settings").click()
    await user.should_see(marker="btn-settings-save")

    user.find(marker="input-model").type("gpt-5")
    user.find(marker="btn-settings-save").click()

    await user.should_see("Please enter the web address.")


async def test_settings_cancel_does_not_save(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(
        "config.settings.save_user_config",
        lambda updates, allow_plaintext_fallback=False: saved.append(updates),
    )

    await user.open("/")
    user.find(marker="btn-sidebar-settings").click()
    await user.should_see(marker="btn-settings-cancel")

    user.find(marker="btn-settings-cancel").click()
    await user.should_not_see(marker="btn-settings-cancel")


# ── Approval dialog (U4) ─────────────────────────────────────────────────────


def _plan_data(**overrides: object) -> dict:
    base = {
        "ops": [
            {
                "op_id": "op1",
                "op_type": "move",
                "src": "a.txt",
                "dst": "b/a.txt",
            }
        ],
        "rationale": "",
        "folder_notes": {},
        "ops_json_path": "",
    }
    base.update(overrides)
    return base


async def test_approval_dialog_shows_rationale_and_disclaimer_when_present(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {"plan_id": "plan-12345678", "plan_data": _plan_data(rationale="Group invoices together")},
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Plan Review · plan-123 · 1 op(s)")
    await user.should_see("Group invoices together")
    await user.should_see("Model-generated rationale — not verified fact")


async def test_approval_dialog_omits_rationale_section_when_blank(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending("approval", {"plan_id": "plan-1", "plan_data": _plan_data(rationale="")})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="approve-btn")

    await user.should_not_see("Model-generated rationale")


async def test_approval_dialog_approve_returns_unchecked_ops_as_removed(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[
                    {"op_id": "op1", "op_type": "move", "src": "a.txt", "dst": "b/a.txt"},
                    {"op_id": "op2", "op_type": "move", "src": "c.txt", "dst": "d/c.txt"},
                ]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="op-op2")

    user.find(marker="op-op2").click()  # uncheck the second op
    user.find(marker="approve-btn").click()

    result = await pending.future
    assert result.approved is True
    assert result.removed_op_ids == ["op2"]


async def test_approval_dialog_shows_before_and_after_tree(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[{"op_id": "op1", "op_type": "move", "src": "/in/a.txt", "dst": "/sorted"}]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Before")
    await user.should_see("After")
    await user.should_see(marker="before-tree")
    await user.should_see(marker="after-tree")
    await user.should_see("a.txt")
    await user.should_see("sorted/")


async def test_approval_dialog_annotates_after_tree_with_folder_notes(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[{"op_id": "op1", "op_type": "move", "src": "/in/a.txt", "dst": "/sorted"}],
                folder_notes={"sorted": "Everything already filed"},
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Everything already filed")


async def test_approval_dialog_tree_shows_outside_target_marker(user: User, tmp_path: Path) -> None:
    """S4/M4 regression guard at the page level: an op whose source resolves
    outside the run's target directory must still carry the advisory marker
    once rendered inside V3's before/after tree, not just in the old flat
    checkbox list."""
    target = tmp_path / "target"
    target.mkdir()
    outside_src = str(tmp_path / "elsewhere" / "secret.env")
    session = web_session.create(target)
    session.started = True
    session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[
                    {
                        "op_id": "op1",
                        "op_type": "rename",
                        "src": outside_src,
                        "dst": "renamed.env",
                    }
                ]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("outside target")


async def test_approval_dialog_every_op_gets_exactly_one_checkbox_regardless_of_type(
    user: User, tmp_path: Path
) -> None:
    """Safety-critical invariant: every op in the plan — whether it lands on
    a tree node (move) or falls back to "Other operations" (create_dir) —
    must be individually deselectable via removed_op_ids."""
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[
                    {"op_id": "op1", "op_type": "move", "src": "/in/a.txt", "dst": "/sorted"},
                    {"op_id": "op2", "op_type": "create_dir", "src": "/sorted", "dst": ""},
                ]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="op-op1")
    await user.should_see(marker="op-op2")
    await user.should_see("Other operations")

    user.find(marker="op-op1").click()  # uncheck the tree-node op
    user.find(marker="op-op2").click()  # uncheck the "Other operations" op
    user.find(marker="approve-btn").click()

    result = await pending.future
    assert result.approved is True
    assert set(result.removed_op_ids) == {"op1", "op2"}


async def test_approval_dialog_shows_checkbox_hint_captions(user: User, tmp_path: Path) -> None:
    """X6: a visible caption (not just a tooltip, unreliable in the headless
    harness) explains what unchecking a box does."""
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[
                    {"op_id": "op1", "op_type": "move", "src": "/in/a.txt", "dst": "/sorted"},
                    {"op_id": "op2", "op_type": "create_dir", "src": "/sorted", "dst": ""},
                ]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="after-checkbox-hint")


async def test_approval_dialog_chained_ops_share_one_checkbox(user: User, tmp_path: Path) -> None:
    """X4/X6: a rename immediately followed by a move of the same file is
    one chained tree line with one checkbox, marked under every op_id in
    the chain — unchecking it excludes the whole chain, per the user's
    confirmed decision, never a partial chain."""
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "approval",
        {
            "plan_id": "plan-1",
            "plan_data": _plan_data(
                ops=[
                    {"op_id": "op1", "op_type": "rename", "src": "/t/a.txt", "dst": "b.txt"},
                    {"op_id": "op2", "op_type": "move", "src": "/t/a.txt", "dst": "/t/sorted"},
                ]
            ),
        },
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="op-op1")
    await user.should_see(marker="op-op2")

    # Both markers must resolve to the SAME checkbox element (X4's chain).
    [cb_by_op1] = user.find(marker="op-op1").elements
    [cb_by_op2] = user.find(marker="op-op2").elements
    assert cb_by_op1.id == cb_by_op2.id

    user.find(marker="op-op1").click()  # unchecking the shared checkbox once
    user.find(marker="approve-btn").click()

    result = await pending.future
    assert result.approved is True
    assert set(result.removed_op_ids) == {"op1", "op2"}


async def test_approval_dialog_hides_reveal_button_when_no_ops_json_path(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending("approval", {"plan_id": "plan-1", "plan_data": _plan_data()})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="approve-btn")
    await user.should_not_see(marker="reveal-ops-json")


async def test_approval_dialog_reveal_button_opens_the_ops_json_file(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(host_paths, "reveal_in_file_manager", lambda p: calls.append(p) or True)

    ops_json_path = str(tmp_path / ".organizer" / "plans" / "plan-1" / "plan_ops.json")
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {"plan_id": "plan-1", "plan_data": _plan_data(ops_json_path=ops_json_path)},
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="reveal-ops-json")
    user.find(marker="reveal-ops-json").click()

    assert calls == [Path(ops_json_path)]


async def test_approval_dialog_reveal_button_refuses_a_path_outside_target(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(host_paths, "reveal_in_file_manager", lambda p: calls.append(p) or True)

    outside_path = str(tmp_path.parent / "elsewhere" / "plan_ops.json")
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "approval",
        {"plan_id": "plan-1", "plan_data": _plan_data(ops_json_path=outside_path)},
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="reveal-ops-json")
    user.find(marker="reveal-ops-json").click()

    assert calls == []


async def test_approval_dialog_refine_with_blank_text_is_a_noop(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("approval", {"plan_id": "plan-1", "plan_data": _plan_data()})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="refine-btn")

    user.find(marker="refine-btn").click()  # blank input — must not resolve

    assert not pending.future.done()
    await user.should_see(marker="approve-btn")  # dialog is still open


async def test_approval_dialog_refine_with_text_sends_refinement(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("approval", {"plan_id": "plan-1", "plan_data": _plan_data()})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="refine-input")

    user.find(marker="refine-input").type("don't touch b.txt")
    user.find(marker="refine-btn").click()

    result = await pending.future
    assert result.approved is False
    assert result.refinement == "don't touch b.txt"


async def test_approval_dialog_reject_sends_bare_rejection(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("approval", {"plan_id": "plan-1", "plan_data": _plan_data()})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="reject-btn")

    user.find(marker="reject-btn").click()

    result = await pending.future
    assert result.approved is False
    assert result.refinement is None
    assert result.removed_op_ids == []


async def test_sidebar_tree_refreshes_when_fs_revision_changes(user: User, tmp_path: Path) -> None:
    # Asserts against the tree's underlying `nodes` prop data directly
    # rather than should_see(content=...): Quasar's QTree only renders a
    # child as *visible* once its parent is expanded in the UI, regardless
    # of whether the data is already loaded — should_see's visibility-based
    # matching can't see it either way, so it isn't the right tool here.
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-sidebar-settings")  # page has rendered

    (tmp_path / "new_file.txt").write_text("x")
    session.bump_fs_revision()

    for _ in range(20):
        if "new_file.txt" in _root_children_labels(user):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(
            f"sidebar tree was not refreshed with new_file.txt; saw {_root_children_labels(user)}"
        )


def _root_children_labels(user: User) -> set[str]:
    [tree] = user.find(kind=ui.tree).elements
    root_children = tree.props["nodes"][0].get("children", [])
    return {child.get("label") for child in root_children}


async def test_sidebar_tree_manual_refresh_button_updates_tree(user: User, tmp_path: Path) -> None:
    """V7: the refresh button calls Shell.reload_tree() directly — no
    fs_revision bump involved at all, unlike U4's execute_plan-triggered
    refresh."""
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-tree-refresh")

    (tmp_path / "manual.txt").write_text("x")
    user.find(marker="btn-tree-refresh").click()

    for _ in range(20):
        if "manual.txt" in _root_children_labels(user):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"tree was not refreshed; saw {_root_children_labels(user)}")


async def test_sidebar_tree_periodic_poll_updates_tree_without_fs_revision_bump(
    user: User, tmp_path: Path
) -> None:
    """V7: the tree poll timer picks up an on-disk change on its own —
    unlike test_sidebar_tree_refreshes_when_fs_revision_changes above, this
    never calls session.bump_fs_revision() at all."""
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-sidebar-settings")

    (tmp_path / "polled.txt").write_text("x")

    for _ in range(40):
        if "polled.txt" in _root_children_labels(user):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"tree was not polled; saw {_root_children_labels(user)}")


# ── Document preview pane (X9) ──────────────────────────────────────────────


async def test_run_page_shows_placeholder_before_any_selection(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")

    await user.should_see(marker="doc-detail-placeholder")
    await user.should_not_see(marker="doc-detail-content")


async def test_run_page_shows_document_preview_for_analyzed_file(
    user: User, tmp_path: Path
) -> None:
    doc_path = tmp_path / "report.pdf"
    doc_path.write_text("hello")
    registry = Registry()
    registry.upsert(
        DocumentRecord.new(
            checksum="aaa",
            path=str(doc_path),
            title="Alpha Report",
            type="report",
            summary="A detailed summary of the report.",
            provenance="found during scan",
            date="2026-01-01",
        )
    )
    save(registry, tmp_path / ".organizer" / "registry.json")
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="doc-detail-placeholder")

    user.find(kind=ui.tree).trigger("update:selected", str(doc_path))

    await user.should_see(marker="doc-detail-content")
    await user.should_see("Alpha Report")
    await user.should_see("A detailed summary of the report.")


async def test_run_page_shows_not_analyzed_yet_for_unanalyzed_file(
    user: User, tmp_path: Path
) -> None:
    unanalyzed = tmp_path / "unanalyzed.txt"
    unanalyzed.write_text("still waiting on the analyzer")
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")

    user.find(kind=ui.tree).trigger("update:selected", str(unanalyzed))

    await user.should_see(marker="doc-detail-content")
    await user.should_see("unanalyzed.txt")
    await user.should_see("Not analyzed yet.")


async def test_run_page_selecting_a_directory_clears_the_preview(
    user: User, tmp_path: Path
) -> None:
    subdir = tmp_path / "sorted"
    subdir.mkdir()
    doc_path = tmp_path / "report.pdf"
    doc_path.write_text("hello")
    registry = Registry()
    registry.upsert(
        DocumentRecord.new(
            checksum="aaa",
            path=str(doc_path),
            title="Alpha Report",
            type="report",
            summary="s",
            provenance="p",
        )
    )
    save(registry, tmp_path / ".organizer" / "registry.json")
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    user.find(kind=ui.tree).trigger("update:selected", str(doc_path))
    await user.should_see(marker="doc-detail-content")

    user.find(kind=ui.tree).trigger("update:selected", str(subdir))

    await user.should_see(marker="doc-detail-placeholder")
    await user.should_not_see(marker="doc-detail-content")


# ── Cost estimate dialog (U5) ─────────────────────────────────────────────────


async def test_cost_dialog_shows_faithful_summary_including_batch_size(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "cost",
        {
            "summary": "fallback, should not be shown when data is present",
            "data": {
                "new": 42,
                "already_analyzed": 7,
                "estimated_tokens": 12345,
                "batch_size": 10,
            },
        },
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Analyze this corpus?")
    await user.should_see("42 new document(s)")
    await user.should_see("7 already analyzed, skipped")
    await user.should_see("12345 input tokens")
    await user.should_see("batched in groups of 10.")
    await user.should_see("A rough estimate from file sizes")


async def test_cost_dialog_falls_back_to_summary_when_data_is_empty(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending("cost", {"summary": "custom fallback summary text", "data": {}})

    await user.open(f"/run/{session.run_id}")

    await user.should_see("custom fallback summary text")


async def test_cost_dialog_proceed_resolves_approved(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "cost", {"summary": "est", "data": {"new": 1, "already_analyzed": 0}}
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="cost-proceed")

    user.find(marker="cost-proceed").click()

    result = await pending.future
    assert result.approved is True


async def test_cost_dialog_cancel_resolves_rejected(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "cost", {"summary": "est", "data": {"new": 1, "already_analyzed": 0}}
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="cost-cancel")

    user.find(marker="cost-cancel").click()

    result = await pending.future
    assert result.approved is False


# ── Ask-user dialog (V12) ─────────────────────────────────────────────────────


async def test_ask_user_dialog_shows_question_text(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.new_pending(
        "ask", {"questions": [{"text": "Group invoices by workstream or by date?"}]}
    )

    await user.open(f"/run/{session.run_id}")

    await user.should_see("telcontar has a question")
    await user.should_see("Group invoices by workstream or by date?")


async def test_ask_user_dialog_submit_composes_reply_from_selected_option(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "ask",
        {"questions": [{"text": "Group by?", "options": ["by-date-option", "by-ws-option"]}]},
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="ask-q0")

    user.find("by-date-option").click()
    user.find(marker="ask-submit").click()

    result = await pending.future
    assert result.provided is True
    assert result.reply == "Group by? → by-date-option"


async def test_ask_user_dialog_submit_appends_additional_comment(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending(
        "ask", {"questions": [{"text": "Group by?", "options": ["date-opt", "ws-opt"]}]}
    )

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="ask-comment")

    user.find("date-opt").click()
    user.find(marker="ask-comment").type("also archive the 2023 folder")
    user.find(marker="ask-submit").click()

    result = await pending.future
    assert "Group by? → date-opt" in result.reply
    assert "Additional comment: also archive the 2023 folder" in result.reply


async def test_ask_user_dialog_submit_with_only_a_comment_and_no_option_picked(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("ask", {"questions": [{"text": "Anything else?"}]})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="ask-comment")

    user.find(marker="ask-comment").type("skip the drafts folder entirely")
    user.find(marker="ask-submit").click()

    result = await pending.future
    assert result.provided is True
    assert result.reply == "Additional comment: skip the drafts folder entirely"


async def test_ask_user_dialog_submit_with_nothing_filled_in_resolves_not_provided(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("ask", {"questions": [{"text": "Group by?"}]})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="ask-submit")

    user.find(marker="ask-submit").click()  # nothing selected, no comment typed

    result = await pending.future
    assert result.provided is False
    assert result.reply == ""


async def test_ask_user_dialog_skip_resolves_not_provided(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    pending = session.new_pending("ask", {"questions": [{"text": "Group by?"}]})

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="ask-skip")

    user.find(marker="ask-skip").click()

    result = await pending.future
    assert result.provided is False
    assert result.reply == ""


# ── Journal dialog (U6) ───────────────────────────────────────────────────────


def _write_journal_entry(tmp_path: Path) -> None:
    journal_dir = tmp_path / ".organizer"
    journal_dir.mkdir(exist_ok=True)
    entry = {
        "op_type": "rename",
        "src": str(tmp_path / "old.txt"),
        "dst": "new.txt",
        "timestamp": "2026-07-01T10:00:00Z",
    }
    (journal_dir / "journal.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")


async def test_journal_button_shows_entry_count(user: User, tmp_path: Path) -> None:
    _write_journal_entry(tmp_path)
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Journal (1)")


async def test_journal_button_shows_zero_with_no_entries(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")

    await user.should_see("Journal (0)")


async def test_journal_dialog_lists_entries(user: User, tmp_path: Path) -> None:
    _write_journal_entry(tmp_path)
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")

    user.find(marker="btn-open-journal").click()

    await user.should_see("new.txt")
    await user.should_see(marker="journal-undo")


async def test_journal_dialog_empty_state(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")

    user.find(marker="btn-open-journal").click()

    await user.should_see("No operations recorded yet.")


async def test_journal_undo_requires_confirm_then_bumps_fs_revision(
    user: User, tmp_path: Path
) -> None:
    _write_journal_entry(tmp_path)
    (tmp_path / "new.txt").write_text("x")
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")
    user.find(marker="btn-open-journal").click()
    await user.should_see(marker="journal-undo")

    user.find(marker="journal-undo").click()
    await user.should_see(marker="journal-undo-confirm")
    # Not yet undone — confirm step must not have fired anything.
    assert session.fs_revision == 0

    user.find(marker="journal-undo-confirm").click()
    await user.should_see("Undone the last operation.")

    assert session.fs_revision == 1
    assert (tmp_path / "old.txt").exists()
    assert not (tmp_path / "new.txt").exists()


async def test_journal_undo_cancel_does_not_undo(user: User, tmp_path: Path) -> None:
    _write_journal_entry(tmp_path)
    (tmp_path / "new.txt").write_text("x")
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")
    user.find(marker="btn-open-journal").click()
    await user.should_see(marker="journal-undo")

    user.find(marker="journal-undo").click()
    await user.should_see(marker="journal-undo-cancel")
    user.find(marker="journal-undo-cancel").click()

    # Dialog.close() only clears its Quasar model-value, not .visible (see
    # gotcha #3), so should_not_see can't verify the confirm dialog closed
    # visually — assert the functional outcome instead: undo never ran.
    assert session.fs_revision == 0
    assert (tmp_path / "new.txt").exists()


async def test_journal_undo_blocked_while_step_is_open(user: User, tmp_path: Path) -> None:
    _write_journal_entry(tmp_path)
    session = web_session.create(tmp_path)
    session.started = True
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")
    user.find(marker="btn-open-journal").click()

    await user.should_see(marker="journal-busy")
    await user.should_not_see(marker="journal-undo")


# ── Step-detail section (T6, V13b) ───────────────────────────────────────────


async def test_step_detail_hidden_until_a_step_row_is_clicked(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")
    session.close_step({"ops_applied": 3}, ok=True)

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="step-detail-1")

    await user.should_not_see(marker="detail-title")


async def test_step_detail_opens_in_left_sidebar_with_title_and_content(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")
    session.close_step({"ops_applied": 3}, ok=True)

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="step-detail-1")

    user.find(marker="step-detail-1").click()

    await user.should_see(marker="detail-title")
    await user.should_see("execute_plan(plan_id='p1')")
    await user.should_see('"ops_applied": 3')


async def test_step_detail_close_button_hides_the_section(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")
    session.close_step({"ops_applied": 3}, ok=True)

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="step-detail-1")
    user.find(marker="step-detail-1").click()
    await user.should_see(marker="btn-detail-close")

    user.find(marker="btn-detail-close").click()

    await user.should_not_see(marker="detail-title")


async def test_step_detail_codemirror_uses_the_dark_theme(user: User, tmp_path: Path) -> None:
    """V13b root cause: ui.codemirror defaults to a light theme regardless
    of the app's own dark palette — every instance must pass theme=
    explicitly (S4/M4-adjacent fixed-white-background bug, #35). The
    element is only reachable via find() once its container is visible —
    find() excludes elements hidden by an invisible ancestor by default."""
    session = web_session.create(tmp_path)
    session.started = True
    session.open_step("execute_plan", "execute_plan(plan_id='p1')")
    session.close_step({"ops_applied": 3}, ok=True)

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="step-detail-1")
    user.find(marker="step-detail-1").click()
    await user.should_see(marker="detail-content")

    [codemirror] = user.find(marker="detail-content").elements
    assert codemirror.props["theme"] == theme.CODEMIRROR_THEME


# ── Query view (U7) ──────────────────────────────────────────────────────────


async def test_query_button_hidden_until_organize_is_done(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.done = False

    await user.open(f"/run/{session.run_id}")

    await user.should_not_see(marker="btn-query-corpus")


async def test_query_button_appears_once_done_and_navigates(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-query-corpus")

    user.find(marker="btn-query-corpus").click()

    await user.should_see(marker="btn-sidebar-settings")  # a query page rendered
    query_sessions = [s for s in web_session.all_sessions() if s.mode == "query"]
    assert len(query_sessions) == 1
    assert query_sessions[0].target == tmp_path


async def test_query_page_not_found_for_unknown_run_id(user: User) -> None:
    await user.open("/query/does-not-exist")

    await user.should_see("Run not found")


async def test_query_page_asks_question_and_shows_answer(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_mcp_session(project_root, target=None):
        yield object()

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)

    async def fake_run_query_loop(**kwargs):
        on_event = kwargs["on_event"]
        on_event(AgentEvent("tool_call", "list_dir(...)", data={"tool": "list_dir"}))
        on_event(AgentEvent("tool_result", "", data={"result": {"entries": []}}))
        on_event(AgentEvent("done", "42 documents"))
        return "42 documents", [{"role": "assistant"}]

    monkeypatch.setattr("host.agent.run_query_loop", fake_run_query_loop)

    session = web_session.create(tmp_path, mode="query")

    await user.open(f"/query/{session.run_id}")
    await user.should_see("Ready — ask a question.")

    user.find(marker="query-input").type("how many documents?")
    user.find(marker="btn-query-ask").click()

    await user.should_see("how many documents?")
    await user.should_see("42 documents")
    # Rendered exactly once — from the return value, not the "done" event too.
    answers = [t for t in session.transcript if t.text == "42 documents"]
    assert len(answers) == 1


# ── Chat bubble alignment & colour (V13a) ────────────────────────────────────
#
# Visual layout/colour itself isn't observable through the headless `user`
# fixture (it renders no CSS — see the module docstring's gotchas) — these
# assert the underlying element got the right classes/props to produce it:
# `.classes("w-full")` is what makes the `sent=` alignment actually visible
# (NiceGUI's `.nicegui-column` CSS otherwise shrink-wraps every bubble to its
# content width regardless of `sent`), and `bg-color`/`text-color` are real
# QChatMessage props (Quasar's own component, confirmed against the vendored
# quasar.umd.js) that resolve against theme.PALETTE — silver/user,
# gold/telcontar, both paired with a `dark` foreground for contrast.
#
# ElementFilter special-cases ChatMessage to match `content=` against its
# `name` prop (see nicegui/element_filter.py), so filtering by `content=
# "user"`/`content="telcontar"` reliably picks out each speaker's own bubble
# — no `.mark(...)` needed on the element itself.


async def test_run_page_chat_bubbles_are_full_width_and_themed_by_speaker(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.add_turn("user", "hello there")
    session.add_turn("telcontar", "hi human")

    await user.open(f"/run/{session.run_id}")
    await user.should_see("hello there")
    await user.should_see("hi human")

    [user_bubble] = user.find(kind=ui.chat_message, content="user").elements
    [telcontar_bubble] = user.find(kind=ui.chat_message, content="telcontar").elements

    assert user_bubble.props["sent"] is True
    assert telcontar_bubble.props["sent"] is False
    assert "w-full" in user_bubble.classes
    assert "w-full" in telcontar_bubble.classes
    assert user_bubble.props["bg-color"] == "secondary"
    assert user_bubble.props["text-color"] == "dark"
    assert telcontar_bubble.props["bg-color"] == "primary"
    assert telcontar_bubble.props["text-color"] == "dark"


async def test_query_page_chat_bubbles_are_full_width_and_themed_by_speaker(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path, mode="query")
    session.started = True  # skip QueryBridge(session).start() — see U7 tests above
    session.add_turn("user", "how many documents?")
    session.add_turn("telcontar", "42 documents")

    await user.open(f"/query/{session.run_id}")
    await user.should_see("how many documents?")
    await user.should_see("42 documents")

    [user_bubble] = user.find(kind=ui.chat_message, content="user").elements
    [telcontar_bubble] = user.find(kind=ui.chat_message, content="telcontar").elements

    assert user_bubble.props["sent"] is True
    assert telcontar_bubble.props["sent"] is False
    assert "w-full" in user_bubble.classes
    assert "w-full" in telcontar_bubble.classes
    assert user_bubble.props["bg-color"] == "secondary"
    assert user_bubble.props["text-color"] == "dark"
    assert telcontar_bubble.props["bg-color"] == "primary"
    assert telcontar_bubble.props["text-color"] == "dark"


def test_sidebar_resize_js_is_an_invoked_iife() -> None:
    """V15: `_RESIZE_JS` used to be a bare arrow-function *expression*.
    NiceGUI's `run_javascript` evaluates the string via `eval`, and `eval` of
    a bare `() => { ... }` just constructs a function object and discards
    it — it never calls it, so the drag handlers never bound, in any
    browser, ever (not an Edge-specific regression).

    This can't be a behavioral test: the headless `user` fixture used
    throughout this file never executes JavaScript at all (see the module
    docstring's gotchas), so there's no way to actually simulate a drag here.
    A string-shape assertion — the snippet is now a self-invoking IIFE,
    `(() => { ... })()`, not a bare literal — is the only coverage possible
    from this suite. Real drag-to-resize behavior still needs manual
    verification in an actual browser.
    """
    code = web_shell._RESIZE_JS.strip()
    assert code.startswith("("), "must be an expression, not a bare statement"
    assert code.endswith(")()"), "must be self-invoking (IIFE) so eval() actually calls it"


async def test_progress_row_hidden_when_no_batch_is_running(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_not_see(marker="progress-row")


async def test_progress_bar_shows_rounded_integer_percent(user: User, tmp_path: Path) -> None:
    """V14: `ui.linear_progress` shows its raw float value ("0.75") by
    default — the row must show a formatted, rounded integer percent instead
    ("75%"), not the float."""
    session = web_session.create(tmp_path)
    session.started = True
    session.progress = {"analyzed": 3, "total": 4}

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="progress-row")
    await user.should_see("75%")

    [percent_label] = user.find(marker="progress-percent").elements
    assert percent_label.text == "75%"
    assert "0.75" not in percent_label.text


async def test_progress_current_document_label_shows_in_flight_filename(
    user: User, tmp_path: Path
) -> None:
    """V8b: the progress row names which document is being analyzed right
    now, using V8a's `"current"` list on the progress event data."""
    session = web_session.create(tmp_path)
    session.started = True
    session.progress = {"analyzed": 3, "total": 4, "current": ["report.pdf"]}

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="progress-row")
    await user.should_see("report.pdf")

    [current_label] = user.find(marker="progress-current").elements
    assert current_label.text == "report.pdf"


async def test_progress_current_document_label_shows_count_suffix_for_multiple_files(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.progress = {
        "analyzed": 1,
        "total": 4,
        "current": ["report.pdf", "notes.docx", "invoice.pdf"],
    }

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="progress-row")

    [current_label] = user.find(marker="progress-current").elements
    assert current_label.text == "report.pdf +2"


async def test_progress_current_document_label_empty_between_batches(
    user: User, tmp_path: Path
) -> None:
    """V8a clears `"current"` to `[]` on the post-batch event — the label
    must not keep showing a just-finished file's name."""
    session = web_session.create(tmp_path)
    session.started = True
    session.progress = {"analyzed": 4, "total": 4, "current": []}

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="progress-row")

    [current_label] = user.find(marker="progress-current").elements
    assert current_label.text == ""


async def test_progress_current_document_label_defensive_when_key_absent(
    user: User, tmp_path: Path
) -> None:
    """The pre-pass snapshot event omits `"current"` entirely (V8a) — must
    not raise, must render as empty."""
    session = web_session.create(tmp_path)
    session.started = True
    session.progress = {"analyzed": 2, "total": 5}

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="progress-row")

    [current_label] = user.find(marker="progress-current").elements
    assert current_label.text == ""


async def test_no_activity_entries_render_when_no_phase_seen_yet(
    user: User, tmp_path: Path
) -> None:
    """X3: activity entries interleave into conversation_column now — there
    is no separate container to assert on, just the absence of any
    activity-entry marker until a phase actually fires."""
    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="btn-open-journal")
    await user.should_not_see(marker="activity-entry")


async def test_activity_entries_interleave_chronologically_with_conversation_turns(
    user: User, tmp_path: Path
) -> None:
    """V16/X3: activity_label used to show one line, overwritten on every
    phase change and lost once done. X3 interleaves every phase into the
    conversation thread, in the same chronological order as the turns
    around it (both share RunSession._seq), instead of a separate column —
    and each phase still stays its own reviewable entry, not just the
    latest."""
    session = web_session.create(tmp_path)
    session.started = True
    session.add_turn("user", "please organize this")
    session.add_activity("Scanning the directory…")
    session.add_activity("Reading documents…")
    session.add_turn("telcontar", "done reading")
    session.add_activity("Planning changes…")

    await user.open(f"/run/{session.run_id}")
    await user.should_see("Planning changes…")

    # ChatMessage's content= filter matches its `name` prop (the speaker),
    # not the message body (module docstring above) — each speaker appears
    # once here, so this uniquely picks out each bubble.
    [user_bubble] = user.find(kind=ui.chat_message, content="user").elements
    [telcontar_bubble] = user.find(kind=ui.chat_message, content="telcontar").elements
    # .elements has no defined order (gotcha #7 above) — sort by NiceGUI's
    # own element id, assigned in creation order, to recover it.
    entries = sorted(user.find(marker="activity-entry").elements, key=lambda e: e.id)
    assert [e.text for e in entries] == [
        "Scanning the directory…",
        "Reading documents…",
        "Planning changes…",
    ]
    # Chronological interleave: user turn, two activity entries, telcontar
    # turn, one more activity entry — ids are assigned in creation order.
    assert user_bubble.id < entries[0].id < entries[1].id < telcontar_bubble.id < entries[2].id
