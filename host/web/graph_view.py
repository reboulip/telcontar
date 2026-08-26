"""Knowledge-graph view (Y1) — a ranked-actors table (primary, sortable
surface) with an optional force-directed graph panel behind a toggle
(default off), document/entity/event kind filters (events off by default —
server/graph.py's event<->entity matching is a naive substring test that
floods the graph with false edges for short entity names), and a node-click
detail pane (reusing host/web/docpane.py's component for document nodes).

Every graph value rendered here is LLM-derived output from
attacker-controllable documents (entity names, document titles, event
sentences): `ui.label`/`ui.table` row values only — never
`ui.markdown`/`ui.html`/`ui.code`. This predates and is unrelated to Y7's
chat-message markdown exception (host/web/chat.py) — nothing here reuses
that reasoning. The optional echart panel disables its tooltip entirely
(`tooltip: {"show": False}`) rather than using an HTML `tooltip.formatter`
over untrusted names/text; every detail surfaces through the Python-side
pane instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import run, ui
from nicegui.events import EChartPointClickEventArguments, GenericEventArguments

from host.web import corpus
from host.web import graph as web_graph
from host.web import session as web_session
from host.web import theme
from host.web.docpane import build_doc_pane
from host.web.session import RunSession

GRAPH_MAX_NODES = 150
GRAPH_MAX_EDGES = 400
_TOP_N_OPTIONS = [25, 50, 100]
_DEFAULT_TOP_N = 50

_CATEGORY_ORDER = ("document", "entity", "event")
_CATEGORY_COLORS = {
    "document": theme.PALETTE["accent"],
    "entity": theme.PALETTE["primary"],
    "event": theme.PALETTE["warning"],
}

_ACTOR_COLUMNS: list[dict] = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {
        "name": "entity_kind",
        "label": "Kind",
        "field": "entity_kind",
        "align": "left",
        "sortable": True,
    },
    {"name": "roles", "label": "Roles", "field": "roles", "align": "left", "sortable": True},
    {
        "name": "document_count",
        "label": "Documents",
        "field": "document_count",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "cooccurrence_weight",
        "label": "Co-occurrence",
        "field": "cooccurrence_weight",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "mention_count",
        "label": "Mentions",
        "field": "mention_count",
        "align": "right",
        "sortable": True,
    },
]


def _actor_row(actor: dict) -> dict:
    # ui.table forbids list-valued cells (crashes the browser) — roles must
    # be pre-flattened to a display string, same trap corpus_view.py
    # documents for entities.
    return {
        "id": actor["id"],
        "name": actor.get("name") or "(unknown)",
        "entity_kind": actor.get("entity_kind") or "",
        "roles": ", ".join(actor.get("roles") or []),
        "document_count": actor.get("document_count", 0),
        "cooccurrence_weight": actor.get("cooccurrence_weight", 0),
        "mention_count": actor.get("mention_count", 0),
    }


def _node_label(node: dict) -> str:
    kind = node.get("kind")
    if kind == "document":
        return str(node.get("title") or "(untitled)")
    if kind == "entity":
        return str(node.get("name") or "(unknown)")
    if kind == "event":
        sentence = str(node.get("sentence") or "")
        return sentence if len(sentence) <= 60 else sentence[:60] + "…"
    return str(node.get("id") or "")


def _echart_options(nodes: list[dict], edges: list[dict]) -> dict:
    index_by_id = {n["id"]: i for i, n in enumerate(nodes) if "id" in n}
    data = [
        {
            "id": n["id"],
            "name": _node_label(n),
            "category": _CATEGORY_ORDER.index(n["kind"]) if n.get("kind") in _CATEGORY_ORDER else 0,
            "symbolSize": 18 if n.get("kind") == "entity" else 12,
        }
        for n in nodes
        if "id" in n
    ]
    links = [
        {"source": index_by_id[e["src"]], "target": index_by_id[e["dst"]]}
        for e in edges
        if e.get("src") in index_by_id and e.get("dst") in index_by_id
    ]
    categories = [
        {"name": kind, "itemStyle": {"color": _CATEGORY_COLORS[kind]}} for kind in _CATEGORY_ORDER
    ]
    return {
        "tooltip": {"show": False},
        "legend": {
            "data": list(_CATEGORY_ORDER),
            "textStyle": {"color": theme.PALETTE["secondary"]},
        },
        "series": [
            {
                "type": "graph",
                "layout": "force",
                "roam": True,
                "label": {"show": False},
                "force": {"repulsion": 80, "edgeLength": 40},
                "categories": categories,
                "data": data,
                "links": links,
                "lineStyle": {"color": theme.PALETTE["secondary"], "opacity": 0.3},
            }
        ],
    }


@dataclass
class _GraphViewState:
    reloading: bool = False
    last_mtime: tuple | None = None
    graph: dict = field(default_factory=lambda: {"nodes": [], "edges": []})
    top_n: int = _DEFAULT_TOP_N


async def build_graph_view(session: RunSession) -> None:
    graph = await run.io_bound(web_graph.load_graph, session.target) or {"nodes": [], "edges": []}
    last_mtime = await run.io_bound(web_graph.graph_mtime, session.target)
    state = _GraphViewState(graph=graph, last_mtime=last_mtime)

    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Knowledge graph").classes("text-h5")
        ui.button(icon="refresh", on_click=lambda: _reload()).props("flat dense round").mark(
            "btn-graph-refresh"
        ).tooltip("Refresh graph")

    empty_label = (
        ui.label(
            "No analyzed documents yet — run Organize first, or check back once "
            "analysis has produced at least one record."
        )
        .classes("text-caption")
        .mark("graph-empty")
    )
    empty_label.visible = not graph.get("nodes")

    with ui.row().classes("w-full items-center gap-4"):
        event_filter = ui.checkbox("Events", value=False).mark("graph-filter-event")
        top_n_select = (
            ui.select({n: str(n) for n in _TOP_N_OPTIONS}, value=_DEFAULT_TOP_N, label="Top actors")
            .props("dense")
            .mark("graph-top-n-select")
        )
        show_force_graph = ui.checkbox("Show force-directed view", value=False).mark(
            "graph-show-echart"
        )

    with ui.row().classes("w-full no-wrap items-start gap-4"):
        with ui.column().classes("w-2/3 gap-4"):
            table = (
                ui.table(columns=_ACTOR_COLUMNS, rows=[], row_key="id", pagination=10)
                .classes("w-full cursor-pointer")
                .mark("graph-table")
            )

            echart_container = ui.column().classes("w-full")
            echart_container.visible = False

        with ui.column().classes("w-1/3 gap-1").mark("graph-detail"):
            doc_pane = build_doc_pane(marker_prefix="graph")

            entity_detail = ui.column().classes("w-full gap-1").mark("graph-entity-detail")
            entity_detail.visible = False
            with entity_detail:
                entity_title = ui.label().classes("text-h6").mark("graph-entity-title")
                entity_meta = ui.label().classes("text-caption").mark("graph-entity-meta")
                ui.separator()
                ui.label("Referencing documents").classes("text-subtitle2")
                entity_docs = ui.column().classes("w-full gap-0").mark("graph-entity-docs")

            event_detail = ui.column().classes("w-full gap-1").mark("graph-event-detail")
            event_detail.visible = False
            with event_detail:
                event_sentence = (
                    ui.label().style("white-space: pre-wrap").mark("graph-event-sentence")
                )
                event_meta = ui.label().classes("text-caption").mark("graph-event-meta")
                ui.label("Mentioned entities").classes("text-subtitle2")
                event_entities = ui.column().classes("w-full gap-0").mark("graph-event-entities")

    def _hide_all_detail() -> None:
        doc_pane.clear()
        entity_detail.visible = False
        event_detail.visible = False

    def _show_entity_detail(node_id: str) -> None:
        _hide_all_detail()
        node = next((n for n in state.graph.get("nodes") or [] if n.get("id") == node_id), None)
        if node is None:
            return
        entity_detail.visible = True
        entity_title.set_text(node.get("name") or "(unknown)")
        entity_meta.set_text(
            f"{node.get('entity_kind') or 'unknown kind'} · roles: "
            + (", ".join(node.get("roles") or []) or "none recorded")
        )
        entity_docs.clear()
        docs = [n for n in web_graph.neighbors(state.graph, node_id) if n.get("kind") == "document"]
        with entity_docs:
            if docs:
                for doc in docs:
                    checksum = str(doc.get("id", "")).removeprefix("doc:")
                    ui.button(
                        doc.get("title") or "(untitled)",
                        on_click=lambda c=checksum: _show_document_detail(f"doc:{c}"),
                    ).props("flat dense align=left").classes("w-full justify-start").mark(
                        "graph-entity-doc-link"
                    )
            else:
                ui.label("None recorded.").classes("text-caption")

    def _show_event_detail(node_id: str) -> None:
        _hide_all_detail()
        node = next((n for n in state.graph.get("nodes") or [] if n.get("id") == node_id), None)
        if node is None:
            return
        event_detail.visible = True
        event_sentence.set_text(node.get("sentence") or "")
        event_meta.set_text(node.get("date") or "")
        event_entities.clear()
        entities = [
            n for n in web_graph.neighbors(state.graph, node_id) if n.get("kind") == "entity"
        ]
        with event_entities:
            if entities:
                for ent in entities:
                    ui.label(ent.get("name") or "(unknown)").mark("graph-event-entity")
            else:
                ui.label("None recorded.").classes("text-caption")

    def _show_document_detail(node_id: str) -> None:
        _hide_all_detail()
        checksum = node_id.removeprefix("doc:")
        record = corpus.get_document(session.target, checksum)
        if record is None:
            return
        doc_pane.show(record)

    def _select_node(node_id: str) -> None:
        if node_id.startswith("doc:"):
            _show_document_detail(node_id)
        elif node_id.startswith("entity:"):
            _show_entity_detail(node_id)
        elif node_id.startswith("event:"):
            _show_event_detail(node_id)

    def _on_row_click(e: GenericEventArguments) -> None:
        if not isinstance(e.args, list) or len(e.args) < 2:
            return
        row = e.args[1]
        if not isinstance(row, dict):
            return
        node_id = row.get("id")
        if node_id:
            _select_node(node_id)

    table.on("rowClick", _on_row_click)

    def _kinds() -> set[str]:
        kinds = {"document", "entity"}
        if event_filter.value:
            kinds.add("event")
        return kinds

    def _current_top_n() -> int:
        try:
            return int(top_n_select.value or _DEFAULT_TOP_N)
        except (TypeError, ValueError):
            return _DEFAULT_TOP_N

    def _apply_filters() -> None:
        state.top_n = _current_top_n()
        actors = web_graph.rank_actors_for(session.target, state.top_n)
        rows = [_actor_row(a) for a in actors]
        if rows != table.rows:
            table.rows = rows

        if show_force_graph.value:
            nodes, edges = web_graph.project(state.graph, kinds=_kinds(), top_actors=state.top_n)
            nodes = nodes[:GRAPH_MAX_NODES]
            edges = edges[:GRAPH_MAX_EDGES]
            echart_container.clear()
            with echart_container:
                chart = (
                    ui.echart(_echart_options(nodes, edges))
                    .classes("w-full")
                    .style("height: 400px")
                    .mark("graph-echart")
                )

                def _on_point_click(e: EChartPointClickEventArguments) -> None:
                    data: dict = e.data if isinstance(e.data, dict) else {}
                    node_id = data.get("id")
                    if node_id:
                        _select_node(node_id)

                chart.on_point_click(_on_point_click)
            echart_container.visible = True
        else:
            echart_container.visible = False

    event_filter.on_value_change(lambda e: _apply_filters())
    top_n_select.on_value_change(lambda e: _apply_filters())
    show_force_graph.on_value_change(lambda e: _apply_filters())

    _apply_filters()

    async def _reload() -> None:
        # Re-entrancy guard + skip-when-unchanged: the same two disciplines
        # host/web/corpus_view.py::_reload already established.
        if state.reloading:
            return
        state.reloading = True
        try:
            mtime = await run.io_bound(web_graph.graph_mtime, session.target)
            if mtime is not None and mtime == state.last_mtime:
                return
            state.last_mtime = mtime
            graph = await run.io_bound(web_graph.load_graph, session.target)
            if graph is None:
                return
            state.graph = graph
            empty_label.visible = not graph.get("nodes")
            _apply_filters()
        finally:
            state.reloading = False

    ui.timer(web_session.GRAPH_POLL_INTERVAL, _reload)
