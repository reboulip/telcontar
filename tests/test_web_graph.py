"""Tests for the knowledge-graph view (Y1): host/web/graph.py's load/
projection logic (plain pytest, no NiceGUI) and host/web/graph_view.py's
rendering (NiceGUI's headless `user` fixture, shared setup from
tests/web_harness.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nicegui.testing import User

from config import settings as settings_module
from host.web import graph as web_graph
from server.events import Event, append as append_event
from server.registry import DocumentRecord, Registry, save

from tests.web_harness import (  # noqa: F401
    _fast_refresh,
    _preserve_dunder_main,
    _reset_session_registry,
)


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard as tests/test_web_corpus.py — resolve_registry_path/
    resolve_events_path route through Settings(), which falls back to a real
    ~/.telcontar/config.env absent an explicit override."""
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")


def _make_record(checksum: str, title: str, entities: list[dict] | None = None) -> DocumentRecord:
    return DocumentRecord.new(
        checksum=checksum,
        path=f"{title}.pdf",
        title=title,
        type="report",
        summary=f"Summary of {title}.",
        provenance="found during scan",
        date="2026-01-01",
        entities=entities or [{"name": "Alice", "role": "author", "kind": "person"}],
    )


def _registry_path(target: Path) -> Path:
    return target / ".organizer" / "registry.json"


def _events_path(target: Path) -> Path:
    return target / ".organizer" / "events.jsonl"


# ── host/web/graph.py ────────────────────────────────────────────────────────


class TestLoadGraph:
    def test_returns_empty_graph_when_registry_missing(self, tmp_path: Path) -> None:
        graph = web_graph.load_graph(tmp_path)
        assert graph == {"nodes": [], "edges": []}

    def test_builds_from_registry_and_events(self, tmp_path: Path) -> None:
        registry = Registry()
        registry.upsert(_make_record("aaa", "Report One"))
        save(registry, _registry_path(tmp_path))
        append_event(_events_path(tmp_path), Event.new("Alice signed off the report."))

        graph = web_graph.load_graph(tmp_path)

        assert graph is not None
        kinds = {n["kind"] for n in graph["nodes"]}
        assert "document" in kinds
        assert "entity" in kinds
        assert "event" in kinds

    def test_returns_none_on_corrupt_registry(self, tmp_path: Path) -> None:
        registry_path = _registry_path(tmp_path)
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("not json", encoding="utf-8")

        assert web_graph.load_graph(tmp_path) is None


class TestGraphMtime:
    def test_none_when_registry_missing(self, tmp_path: Path) -> None:
        assert web_graph.graph_mtime(tmp_path) is None

    def test_none_when_events_missing(self, tmp_path: Path) -> None:
        save(Registry(), _registry_path(tmp_path))
        assert web_graph.graph_mtime(tmp_path) is None

    def test_returns_a_tuple_when_both_present(self, tmp_path: Path) -> None:
        save(Registry(), _registry_path(tmp_path))
        append_event(_events_path(tmp_path), Event.new("x"))

        assert web_graph.graph_mtime(tmp_path) is not None

    def test_changes_when_registry_is_touched(self, tmp_path: Path) -> None:
        save(Registry(), _registry_path(tmp_path))
        append_event(_events_path(tmp_path), Event.new("x"))
        first = web_graph.graph_mtime(tmp_path)

        registry = Registry()
        registry.upsert(_make_record("aaa", "New Doc"))
        save(registry, _registry_path(tmp_path))

        assert web_graph.graph_mtime(tmp_path) != first


class TestRankActorsFor:
    def test_empty_when_no_registry(self, tmp_path: Path) -> None:
        assert web_graph.rank_actors_for(tmp_path, 50) == []

    def test_ranks_actors_by_document_count(self, tmp_path: Path) -> None:
        registry = Registry()
        registry.upsert(
            _make_record(
                "aaa",
                "Doc A",
                entities=[{"name": "Popular", "role": "author", "kind": "person"}],
            )
        )
        registry.upsert(
            _make_record(
                "bbb",
                "Doc B",
                entities=[{"name": "Popular", "role": "author", "kind": "person"}],
            )
        )
        registry.upsert(
            _make_record(
                "ccc",
                "Doc C",
                entities=[{"name": "Rare", "role": "mentioned", "kind": "person"}],
            )
        )
        save(registry, _registry_path(tmp_path))

        actors = web_graph.rank_actors_for(tmp_path, 50)

        assert actors[0]["name"] == "Popular"
        assert actors[0]["document_count"] == 2


class TestProject:
    def test_filters_by_kind(self) -> None:
        graph = {
            "nodes": [
                {"id": "doc:1", "kind": "document"},
                {"id": "entity:a", "kind": "entity", "name": "A"},
                {"id": "event:1", "kind": "event"},
            ],
            "edges": [],
        }

        nodes, edges = web_graph.project(graph, kinds={"document"}, top_actors=50)

        assert [n["id"] for n in nodes] == ["doc:1"]

    def test_edges_only_kept_when_both_endpoints_survive(self) -> None:
        graph = {
            "nodes": [
                {"id": "doc:1", "kind": "document"},
                {"id": "entity:a", "kind": "entity", "name": "A"},
            ],
            "edges": [{"src": "doc:1", "dst": "entity:a", "type": "author"}],
        }

        nodes, edges = web_graph.project(graph, kinds={"document"}, top_actors=50)

        assert edges == []  # entity:a was filtered out, so this edge can't survive

    def test_caps_entities_by_document_count(self) -> None:
        graph = {
            "nodes": [
                {"id": "doc:1", "kind": "document"},
                {"id": "entity:a", "kind": "entity", "name": "A"},
                {"id": "entity:b", "kind": "entity", "name": "B"},
            ],
            "edges": [
                {"src": "doc:1", "dst": "entity:a", "type": "author"},
                {"src": "doc:1", "dst": "entity:b", "type": "mentioned"},
                {"src": "doc:1", "dst": "entity:b", "type": "mentioned2"},
            ],
        }

        nodes, edges = web_graph.project(graph, kinds={"document", "entity"}, top_actors=1)

        entity_ids = {n["id"] for n in nodes if n["kind"] == "entity"}
        assert entity_ids == {"entity:b"}  # b has 2 doc-edges from doc:1, a has only 1


