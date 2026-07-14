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
    lookup_documents,
    record_document,
    record_document_batch,
    rehome_documents,
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


# --- record_document_batch ---------------------------------------------------


def _doc(**kw: object) -> dict:
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
    return defaults


def test_record_document_batch_records_all_valid_docs(reg_path: Path, profile: Profile) -> None:
    docs = [_doc(checksum="c1", path="/p/a.txt"), _doc(checksum="c2", path="/p/b.txt")]
    result = record_document_batch(docs, reg_path, profile)
    assert result["errors"] == []
    assert {r["checksum"] for r in result["recorded"]} == {"c1", "c2"}
    assert len(list_documents(reg_path)) == 2


def test_record_document_batch_one_bad_doc_does_not_fail_the_batch(
    reg_path: Path, profile: Profile
) -> None:
    docs = [
        _doc(checksum="c1", path="/p/a.txt"),
        _doc(checksum="c2", path="/p/b.txt", type="not_a_real_type"),
    ]
    result = record_document_batch(docs, reg_path, profile)
    assert {r["checksum"] for r in result["recorded"]} == {"c1"}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1
    assert result["errors"][0]["checksum"] == "c2"
    assert "Invalid document type" in result["errors"][0]["error"]
    assert len(list_documents(reg_path)) == 1


def test_record_document_batch_preserves_exact_validation_error_strings(
    reg_path: Path, profile: Profile
) -> None:
    docs = [
        _doc(checksum="c1", type="not_a_real_type"),
        _doc(checksum="c2", entities=[{"name": "Bob", "role": "wizard", "kind": "person"}]),
        _doc(checksum="c3", entities=[{"role": "author"}]),
    ]
    result = record_document_batch(docs, reg_path, profile)
    errors_by_checksum = {e["checksum"]: e["error"] for e in result["errors"]}
    assert "Invalid document type" in errors_by_checksum["c1"]
    assert "Invalid entity role" in errors_by_checksum["c2"]
    assert "missing 'name'" in errors_by_checksum["c3"]


def test_record_document_batch_empty_list(reg_path: Path, profile: Profile) -> None:
    result = record_document_batch([], reg_path, profile)
    assert result == {"recorded": [], "errors": []}


def test_record_document_batch_upserts_by_checksum(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1", title="First")
    result = record_document_batch([_doc(checksum="c1", title="Second")], reg_path, profile)
    assert result["recorded"][0]["title"] == "Second"
    docs = list_documents(reg_path)
    assert len(docs) == 1
    assert docs[0]["title"] == "Second"


# --- queries ----------------------------------------------------------------


def test_get_document_missing_returns_none(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1")
    assert get_document("nope", reg_path) is None


def test_lookup_documents_returns_hits_and_misses_keyed_by_input(
    reg_path: Path, profile: Profile
) -> None:
    _record(reg_path, profile, checksum="c1", title="One")
    _record(reg_path, profile, checksum="c2", title="Two")

    result = lookup_documents(["c1", "missing", "c2"], reg_path)

    assert set(result.keys()) == {"c1", "missing", "c2"}
    assert result["c1"]["title"] == "One"
    assert result["c2"]["title"] == "Two"
    assert result["missing"] is None


def test_lookup_documents_empty_input_returns_empty_dict(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1")
    assert lookup_documents([], reg_path) == {}


def test_rehome_documents_updates_path_by_checksum(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1", path="/old/a.txt")

    result = rehome_documents({"c1": "/new/a.txt"}, reg_path)

    assert result == {"updated": ["c1"], "missing": []}
    assert get_document("c1", reg_path)["path"] == "/new/a.txt"


def test_rehome_documents_reports_missing_checksum(reg_path: Path, profile: Profile) -> None:
    _record(reg_path, profile, checksum="c1", path="/a.txt")

    result = rehome_documents({"nope": "/new/a.txt"}, reg_path)

    assert result == {"updated": [], "missing": ["nope"]}
    assert get_document("c1", reg_path)["path"] == "/a.txt"


def test_rehome_documents_preserves_last_analyzed_bump(reg_path: Path, profile: Profile) -> None:
    out = _record(reg_path, profile, checksum="c1", path="/a.txt")
    first_seen = out["first_seen"]

    rehome_documents({"c1": "/b.txt"}, reg_path)

    rec = get_document("c1", reg_path)
    assert rec["first_seen"] == first_seen
    assert rec["path"] == "/b.txt"


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
