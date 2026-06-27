"""Tests for the domain profile loader (server/profile.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.profile import DocumentType, Profile, load_profile

# Repo-root/profiles, resolved from this test file so cwd does not matter.
_BUNDLED_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"

_MINIMAL_TOML = """\
name = "test_profile"
description = "A tiny test profile."

[[document_types]]
id = "note"
label = "Note"
description = "A note."

[[document_types]]
id = "report"
label = "Report"

[entities]
kinds = ["person"]
role_taxonomy = ["author", "mentioned"]
salient_cap = 3

[extraction]
required = ["title", "summary"]
optional = ["date"]

[synthesis]
template = "generic"

[naming]
convention = "snake_case"
instructions = "Use snake_case."

[sinks]
default = ["local_markdown"]
"""


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "test_profile.toml").write_text(_MINIMAL_TOML, encoding="utf-8")
    return d


# --- loading ----------------------------------------------------------------


def test_load_profile_reads_and_parses(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert isinstance(p, Profile)
    assert p.name == "test_profile"
    assert p.description == "A tiny test profile."


def test_load_profile_missing_raises(profiles_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_profile("does_not_exist", profiles_dir)


# --- accessors --------------------------------------------------------------


def test_document_type_ids(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert p.document_type_ids() == ["note", "report"]


def test_entity_roles(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert p.entity_roles() == ["author", "mentioned"]


def test_salient_cap(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert p.salient_cap == 3


def test_extraction_fields(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert p.extraction_fields() == {"required": ["title", "summary"], "optional": ["date"]}


def test_naming(profiles_dir: Path) -> None:
    p = load_profile("test_profile", profiles_dir)
    assert p.naming() == "snake_case"


def test_synthesis_accessor_defaults_when_minimal(profiles_dir: Path) -> None:
    # minimal profile only declares template = "generic"; title/sections absent
    p = load_profile("test_profile", profiles_dir)
    syn = p.synthesis()
    assert syn["template"] == "generic"
    assert syn["title"] == ""
    assert syn["sections"] == []
    assert syn["instructions"] == ""


def test_synthesis_fields_absent_default_empty() -> None:
    p = Profile.from_dict({"name": "p", "document_types": [{"id": "a"}]})
    assert p.synthesis_title == ""
    assert p.synthesis_sections == []
    assert p.synthesis_instructions == ""


def test_document_type_label_defaults_to_id() -> None:
    dt = DocumentType.from_dict({"id": "x"})
    assert dt.label == "x"
    assert dt.description == ""


# --- validation -------------------------------------------------------------


def test_from_dict_missing_name_raises() -> None:
    with pytest.raises(ValueError, match="name"):
        Profile.from_dict({"document_types": [{"id": "a"}]})


def test_from_dict_no_document_types_raises() -> None:
    with pytest.raises(ValueError, match="document_types"):
        Profile.from_dict({"name": "p"})


def test_from_dict_duplicate_ids_raises() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Profile.from_dict({"name": "p", "document_types": [{"id": "a"}, {"id": "a"}]})


def test_document_type_missing_id_raises() -> None:
    with pytest.raises(ValueError, match="id"):
        DocumentType.from_dict({"label": "no id here"})


def test_from_dict_applies_defaults() -> None:
    p = Profile.from_dict({"name": "p", "document_types": [{"id": "a"}]})
    assert p.entity_kinds == ["person", "org"]
    assert p.salient_cap == 5
    assert p.sinks_default == ["local_markdown"]
    assert p.extraction_required == []


# --- bundled profile #1 -----------------------------------------------------


def test_bundled_is_it_project_loads() -> None:
    p = load_profile("is_it_project", _BUNDLED_PROFILES_DIR)
    assert p.name == "is_it_project"
    # the nine French document types
    assert p.document_type_ids() == [
        "communication_formelle",
        "releve_de_decision",
        "document_de_travail",
        "support_copil",
        "support_reunion",
        "draft_officiel",
        "notes",
        "echanges",
        "autre",
    ]
    assert p.salient_cap == 5
    assert p.extraction_fields() == {
        "required": ["title", "summary", "provenance"],
        "optional": ["date", "author"],
    }
    assert "author" in p.entity_roles()
    assert p.naming() == "snake_case_iso_dates"
    assert p.sinks_default == ["local_markdown"]


def test_bundled_is_it_project_synthesis_template() -> None:
    p = load_profile("is_it_project", _BUNDLED_PROFILES_DIR)
    syn = p.synthesis()
    assert syn["template"] == "project_synthesis"
    assert syn["title"] == "Synthèse du projet"
    # ordered narrative sections are present
    assert len(syn["sections"]) >= 5
    assert any("Acteurs" in s for s in syn["sections"])
    assert any("Chronologie" in s for s in syn["sections"])
    assert syn["instructions"].strip()
