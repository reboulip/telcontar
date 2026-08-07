"""NiceGUI-free directory-tree node builder for the sidebar.

No nicegui import — the same invariant as session.py and bridge.py, so this
logic is testable in plain pytest before Phase 20 U9 introduces a real
NiceGUI test harness. Node shape matches what `ui.tree` expects:
``{"id": <absolute path str>, "label": <basename>, "children": [...]}`` — the
id is always an absolute path string, so it stays a stable, collision-free
key across a page reload.

Lazy loading: a not-yet-expanded directory gets one placeholder child (its id
ends in `PLACEHOLDER_SUFFIX`) so `ui.tree` shows an expand arrow without this
module walking into it. `host/web/shell.py`'s expand handler calls
`load_children` (via `run.io_bound`) the first time a node is actually
expanded and splices the real children in, replacing the placeholder — S5's
blocking-I/O rule: eagerly walking the whole tree would stall the server for
every connected tab, not just one.
"""

from __future__ import annotations

import os
from pathlib import Path

PLACEHOLDER_SUFFIX = "\x00…"


def _entry_node(path: Path) -> dict:
    node: dict = {"id": str(path), "label": path.name or str(path), "children": []}
    if path.is_dir() and not path.is_symlink():
        node["children"] = [{"id": str(path) + PLACEHOLDER_SUFFIX, "label": "…", "children": []}]
    return node


def load_children(path: Path) -> list[dict]:
    """List one directory's immediate entries — files and folders, both
    shown (the sidebar's job is letting the user verify files actually
    moved). Hides dotfiles (``.organizer``, ``.git``, ...) but never
    ``_quarantine`` — the only removal path, the user must be able to see
    what landed there. Never follows symlinks/junctions (Windows profile
    directories like "Application Data" can loop back on themselves). Never
    raises: a permission-denied or vanished directory yields an empty list
    rather than blanking the whole tree.
    """
    try:
        entries = [p for p in path.iterdir() if not p.name.startswith(".") and not p.is_symlink()]
    except OSError:
        return []
    entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    return [_entry_node(p) for p in entries]


def build_nodes(root: Path) -> list[dict]:
    """Build the top-level node list for `ui.tree`, rooted at ``root``.

    The root's own immediate children are loaded eagerly (one directory
    listing, not a whole-tree walk) so the sidebar shows useful content on
    first render; deeper levels stay lazy via the placeholder-child scheme.
    """
    return [
        {
            "id": str(root),
            "label": root.name or str(root),
            "children": load_children(root),
        }
    ]


def find_node(nodes: list[dict], node_id: str) -> dict | None:
    """Depth-first search for a node by id within a `ui.tree`-shaped nested
    node list — used by shell.py's expand handler to locate the node whose
    placeholder child needs replacing with real ones."""
    for node in nodes:
        if node["id"] == node_id:
            return node
        found = find_node(node.get("children") or [], node_id)
        if found is not None:
            return found
    return None


def needs_loading(node: dict) -> bool:
    """True if ``node`` still carries the lazy-load placeholder rather than
    real children."""
    children = node.get("children") or []
    return len(children) == 1 and str(children[0]["id"]).endswith(PLACEHOLDER_SUFFIX)


def list_drive_roots() -> list[Path]:
    """Windows drive roots (``C:\\``, ``D:\\``, ...), so the picker can reach
    outside the home directory. Empty (never raised) on platforms/Python
    versions without ``os.listdrives`` (3.12+, Windows-only) or on any
    enumeration error."""
    if not hasattr(os, "listdrives"):
        return []
    try:
        return [Path(d) for d in os.listdrives()]
    except OSError:
        return []
