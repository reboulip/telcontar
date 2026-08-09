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
hook, kept in reserve for anything from `host/web/theme.py` that turns out to
need per-mount (not just once-per-process) application. T7's tab title
(`ui.page_title()`/`ui.run(title=...)`) didn't need it — both call sites
naturally live in `host/web/main.py` instead — and T8's palette/CSS/favicon
are also applied once in `run_web()`, not here. If nothing ever needs this
hook, it can be dropped.

The sidebar tree doubles as the directory picker (T3) — the "go up" /
drive-root controls below the header are shown only when ``on_select`` is
wired in (i.e. only on the picker route), since re-rooting the tree away
from a run's target directory would be confusing on `/run/{run_id}`, where
the tree's only job is letting the user verify files actually moved.

Sidebar width (T4) is a single in-memory preference in
`host/web/session.py`, not a per-page setting — see that module's docstring
for why. The drag handle only updates the DOM live in JS; the width is only
persisted (and the Quasar `width` prop re-applied) once, on pointerup, via a
custom `tc_sidebar_resized` event.

The internal-step detail zone (T6) is a `ui.right_drawer`, created here
alongside the left one — both are top-level layout elements, so both must be
direct children of the page's content, per the same `require_top_level_layout`
constraint. `Shell.show_detail()` is the only way `host/web/main.py` touches
it, so the widget choice stays an implementation detail of this module.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import run, ui
from nicegui.events import GenericEventArguments, ValueChangeEventArguments

from host.web import session as web_session
from host.web import tree as web_tree

# Wired once per page build via a `window.__tcSidebarResizeWired` guard —
# NOT a `dataset` flag on the handle element, because `run_javascript` can be
# evaluated before the drawer/handle exist in the freshly-rendered DOM.
# Listeners are therefore delegated to `document` rather than queried up
# front: pointerdown is matched with `e.target.closest('.tc-sidebar-resize')`
# and the drawer itself is looked up fresh (not cached from page-load time)
# at the start of each drag. Pointer events (not mouse events) are used so
# pen/touch input works too. Drags are tracked on `document`, not just the
# 6px handle, so the pointer can leave the handle mid-drag without breaking
# the resize. The live width during the drag is DOM-only — nothing is
# persisted until pointerup, when the actual Quasar `width` prop is set via
# the `tc_sidebar_resized` event below (never raw CSS: Quasar also offsets
# `.q-page-container` from that same prop, so a CSS-only width would leave
# the page content overlapped). Must be an invoked IIFE — `run_javascript`
# evaluates this string with `eval`, and a bare arrow-function expression
# would just be constructed and discarded, never called (this was V15's bug:
# the handlers below were never bound, in any browser).
_RESIZE_JS = """
(() => {
  if (window.__tcSidebarResizeWired) return;
  window.__tcSidebarResizeWired = true;
  let dragging = false;
  let startX = 0;
  let startWidth = 0;
  let drawer = null;
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('.tc-sidebar-resize')) return;
    drawer = document.querySelector('.tc-sidebar');
    if (!drawer) return;
    dragging = true;
    startX = e.clientX;
    startWidth = drawer.offsetWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('pointermove', (e) => {
    if (!dragging || !drawer) return;
    const width = Math.max(240, Math.min(720, startWidth + (e.clientX - startX)));
    drawer.style.width = width + 'px';
  });
  document.addEventListener('pointerup', () => {
    if (!dragging || !drawer) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const width = Math.round(drawer.getBoundingClientRect().width);
    emitEvent('tc_sidebar_resized', width);
    drawer = null;
  });
})()
"""


