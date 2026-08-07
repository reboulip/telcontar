"""Persistent left-sidebar shell mounted by every `@ui.page` route (T2).

`ui.left_drawer` must be created as a direct child of the page's content —
NiceGUI's `require_top_level_layout` raises `RuntimeError` if it's created
inside any other container (`ui.column`, `ui.row`, ...). `app_shell()` is a
plain `@contextmanager`, so calling it does not itself push a NiceGUI slot —
it's safe to open directly in a page body as long as nothing else wraps it.
Every route — including the early-return branches for "not configured" and
"run not found" in `host/web/main.py` — mounts through this context manager,
so the sidebar stays visible everywhere.

`app_shell()`'s signature is frozen: Phase 20's U1-U7 and Phase 21's V7 live
tree refresh all mount through it. `_apply_theme()` is a deliberately empty
hook — T7/T8 wire `host/web/theme.py` in here without needing to touch this
module's structure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from host.web import tree as web_tree


@dataclass
class Shell:
    """Handle to one page build's mounted shell — the sidebar drawer/tree and
    the page's main content column."""

    drawer: ui.left_drawer
    tree: ui.tree
    content: ui.column
    target: Path | None = None
    selected: Path | None = None

    def refresh_tree(self) -> None:
        """No-op today — T3 gives this real node-rebuilding logic; Phase 20's
        U4 and Phase 21's V7 call it after ops execute."""


def _apply_theme() -> None:
    """Hook point for T7/T8's host/web/theme.py — empty until then."""


@contextmanager
def app_shell(
    *, target: Path | None = None, on_select: Callable[[Path], None] | None = None
) -> Iterator[Shell]:
    """Mount the persistent shell for one page build.

    ``target`` roots the sidebar tree — the run's target directory on
    `/run/{run_id}`, or ``None`` on the picker/error routes, where it falls
    back to the user's home directory. ``on_select`` is called with the
    selected path whenever the user clicks a tree node.
    """
    _apply_theme()

    root = target or Path.home()
    with ui.left_drawer().classes("tc-sidebar") as drawer:
        ui.label("telcontar").classes("text-subtitle2 q-pa-sm")
        nodes = web_tree.build_nodes(root)
        tree_widget = ui.tree(nodes, node_key="id", label_key="label", children_key="children")

    content = ui.column().classes("w-full")
    shell = Shell(drawer=drawer, tree=tree_widget, content=content, target=target)

    def _handle_select(e: ValueChangeEventArguments) -> None:
        if not e.value:
            return
        shell.selected = Path(e.value)
        if on_select is not None:
            on_select(shell.selected)

    tree_widget.on_select(_handle_select)

    with content:
        yield shell
