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
)


def _rendered_lines(app: OrganizerApp) -> list[str]:
    """Flatten the current screen's compositor strips into plain-text lines."""
    return ["".join(seg.text for seg in strip) for strip in app.screen._compositor.render_strips()]


def _richlog_text(widget: RichLog) -> str:
    """Flatten a RichLog's own stored lines — avoids compositor repaint timing."""
    return "\n".join("".join(seg.text for seg in strip) for strip in widget.lines)


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
    monkeypatch.setattr("config.settings.save_user_config", lambda updates: saved.update(updates))

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


async def test_config_screen_prefills_and_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.settings.read_user_config",
        lambda: {"llm_base_url": "https://example.com/v1", "llm_model": "claude-sonnet-5"},
    )
    saved: dict = {}
    monkeypatch.setattr("config.settings.save_user_config", lambda updates: saved.update(updates))

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


async def test_organizer_screen_routes_tool_events_to_timeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.settings import Settings

    monkeypatch.setattr(
        "config.settings.load",
        lambda: Settings(llm_base_url="https://example.com", llm_api_key="k"),
    )
    monkeypatch.setattr("host.app._send_notification", lambda target: None)

    async def fake_run_agent(
        *, target, settings, llm, on_event, on_approval_needed, on_questions_needed=None
    ):
        on_event(AgentEvent("tool_call", "list_dir(path='.')"))
        on_event(AgentEvent("tool_result", "{'entries': []}"))
        on_event(AgentEvent("done", "All done."))
        return "All done."

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.pause(0.2)
        screen = app.screen
        conversation = _richlog_text(screen.query_one("#conversation-log", RichLog))
        timeline = _richlog_text(screen.query_one("#tool-timeline", RichLog))

        assert "list_dir" in timeline
        assert "All done" in conversation
        assert "list_dir" not in conversation
        assert "All done" not in timeline


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
    async def fake_mcp_session(project_root):
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
