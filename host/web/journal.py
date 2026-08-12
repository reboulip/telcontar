"""Journal load/undo logic (U6) — NiceGUI-free, mirroring how
host/web/tree.py relates to host/web/shell.py: this module owns the
filesystem/MCP-adjacent logic, host/web/dialogs.py owns the rendering.

Undo stays user-only and out of MCP by design — this module calls
server.tools.undo_last directly (a local function call, same machine), the
same way host.app.JournalScreen does; there is no agent-reachable path to
it. server.journal/server.tools imports are late (inside the functions) to
avoid dragging in their heavier dependency chains at module import time —
the same discipline host.app.JournalScreen already follows.
"""

from __future__ import annotations

from pathlib import Path

from host.paths import resolve_journal_path, resolve_plans_dir


def load_entries(target: Path) -> list[dict]:
    """Return all undo-journal entries for ``target``, oldest first. Never
    raises — mirrors the TUI's JournalScreen defensive handling, so a
    broken Settings()/config never blanks the view."""
    try:
        from server.journal import all_entries

        return all_entries(resolve_journal_path(target))
    except Exception:
        return []


def do_undo(target: Path) -> dict:
    """Revert the most recent journaled op. Returns undo_last's raw result
    dict verbatim — ``{"undone": None, "error": ...}`` on failure/empty,
    ``{"undone": entry, ...}`` on success."""
    from server.tools import undo_last

    return undo_last(resolve_journal_path(target), resolve_plans_dir(target))