class TestNeighbors:
    def test_returns_immediate_neighbors_either_direction(self) -> None:
        graph = {
            "nodes": [
                {"id": "doc:1", "kind": "document", "title": "Doc"},
                {"id": "entity:a", "kind": "entity", "name": "A"},
            ],
            "edges": [{"src": "doc:1", "dst": "entity:a", "type": "author"}],
        }

        assert web_graph.neighbors(graph, "entity:a") == [
            {"id": "doc:1", "kind": "document", "title": "Doc"}
        ]
        assert web_graph.neighbors(graph, "doc:1") == [
            {"id": "entity:a", "kind": "entity", "name": "A"}
        ]

    def test_empty_for_unconnected_node(self) -> None:
        graph = {"nodes": [{"id": "entity:a", "kind": "entity", "name": "A"}], "edges": []}

        assert web_graph.neighbors(graph, "entity:a") == []


# ── host/web/graph_view.py ───────────────────────────────────────────────────


async def test_graph_page_shows_empty_state_when_no_registry(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")

    await user.should_see(marker="graph-empty")
    await user.should_see(marker="graph-table")
    [table] = user.find(marker="graph-table").elements
    assert table.rows == []


async def test_graph_table_shows_ranked_actors(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    registry = Registry()
    registry.upsert(
        _make_record(
            "aaa", "Doc A", entities=[{"name": "Popular", "role": "author", "kind": "person"}]
        )
    )
    save(registry, _registry_path(tmp_path))
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")

    # ui.table's row data lives in a `rows` prop, not as individually
    # rendered/content-searchable elements — should_see("Popular") would
    # never find it. should_see(marker=...) is what gives this the same
    # retry-until-rendered safety; the actual assertion reads table.rows.
    await user.should_see(marker="graph-table")
    [table] = user.find(marker="graph-table").elements
    assert table.rows == [
        {
            "id": "entity:popular",
            "name": "Popular",
            "entity_kind": "person",
            "roles": "author",
            "document_count": 1,
            "cooccurrence_weight": 0,
            "mention_count": 0,
        }
    ]


async def test_graph_events_are_excluded_by_default(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    registry = Registry()
    registry.upsert(_make_record("aaa", "Doc A"))
    save(registry, _registry_path(tmp_path))
    append_event(_events_path(tmp_path), Event.new("Alice did something notable."))
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")
    await user.should_see(marker="graph-filter-event")

    [event_checkbox] = user.find(marker="graph-filter-event").elements
    assert event_checkbox.value is False


async def test_graph_entity_click_shows_detail_pane(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    registry = Registry()
    registry.upsert(
        _make_record(
            "aaa", "Doc A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]
        )
    )
    save(registry, _registry_path(tmp_path))
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")
    await user.should_see(marker="graph-table")

    [table] = user.find(marker="graph-table").elements
    [row] = [r for r in table.rows if r["name"] == "Alice"]
    user.find(marker="graph-table").trigger("rowClick", [{}, row, 0])

    await user.should_see(marker="graph-entity-detail")
    await user.should_see("Alice")


async def test_graph_document_click_reuses_docpane_markers(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    registry = Registry()
    registry.upsert(
        _make_record(
            "aaa", "Doc A", entities=[{"name": "Alice", "role": "author", "kind": "person"}]
        )
    )
    save(registry, _registry_path(tmp_path))
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")
    await user.should_see(marker="graph-table")

    [table] = user.find(marker="graph-table").elements
    [row] = [r for r in table.rows if r["name"] == "Alice"]
    user.find(marker="graph-table").trigger("rowClick", [{}, row, 0])
    await user.should_see(marker="graph-entity-doc-link")

    user.find(marker="graph-entity-doc-link").click()

    await user.should_see(marker="graph-detail-content")
    await user.should_see("Doc A")


async def test_graph_show_force_graph_toggle_renders_echart(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    registry = Registry()
    registry.upsert(_make_record("aaa", "Doc A"))
    save(registry, _registry_path(tmp_path))
    session = web_session.create(tmp_path)
    session.started = True
    session.done = True

    await user.open(f"/graph/{session.run_id}")
    await user.should_see(marker="graph-show-echart")
    await user.should_not_see(marker="graph-echart")

    user.find(marker="graph-show-echart").click()

    await user.should_see(marker="graph-echart")


async def test_graph_nav_tab_disabled_without_organizer_root(user: User, tmp_path: Path) -> None:
    from host.web import session as web_session

    session = web_session.create(tmp_path)
    session.started = True

    await user.open(f"/run/{session.run_id}")
    await user.should_see(marker="nav-graph")

    [graph_tab] = user.find(marker="nav-graph").elements
    assert graph_tab.enabled is False
