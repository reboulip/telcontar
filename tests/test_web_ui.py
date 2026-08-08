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

from host.agent import AgentEvent
from host.web import session as web_session
from host.web import shell as web_shell


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

    def _root_children_labels() -> set[str]:
        [tree] = user.find(kind=ui.tree).elements
        root_children = tree.props["nodes"][0].get("children", [])
        return {child.get("label") for child in root_children}

    for _ in range(20):
        if "new_file.txt" in _root_children_labels():
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(
            f"sidebar tree was not refreshed with new_file.txt; saw {_root_children_labels()}"
        )


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
