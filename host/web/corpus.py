"""Registry load logic (V5) — NiceGUI-free, mirroring host/web/journal.py's
contract exactly: this module owns the filesystem-adjacent logic, host/web/
corpus_view.py owns the rendering.

Read-only by design — this module calls server.registry directly (a local
function call, same machine), never through MCP, since there is no
agent-reachable reason for a corpus *browse* to exist. server.registry
imports are late (inside the functions) to avoid dragging in its dependency
chain at module import time — the same discipline host.web.journal follows.
"""

from __future__ import annotations

import os
from pathlib import Path

from host.paths import resolve_registry_path


def list_documents(target: Path) -> list[dict]:
    """Return all registry records for ``target``, as plain dicts. Never
    raises — mirrors journal.load_entries's defensive handling, so a missing
    or corrupt registry.json never blanks the whole page, just shows empty."""
    try:
        from server.registry import load

        registry = load(resolve_registry_path(target))
        return [rec.to_dict() for rec in registry.records()]
    except Exception:
        return []


def get_document(target: Path, checksum: str) -> dict | None:
    """Return one registry record by checksum, or None if missing/unreadable."""
    try:
        from server.registry import load

        registry = load(resolve_registry_path(target))
        rec = registry.get(checksum)
        return rec.to_dict() if rec is not None else None
    except Exception:
        return None


def registry_mtime(target: Path) -> tuple[float, int] | None:
    """(mtime, size) of ``target``'s registry.json, or None if missing/
    unreadable — same defensive contract as `list_documents`. A cheap
    pre-check (X10) so a poll can skip re-parsing the whole registry on
    every tick when nothing has actually changed since the last one."""
    try:
        st = resolve_registry_path(target).stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def find_by_path(target: Path, path: Path) -> dict | None:
    """Match a registry record by its recorded path (X9) — exact
    ``os.path.normcase(os.path.normpath(...))`` comparison, the same idiom
    ``server/registry.py``'s ``_same_path`` uses. Never a basename fallback:
    that could silently show the wrong document's summary if the match is
    ever missed. Never raises — same defensive contract as
    ``list_documents``, which this is built on."""
    needle = os.path.normcase(os.path.normpath(str(path)))
    for record in list_documents(target):
        recorded = os.path.normcase(os.path.normpath(record.get("path") or ""))
        if recorded == needle:
            return record
    return None
