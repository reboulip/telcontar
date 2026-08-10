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

The internal-step detail zone (T6) lives inside this same left drawer as of
V13b — stacked below the file tree, not a separate `ui.right_drawer` (its
original T6 home). It starts hidden (`.visible = False`, NiceGUI's standard
bindable visibility toggle) and is revealed by `Shell.show_detail()`, the
only way `host/web/main.py`/`steplog.py` touch it,
so the widget choice stays an implementation detail of this module. The tree
gets a bounded `max-height` so the detail section always has room below it
without the whole drawer needing to scroll to reach it. `SIDEBAR_WIDTH_MAX`
(`host/web/session.py`) was raised accordingly — detail content (JSON tool
results) wants more horizontal room than the tree alone ever needed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import run, ui
from nicegui.events import GenericEventArguments, ValueChangeEventArguments

from host.paths import find_organizer_root
from host.web import session as web_session
from host.web import theme
from host.web import tree as web_tree
from host.web.session import RunSession

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
# the handlers below were never bound, in any browser). The min/max clamp is
# interpolated from web_session.SIDEBAR_WIDTH_MIN/MAX (V13b) rather than
# duplicated as JS literals, so the two can never silently drift apart again.
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
    const width = Math.max(__MIN__, Math.min(__MAX__, startWidth + (e.clientX - startX)));
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
""".replace("__MIN__", str(web_session.SIDEBAR_WIDTH_MIN)).replace(
    "__MAX__", str(web_session.SIDEBAR_WIDTH_MAX)
)


@dataclass
class Shell:
    """Handle to one page build's mounted shell — the sidebar drawer/tree,
    the internal-step detail section (V13b: stacked inside this same left
    drawer, not a separate right-side one), and the page's main content
    column."""

    drawer: ui.left_drawer
    tree: ui.tree
    content: ui.column
    detail_section: ui.column
    detail_title: ui.label
    detail_content: ui.codemirror
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
        """Populate and reveal the step-detail section (T6) — stacked below
        the file tree inside this left drawer as of V13b, rather than a
        separate right-side drawer.

        Never `ui.code`/`ui.markdown` here: both render through a markdown
        fenced-code path, and step detail can carry untrusted document
        content that must never be interpreted as markup. `ui.codemirror`
        takes the content as a plain value/prop instead — no injection path
        — and is set read-only via `.disable()` since this is a display-only
        view, not an editor. `theme.CODEMIRROR_THEME` is applied once at
        creation time (below); only the value/title change per call.
        """
        self.detail_title.set_text(title)
        self.detail_content.set_value(detail)
        self.detail_section.visible = True

    def hide_detail(self) -> None:
        """Collapse the step-detail section, returning the sidebar's full
        height to the file tree (V13b)."""
        self.detail_section.visible = False


def _apply_theme() -> None:
    """Hook point for T7/T8's host/web/theme.py — empty until then."""


_NAV_TABS = ("conversation", "corpus", "query", "settings")


@contextmanager
def app_shell(
    *,
    target: Path | None = None,
    on_select: Callable[[Path], None] | None = None,
    session: RunSession | None = None,
    active: str | None = None,
    nav: bool = True,
) -> Iterator[Shell]:
    """Mount the persistent shell for one page build.

    ``target`` roots the sidebar tree — the run's target directory on
    `/run/{run_id}`, or ``None`` on the picker/error routes, where it falls
    back to the user's home directory. ``on_select`` is called with the
    selected path whenever the user clicks a tree node.

    X11: ``session`` is the *organize*-mode `RunSession` driving the current
    route (``/run/{run_id}`` and ``/corpus/{run_id}`` pass their own; a query
    session is deliberately never passed here — see the module docstring's
    nav section). It both enables the Conversation/Corpus tabs and — via
    ``web_session.set_active`` — becomes the fallback the *next* mount uses
    when it has no session of its own in scope (e.g. `/settings`), so those
    tabs still point at the run in progress instead of just being disabled.
    ``active`` names which of `_NAV_TABS` is the current route, if any.
    ``nav=False`` hides the header entirely — only `/setup` uses this, since
    the first-run wizard has nowhere valid to navigate to yet.
    """
    _apply_theme()

    if session is not None:
        web_session.set_active(session.run_id)
    effective_session = session or web_session.get_active()

    root = target or Path.home()
    width = web_session.get_sidebar_width()

    if nav:
        with ui.header().classes("items-center justify-between q-px-md"):
            ui.label("telcontar").classes("text-h6 tc-display")
            with ui.tabs(value=active).props("dense") as tabs:
                conversation_tab = ui.tab("conversation", label="Conversation").mark(
                    "nav-conversation"
                )
                corpus_tab = ui.tab("corpus", label="Corpus").mark("nav-corpus")
                query_tab = ui.tab("query", label="Query").mark("nav-query")
                ui.tab("settings", label="Settings").mark("nav-settings")

            if effective_session is None:
                conversation_tab.disable()
                corpus_tab.disable()
            effective_target = target or (
                effective_session.target if effective_session is not None else None
            )
            if effective_target is None or find_organizer_root(effective_target) is None:
                query_tab.disable()

            def _on_nav_change(e: ValueChangeEventArguments) -> None:
                # Constructing ui.tabs(value=active) above must not itself
                # trigger a navigation — only a genuine user click should.
                if e.value == active:
                    return
                if e.value == "conversation" and effective_session is not None:
                    ui.navigate.to(f"/run/{effective_session.run_id}")
                elif e.value == "corpus" and effective_session is not None:
                    ui.navigate.to(f"/corpus/{effective_session.run_id}")
                elif e.value == "query" and effective_target is not None:
                    query_session = web_session.find_by_target(
                        effective_target, mode="query"
                    ) or web_session.create(effective_target, mode="query")
                    ui.navigate.to(f"/query/{query_session.run_id}")
                elif e.value == "settings":
                    ui.navigate.to("/settings")

            tabs.on_value_change(_on_nav_change)
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
        tree_widget = (
            ui.tree(nodes, node_key="id", label_key="label", children_key="children")
            # selected-color=primary (X1): highlights the run's target
            # folder once app_shell()'s caller selects it — a Quasar prop,
            # no CSS needed, so this stays out of theme.py.
            .props("dense no-connectors selected-color=primary")
            .classes("w-full")
            .style("max-height: 45vh; overflow-y: auto")
        )

        # V13b: step-detail section, stacked below the tree inside this same
        # drawer — hidden until Shell.show_detail() populates and reveals it.
        detail_section = ui.column().classes("w-full gap-0").mark("detail-section")
        detail_section.visible = False
        with detail_section:
            ui.separator()
            with ui.row().classes("w-full items-center justify-between q-px-sm"):
                detail_title = ui.label().classes("text-subtitle2 ellipsis").mark("detail-title")
                ui.button(icon="close", on_click=lambda: shell.hide_detail()).props(
                    "flat dense round size=sm"
                ).mark("btn-detail-close")
            detail_content = (
                ui.codemirror("", language="JSON", theme=theme.CODEMIRROR_THEME)
                .classes("w-full")
                .style("max-height: 40vh")
                .disable()
                .mark("detail-content")
            )

    content = ui.column().classes("w-full")
    shell = Shell(
        drawer=drawer,
        tree=tree_widget,
        content=content,
        detail_section=detail_section,
        detail_title=detail_title,
        detail_content=detail_content,
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
