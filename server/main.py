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


# ── Plan management tools ────────────────────────────────────────────────────

@mcp.tool()
def create_plan() -> dict:
    """Create a new empty plan; returns plan_id and initial metadata."""
    cfg = _get_settings()
    return tools.create_plan(cfg.plans_dir)


@mcp.tool()
def get_plan(plan_id: str) -> dict:
    """Load and return a plan by plan_id, including all proposed ops."""
    cfg = _get_settings()
    return tools.get_plan(plan_id, cfg.plans_dir)


@mcp.tool()
def list_plans() -> list:
    """Return all plans with their current state and ops."""
    cfg = _get_settings()
    return tools.list_plans(cfg.plans_dir)


@mcp.tool()
def review_plan(plan_id: str) -> dict:
    """Deduplication and pre-flight check; returns a report without modifying the plan."""
    cfg = _get_settings()
    return tools.review_plan(plan_id, cfg.plans_dir)


@mcp.tool()
def approve_plan(plan_id: str) -> dict:
    """Transition a plan from pending to approved, authorizing execution."""
    cfg = _get_settings()
    return tools.approve_plan(plan_id, cfg.plans_dir)


# ── Plan-building tools (write to plan, do not execute) ──────────────────────

@mcp.tool()
def propose_rename(path: str, new_name: str, plan_id: str) -> dict:
    """Stage a rename operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_rename(path, new_name, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_move(path: str, dest_dir: str, plan_id: str) -> dict:
    """Stage a move operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_move(path, dest_dir, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_quarantine(path: str, plan_id: str) -> dict:
    """Stage a quarantine operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_quarantine(path, plan_id, cfg.plans_dir, cfg.quarantine_dir)


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
    cfg = _get_settings()
    return tools.execute_plan(plan_id, cfg.plans_dir, cfg.journal_path)


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
    cfg = _get_settings()
    return tools.undo_last(cfg.journal_path, cfg.plans_dir)


def main() -> None:
    mcp.run(transport="stdio")
