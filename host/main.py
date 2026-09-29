"""MCP host entry point — launches the NiceGUI web UI."""

from __future__ import annotations

import argparse
import sys
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
        "--target",
        type=Path,
        default=None,
        help="Directory to organize. Skips the web landing page's directory picker.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Launch the web UI in the system browser instead of a native window.",
    )
    parser.add_argument(
        "--auto-class",
        action="store_true",
        help=(
            "Headless mode: analyze new documents at the root of an already-organized "
            "--target directory and file them into its existing folders, with no "
            "approval prompt and no structure change. Needs .organizer/ and INDEX.md."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --auto-class: print the planned moves, change nothing.",
    )
    # Tolerate unrecognized args so bare launch keeps working; --help/--version
    # are handled here and exit before either UI starts.
    args, _unknown = parser.parse_known_args()

    if args.auto_class:
        if args.target is None:
            parser.error("--auto-class requires --target")
        # Before any web import: the headless path never loads nicegui.
        from host.autoclass import run_auto_class_cli

        sys.exit(run_auto_class_cli(args.target, dry_run=args.dry_run))
    if args.dry_run:
        parser.error("--dry-run only works with --auto-class")

    # Print before the heavy imports (nicegui, mcp, openai...) so the user
    # sees something immediately instead of a frozen terminal during that
    # ~1s load.
    print("Loading telcontar…", flush=True)

    from host.web.main import run_web

    run_web(target=args.target, native=not args.browser)


if __name__ == "__main__":
    main()
