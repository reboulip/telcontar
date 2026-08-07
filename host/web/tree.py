"""NiceGUI-free directory-tree node builder for the sidebar.

No nicegui import — the same invariant as session.py and bridge.py, so this
logic is testable in plain pytest before Phase 20 U9 introduces a real
NiceGUI test harness. Node shape matches what `ui.tree` expects:
``{"id": <absolute path str>, "label": <basename>, "children": [...]}`` — the
id is always an absolute path string, so it stays a stable, collision-free
key across a page reload.

T2 scaffolding only: this returns a single, childless root node so the
sidebar shell has something valid to mount without eagerly walking the
filesystem. T3 replaces this with real (lazily-loaded) directory traversal.
"""

from __future__ import annotations

from pathlib import Path


def build_nodes(root: Path) -> list[dict]:
    """Build the top-level node list for `ui.tree`, rooted at ``root``."""
    return [{"id": str(root), "label": root.name or str(root), "children": []}]
