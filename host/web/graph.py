"""Knowledge-graph load/projection logic (Y1) — NiceGUI-free, mirroring
host/web/corpus.py's contract exactly: this module owns the filesystem-
adjacent logic, host/web/graph_view.py owns the rendering.

Always builds the graph fresh, in-process, from the registry + event
journal (``server.graph.build``) — never from the persisted
``.organizer/graph.json``. That file only exists once an organize run
reaches its final write-outputs step, so reading it would show an empty
graph during and immediately after most runs, and it goes stale the moment
a single document is (re-)recorded. ``server.graph.build`` is a documented
pure function of the same two stores this module already needs to poll
(registry + events), so building fresh costs nothing extra.

server.registry/server.events/server.graph imports are late (inside the
functions) to avoid dragging in their dependency chain at module import
time — the same discipline host.web.corpus/host.web.journal follow.
"""

from __future__ import annotations

from pathlib import Path

from host.paths import resolve_events_path, resolve_registry_path


def load_graph(target: Path) -> dict | None:
    """Build the knowledge graph fresh for ``target``, as a plain dict
    (``{"nodes": [...], "edges": [...]}``). Never raises — returns None on
    any error, mirroring host/web/corpus.py's defensive contract, so a
    missing/corrupt registry or event journal never blanks the whole page."""
    try:
        from server import events as _events
        from server import graph as _graph
        from server import registry as _registry

        registry = _registry.load(resolve_registry_path(target))
        events = _events.all_events(resolve_events_path(target))
        return _graph.build(registry, events).to_dict()
    except Exception:
        return None


def graph_mtime(target: Path) -> tuple[float, int, float, int] | None:
    """Combined (mtime, size) of both registry.json and events.jsonl, or
    None if either is missing/unreadable — the poll pre-check (mirrors
    host/web/corpus.py::registry_mtime), so a tick can skip rebuilding the
    whole graph when neither source has actually changed."""
    try:
        reg_st = resolve_registry_path(target).stat()
        ev_st = resolve_events_path(target).stat()
        return (reg_st.st_mtime, reg_st.st_size, ev_st.st_mtime, ev_st.st_size)
    except OSError:
        return None


def rank_actors_for(target: Path, cap: int) -> list[dict]:
    """Ranked-actors list for ``target``, capped at ``cap`` (``cap <= 0`` =
    no limit). Never raises — [] on any error."""
    try:
        from server import events as _events
        from server import graph as _graph
        from server import registry as _registry

        registry = _registry.load(resolve_registry_path(target))
        events = _events.all_events(resolve_events_path(target))
        built = _graph.build(registry, events)
        return _graph.rank_actors(built, cap)
    except Exception:
        return []


def project(graph: dict, *, kinds: set[str], top_actors: int) -> tuple[list[dict], list[dict]]:
    """Pure projection over an already-loaded graph dict: keep only nodes
    whose ``kind`` is in ``kinds``, cap entity nodes to the top
    ``top_actors`` by centrality (document_count, then cooccurrence_weight,
    then mention_count — same ordering as server.graph.rank_actors), and
    keep only edges whose both endpoints survived. No I/O — the
    unit-testable heart of this item."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    kept_nodes = [n for n in nodes if n.get("kind") in kinds]

    if "entity" in kinds and top_actors > 0:
        entity_nodes = [n for n in kept_nodes if n.get("kind") == "entity"]
        other_nodes = [n for n in kept_nodes if n.get("kind") != "entity"]
        scored = _score_entities(entity_nodes, edges)
        scored.sort(
            key=lambda item: (
                -item[1][0],
                -item[1][1],
                -item[1][2],
                str(item[0].get("name", "")).lower(),
            )
        )
        kept_nodes = other_nodes + [n for n, _ in scored[:top_actors]]

    kept_ids = {n["id"] for n in kept_nodes if "id" in n}
    kept_edges = [e for e in edges if e.get("src") in kept_ids and e.get("dst") in kept_ids]
    return kept_nodes, kept_edges


def _score_entities(
    entity_nodes: list[dict], edges: list[dict]
) -> list[tuple[dict, tuple[int, int, int]]]:
    """(node, (document_count, cooccurrence_weight, mention_count)) pairs —
    the same three centrality components server.graph.rank_actors computes,
    recomputed here since project() works from an already-serialized graph
    dict rather than a server.graph.Graph object."""
    doc_count: dict[str, int] = {}
    mention_count: dict[str, int] = {}
    cooc_weight: dict[str, int] = {}

    for e in edges:
        etype = e.get("type")
        src = e.get("src", "")
        dst = e.get("dst", "")
        if etype == "co_occurrence":
            weight = e.get("weight", 1)
            cooc_weight[src] = cooc_weight.get(src, 0) + weight
            cooc_weight[dst] = cooc_weight.get(dst, 0) + weight
        elif etype == "mentions" and src.startswith("event:"):
            mention_count[dst] = mention_count.get(dst, 0) + 1
        elif src.startswith("doc:"):
            doc_count[dst] = doc_count.get(dst, 0) + 1

    return [
        (
            n,
            (
                doc_count.get(n["id"], 0),
                cooc_weight.get(n["id"], 0),
                mention_count.get(n["id"], 0),
            ),
        )
        for n in entity_nodes
    ]


def neighbors(graph: dict, node_id: str) -> list[dict]:
    """Immediate-neighbor nodes of ``node_id`` (either edge direction), for
    a node-click detail pane's "referencing documents"/"mentioned entities"
    listing. [] if ``node_id`` has no edges or isn't in the graph."""
    nodes_by_id = {n["id"]: n for n in graph.get("nodes") or [] if "id" in n}
    neighbor_ids: set[str] = set()
    for e in graph.get("edges") or []:
        src, dst = e.get("src"), e.get("dst")
        if src == node_id and dst in nodes_by_id:
            neighbor_ids.add(dst)
        elif dst == node_id and src in nodes_by_id:
            neighbor_ids.add(src)
    return [nodes_by_id[nid] for nid in neighbor_ids]
