"""Headless Textual Pilot smoke tests for host/app.py.

These render real screens off-screen (no terminal needed) and inspect the
compositor output as plain text, so CSS/layout regressions — e.g. a Label
truncating instead of wrapping — are caught the same way a human eyeballing
the TUI would catch them.

Gotchas learned the hard way:
- `Label`/`Static` expose their text via `.content`, not `.renderable`.
- After a button handler calls `widget.update(...)`, scanning the screen via
  `app.screen._compositor.render_strips()` can show stale/blank text even
  after `await pilot.pause()` with a real delay — it's an unreliable way to
  assert on freshly-updated content. Query the widget's own state instead:
  `.content` for Label/Static, or `_richlog_text()` (below) for RichLog,
  which reads `RichLog.lines` directly.
- `pilot.click("#some-id")` needs the widget to actually be within the
  visible viewport for the given `run_test(size=...)` — if the panel content
  overflows, clicks can miss or land on the wrong element without raising
  `OutOfBounds`. Size the test terminal generously, and click through
  multi-step wizards in order (later steps are `display: none`, hence
  un-clickable, until earlier steps have been advanced past).
- A literal `[text]` inside a markup-enabled string (the default for
  `Label`/`Checkbox` labels) is parsed as a Textual style tag — if `text` isn't
  a recognized style name, it's silently dropped instead of rendered, e.g.
  `"foo [overwrite]"` renders as just `"foo"`. Use parentheses (`"(overwrite)"`)
  or another non-bracket delimiter for literal emphasis text, reserving `[...]`
  for real markup tags like `[dim]...[/dim]`.
- A rapid warn-then-reclick confirmation flow (assert a warning appears, click
  the same button again to confirm): `Button.press()` adds an `-active` CSS
  class for `active_effect_duration` (0.2s), and `Button._on_click` silently
  drops any click that arrives while that class is set — so a same-button
  re-click issued too soon is swallowed with no error, no exception, nothing.
  A fixed `await pilot.pause(0.1)` isn't a long enough guess (< 0.2s) and
  flaked in CI; polling only for the label update (e.g. `_wait_until`, below)
  is *worse* since it returns near-instantly, leaving less real time before
  the re-click than the naive sleep did. Poll `not btn.has_class("-active")`
  (in addition to whatever state the click was supposed to change) before
  issuing the second click.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Input, Label, RichLog

from host.agent import AgentEvent
from host.app import (
    ConfigScreen,
    JournalScreen,
    OrganizerApp,
    OrganizerScreen,
    QueryScreen,
    SetupScreen,
    StartupScreen,
)


def _rendered_lines(app: OrganizerApp) -> list[str]:
    """Flatten the current screen's compositor strips into plain-text lines."""
    return ["".join(seg.text for seg in strip) for strip in app.screen._compositor.render_strips()]


def _richlog_text(widget: RichLog) -> str:
    """Flatten a RichLog's own stored lines — avoids compositor repaint timing."""
    return "\n".join("".join(seg.text for seg in strip) for strip in widget.lines)


def _transcript_text(screen) -> str:
    """Join the speaker-turn text of the OrganizerScreen chat transcript."""
    return "\n".join(str(w.content) for w in screen.query(".turn"))


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> None:
    """Poll `predicate` after each pump of the message loop until it's true.

    Replaces a fixed `pilot.pause(delay)` for post-click assertions: a fixed
    delay is a guess at how long the handler takes to settle, and guesses that
    hold locally can still lose the race under CI load. Polling the actual
    condition removes the guess; the timeout still bounds worst-case runtime.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if predicate():
            return
    assert predicate(), f"condition not met within {timeout}s"


def _steps_text(screen) -> str:
    """Join the raw tool lines tucked into the collapsible 'internal steps' groups."""
    return "\n".join(str(w.content) for w in screen.query(".steps-log"))


def _patch_run_agent_loop(monkeypatch: pytest.MonkeyPatch, fake_run_agent_loop) -> None:
    """Patch OrganizerScreen's agent-loop entry points for a test double (O7).

    O7 restructured `OrganizerScreen._agent_worker` off the one-shot `run_agent`
    convenience call onto `mcp_session(...) + run_agent_loop(...)` directly (so the
    MCP session stays open across the initial run and any follow-up chat turns) —
    mirroring `QueryScreen._query_worker`'s pattern. Test doubles patch at this
    same seam: a no-op `mcp_session` context manager plus a `fake_run_agent_loop`
    matching `run_agent_loop`'s signature, returning `(text, history)`.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_mcp_session(project_root, target=None):
        yield object()

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)
    monkeypatch.setattr("host.agent.run_agent_loop", fake_run_agent_loop)


async def test_setup_wizard_welcome_step_wraps_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.settings.is_configured", lambda: False)
    app = OrganizerApp()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        text = " ".join(_rendered_lines(app))
        assert "AI service" in text
        assert "of steps." in text