@dataclass
class Shell:
    """Handle to one page build's mounted shell — the sidebar drawer/tree,
    the internal-step detail drawer, and the page's main content column."""

    drawer: ui.left_drawer
    tree: ui.tree
    content: ui.column
    detail_drawer: ui.right_drawer
    target: Path | None = None
    selected: Path | None = None
    _reloading: bool = field(default=False, repr=False)

    def refresh_tree(self, root: Path) -> None:
        """Re-root the sidebar tree at ``root`` — used by the picker's "go
        up" / drive controls (T3), and available for Phase 20's U4 and Phase
        21's V7 (live refresh after ops execute) to call later."""
        self.target = root
        self.tree.props["nodes"] = web_tree.build_nodes(root)
        self.tree.update()

    async def reload_tree(self) -> None:
        """Rebuild the sidebar tree from disk in place, preserving whatever
        the user had expanded (V7) — the one writer for ``tree.props["nodes"]``,
        called by the manual refresh button, the periodic poll timer, and
        (via U4) `host/web/main.py`'s post-``execute_plan`` ``fs_revision``
        refresh.

        Guarded against overlapping calls: the page's own ``REFRESH_INTERVAL``
        poll and this tree's ``TREE_POLL_INTERVAL`` poll are two *different*
        ``ui.timer``s, so nothing stops them firing concurrently even though a
        single timer never overlaps itself. Skips the actual prop assignment
        when the rebuilt nodes are unchanged — a QTree ``nodes`` replacement
        re-renders the whole subtree and can reset scroll/selection, so "does
        not disturb the running agent" means not touching the prop at all
        when nothing changed, not just being fast about it.
        """
        if self._reloading or self.target is None:
            return
        self._reloading = True
        try:
            expanded = set(self.tree.props.get("expanded") or [])
            nodes = await run.io_bound(web_tree.rebuild_nodes, self.target, expanded)
            if nodes is not None and nodes != self.tree.props.get("nodes"):
                self.tree.props["nodes"] = nodes
                self.tree.update()
        finally:
            self._reloading = False

    def show_detail(self, title: str, detail: str) -> None:
        """Populate and open the step-detail drawer (T6).

        Never `ui.code`/`ui.markdown` here: both render through a markdown
        fenced-code path, and step detail can carry untrusted document
        content that must never be interpreted as markup. `ui.codemirror`
        takes the content as a plain value/prop instead — no injection path
        — and is set read-only via `.disable()` since this is a display-only
        view, not an editor.
        """
        self.detail_drawer.clear()
        with self.detail_drawer:
            ui.label(title).classes("text-subtitle2 q-pa-sm")
            ui.codemirror(detail, language="JSON").classes("w-full h-full").disable()
        self.detail_drawer.show()


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
    width = web_session.get_sidebar_width()
    with ui.left_drawer().classes("tc-sidebar").props(f"width={width}") as drawer:
        ui.element("div").classes("tc-sidebar-resize").style(
            "position:absolute; top:0; right:0; width:6px; height:100%; "
            "cursor:ew-resize; z-index:10;"
        )
        with ui.row().classes("w-full items-center justify-between q-px-sm"):
            ui.label("telcontar").classes("text-subtitle2 tc-display")
            ui.button(icon="refresh", on_click=lambda: shell.reload_tree()).props(
                "flat dense round"
            ).mark("btn-tree-refresh").tooltip("Refresh file tree")

        # Persistent Settings link — reachable from every route (T2), not
        # just the picker/startup page, mirroring the TUI's global
        # priority=True Settings keybinding (host/app.py).
        ui.button("Settings", icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
            "flat dense align=left"
        ).classes("w-full justify-start").mark("btn-sidebar-settings")

        if on_select is not None:
            with ui.row().classes("w-full items-center q-px-sm q-gutter-xs"):

                def _go_up() -> None:
                    parent = shell.target.parent if shell.target else root.parent
                    if shell.target is not None and parent != shell.target:
                        shell.refresh_tree(parent)

                ui.button(icon="arrow_upward", on_click=_go_up).props("flat dense").tooltip(
                    "Up one level"
                )
                drives = web_tree.list_drive_roots()
                if drives:
                    ui.select(
                        {str(d): str(d) for d in drives},
                        on_change=lambda e: shell.refresh_tree(Path(e.value)),
                    ).props("dense borderless").classes("flex-grow")

        nodes = web_tree.build_nodes(root)
        tree_widget = ui.tree(
            nodes, node_key="id", label_key="label", children_key="children"
        ).props("dense no-connectors")

    detail_drawer = ui.right_drawer(value=False).classes("tc-detail-drawer")

    content = ui.column().classes("w-full")
    shell = Shell(
        drawer=drawer,
        tree=tree_widget,
        content=content,
        detail_drawer=detail_drawer,
        target=root,
    )

    def _handle_select(e: ValueChangeEventArguments) -> None:
        if not e.value or e.value.endswith(web_tree.PLACEHOLDER_SUFFIX):
            return
        shell.selected = Path(e.value)
        if on_select is not None:
            on_select(shell.selected)

    async def _handle_expand(e: ValueChangeEventArguments) -> None:
        for node_id in e.value or []:
            if node_id.endswith(web_tree.PLACEHOLDER_SUFFIX):
                continue
            node = web_tree.find_node(tree_widget.props["nodes"], node_id)
            if node is None or not web_tree.needs_loading(node):
                continue
            children = await run.io_bound(web_tree.load_children, Path(node_id))
            # V7: tree_widget.props["nodes"] may have been replaced wholesale
            # by a poll (or another expand) while the above await was in
            # flight, detaching `node` from what's actually live now — never
            # mutate the pre-await reference, re-locate it in the current
            # nodes first (a no-op re-lookup in the common case).
            current = web_tree.find_node(tree_widget.props["nodes"], node_id)
            if current is not None:
                current["children"] = children
        tree_widget.update()

    def _handle_resize(e: GenericEventArguments) -> None:
        try:
            new_width = int(e.args)
        except (TypeError, ValueError):
            return
        drawer.props(f"width={web_session.set_sidebar_width(new_width)}")

    tree_widget.on_select(_handle_select)
    tree_widget.on_expand(_handle_expand)
    ui.on("tc_sidebar_resized", _handle_resize, throttle=0.05)
    ui.run_javascript(_RESIZE_JS)

    # V7: only on routes with a real target — otherwise this would poll
    # Path.home() forever on the picker/setup/settings routes for no reason.
    if target is not None:
        ui.timer(web_session.TREE_POLL_INTERVAL, shell.reload_tree)

    with content:
        yield shell
