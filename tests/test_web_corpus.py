"""Tests for the corpus browser (V5): host/web/corpus.py's registry-loading
logic (plain pytest, no NiceGUI) and host/web/corpus_view.py's rendering
(NiceGUI's headless `user` fixture, shared setup from tests/web_harness.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nicegui.testing import User

from config import settings as settings_module
from host.web import corpus, session as web_session
from server.registry import DocumentRecord, Registry, save

from tests.web_harness import (  # noqa: F401
    _fast_refresh,
    _preserve_dunder_main,
    _reset_session_registry,
)


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard as tests/test_host_paths.py — resolve_registry_path routes
    through Settings(), which falls back to a real ~/.telcontar/config.env
    absent an explicit override."""
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")


def _make_record(checksum: str, title: str, **overrides: object) -> DocumentRecord:
    base = dict(
        checksum=checksum,
        path=f"{title}.pdf",
        title=title,
        type="report",
        summary=f"Summary of {title}.",
        provenance="found during scan",
        date="2026-01-01",
        entities=[{"name": "Alice", "role": "author", "kind": "person"}],
    )
    base.update(overrides)
    return DocumentRecord.new(**base)  # type: ignore[arg-type]


# ── host/web/corpus.py ───────────────────────────────────────────────────────


class TestListDocuments:
    def test_returns_empty_list_when_registry_missing(self, tmp_path: Path) -> None:
        assert corpus.list_documents(tmp_path) == []

    def test_returns_empty_list_on_corrupt_registry(self, tmp_path: Path) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("not json", encoding="utf-8")

        assert corpus.list_documents(tmp_path) == []

    def test_returns_all_records_as_dicts(self, tmp_path: Path) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        registry = Registry()
        registry.upsert(_make_record("aaa", "Report One"))
        registry.upsert(_make_record("bbb", "Report Two"))
        save(registry, registry_path)

        docs = corpus.list_documents(tmp_path)

        assert {d["checksum"] for d in docs} == {"aaa", "bbb"}
        assert all(isinstance(d, dict) for d in docs)


class TestGetDocument:
    def test_returns_none_when_registry_missing(self, tmp_path: Path) -> None:
        assert corpus.get_document(tmp_path, "aaa") is None

    def test_returns_none_when_checksum_unknown(self, tmp_path: Path) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        registry = Registry()
        registry.upsert(_make_record("aaa", "Report One"))
        save(registry, registry_path)

        assert corpus.get_document(tmp_path, "does-not-exist") is None

    def test_returns_matching_record(self, tmp_path: Path) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        registry = Registry()
        registry.upsert(_make_record("aaa", "Report One"))
        save(registry, registry_path)

        doc = corpus.get_document(tmp_path, "aaa")

        assert doc is not None
        assert doc["title"] == "Report One"


# ── host/web/corpus_view.py ──────────────────────────────────────────────────


def _seed_registry(target: Path, *records: DocumentRecord) -> None:
    registry_path = target / ".organizer" / "registry.json"
    registry = Registry()
    for record in records:
        registry.upsert(record)
    save(registry, registry_path)


async def test_corpus_page_shows_empty_state_when_no_registry(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-empty")


async def test_corpus_page_not_found_for_unknown_run_id(user: User) -> None:
    await user.open("/corpus/does-not-exist")
    await user.should_see("Run not found")


async def test_corpus_table_renders_a_row_per_document(user: User, tmp_path: Path) -> None:
    _seed_registry(
        tmp_path,
        _make_record("aaa", "Alpha Report"),
        _make_record("bbb", "Beta Report"),
    )
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-table")

    # ui.table content is invisible to should_see (no ElementFilter case for
    # Table) — assert on the rows prop directly.
    [table] = user.find(marker="corpus-table").elements
    assert {row["title"] for row in table.rows} == {"Alpha Report", "Beta Report"}


async def test_corpus_table_entities_column_is_a_string_not_a_list(
    user: User, tmp_path: Path
) -> None:
    """ui.table crashes the browser if a cell value is a list — entities
    must be pre-flattened before reaching the rows prop."""
    _seed_registry(tmp_path, _make_record("aaa", "Alpha Report"))
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-table")

    [table] = user.find(marker="corpus-table").elements
    assert isinstance(table.rows[0]["entities"], str)
    assert "Alice" in table.rows[0]["entities"]


async def test_corpus_search_filters_rows_by_title(user: User, tmp_path: Path) -> None:
    _seed_registry(
        tmp_path,
        _make_record("aaa", "Alpha Report"),
        _make_record("bbb", "Beta Invoice"),
    )
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-search")

    user.find(marker="corpus-search").type("Invoice")

    [table] = user.find(marker="corpus-table").elements
    assert [row["title"] for row in table.rows] == ["Beta Invoice"]


async def test_corpus_search_cleared_restores_all_rows(user: User, tmp_path: Path) -> None:
    _seed_registry(
        tmp_path,
        _make_record("aaa", "Alpha Report"),
        _make_record("bbb", "Beta Invoice"),
    )
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-search")

    search = user.find(marker="corpus-search")
    search.type("Invoice")
    search.clear()

    [table] = user.find(marker="corpus-table").elements
    assert len(table.rows) == 2


async def test_corpus_row_selection_populates_detail_pane(user: User, tmp_path: Path) -> None:
    _seed_registry(
        tmp_path,
        _make_record(
            "aaa",
            "Alpha Report",
            summary="A detailed summary of the alpha report.",
            provenance="found alongside build logs",
            entities=[{"name": "Alice", "role": "author", "kind": "person"}],
        ),
    )
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-table")
    await user.should_not_see(marker="corpus-detail-content")

    # ui.table has no ElementFilter case and setting `.selected` directly
    # doesn't fire the "selection" event `on_select` listens for — `.trigger`
    # dispatches straight to the registered listener the same way a real
    # client-side selection would (gotcha #7-adjacent: know your harness API
    # before reaching for a raw workaround).
    [table] = user.find(marker="corpus-table").elements
    [row] = [r for r in table.rows if r["title"] == "Alpha Report"]
    user.find(marker="corpus-table").trigger(
        "selection", {"added": True, "rows": [row], "keys": [row["checksum"]]}
    )

    await user.should_see(marker="corpus-detail-content")
    await user.should_see("Alpha Report")
    await user.should_see("A detailed summary of the alpha report.")
    await user.should_see("found alongside build logs")
    await user.should_see("Alice")


async def test_corpus_detail_pane_handles_missing_entities(user: User, tmp_path: Path) -> None:
    _seed_registry(tmp_path, _make_record("aaa", "Alpha Report", entities=[]))
    session = web_session.create(tmp_path)

    await user.open(f"/corpus/{session.run_id}")
    await user.should_see(marker="corpus-table")

    [table] = user.find(marker="corpus-table").elements
    row = table.rows[0]
    user.find(marker="corpus-table").trigger(
        "selection", {"added": True, "rows": [row], "keys": [row["checksum"]]}
    )

    await user.should_see(marker="corpus-detail-content")
    await user.should_see("None recorded.")


async def test_browse_corpus_button_visible_only_once_run_is_done(
    user: User, tmp_path: Path
) -> None:
    session = web_session.create(tmp_path)
    session.started = True
    session.done = False

    await user.open(f"/run/{session.run_id}")
    await user.should_not_see(marker="btn-browse-corpus")

    session.done = True
    await user.should_see(marker="btn-browse-corpus")
