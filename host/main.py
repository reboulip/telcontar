"""MCP host entry point — launches the NiceGUI web UI."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TextIO


def _version() -> str:
    try:
        return version("telcontar")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return "0.0.0+unknown"


def _ensure_std_streams() -> None:
    """Give a windowed (no-console) frozen exe usable stdio.

    A PyInstaller ``console=False`` build starts with ``sys.stdin/stdout/stderr``
    set to ``None``, which crashes anything that calls ``.isatty()`` or writes to
    them (uvicorn's log formatter, for one). Point them at a log file under the
    user config dir — also the first place to look when a double-clicked build
    misbehaves — or at the null device if that file cannot be opened.
    """
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return
    try:
        from config import settings

        settings._USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log: TextIO = open(
            settings._USER_CONFIG_DIR / "telcontar.log", "a", encoding="utf-8", buffering=1
        )
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    if sys.stdin is None:
        sys.stdin = open(os.devnull, encoding="utf-8")


def main() -> None:
    # Hidden mode for frozen builds: the MCP host spawns `telcontar-cli --mcp-server`
    # as its server subprocess (see host.agent._server_command). Checked before
    # argparse because the server owns stdout for the MCP protocol.
    if "--mcp-server" in sys.argv[1:]:
        from server.main import main as server_main

        server_main()
        return

    _ensure_std_streams()

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
