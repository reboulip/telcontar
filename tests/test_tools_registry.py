"""Tests for the document-registry server tools (record_document + queries)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.profile import Profile, load_profile
from server.tools import (
    find_duplicates,
    find_modified_documents,
    get_document,
    get_registry,
    list_documents,
    record_document,
)

_BUNDLED_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


@pytest.fixture()
def profile() -> Profile:
    return load_profile("is_it_project", _BUNDLED_PROFILES_DIR)


@pytest.fixture()
def reg_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "registry.json"


def _record(reg_path: Path, profile: Profile, **kw: object) -> dict:
    defaults: dict = dict(
        checksum="c1",
        path="/p/a.txt",
        title="Titre",
        type="notes",
        summary="résumé",
        provenance="apport",
        date=None,
        entities=None,
        attributes=None,
        status="active",
    )
    defaults.update(kw)
    return record_document(registry_path=reg_path, profile=profile, **defaults)  # type: ignore[arg-type]


# --- record_document --------------------------------------------------------


def test_record_document_persists(reg_path: Path, profile: Profile) -> None:
    out = _record(reg_path, profile)
    assert out["checksum"] == "c1"
    assert out["status"] == "active"
    assert reg_path.is_file()
    assert get_document("c1", reg_path) is not None


def test_record_document_rejects_invalid_type(reg_path: Path, profile: Profile) -> None:
    with pytest.raises(ValueError, match="Invalid document type"):
        _record(reg_path, profile, type="not_a_real_type")


def test_record_document_accepts_profile_types(reg_path: Path, profile: Profile) -> None:
    for t in profile.document_type_ids():
        _record(reg_path, profile, checksum=f"c-{t}", type=t)
    assert len(list_documents(reg_path)) == len(profile.document_type_ids())


def test_record_document_validates_entity_role(reg_path: Path, profile: Profile) -> None:
    with pytest.raises(ValueError, match="Invalid entity role"):
        _record(
            reg_path,
            profile,
            entities=[{"name": "Bob", "role": "wizard", "kind": "person"}],
        )


def test_record_document_accepts_author_entity(reg_path: Path, profile: Profile) -> None:
    out = _record(
        reg_path,
        profile,
        entities=[{"name": "Alice", "role": "author", "kind": "person"}],
    )
    assert out["entities"] == [{"name": "Alice", "role": "author", "kind": "person"}]


def test_record_document_rejects_entity_without_name(reg_path: Path, profile: Profile) -> None:
    with pytest.raises(ValueError, match="missing 'name'"):
        _record(reg_path, profile, entities=[{"role": "author"}])


def test_record_document_upserts_by_checksum(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1", title="First")
    _record(reg_path, profile, checksum="c1", title="Second")
    docs = list_documents(reg_path)
    assert len(docs) == 1
    assert docs[0]["title"] == "Second"


# --- queries ----------------------------------------------------------------


def test_get_document_missing_returns_none(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1")
    assert get_document("nope", reg_path) is None


def test_get_registry_shape(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1")
    reg = get_registry(reg_path)
    assert "documents" in reg and "c1" in reg["documents"]


def test_find_modified_documents(reg_path: Path, profile: Profile) -> None:
    _record(
        reg_path,
        profile,
        checksum="c1",
        path="/p/copil.pptx",
        title="COPIL Projet X",
        type="support_copil",
    )
    _record(
        reg_path,
        profile,
        checksum="c2",
        path="/p/copil2.pptx",
        title="COPIL Projet X",
        type="support_copil",
    )
    groups = find_modified_documents(reg_path)
    assert len(groups) == 1
    assert {r["checksum"] for r in groups[0]} == {"c1", "c2"}


def test_find_duplicates(reg_path: Path, profile: Profile) -> None:
    _record(
        reg_path,
        profile,
        checksum="c1",
        path="/p/v1.pptx",
        title="Support COPIL Projet Apollo v1",
        type="support_copil",
    )
    _record(
        reg_path,
        profile,
        checksum="c2",
        path="/p/v2.pptx",
        title="Support COPIL Projet Apollo v2",
        type="support_copil",
    )
    clusters = find_duplicates(reg_path)
    assert len(clusters) == 1
    assert {r["checksum"] for r in clusters[0]} == {"c1", "c2"}
