"""MCP host entry point — launches the Textual TUI organizer app."""
from __future__ import annotations

from host.app import OrganizerApp


def main() -> None:
    OrganizerApp().run()
