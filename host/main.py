"""MCP host entry point — launches the Textual TUI or the NiceGUI web UI."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _version() -> str:
    try:
        return version("telcontar")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return "0.0.0+unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="telcontar",
        description="Local, profile-driven document-intelligence engine (MCP-based, LLM-agnostic).",
    )
    parser.add_argument("--version", action="version", version=f"telcontar {_version()}")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the NiceGUI web UI instead of the Textual TUI (default: the TUI).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Directory to organize. With --web, skips the landing page's directory picker. "
        "Ignored otherwise.",
    )
    # Tolerate unrecognized args so bare launch keeps working; --help/--version
    # are handled here and exit before either UI starts.
    args, _unknown = parser.parse_known_args()

    # Print before the heavy imports (textual/nicegui, mcp, openai...) so the
    # user sees something immediately instead of a frozen terminal during
    # that ~1s load.
    print("Loading telcontar…", flush=True)

    if args.web:
        # Lazy import, same discipline as the TUI import below: the other
        # UI's dependency (nicegui vs. textual) is never paid for unless that
        # UI is actually launched.
        from host.web.main import run_web

        run_web(target=args.target)
        return

    from host.app import OrganizerApp

    OrganizerApp().run()


if __name__ == "__main__":
    main()
