"""MCP server entrypoint — exposes file tools over stdio transport."""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from server import tools

mcp = FastMCP("directory-organizer")

_settings = None


def _get_settings():
    global _settings
    if _settings is None:
        from config.settings import load
        _settings = load()
    return _settings


# ── Read-only tools ──────────────────────────────────────────────────────────

@mcp.tool()
def list_dir(path: str) -> dict:
    """Enumerate directory entries with metadata (size, type, mtime)."""
    return tools.list_dir(path)


@mcp.tool()
def read_file(path: str, max_chars: int = 4000) -> str:
    """Return file content up to max_chars characters."""
    cfg = _get_settings()
    from server.guards import check_allowlist
    check_allowlist(Path(path), cfg.allowlist_dirs)
    return tools.read_file(path, min(max_chars, cfg.max_snippet_chars))


@mcp.tool()
def extract_text(path: str, max_chars: int = 4000) -> str:
    """Extract plain text from a PDF or Office file via markitdown."""
    cfg = _get_settings()
    from server.guards import check_allowlist
    check_allowlist(Path(path), cfg.allowlist_dirs)
    return tools.extract_text(path, min(max_chars, cfg.max_snippet_chars))


# ── Plan-building tools (write to plan, do not execute) ──────────────────────

@mcp.tool()
def propose_rename(path: str, new_name: str) -> str:
    """Stage a rename operation in the current plan."""
    return tools.propose_rename(path, new_name)


@mcp.tool()
def propose_move(path: str, dest_dir: str) -> str:
    """Stage a move operation in the current plan."""
    return tools.propose_move(path, dest_dir)


@mcp.tool()
def propose_quarantine(path: str) -> str:
    """Stage a quarantine operation in the current plan."""
    return tools.propose_quarantine(path)


# ── Direct file operations ───────────────────────────────────────────────────

@mcp.tool()
def move_file(path: str, dest_dir: str) -> dict:
    """Move a file to dest_dir; raises if the destination already exists."""
    return tools.move_file(path, dest_dir)


@mcp.tool()
def rename_file(path: str, new_name: str) -> dict:
    """Rename a file in place; raises if the new name already exists."""
    return tools.rename_file(path, new_name)


@mcp.tool()
def create_file(path: str, content: str) -> dict:
    """Write content to path; raises if the file already exists."""
    return tools.create_file(path, content)


@mcp.tool()
def update_file(path: str, content: str) -> dict:
    """Overwrite or create content at path."""
    return tools.update_file(path, content)


# ── Gated execution tools ────────────────────────────────────────────────────

@mcp.tool()
def execute_plan(plan_id: str) -> dict:
    """Apply all operations in an approved plan; journals each one."""
    return tools.execute_plan(plan_id)


@mcp.tool()
def write_index(path: str) -> str:
    """Emit INDEX.md and manifest.json for the organized tree at path."""
    return tools.write_index(path)


@mcp.tool()
def write_summary(path: str) -> str:
    """Emit SUMMARY.md describing the contents of the organized tree at path."""
    return tools.write_summary(path)


# ── Recovery tools ───────────────────────────────────────────────────────────

@mcp.tool()
def undo_last() -> dict:
    """Revert the most recent journaled operation."""
    return tools.undo_last()


def main() -> None:
    mcp.run(transport="stdio")
