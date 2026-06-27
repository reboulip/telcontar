"""Domain profile: declarative config that adapts the engine to a document corpus.

A profile externalizes everything domain-specific (document-type vocabulary,
entity/role model, extraction guardrails, naming, synthesis template, output
sinks) so the same engine can serve different kinds of document piles by
swapping a TOML file. The bundled ``is_it_project`` profile is profile #1.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DocumentType:
    """One entry of a profile's controlled document-type vocabulary."""

    id: str
    label: str
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentType":
        if "id" not in d:
            raise ValueError(f"document_type entry missing required 'id': {d!r}")
        return cls(
            id=d["id"],
            label=d.get("label", d["id"]),
            description=d.get("description", ""),
        )


@dataclass
class Profile:
    """A loaded, validated domain profile with typed accessors."""

    name: str
    description: str
    document_types: list[DocumentType]
    entity_kinds: list[str]
    role_taxonomy: list[str]
    salient_cap: int
    extraction_required: list[str]
    extraction_optional: list[str]
    synthesis_template: str
    synthesis_title: str
    synthesis_sections: list[str]
    synthesis_instructions: str
    naming_convention: str
    naming_instructions: str
    sinks_default: list[str]

    # --- typed accessors ---------------------------------------------------

    def document_type_ids(self) -> list[str]:
        """Valid ``type`` values for this profile (used to validate records)."""
        return [dt.id for dt in self.document_types]

    def entity_roles(self) -> list[str]:
        """Allowed entity roles (e.g. author, mentioned, project roles)."""
        return list(self.role_taxonomy)

    def extraction_fields(self) -> dict[str, list[str]]:
        """Required vs optional metadata fields the analysis pass should fill."""
        return {
            "required": list(self.extraction_required),
            "optional": list(self.extraction_optional),
        }

    def naming(self) -> str:
        """The naming convention id (e.g. ``snake_case_iso_dates``)."""
        return self.naming_convention

    def synthesis(self) -> dict:
        """The project-synthesis template: name, title, ordered sections, prose rules."""
        return {
            "template": self.synthesis_template,
            "title": self.synthesis_title,
            "sections": list(self.synthesis_sections),
            "instructions": self.synthesis_instructions,
        }

    # --- (de)serialization -------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        name = d.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("Profile missing required 'name' (string)")

        raw_types = d.get("document_types", [])
        if not raw_types:
            raise ValueError(f"Profile {name!r} must define at least one [[document_types]] entry")
        document_types = [DocumentType.from_dict(t) for t in raw_types]
        ids = [t.id for t in document_types]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Profile {name!r} has duplicate document_type ids: {ids}")

        entities = d.get("entities", {})
        extraction = d.get("extraction", {})
        synthesis = d.get("synthesis", {})
        naming = d.get("naming", {})
        sinks = d.get("sinks", {})

        return cls(
            name=name,
            description=d.get("description", ""),
            document_types=document_types,
            entity_kinds=list(entities.get("kinds", ["person", "org"])),
            role_taxonomy=list(entities.get("role_taxonomy", [])),
            salient_cap=int(entities.get("salient_cap", 5)),
            extraction_required=list(extraction.get("required", [])),
            extraction_optional=list(extraction.get("optional", [])),
            synthesis_template=synthesis.get("template", ""),
            synthesis_title=synthesis.get("title", ""),
            synthesis_sections=list(synthesis.get("sections", [])),
            synthesis_instructions=synthesis.get("instructions", ""),
            naming_convention=naming.get("convention", ""),
            naming_instructions=naming.get("instructions", ""),
            sinks_default=list(sinks.get("default", ["local_markdown"])),
        )


def load_profile(name: str, profiles_dir: Path) -> Profile:
    """Load and validate ``{profiles_dir}/{name}.toml``.

    Raises FileNotFoundError if the profile file does not exist, and ValueError
    if the TOML is malformed or fails schema validation.
    """
    path = profiles_dir / f"{name}.toml"
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {name!r} (looked in {path})")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Profile.from_dict(data)
