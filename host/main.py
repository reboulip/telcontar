"""MCP host entry point — launches the Textual TUI organizer app."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    try:
        return version("telcontar")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return "0.0.0+unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="telcontar",
        description="Local, profile-driven document-intelligence engine (MCP + GPT-5).",
    )
    parser.add_argument("--version", action="version", version=f"telcontar {_version()}")
    # Tolerate unrecognized args (e.g. a future/ignored --target) so bare launch
    # keeps working; --help/--version are handled here and exit before the TUI.
    parser.parse_known_args()

    # Print before the heavy imports (textual, mcp, openai...) so the user sees
    # something immediately instead of a frozen terminal during that ~1s load.
    print("Loading telcontar…", flush=True)

    from host.app import OrganizerApp

    OrganizerApp().run()


if __name__ == "__main__":
    main()
