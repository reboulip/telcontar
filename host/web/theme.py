"""Product identity — no NiceGUI import, matching the invariant already kept
by session.py/bridge.py/tree.py, so this stays testable in plain pytest.

`window_title()` (T7) is the browser tab title, computed fresh per page
build since it depends on the run's target directory — unlike `ui.run()`'s
own `title=` kwarg (used for the global default, before any run exists),
this must be pushed live via `ui.page_title()` inside the page body.
"""

from __future__ import annotations

from pathlib import Path


def window_title(target: Path | None = None) -> str:
    """ "telcontar", or "telcontar — <name>" once a target directory is
    selected. Falls back to the full path string for a drive root (e.g.
    ``Path("C:\\\\").name == ""`` on Windows) so the title is never left
    with a blank, dangling suffix."""
    if target is None:
        return "telcontar"
    suffix = target.name or str(target)
    return f"telcontar — {suffix}"
