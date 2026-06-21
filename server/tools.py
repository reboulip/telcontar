"""Tool implementations — called by the MCP server handlers in main.py."""
from __future__ import annotations

import shutil
from pathlib import Path

from server.extract import extract as _extract
from server.guards import check_no_overwrite


def list_dir(path: str) -> dict:
    """Enumerate directory entries with size, type, and mtime."""
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"Not a directory: {path}")
    entries = []
    for entry in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name)):
        try:
            st = entry.stat()
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except OSError:
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "type": "unknown",
                "size": None,
                "mtime": None,
            })
    return {"path": str(p), "entries": entries}


def read_file(path: str, max_chars: int) -> str:
    """Return file text up to max_chars characters."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text


def extract_text(path: str, max_chars: int) -> str:
    """Extract plain text from a PDF or Office file via markitdown."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    return _extract(p, max_chars)


def move_file(path: str, dest_dir: str) -> dict:
    """Move a file to dest_dir, raising if the destination already exists."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"Not a file: {path}")
    dst_dir = Path(dest_dir)
    if not dst_dir.is_dir():
        raise ValueError(f"Not a directory: {dest_dir}")
    dest = dst_dir / src.name
    check_no_overwrite(dest)
    shutil.move(str(src), str(dest))
    return {"moved": str(dest)}


def rename_file(path: str, new_name: str) -> dict:
    """Rename a file in place, raising if the new name already exists."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Not found: {path}")
    dest = src.parent / new_name
    check_no_overwrite(dest)
    src.rename(dest)
    return {"renamed": str(dest)}


def create_file(path: str, content: str) -> dict:
    """Write content to path; raises if the file already exists."""
    p = Path(path)
    check_no_overwrite(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"created": str(p)}


def update_file(path: str, content: str) -> dict:
    """Overwrite or create content at path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"updated": str(p)}


# ── v0.3.0 stubs ────────────────────────────────────────────────────────────

def propose_rename(path: str, new_name: str) -> str:
    raise NotImplementedError


def propose_move(path: str, dest_dir: str) -> str:
    raise NotImplementedError


def propose_quarantine(path: str) -> str:
    raise NotImplementedError


def execute_plan(plan_id: str) -> dict:
    raise NotImplementedError


def write_index(path: str) -> str:
    raise NotImplementedError


def write_summary(path: str) -> str:
    raise NotImplementedError


def undo_last() -> dict:
    raise NotImplementedError
