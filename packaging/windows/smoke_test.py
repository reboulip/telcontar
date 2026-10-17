"""Smoke-test a frozen telcontar build: ``python smoke_test.py dist/telcontar``.

Checks what a unit test cannot: that the bundle starts, reports its version, and
that its MCP server subprocess (the host's real launch path) completes the MCP
handshake and lists its tools. Exits non-zero on any failure.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# Tools the host cannot work without — a bundle missing these is broken.
REQUIRED_TOOLS = {"list_dir", "compute_checksum", "record_document", "execute_plan"}


def _exe(bundle: Path, name: str) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    exe = bundle / (name + suffix)
    if not exe.is_file():
        sys.exit(f"FAIL: {exe} not found")
    return exe


async def _check_server(cli: Path) -> None:
    params = StdioServerParameters(command=str(cli), args=["--mcp-server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
    missing = REQUIRED_TOOLS - tools
    if missing:
        sys.exit(f"FAIL: MCP server is missing tools: {sorted(missing)}")
    print(f"ok: MCP handshake, {len(tools)} tools")


def main() -> None:
    bundle = Path(sys.argv[1]).resolve()
    _exe(bundle, "telcontar")  # the windowed exe must at least be present
    cli = _exe(bundle, "telcontar-cli")

    out = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or not out.stdout.startswith("telcontar "):
        sys.exit(f"FAIL: --version exited {out.returncode}: {out.stdout!r} {out.stderr!r}")
    print(f"ok: {out.stdout.strip()}")

    asyncio.run(asyncio.wait_for(_check_server(cli), timeout=180))


if __name__ == "__main__":
    main()
