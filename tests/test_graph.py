"""Tests for the knowledge graph (server/graph.py + build_graph/get_graph tools)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.events import Event
from server.graph import Graph, build, load, rank_actors, save
from server.registry import DocumentRecord, Registry
from server.tools import build_graph, get_actors, get_graph


def _reg(*records: DocumentRecord) -> Registry:
    return Registry(documents={r.checksum: r for r in records})


def _doc(checksum: str, title: str, entities: list[dict] | None = None, **kw: object) -> DocumentRecord:
    return DocumentRecord.new(
        checksum=checksum,
        path=kw.get("path", f"/p/{checksum}.txt"),  # type: ignore[arg-type]
        title=title,
        type=kw.get("type", "notes"),  # type: ignore[arg-type]
        summary="résumé",
        provenance="apport",
        date=kw.get("date"),  # type: ignore[arg-type]
        entities=entities or [],
    )


def _nodes_of_kind(g: Graph, kind: str) -> list[dict]:
    return [n for n in g.nodes if n["kind"] == kind]


def _edges_of_type(g: Graph, type_: str) -> list[dict]:
    return [e for e in g.edges if e["type"] == type_]


# --- nodes ------------------------------------------------------------------


class TestNodes:
    def test_empty_registry_yields_empty_graph(self) -> None:
        g = build(_reg(), [])
        assert g.nodes == []
        assert g.edges == []

    def test_document_node_per_record(self) -> None:
        g = build(_reg(_doc("c1", "Titre A"), _doc("c2", "Titre B")), [])
        docs = _nodes_of_kind(g, "document")
        assert {n["id"] for n in docs} == {"doc:c1", "doc:c2"}
        assert docs[0]["title"] == "Titre A"

    def test_entity_deduplicated_across_documents(self) -> None:
        g = build(
            _reg(
                _doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]),
                _doc("c2", "B", entities=[{"name": "alice", "role": "mentioned", "kind": "person"}]),
            ),
            [],
        )
        ents = _nodes_of_kind(g, "entity")
        assert len(ents) == 1
        assert ents[0]["id"] == "entity:alice"

    def test_entity_accrues_all_roles(self) -> None:
        g = build(
            _reg(
                _doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]),
                _doc("c2", "B", entities=[{"name": "Alice", "role": "sponsor", "kind": "person"}]),
            ),
            [],
        )
        ent = _nodes_of_kind(g, "entity")[0]
        assert set(ent["roles"]) == {"author", "sponsor"}

    def test_event_node_per_event(self) -> None:
        g = build(_reg(_doc("c1", "A")), [Event.new("Lancé le projet", "2024-01-01")])
        events = _nodes_of_kind(g, "event")
        assert len(events) == 1
        assert events[0]["sentence"] == "Lancé le projet"
        assert events[0]["date"] == "2024-01-01"


# --- edges ------------------------------------------------------------------


class TestEdges:
    def test_doc_entity_edge_uses_role(self) -> None:
        g = build(
            _reg(_doc("c1", "A", entities=[{"name": "Bob", "role": "chef_de_projet", "kind": "person"}])),
            [],
        )
        edges = _edges_of_type(g, "chef_de_projet")
        assert edges == [{"src": "doc:c1", "dst": "entity:bob", "type": "chef_de_projet"}]

    def test_roleless_entity_defaults_to_mentioned(self) -> None:
        g = build(_reg(_doc("c1", "A", entities=[{"name": "Bob", "kind": "person"}])), [])
        assert _edges_of_type(g, "mentioned") == [
            {"src": "doc:c1", "dst": "entity:bob", "type": "mentioned"}
        ]

    def test_cooccurrence_weight_counts_shared_documents(self) -> None:
        ents = [
            {"name": "Alice", "role": "author", "kind": "person"},
            {"name": "Bob", "role": "mentioned", "kind": "person"},
        ]
        g = build(_reg(_doc("c1", "A", entities=ents), _doc("c2", "B", entities=ents)), [])
        cooc = _edges_of_type(g, "co_occurrence")
        assert len(cooc) == 1
        assert cooc[0]["weight"] == 2
        assert {cooc[0]["src"], cooc[0]["dst"]} == {"entity:alice", "entity:bob"}

    def test_no_self_cooccurrence_for_single_entity(self) -> None:
        g = build(
            _reg(_doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}])),
            [],
        )
        assert _edges_of_type(g, "co_occurrence") == []

    def test_event_mentions_known_entity(self) -> None:
        g = build(
            _reg(_doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}])),
            [Event.new("Alice a validé le cadrage", "2024-02-01")],
        )
        mentions = _edges_of_type(g, "mentions")
        assert len(mentions) == 1
        assert mentions[0]["dst"] == "entity:alice"
        assert mentions[0]["src"].startswith("event:")

    def test_event_without_known_entity_has_no_mentions_edge(self) -> None:
        g = build(
            _reg(_doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}])),
            [Event.new("Clôturé la phase 1", "2024-03-01")],
        )
        assert _edges_of_type(g, "mentions") == []


# --- reproducibility + persistence ------------------------------------------


def test_build_is_deterministic() -> None:
    reg = _reg(
        _doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]),
        _doc("c2", "B", entities=[{"name": "Bob", "role": "mentioned", "kind": "person"}]),
    )
    evs = [Event.new("Alice a lancé", "2024-01-01")]
    assert build(reg, evs).to_dict() == build(reg, evs).to_dict()


def test_load_save_roundtrip(tmp_path: Path) -> None:
    g = build(_reg(_doc("c1", "A")), [])
    path = tmp_path / ".organizer" / "graph.json"
    save(g, path)
    assert path.is_file()
    assert load(path).to_dict() == g.to_dict()


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json").to_dict() == {"nodes": [], "edges": []}


# --- actors (H3) ------------------------------------------------------------


def _author(name: str) -> dict:
    return {"name": name, "role": "author", "kind": "person"}


class TestRankActors:
    def test_empty_graph_no_actors(self) -> None:
        assert rank_actors(build(_reg(), []), 5) == []

    def test_ranked_by_document_count(self) -> None:
        g = build(
            _reg(
                _doc("c1", "A", entities=[_author("Alice")]),
                _doc("c2", "B", entities=[_author("Alice")]),
                _doc("c3", "C", entities=[_author("Bob")]),
            ),
            [],
        )
        actors = rank_actors(g, 5)
        assert [a["name"] for a in actors] == ["Alice", "Bob"]
        assert actors[0]["document_count"] == 2
        assert actors[1]["document_count"] == 1

    def test_cap_limits_results(self) -> None:
        reg = _reg(*[_doc(f"c{i}", f"T{i}", entities=[_author(f"P{i}")]) for i in range(8)])
        actors = rank_actors(build(reg, []), 3)
        assert len(actors) == 3

    def test_cap_zero_means_no_limit(self) -> None:
        reg = _reg(*[_doc(f"c{i}", f"T{i}", entities=[_author(f"P{i}")]) for i in range(8)])
        assert len(rank_actors(build(reg, []), 0)) == 8

    def test_deterministic_name_tiebreak(self) -> None:
        # equal document_count → ranked by lowercased name
        g = build(
            _reg(
                _doc("c1", "A", entities=[_author("Zoe")]),
                _doc("c2", "B", entities=[_author("Amir")]),
            ),
            [],
        )
        assert [a["name"] for a in rank_actors(g, 5)] == ["Amir", "Zoe"]

    def test_cooccurrence_breaks_doc_count_tie(self) -> None:
        # Bob and Carol each referenced by 2 docs; Bob also co-occurs with Alice.
        g = build(
            _reg(
                _doc("c1", "A", entities=[_author("Bob"), _author("Alice")]),
                _doc("c2", "B", entities=[_author("Bob"), _author("Alice")]),
                _doc("c3", "C", entities=[_author("Carol")]),
                _doc("c4", "D", entities=[_author("Carol")]),
            ),
            [],
        )
        actors = {a["name"]: a for a in rank_actors(g, 10)}
        # Bob (doc=2, cooc>0) outranks Carol (doc=2, cooc=0)
        names = [a["name"] for a in rank_actors(g, 10)]
        assert names.index("Bob") < names.index("Carol")
        assert actors["Bob"]["cooccurrence_weight"] == 2

    def test_event_mentions_counted(self) -> None:
        g = build(
            _reg(_doc("c1", "A", entities=[_author("Alice")])),
            [Event.new("Alice a validé", "2024-01-01")],
        )
        assert rank_actors(g, 5)[0]["mention_count"] == 1


# --- tool wrappers ----------------------------------------------------------


class TestGraphTools:
    @pytest.fixture()
    def paths(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        from server import registry as _registry

        reg_path = tmp_path / ".organizer" / "registry.json"
        events_path = tmp_path / ".organizer" / "events.jsonl"
        graph_path = tmp_path / ".organizer" / "graph.json"
        reg = _reg(_doc("c1", "A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]))
        _registry.save(reg, reg_path)
        return reg_path, events_path, graph_path

    def test_build_graph_persists_and_returns(self, paths: tuple[Path, Path, Path]) -> None:
        reg_path, events_path, graph_path = paths
        out = build_graph(reg_path, events_path, graph_path)
        assert graph_path.is_file()
        assert any(n["kind"] == "document" for n in out["nodes"])
        assert any(n["kind"] == "entity" for n in out["nodes"])

    def test_get_graph_reads_persisted(self, paths: tuple[Path, Path, Path]) -> None:
        reg_path, events_path, graph_path = paths
        build_graph(reg_path, events_path, graph_path)
        assert get_graph(graph_path) == build_graph(reg_path, events_path, graph_path)

    def test_get_graph_empty_when_never_built(self, tmp_path: Path) -> None:
        assert get_graph(tmp_path / "graph.json") == {"nodes": [], "edges": []}

    def test_get_actors_reads_persisted_graph(self, paths: tuple[Path, Path, Path]) -> None:
        reg_path, events_path, graph_path = paths
        build_graph(reg_path, events_path, graph_path)
        actors = get_actors(graph_path, 5)
        assert [a["name"] for a in actors] == ["Alice"]

    def test_get_actors_respects_cap(self, tmp_path: Path) -> None:
        from server import registry as _registry

        reg_path = tmp_path / "registry.json"
        events_path = tmp_path / "events.jsonl"
        graph_path = tmp_path / "graph.json"
        reg = _reg(*[_doc(f"c{i}", f"T{i}", entities=[_author(f"P{i}")]) for i in range(6)])
        _registry.save(reg, reg_path)
        build_graph(reg_path, events_path, graph_path)
        assert len(get_actors(graph_path, 2)) == 2

    def test_get_actors_empty_when_no_graph(self, tmp_path: Path) -> None:
        assert get_actors(tmp_path / "graph.json", 5) == []
