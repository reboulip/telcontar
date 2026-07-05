"""MCP host entry point — launches the Textual TUI organizer app."""

from __future__ import annotations


def main() -> None:
    # Print before the heavy imports (textual, mcp, openai...) so the user sees
    # something immediately instead of a frozen terminal during that ~1s load.
    print("Loading telcontar…", flush=True)

    from host.app import OrganizerApp

    OrganizerApp().run()
