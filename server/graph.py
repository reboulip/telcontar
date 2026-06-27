"""Knowledge graph — a derived projection of the registry + event journal.

The graph is reproducible from ``.organizer/registry.json`` and
``.organizer/events.jsonl``: every build is a pure function of those two stores,
persisted as nodes/edges at ``graph_path`` (default ``.organizer/graph.json``).
It holds no state of its own — rebuild it any time the registry or events change.

Node kinds:
  - ``document`` — one per registry record (id ``doc:{checksum}``).
  - ``entity``   — one per unique person/org, deduplicated by normalized name
    (id ``entity:{key}``); carries the union of roles it appears under.
  - ``event``    — one per recorded project event (id ``event:{event_id}``).

Edge types:
  - doc → entity, ``type`` = the entity's role on that document (e.g. ``author``,
    ``mentioned``).
  - entity ↔ entity, ``type`` = ``co_occurrence``, ``weight`` = number of
    documents the pair share.
  - event → entity, ``type`` = ``mentions`` when the entity's name appears in the
    event sentence.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from server import events as _events
from server import registry as _registry


def _entity_key(name: str) -> str:
    """Normalized dedup key for an entity name (lowercased, whitespace-collapsed)."""
    return re.sub(r"\s+", " ", name.strip().lower())


@dataclass
class Graph:
    """Nodes/edges container. Plain dicts inside so it serializes straight to JSON."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges}

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        return cls(nodes=list(d.get("nodes", [])), edges=list(d.get("edges", [])))


def build(registry: _registry.Registry, events: list[_events.Event]) -> Graph:
    """Project the registry + events into a deterministic node/edge graph."""
    nodes: list[dict] = []
    edges: list[dict] = []

    # Entity dedup: normalized key → node id; nodes built lazily, roles accrued.
    entity_id: dict[str, str] = {}
    entity_node: dict[str, dict] = {}

    def _ensure_entity(name: str, kind: str) -> str:
        key = _entity_key(name)
        nid = entity_id.get(key)
        if nid is None:
            nid = f"entity:{key}"
            entity_id[key] = nid
            entity_node[key] = {
                "id": nid,
                "kind": "entity",
                "name": name,
                "entity_kind": kind,
                "roles": [],
            }
        return nid

    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)

    # --- documents, doc→entity edges, co-occurrence accumulation ---------------
    for rec in registry.records():
        doc_id = f"doc:{rec.checksum}"
        nodes.append(
            {
                "id": doc_id,
                "kind": "document",
                "title": rec.title,
                "type": rec.type,
                "date": rec.date,
                "status": rec.status,
                "path": rec.path,
            }
        )
        doc_keys: list[str] = []
        for e in rec.entities:
            name = e.get("name")
            if not name:
                continue
            key = _entity_key(name)
            nid = _ensure_entity(name, e.get("kind", "person"))
            role = e.get("role", "") or "mentioned"
            if role not in entity_node[key]["roles"]:
                entity_node[key]["roles"].append(role)
            edges.append({"src": doc_id, "dst": nid, "type": role})
            doc_keys.append(key)

        unique = sorted(set(doc_keys))
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                cooccurrence[(unique[i], unique[j])] += 1

    # --- entity nodes, then co-occurrence edges -------------------------------
    nodes.extend(entity_node.values())
    for (k1, k2), weight in cooccurrence.items():
        edges.append(
            {"src": entity_id[k1], "dst": entity_id[k2], "type": "co_occurrence", "weight": weight}
        )

    # --- events + event→entity "mentions" edges -------------------------------
    for ev in events:
        ev_id = f"event:{ev.event_id}"
        nodes.append({"id": ev_id, "kind": "event", "sentence": ev.sentence, "date": ev.date})
        low = ev.sentence.lower()
        for key, nid in entity_id.items():
            if key and key in low:
                edges.append({"src": ev_id, "dst": nid, "type": "mentions"})

    return Graph(nodes=nodes, edges=edges)


def load(graph_path: Path) -> Graph:
    """Load the persisted graph; returns an empty Graph if the file is absent."""
    if not graph_path.is_file():
        return Graph()
    return Graph.from_dict(json.loads(graph_path.read_text(encoding="utf-8")))


def save(graph: Graph, graph_path: Path) -> None:
    """Persist the graph to disk (pretty JSON, Unicode preserved)."""
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
