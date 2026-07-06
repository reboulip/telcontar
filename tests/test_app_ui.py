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


def _steps_text(screen) -> str:
    """Join the raw tool lines tucked into the collapsible 'internal steps' groups."""
    return "\n".join(str(w.content) for w in screen.query(".steps-log"))


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
        on_event,
        on_approval_needed,
        on_questions_needed=None,
        instructions=None,
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

    async def fake_run_agent(**kwargs: object) -> str:
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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
    async def fake_mcp_session(project_root):
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
        on_event,
        on_approval_needed,
        on_questions_needed=None,
        instructions=None,
    ):
        on_event(AgentEvent("tokens", "12.3K in / 1.0K out", data={"in": 12300, "out": 1000}))
        on_event(AgentEvent("done", "done"))
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

    app = OrganizerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(OrganizerScreen(tmp_path))
        await pilot.pause()
        await pilot.click("#proceed-btn")  # leave the L3 starter pane
        await pilot.pause()
        await pilot.pause(0.2)
        status = str(app.screen.query_one("#status-bar", Static).content)
        assert "12.3K in" in status


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
        on_event,
        on_approval_needed,
        on_questions_needed=None,
        instructions=None,
    ):
        on_event(AgentEvent("tool_call", "read_file(path='a')", data={"tool": "read_file"}))
        # Same macro-task → must collapse to one narration turn.
        on_event(AgentEvent("tool_call", "extract_text(path='b')", data={"tool": "extract_text"}))
        on_event(
            AgentEvent("tool_call", "compute_checksum(path='a')", data={"tool": "compute_checksum"})
        )
        on_event(AgentEvent("done", "done"))
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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
        *, target, settings, llm, on_event, on_approval_needed, on_questions_needed=None
    ):
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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

    async def fake_run_agent(**kwargs: object) -> str:
        started["count"] += 1
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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

    async def fake_run_agent(*, instructions=None, **kwargs: object) -> str:
        captured["instructions"] = instructions
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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

    async def fake_run_agent(*, on_event, instructions=None, **kwargs: object) -> str:
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
        return "done"

    monkeypatch.setattr("host.agent.run_agent", fake_run_agent)

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
