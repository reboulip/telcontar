"""MCP host entry point — launches the NiceGUI web UI."""

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
    # Tolerate unrecognized args so bare launch keeps working; --help/--version
    # are handled here and exit before either UI starts.
    args, _unknown = parser.parse_known_args()

    # Print before the heavy imports (nicegui, mcp, openai...) so the user
    # sees something immediately instead of a frozen terminal during that
    # ~1s load.
    print("Loading telcontar…", flush=True)

    from host.web.main import run_web

    run_web(target=args.target, native=not args.browser)


if __name__ == "__main__":
    main()
