"""Headless Textual Pilot smoke tests for host/app.py.

These render real screens off-screen (no terminal needed) and inspect the
compositor output as plain text, so CSS/layout regressions — e.g. a Label
truncating instead of wrapping — are caught the same way a human eyeballing
the TUI would catch them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.app import JournalScreen, OrganizerApp


def _rendered_lines(app: OrganizerApp) -> list[str]:
    """Flatten the current screen's compositor strips into plain-text lines."""
    return ["".join(seg.text for seg in strip) for strip in app.screen._compositor.render_strips()]


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