async def test_journal_screen_lists_full_entry_detail(tmp_path: Path) -> None:
    journal_dir = tmp_path / ".organizer"
    journal_dir.mkdir()
    entry = {
        "op_type": "rename",
        "plan_id": "p1",
        "op_id": "o1",
        "src": "report_final_v2.docx",
        "dst": "2024-01-15_report.docx",
        "timestamp": "2026-07-01T10:00:00Z",
    }
    (journal_dir / "journal.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    app = OrganizerApp()
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        app.push_screen(JournalScreen(tmp_path))
        await pilot.pause()
        text = " ".join(_rendered_lines(app))
        assert "report_final_v2.docx" in text
        assert "2024-01-15_report.docx" in text


async def test_journal_screen_empty_state(tmp_path: Path) -> None:
    app = OrganizerApp()
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        app.push_screen(JournalScreen(tmp_path))
        await pilot.pause()
        text = " ".join(_rendered_lines(app))
        assert "No operations recorded yet." in text


async def test_journal_screen_undo_action_reverses_last_op(tmp_path: Path) -> None:
    """S1: undo is only reachable as an explicit user action from this screen."""
    original = tmp_path / "old.txt"
    (tmp_path / "new.txt").write_text("x")
    journal_dir = tmp_path / ".organizer"
    journal_dir.mkdir()
    entry = {
        "op_type": "rename",
        "plan_id": "p1",
        "op_id": "o1",
        "src": str(original),
        "dst": "new.txt",
        "timestamp": "2026-07-01T10:00:00Z",
    }
    (journal_dir / "journal.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    app = OrganizerApp()
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        app.push_screen(JournalScreen(tmp_path))
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()

    assert original.exists()
    assert not (tmp_path / "new.txt").exists()


async def test_setup_wizard_mammouth_sets_model_hint_and_placeholder() -> None:
    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(SetupScreen())
        await pilot.pause()
        await pilot.click("#btn-welcome-next")
        await pilot.pause()
        await pilot.click("#btn-svc-mammouth")
        await pilot.pause()
        screen = app.screen
        assert "Mammouth" in str(screen.query_one("#model-hint").content)
        assert screen.query_one("#input-model", Input).placeholder == "e.g. gpt-5"


async def test_setup_wizard_blocks_on_empty_model() -> None:
    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(SetupScreen())
        await pilot.pause()
        await pilot.click("#btn-welcome-next")
        await pilot.pause()
        await pilot.click("#btn-svc-other")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#input-url", Input).value = "https://example.com/v1"
        screen.query_one("#input-key", Input).value = "sk-test"
        await pilot.click("#btn-api-next")
        await pilot.pause()
        assert screen.query_one("#api-error", Label).content == "Please enter the model name."
        # Still on the API step — did not advance to profile selection.
        assert screen.query_one("#step-api").display is True


async def test_setup_wizard_saves_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict = {}
    monkeypatch.setattr(
        "config.settings.save_user_config",
        lambda updates, allow_plaintext_fallback=False: saved.update(updates),
    )

    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(SetupScreen())
        await pilot.pause()
        await pilot.click("#btn-welcome-next")
        await pilot.pause()
        await pilot.click("#btn-svc-other")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#input-url", Input).value = "https://example.com/v1"
        screen.query_one("#input-key", Input).value = "sk-test"
        screen.query_one("#input-model", Input).value = "gpt-4o"
        await pilot.click("#btn-api-next")
        await pilot.pause()
        await pilot.click("#btn-profile-next")
        await pilot.pause()

    assert saved.get("llm_model") == "gpt-4o"


async def test_setup_wizard_warns_before_plaintext_key_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S8: keyring-unavailable never silently falls back to plaintext."""
    from config.settings import PlaintextKeyFallbackNeeded

    saved: dict = {}

    def fake_save(updates: dict, allow_plaintext_fallback: bool = False) -> None:
        if not allow_plaintext_fallback:
            raise PlaintextKeyFallbackNeeded("keyring unavailable")
        saved.update(updates)

    monkeypatch.setattr("config.settings.save_user_config", fake_save)

    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(SetupScreen())
        await pilot.pause()
        await pilot.click("#btn-welcome-next")
        await pilot.pause()
        await pilot.click("#btn-svc-other")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#input-url", Input).value = "https://example.com/v1"
        screen.query_one("#input-key", Input).value = "sk-test"
        screen.query_one("#input-model", Input).value = "gpt-4o"
        await pilot.click("#btn-api-next")
        await pilot.pause()

        # First attempt: warned, not saved yet.
        btn = screen.query_one("#btn-profile-next")
        await pilot.click(btn)
        await _wait_until(
            pilot,
            lambda: (
                "keyring" in str(screen.query_one("#profile-error", Label).content).lower()
                # Button.press() holds an `-active` class for `active_effect_duration`
                # (0.2s) and silently drops clicks while it's set — wait it out before
                # re-clicking, or the confirmation click gets swallowed.
                and not btn.has_class("-active")
            ),
        )
        assert not saved
        assert screen.query_one("#step-profile").display is True  # still on this step

        # Second click (explicit confirmation): now allowed to proceed.
        await pilot.click(btn)
        await _wait_until(pilot, lambda: bool(saved))

    assert saved.get("llm_api_key") == "sk-test"


async def test_config_screen_prefills_and_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.settings.read_user_config",
        lambda: {"llm_base_url": "https://example.com/v1", "llm_model": "claude-sonnet-5"},
    )
    saved: dict = {}
    monkeypatch.setattr(
        "config.settings.save_user_config",
        lambda updates, allow_plaintext_fallback=False: saved.update(updates),
    )

    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(ConfigScreen())
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#cfg-model", Input).value == "claude-sonnet-5"

        screen.query_one("#cfg-model", Input).value = ""
        await pilot.click("#btn-cfg-save")
        await pilot.pause()
        assert screen.query_one("#cfg-error", Label).content == "Please enter the model name."
        assert not saved  # blocked before save_user_config was ever called


async def test_config_screen_warns_before_plaintext_key_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S8: keyring-unavailable never silently falls back to plaintext."""
    from config.settings import PlaintextKeyFallbackNeeded

    monkeypatch.setattr(
        "config.settings.read_user_config",
        lambda: {"llm_base_url": "https://example.com/v1", "llm_model": "claude-sonnet-5"},
    )
    saved: dict = {}

    def fake_save(updates: dict, allow_plaintext_fallback: bool = False) -> None:
        if not allow_plaintext_fallback:
            raise PlaintextKeyFallbackNeeded("keyring unavailable")
        saved.update(updates)

    monkeypatch.setattr("config.settings.save_user_config", fake_save)

    app = OrganizerApp()
    async with app.run_test(size=(90, 50)) as pilot:
        app.push_screen(ConfigScreen())
        await pilot.pause()
        screen = app.screen
        screen.query_one("#cfg-key", Input).value = "sk-new"

        btn = screen.query_one("#btn-cfg-save")
        await pilot.click(btn)
        await _wait_until(
            pilot,
            lambda: (
                "keyring" in str(screen.query_one("#cfg-error", Label).content).lower()
                and not btn.has_class("-active")
            ),
        )
        assert not saved

        await pilot.click(btn)
        await _wait_until(pilot, lambda: bool(saved))

    assert saved.get("llm_api_key") == "sk-new"


async def test_organizer_screen_groups_tool_events_into_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L2: raw tool calls go into the collapsible internal-steps group; the
    plain-language 'done' turn lands in the speaker transcript."""
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(AgentEvent("tool_call", "list_dir(path='.')"))
        on_event(AgentEvent("tool_result", "{'entries': []}"))
        on_event(AgentEvent("done", "All done."))
        return "All done.", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the L3 starter pane
        await pilot.pause()
        await pilot.pause(0.2)
        screen = app.screen
        transcript = _transcript_text(screen)
        steps = _steps_text(screen)

        # Raw tool activity is tucked into the collapsible internal-steps group…
        assert "list_dir" in steps
        # …while the plain-language speaker turns carry the narrative.
        assert "All done" in transcript
        assert "list_dir" not in transcript
        assert "All done" not in steps


# ── F5: quit bindings actually terminate the app ──────────────────────────────


async def test_organizer_screen_q_shortcut_quits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("config.settings.is_configured", lambda: True)
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(**kwargs: object) -> tuple[str, list]:
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the starter pane so 'q' isn't typed
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        # The screen binding routes to app.quit, so the app actually exits.
        assert app._exit is True


async def test_startup_screen_escape_quits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("config.settings.is_configured", lambda: True)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(StartupScreen())
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app._exit is True


# ── P2: per-directory memory — Query mode target resolution ───────────────────


def test_find_organizer_root_finds_organizer_at_start(tmp_path: Path) -> None:
    from host.app import _find_organizer_root

    (tmp_path / ".organizer").mkdir()

    assert _find_organizer_root(tmp_path) == tmp_path.resolve()


def test_find_organizer_root_walks_up_to_parent(tmp_path: Path) -> None:
    from host.app import _find_organizer_root

    (tmp_path / ".organizer").mkdir()
    sub = tmp_path / "docs" / "2024"
    sub.mkdir(parents=True)

    assert _find_organizer_root(sub) == tmp_path.resolve()


def test_find_organizer_root_returns_none_when_absent(tmp_path: Path) -> None:
    from host.app import _find_organizer_root

    sub = tmp_path / "docs"
    sub.mkdir()

    assert _find_organizer_root(sub) is None


async def test_query_button_shows_error_when_no_organizer_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("config.settings.is_configured", lambda: True)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(StartupScreen())
        await pilot.pause()
        screen = app.screen
        screen._on_dir_selected(SimpleNamespace(path=tmp_path))
        await pilot.click("#query-btn")
        await pilot.pause()
        assert isinstance(app.screen, StartupScreen)
        assert "No analyzed corpus found" in str(screen.query_one("#error-label", Label).content)


async def test_query_button_resolves_organizer_root_from_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("config.settings.is_configured", lambda: True)

    (tmp_path / ".organizer").mkdir()
    sub = tmp_path / "docs"
    sub.mkdir()

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(StartupScreen())
        await pilot.pause()
        screen = app.screen
        # Select a subfolder of the previously-organized tree — the ancestor's
        # `.organizer` should still be found and used as the query target.
        screen._on_dir_selected(SimpleNamespace(path=sub))
        await pilot.click("#query-btn")
        await pilot.pause()
        assert isinstance(app.screen, QueryScreen)
        assert app.screen._target == tmp_path.resolve()


async def test_query_screen_routes_tool_events_to_timeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from contextlib import asynccontextmanager

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    @asynccontextmanager
    async def fake_mcp_session(project_root, target=None):
        yield None

    async def fake_run_query_loop(
        *, question, settings, llm, session, on_event, history, project_root
    ):
        on_event(AgentEvent("tool_call", "list_documents()"))
        on_event(AgentEvent("tool_result", "[]"))
        return "Here is your answer.", history or []

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)
    monkeypatch.setattr("host.agent.run_query_loop", fake_run_query_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(QueryScreen(tmp_path))
        await pilot.pause()
        await pilot.pause(0.2)
        screen = app.screen
        screen.query_one("#query-input", Input).value = "What's in here?"
        await pilot.press("enter")
        await pilot.pause(0.2)

        conversation = _richlog_text(screen.query_one("#query-log", RichLog))
        timeline = _richlog_text(screen.query_one("#query-timeline", RichLog))

        assert "list_documents" in timeline
        assert "Here is your answer." in conversation
        assert "list_documents" not in conversation
        assert "Here is your answer." not in timeline


# ── F6: query-screen log helpers survive the screen being popped ──────────────


async def test_query_screen_log_helpers_safe_after_pop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A background query worker may call _log after the user pops the screen (#9)."""
    from contextlib import asynccontextmanager

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    @asynccontextmanager
    async def fake_mcp_session(project_root, target=None):
        yield None

    async def fake_run_query_loop(**kwargs):
        return "answer", []

    monkeypatch.setattr("host.agent.mcp_session", fake_mcp_session)
    monkeypatch.setattr("host.agent.run_query_loop", fake_run_query_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(QueryScreen(tmp_path))
        await pilot.pause()
        screen = app.screen
        app.pop_screen()
        await pilot.pause()
        # Widgets are gone now; the helpers must no-op, not raise NoMatches.
        screen._log("late line")
        screen._log_tool("late tool")
        screen._set_status("late status")


# ── F8: approval modal shows the agent's plan rationale ───────────────────────


async def test_approval_modal_shows_rationale(tmp_path: Path) -> None:
    from textual.widgets import Static

    from host.app import ApprovalModal

    plan_data = {
        "ops": [{"op_type": "move", "src": "/a/b.pdf", "dst": "/sorted", "op_id": "o1"}],
        "rationale": "Grouped COPIL decks as a dated series; drafts quarantined.",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        rationale = app.screen.query_one("#plan-rationale", Static)
        assert "COPIL decks" in str(rationale.content)


async def test_approval_modal_without_rationale_has_no_rationale_widget(tmp_path: Path) -> None:
    from textual.css.query import NoMatches

    from host.app import ApprovalModal

    plan_data = {"ops": [], "rationale": ""}
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#plan-rationale")


# ── M5: rationale/folder-notes labeled as untrusted, model-generated text ────


async def test_approval_modal_rationale_has_disclaimer(tmp_path: Path) -> None:
    from textual.widgets import Label

    from host.app import ApprovalModal

    plan_data = {
        "ops": [{"op_type": "move", "src": "/a/b.pdf", "dst": "/sorted", "op_id": "o1"}],
        "rationale": "Grouped COPIL decks as a dated series; drafts quarantined.",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        disclaimer = app.screen.query_one("#rationale-disclaimer", Label)
        assert "model-generated" in str(disclaimer.content).lower()
        assert "not verified fact" in str(disclaimer.content).lower()


async def test_approval_modal_without_rationale_has_no_disclaimer(tmp_path: Path) -> None:
    from textual.css.query import NoMatches

    from host.app import ApprovalModal

    plan_data = {"ops": [], "rationale": ""}
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#rationale-disclaimer")


async def test_approval_modal_folder_notes_have_disclaimer(tmp_path: Path) -> None:
    from textual.widgets import Label

    from host.app import ApprovalModal

    plan_data = {
        "ops": [{"op_type": "move", "src": "/in/a.pdf", "dst": "/t/01_decisions", "op_id": "o1"}],
        "rationale": "",
        "folder_notes": {"01_decisions": "Formal decision records"},
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        disclaimer = app.screen.query_one("#layout-notes-disclaimer", Label)
        assert "model-generated" in str(disclaimer.content).lower()


async def test_approval_modal_without_folder_notes_has_no_disclaimer(tmp_path: Path) -> None:
    from textual.css.query import NoMatches

    from host.app import ApprovalModal

    # A target layout with no folder notes at all — bare tree, no LLM commentary.
    plan_data = {
        "ops": [{"op_type": "move", "src": "/in/a.pdf", "dst": "/t/01_decisions", "op_id": "o1"}],
        "rationale": "",
        "folder_notes": {},
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#layout-notes-disclaimer")


# ── M3: update_file collision-safety surfaced in the approval modal ──────────


def test_fmt_op_update_file_without_overwrite_has_no_flag() -> None:
    from host.app import _fmt_op

    op = {"op_type": "update_file", "src": "/a/notes.md", "dst": "", "params": {"content": "x"}}
    assert _fmt_op(op) == "UPDATE   notes.md"


def test_fmt_op_update_file_with_overwrite_shows_subtle_flag() -> None:
    from host.app import _fmt_op

    op = {
        "op_type": "update_file",
        "src": "/a/notes.md",
        "dst": "",
        "params": {"content": "x", "overwrite": True},
    }
    formatted = _fmt_op(op)
    assert "notes.md" in formatted
    assert "overwrite" in formatted


async def test_approval_modal_shows_update_file_overwrite_flag(tmp_path: Path) -> None:
    from textual.widgets import Checkbox

    from host.app import ApprovalModal

    plan_data = {
        "ops": [
            {
                "op_type": "update_file",
                "src": "/a/notes.md",
                "dst": "",
                "op_id": "o1",
                "params": {"content": "x", "overwrite": True},
            }
        ],
        "rationale": "",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        checkbox = app.screen.query_one(Checkbox)
        assert "overwrite" in str(checkbox.label)


# ── M4: discreet out-of-scope indicator in the approval modal ────────────────


def test_fmt_op_in_scope_has_no_indicator(tmp_path: Path) -> None:
    from host.app import _fmt_op

    op = {"op_type": "move", "src": str(tmp_path / "doc.pdf"), "dst": "/sorted", "op_id": "o1"}
    assert _fmt_op(op, tmp_path) == "MOVE     doc.pdf  →  /sorted"


def test_fmt_op_out_of_scope_shows_subtle_indicator(tmp_path: Path) -> None:
    from host.app import _fmt_op

    target = tmp_path / "target"
    outside = tmp_path / "elsewhere" / "secret.env"
    op = {"op_type": "rename", "src": str(outside), "dst": "renamed.env", "op_id": "o1"}
    formatted = _fmt_op(op, target)
    assert "secret.env" in formatted
    assert "outside target" in formatted


def test_fmt_op_no_target_has_no_indicator() -> None:
    from host.app import _fmt_op

    op = {"op_type": "rename", "src": "/anywhere/doc.pdf", "dst": "new.pdf", "op_id": "o1"}
    assert "outside target" not in _fmt_op(op, None)


async def test_approval_modal_flags_out_of_scope_op(tmp_path: Path) -> None:
    from textual.widgets import Checkbox

    from host.app import ApprovalModal

    target = tmp_path / "target"
    target.mkdir()
    plan_data = {
        "ops": [
            {
                "op_type": "rename",
                "src": str(tmp_path / "elsewhere" / "secret.env"),
                "dst": "renamed.env",
                "op_id": "o1",
            }
        ],
        "rationale": "",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data, target))
        await pilot.pause()
        checkbox = app.screen.query_one(Checkbox)
        assert "outside target" in str(checkbox.label)


async def test_approval_modal_does_not_flag_in_scope_op(tmp_path: Path) -> None:
    from textual.widgets import Checkbox

    from host.app import ApprovalModal

    target = tmp_path / "target"
    target.mkdir()
    plan_data = {
        "ops": [{"op_type": "rename", "src": str(target / "a.pdf"), "dst": "b.pdf", "op_id": "o1"}],
        "rationale": "",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data, target))
        await pilot.pause()
        checkbox = app.screen.query_one(Checkbox)
        assert "outside target" not in str(checkbox.label)


# ── L5: plan target-layout preview ────────────────────────────────────────────


def test_render_target_layout_builds_tree_with_notes() -> None:
    from host.app import _render_target_layout

    ops = [
        {"op_type": "move", "src": "/in/a.pdf", "dst": "/t/01_decisions", "op_id": "o1"},
        {"op_type": "move", "src": "/in/b.pdf", "dst": "/t/02_copil", "op_id": "o2"},
        {"op_type": "quarantine", "src": "/in/c.pdf", "dst": "/t/_quarantine/c.pdf", "op_id": "o3"},
    ]
    notes = {"01_decisions": "Formal decision records", "_quarantine": "Duplicates"}
    text = "\n".join(_render_target_layout(ops, notes))
    assert "01_decisions/" in text
    assert "Formal decision records" in text
    assert "_quarantine/" in text
    assert "Duplicates" in text
    # A target folder without a note still appears in the tree (bare node).
    assert "02_copil/" in text


def test_render_target_layout_empty_without_folder_ops() -> None:
    from host.app import _render_target_layout

    # Rename-only plans move nothing into a folder, so there's no target tree.
    ops = [{"op_type": "rename", "src": "/in/a.pdf", "dst": "a_clean.pdf", "op_id": "o1"}]
    assert _render_target_layout(ops, {}) == []


async def test_approval_modal_shows_target_layout(tmp_path: Path) -> None:
    from textual.widgets import Static

    from host.app import ApprovalModal

    plan_data = {
        "ops": [
            {"op_type": "move", "src": "/in/a.pdf", "dst": "/t/01_decisions", "op_id": "o1"},
            {
                "op_type": "quarantine",
                "src": "/in/c.pdf",
                "dst": "/t/_quarantine/c.pdf",
                "op_id": "o2",
            },
        ],
        "rationale": "",
        "folder_notes": {"01_decisions": "Formal decision records", "_quarantine": "Duplicates"},
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        layout = str(app.screen.query_one("#target-layout", Static).content)
        assert "01_decisions" in layout
        assert "Formal decision records" in layout
        assert "_quarantine" in layout


async def test_approval_modal_without_folder_ops_has_no_target_layout(tmp_path: Path) -> None:
    from textual.css.query import NoMatches

    from host.app import ApprovalModal

    plan_data = {
        "ops": [{"op_type": "rename", "src": "/in/a.pdf", "dst": "a_clean.pdf", "op_id": "o1"}],
        "rationale": "",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#target-layout")


# ── L6: natural-language plan editing ─────────────────────────────────────────


async def test_approval_modal_refine_returns_refinement(tmp_path: Path) -> None:
    from host.app import ApprovalModal

    plan_data = {
        "ops": [{"op_type": "move", "src": "/a/b.pdf", "dst": "/sorted", "op_id": "o1"}],
        "rationale": "",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        captured: dict = {}
        app.push_screen(ApprovalModal("abc12345", plan_data), lambda res: captured.update(res=res))
        await pilot.pause()
        app.screen.query_one("#refine-input", Input).value = "merge the drafts into one folder"
        await pilot.click("#refine-btn")
        await pilot.pause()
        res = captured["res"]
        assert res.approved is False
        assert res.refinement == "merge the drafts into one folder"


async def test_approval_modal_blank_refine_is_noop(tmp_path: Path) -> None:
    from host.app import ApprovalModal

    plan_data = {"ops": [], "rationale": ""}
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        # Refine with an empty field must not dismiss the modal.
        await pilot.click("#refine-btn")
        await pilot.pause()
        assert isinstance(app.screen, ApprovalModal)


async def test_approval_modal_shows_ops_json_path(tmp_path: Path) -> None:
    from textual.widgets import Label

    from host.app import ApprovalModal

    plan_data = {
        "ops": [],
        "rationale": "",
        "ops_json_path": "/x/.organizer/plan_ops.json",
    }
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ApprovalModal("abc12345", plan_data))
        await pilot.pause()
        label = str(app.screen.query_one("#ops-json-path", Label).content)
        assert "plan_ops.json" in label


# ── P8: ask_user chat checkpoint ────────────────────────────────────────────


async def test_ask_user_renders_question_and_resolves_from_chat_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P8: ask_user has no modal — it renders as a transcript turn and blocks
    on the same live-chat queue #organize-input already feeds (P7)."""
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    captured: dict = {}

    async def fake_run_agent_loop(*, on_ask_user_needed=None, **kwargs: object) -> tuple[str, list]:
        result = await on_ask_user_needed(
            [{"text": "Group by?", "options": ["by date", "by workstream"]}]
        )
        captured["result"] = result
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause(0.2)

        transcript = _transcript_text(app.screen)
        assert "I have a question for you" in transcript
        assert "Group by?" in transcript

        organize_input = app.screen.query_one("#organize-input", Input)
        organize_input.focus()
        await pilot.pause()
        organize_input.value = "by workstream"
        await pilot.press("enter")
        await pilot.pause(0.2)

    assert captured["result"].reply == "by workstream"
    assert captured["result"].provided is True


# ── O8: pre-ANALYZE cost-estimate modal ───────────────────────────────────────


async def test_cost_estimate_modal_shows_summary(tmp_path: Path) -> None:
    from textual.widgets import Static

    from host.app import CostEstimateModal

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(
            CostEstimateModal(new_documents=42, already_analyzed=7, estimated_tokens=12345)
        )
        await pilot.pause()
        summary = app.screen.query_one("#cost-summary", Static)
        assert "42 new document(s)" in str(summary.content)
        assert "7 already analyzed, skipped" in str(summary.content)
        assert "12345 input tokens" in str(summary.content)
        assert "groups of 10" in str(summary.content)


async def test_cost_estimate_modal_proceed_approves(tmp_path: Path) -> None:
    from host.app import CostEstimateModal

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        captured: dict = {}
        app.push_screen(
            CostEstimateModal(new_documents=1, already_analyzed=0, estimated_tokens=100),
            lambda res: captured.update(res=res),
        )
        await pilot.pause()
        await pilot.click("#cost-proceed-btn")
        await pilot.pause()
        assert captured["res"].approved is True


async def test_cost_estimate_modal_cancel_rejects(tmp_path: Path) -> None:
    from host.app import CostEstimateModal

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        captured: dict = {}
        app.push_screen(
            CostEstimateModal(new_documents=1, already_analyzed=0, estimated_tokens=100),
            lambda res: captured.update(res=res),
        )
        await pilot.pause()
        await pilot.click("#cost-cancel-btn")
        await pilot.pause()
        assert captured["res"].approved is False


# ── F9: the status bar surfaces running token usage ──────────────────────────


async def test_organizer_status_bar_shows_token_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from textual.widgets import Static

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(AgentEvent("tokens", "12.3K in / 1.0K out", data={"in": 12300, "out": 1000}))
        on_event(AgentEvent("done", "done"))
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the L3 starter pane
        await pilot.pause()
        await pilot.pause(0.2)
        status = str(app.screen.query_one("#status-bar", Static).content)
        assert "12.3K in" in status


# ── O6: ANALYZE progress bar ──────────────────────────────────────────────────


async def test_organizer_progress_row_hidden_until_first_progress_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        assert app.screen.query_one("#progress-row").display is False
        await pilot.click("#proceed-btn")
        await pilot.pause()
        assert app.screen.query_one("#progress-row").display is False


async def test_organizer_progress_row_shows_and_updates_on_progress_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from textual.widgets import Label

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(
            AgentEvent("progress", "Analyzed 3 / 10 documents", data={"analyzed": 3, "total": 10})
        )
        return "in progress", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert app.screen.query_one("#progress-row").display is True
        label = str(app.screen.query_one("#progress-label", Label).content)
        assert "3 / 10 documents" in label


async def test_organizer_progress_row_hides_on_done(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(
            AgentEvent("progress", "Analyzed 3 / 10 documents", data={"analyzed": 3, "total": 10})
        )
        on_event(AgentEvent("done", "done"))
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert app.screen.query_one("#progress-row").display is False


async def test_organizer_progress_row_hides_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(
            AgentEvent("progress", "Analyzed 3 / 10 documents", data={"analyzed": 3, "total": 10})
        )
        on_event(AgentEvent("error", "Reached maximum turns (50); stopping."))
        return "Stopped: maximum turns (50) reached.", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert app.screen.query_one("#progress-row").display is False


# ── F10: macro-task narration in the conversation pane ───────────────────────


async def test_organizer_narrates_macro_tasks_in_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        on_event(AgentEvent("tool_call", "read_file(path='a')", data={"tool": "read_file"}))
        # Same macro-task → must collapse to one narration turn.
        on_event(AgentEvent("tool_call", "extract_text(path='b')", data={"tool": "extract_text"}))
        on_event(
            AgentEvent("tool_call", "compute_checksum(path='a')", data={"tool": "compute_checksum"})
        )
        on_event(AgentEvent("done", "done"))
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the L3 starter pane
        await pilot.pause()
        await pilot.pause(0.2)
        transcript = _transcript_text(app.screen)
        steps = _steps_text(app.screen)

        # Plain-language narration lands as telcontar turns in the transcript…
        assert "Reading documents" in transcript
        assert "Computing checksums" in transcript
        # …and consecutive same-task calls collapse to a single turn.
        assert transcript.count("Reading documents") == 1
        # Raw tool names stay in the internal-steps group, not the speaker turns.
        assert "read_file" in steps
        assert "read_file" not in transcript


# ── M6: M1's new propose_* plan-flow tools are narrated, not silent ──────────


async def test_organizer_narrates_new_propose_tools_as_planning_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S4/S7: staging a create/update/archive/compress op into a plan must be
    visible narration (M1 gated these behind propose_* tools instead of the
    ungated direct tools they replaced) — not a silent internal step."""
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    new_propose_tools = [
        "propose_create_file",
        "propose_update_file",
        "propose_create_dir",
        "propose_archive_document",
        "propose_compress_quarantine",
    ]

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        session=None,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        on_cost_approval_needed=None,
        project_root=None,
        instructions=None,
        history=None,
        message=None,
        message_queue=None,
    ):
        for tool in new_propose_tools:
            on_event(AgentEvent("tool_call", f"{tool}(...)", data={"tool": tool}))
        on_event(AgentEvent("done", "done"))
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the L3 starter pane
        await pilot.pause()
        await pilot.pause(0.2)
        transcript = _transcript_text(app.screen)
        steps = _steps_text(app.screen)

        # All 5 new propose_* tools narrate as visible "Planning changes…" turns…
        assert "Planning changes" in transcript
        # …while the raw tool names stay tucked into internal steps, not the
        # speaker turns themselves (same pattern as the pre-existing tools).
        for tool in new_propose_tools:
            assert tool in steps
            assert tool not in transcript


# ── F11: folder-browsing directory picker on the startup screen ──────────────


async def test_startup_screen_has_directory_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.css.query import NoMatches
    from textual.widgets import DirectoryTree

    monkeypatch.setattr("config.settings.is_configured", lambda: True)
    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(StartupScreen())
        await pilot.pause()
        screen = app.screen
        # The raw path input is gone; a folder-browsing tree replaces it.
        assert screen.query_one("#target-tree", DirectoryTree)
        with pytest.raises(NoMatches):
            screen.query_one("#target-input")
        assert "Selected:" in str(screen.query_one("#selected-label", Label).content)


async def test_startup_picker_selection_drives_organize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("config.settings.is_configured", lambda: True)
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *,
        target,
        settings,
        llm,
        on_event,
        on_approval_needed,
        on_ask_user_needed=None,
        **kwargs: object,
    ):
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(StartupScreen())
        await pilot.pause()
        screen = app.screen
        # Selecting a folder in the tree updates the target and the label.
        screen._on_dir_selected(SimpleNamespace(path=tmp_path))
        assert screen._selected == tmp_path
        assert str(tmp_path) in str(screen.query_one("#selected-label", Label).content)
        # Organize launches the agent on the picked folder.
        await pilot.click("#organize-btn")
        await pilot.pause()
        assert isinstance(app.screen, OrganizerScreen)
        assert app.screen._target == tmp_path


# ── L3: prior-instructions conversation starter ───────────────────────────────


async def test_organizer_starter_shows_directory_overview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from textual.widgets import Static

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )

    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"y")
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "sub").mkdir()

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        overview = str(app.screen.query_one("#dir-overview", Static).content)
        # Code-generated, deterministic: counts + file-type breakdown, no LLM.
        assert "3 file(s)" in overview
        assert "1 subfolder(s)" in overview
        assert ".pdf" in overview
        # The transcript stays hidden until the user chooses to proceed.
        assert app.screen.query_one("#main-split").display is False


async def test_organizer_proceed_reveals_transcript_and_starts_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    started = {"count": 0}

    async def fake_run_agent(**kwargs: object) -> tuple[str, list]:
        started["count"] += 1
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        assert app.screen.query_one("#starter-pane").display is True
        assert app.screen.query_one("#main-split").display is False
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.2)
        # Starter pane hidden, transcript shown, agent worker started exactly once.
        assert app.screen.query_one("#starter-pane").display is False
        assert app.screen.query_one("#main-split").display is True
        assert started["count"] == 1


async def test_organizer_passes_steering_instructions_to_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    captured: dict = {}

    async def fake_run_agent(*, instructions=None, **kwargs: object) -> tuple[str, list]:
        captured["instructions"] = instructions
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        app.screen.query_one("#instructions-input", Input).value = "group by workstream"
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.2)
        assert captured["instructions"] == "group by workstream"
        # The typed instructions also surface as a user turn in the transcript.
        assert "group by workstream" in _transcript_text(app.screen)


# ── L4: operations journal at the bottom ──────────────────────────────────────


async def test_ops_journal_shows_existing_entries_on_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    journal_path = tmp_path / "journal.jsonl"
    journal_path.write_text(
        json.dumps(
            {
                "op_type": "rename",
                "src": "old_name_v2.docx",
                "dst": "2024-01-15_report.docx",
                "timestamp": "2026-07-01T10:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("host.app._resolve_journal_path", lambda root: journal_path)

    app = OrganizerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        journal_log = _richlog_text(app.screen.query_one("#ops-journal", RichLog))
        assert "old_name_v2.docx" in journal_log
        assert "2024-01-15_report.docx" in journal_log


async def test_ops_journal_empty_then_updates_as_operations_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    journal_path = tmp_path / "journal.jsonl"  # does not exist yet
    monkeypatch.setattr("host.app._resolve_journal_path", lambda root: journal_path)

    async def fake_run_agent(*, on_event, instructions=None, **kwargs: object) -> tuple[str, list]:
        # A move operation lands in the undo journal mid-run…
        journal_path.write_text(
            json.dumps(
                {
                    "op_type": "move",
                    "src": "report.pdf",
                    "dst": "/sorted",
                    "timestamp": "2026-07-06T10:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        on_event(
            AgentEvent("tool_call", "execute_plan(plan_id='p1')", data={"tool": "execute_plan"})
        )
        on_event(AgentEvent("tool_result", "{'ops_completed': 1}"))
        on_event(AgentEvent("done", "done"))
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        # Nothing recorded yet at mount.
        assert "No operations yet." in _richlog_text(app.screen.query_one("#ops-journal", RichLog))
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.2)
        # After the execute_plan result the bottom journal reflects the new op.
        journal_log = _richlog_text(app.screen.query_one("#ops-journal", RichLog))
        assert "report.pdf" in journal_log
        assert "move" in journal_log


# ── O7: resumable chat after a stop ───────────────────────────────────────────


async def test_organize_input_enabled_from_run_start_not_just_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P7: chat is live for the whole run, not just after it stops — the input
    must already be enabled while the agent is still working, not only once
    run_agent_loop returns."""
    import asyncio

    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_agent_loop(**kwargs: object) -> tuple[str, list]:
        started.set()
        await release.wait()
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        assert app.screen.query_one("#organize-input", Input).disabled is True
        await pilot.click("#proceed-btn")
        await asyncio.wait_for(started.wait(), timeout=2)
        await pilot.pause()
        # Still mid-run (the fake hasn't returned yet) — input is already live.
        assert app.screen.query_one("#organize-input", Input).disabled is False
        release.set()
        await pilot.pause()
        await pilot.pause(0.1)
        assert app.screen.query_one("#organize-input", Input).disabled is False


async def test_organize_input_submit_continues_the_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    calls: list[dict] = []

    async def fake_run_agent_loop(
        *, on_event, history=None, message=None, **kwargs: object
    ) -> tuple[str, list]:
        calls.append({"history": history, "message": message})
        if history is None:
            on_event(AgentEvent("done", "Initial done."))
            return "Initial done.", [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]
        on_event(AgentEvent("done", "Follow-up done."))
        return "Follow-up done.", [
            *history,
            {"role": "user", "content": message},
            {"role": "assistant"},
        ]

    _patch_run_agent_loop(monkeypatch, fake_run_agent_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert len(calls) == 1
        assert calls[0]["history"] is None

        organize_input = app.screen.query_one("#organize-input", Input)
        organize_input.focus()
        await pilot.pause()
        organize_input.value = "also quarantine the drafts"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause(0.1)

        assert len(calls) == 2
        assert calls[1]["message"] == "also quarantine the drafts"
        assert calls[1]["history"] is not None
        transcript = _transcript_text(app.screen)
        assert "also quarantine the drafts" in transcript
        assert "Follow-up done." in transcript
        # Input clears after submit and re-enables once the follow-up turn finishes.
        assert organize_input.value == ""
        assert organize_input.disabled is False


async def test_organize_input_blank_submit_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    calls: list[dict] = []

    async def fake_run_agent_loop(
        *, history=None, message=None, **kwargs: object
    ) -> tuple[str, list]:
        calls.append({"history": history, "message": message})
        return "done", []

    _patch_run_agent_loop(monkeypatch, fake_run_agent_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert len(calls) == 1

        organize_input = app.screen.query_one("#organize-input", Input)
        organize_input.value = "   "
        await pilot.press("enter")
        await pilot.pause()

        # A blank submit never queues a second call.
        assert len(calls) == 1


async def test_organizer_notification_and_g_keybinding_fire_once_across_continuations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    notifications: list[Path] = []
    monkeypatch.setattr("host.app._send_notification", lambda target: notifications.append(target))

    async def fake_run_agent_loop(
        *, on_event, history=None, message=None, **kwargs: object
    ) -> tuple[str, list]:
        if history is None:
            on_event(AgentEvent("done", "Initial done."))
            return "Initial done.", [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]
        on_event(AgentEvent("done", "Follow-up done."))
        return "Follow-up done.", [
            *history,
            {"role": "user", "content": message},
            {"role": "assistant"},
        ]

    _patch_run_agent_loop(monkeypatch, fake_run_agent_loop)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")
        await pilot.pause()
        await pilot.pause(0.1)
        assert len(notifications) == 1

        organize_input = app.screen.query_one("#organize-input", Input)
        organize_input.focus()
        await pilot.pause()
        organize_input.value = "one more thing"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause(0.1)

        # The follow-up turn's own "done" event must not repeat the notification.
        assert len(notifications) == 1
