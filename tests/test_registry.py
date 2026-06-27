"""Tests for the document registry (server/registry.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server import registry
from server.registry import DocumentRecord, Registry


def _rec(checksum: str, path: str, title: str, type: str = "notes", **kw: object) -> DocumentRecord:
    return DocumentRecord.new(
        checksum=checksum,
        path=path,
        title=title,
        type=type,
        summary=kw.get("summary", "s"),  # type: ignore[arg-type]
        provenance=kw.get("provenance", "p"),  # type: ignore[arg-type]
        date=kw.get("date"),  # type: ignore[arg-type]
        entities=kw.get("entities"),  # type: ignore[arg-type]
    )


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "registry.json"


# --- CRUD + persistence -----------------------------------------------------


def test_load_missing_returns_empty(registry_path: Path) -> None:
    reg = registry.load(registry_path)
    assert isinstance(reg, Registry)
    assert reg.records() == []


def test_upsert_and_get() -> None:
    reg = Registry()
    rec = reg.upsert(_rec("abc", "/p/a.txt", "Note A"))
    assert reg.get("abc") is rec
    assert reg.get("missing") is None


def test_upsert_same_checksum_preserves_first_seen() -> None:
    reg = Registry()
    first = reg.upsert(_rec("abc", "/p/a.txt", "Note A"))
    original_first_seen = first.first_seen
    updated = reg.upsert(_rec("abc", "/p/a.txt", "Note A — refined"))
    assert updated.first_seen == original_first_seen
    assert updated.title == "Note A — refined"
    assert len(reg.records()) == 1


def test_save_load_round_trip(registry_path: Path) -> None:
    reg = Registry()
    reg.upsert(
        _rec(
            "abc",
            "/p/a.txt",
            "Café résumé",
            date="2026-01-02",
            entities=[{"name": "Alice", "role": "author", "kind": "person"}],
        )
    )
    registry.save(reg, registry_path)

    loaded = registry.load(registry_path)
    rec = loaded.get("abc")
    assert rec is not None
    assert rec.title == "Café résumé"
    assert rec.date == "2026-01-02"
    assert rec.entities == [{"name": "Alice", "role": "author", "kind": "person"}]


# --- status + path reconcile (G5 support) -----------------------------------


def test_set_status() -> None:
    reg = Registry()
    reg.upsert(_rec("abc", "/p/a.txt", "Note A"))
    rec = reg.set_status("abc", "archived")
    assert rec is not None and rec.status == "archived"
    assert reg.set_status("nope", "archived") is None


def test_update_path_keeps_identity() -> None:
    """Checksum is identity; the path tracks a move while the record survives."""
    reg = Registry()
    reg.upsert(_rec("abc", "/old/dir/a.txt", "Note A"))
    rec = reg.update_path("/old/dir/a.txt", "/new/dir/a.txt", status="active")
    assert rec is not None
    assert rec.checksum == "abc"
    assert rec.path == "/new/dir/a.txt"


def test_update_path_to_quarantine() -> None:
    reg = Registry()
    reg.upsert(_rec("abc", "/p/junk.txt", "Junk"))
    rec = reg.update_path("/p/junk.txt", "/p/_quarantine/junk.txt", status="quarantined")
    assert rec is not None and rec.status == "quarantined"


def test_update_path_no_match_returns_none() -> None:
    reg = Registry()
    reg.upsert(_rec("abc", "/p/a.txt", "Note A"))
    assert reg.update_path("/nowhere/x.txt", "/elsewhere/x.txt") is None


# --- find_modified ----------------------------------------------------------


def test_find_modified_same_title_different_checksum() -> None:
    reg = Registry()
    reg.upsert(_rec("c1", "/p/copil.pptx", "COPIL Projet X"))
    reg.upsert(_rec("c2", "/p/copil_v2.pptx", "COPIL Projet X"))
    reg.upsert(_rec("c3", "/p/other.txt", "Autre sujet"))
    groups = reg.find_modified()
    assert len(groups) == 1
    assert {r.checksum for r in groups[0]} == {"c1", "c2"}


def test_find_modified_ignores_singletons() -> None:
    reg = Registry()
    reg.upsert(_rec("c1", "/p/a.txt", "Unique title"))
    assert reg.find_modified() == []


# --- find_duplicates (fuzzy pre-grouping) -----------------------------------


def test_find_duplicates_clusters_similar_titles_same_type() -> None:
    reg = Registry()
    reg.upsert(_rec("c1", "/p/v1.pptx", "Support COPIL Projet Apollo v1", type="support_copil"))
    reg.upsert(_rec("c2", "/p/v2.pptx", "Support COPIL Projet Apollo v2", type="support_copil"))
    reg.upsert(_rec("c3", "/p/x.txt", "Recette des sardines", type="notes"))
    clusters = reg.find_duplicates()
    assert len(clusters) == 1
    assert {r.checksum for r in clusters[0]} == {"c1", "c2"}


def test_find_duplicates_exact_title_across_types() -> None:
    reg = Registry()
    reg.upsert(_rec("c1", "/p/a.docx", "Plan de migration", type="document_de_travail"))
    reg.upsert(_rec("c2", "/p/b.pdf", "Plan de migration", type="draft_officiel"))
    clusters = reg.find_duplicates()
    assert len(clusters) == 1
    assert {r.checksum for r in clusters[0]} == {"c1", "c2"}


def test_find_duplicates_no_false_grouping() -> None:
    reg = Registry()
    reg.upsert(_rec("c1", "/p/a.txt", "Budget janvier", type="notes"))
    reg.upsert(_rec("c2", "/p/b.txt", "Compte rendu réunion sécurité", type="notes"))
    assert reg.find_duplicates() == []
