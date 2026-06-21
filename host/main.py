"""MCP host: drives the GPT-5 agent loop and routes tool calls to the MCP server."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from config import settings as settings_module
from host.llm import make_client


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the directory organizer agent")
    p.add_argument("--target", type=Path, required=True, help="Directory to organize")
    return p.parse_args()


async def run(target: Path) -> None:
    cfg = settings_module.load()
    _client = make_client(cfg)  # noqa: F841  — used in the agent loop (not yet implemented)
    raise NotImplementedError


def main() -> None:
    args = _parse_args()
    asyncio.run(run(args.target))
