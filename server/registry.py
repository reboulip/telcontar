"""Document registry — the engine's persistent, content-addressed memory.

A single JSON store at ``registry_path`` (default ``.organizer/registry.json``)
holding one record per unique document, keyed by its sha256 checksum. Because
the checksum is the identity, the *path* may change as files are organized while
the record (and its accumulated analysis) survives.

This module is deliberately profile-agnostic: validation of a document's ``type``
against the active profile lives in the tools layer, not here.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

DocStatus = Literal["active", "archived", "quarantined"]


@dataclass
class DocumentRecord:
    """One analyzed document. Identity = ``checksum``; ``path`` tracks location."""

    checksum: str
    path: str
    title: str
    type: str
    summary: str
    provenance: str
    date: str | None = None
    entities: list[dict] = field(default_factory=list)  # {name, role, kind}
    attributes: dict = field(default_factory=dict)
    status: DocStatus = "active"
    first_seen: str = ""
    last_analyzed: str = ""

    @classmethod
    def new(
        cls,
        checksum: str,
        path: str,
        title: str,
        type: str,
        summary: str,
        provenance: str,
        date: str | None = None,
        entities: list[dict] | None = None,
        attributes: dict | None = None,
        status: DocStatus = "active",
    ) -> "DocumentRecord":
        now = _now()
        return cls(
            checksum=checksum,
            path=path,
            title=title,
            type=type,
            summary=summary,
            provenance=provenance,
            date=date,
            entities=entities or [],
            attributes=attributes or {},
            status=status,
            first_seen=now,
            last_analyzed=now,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentRecord":
        return cls(
            checksum=d["checksum"],
            path=d["path"],
            title=d["title"],
            type=d["type"],
            summary=d["summary"],
            provenance=d.get("provenance", ""),
            date=d.get("date"),
            entities=d.get("entities", []),
            attributes=d.get("attributes", {}),
            status=d.get("status", "active"),
            first_seen=d.get("first_seen", ""),
            last_analyzed=d.get("last_analyzed", ""),
        )


@dataclass
class Registry:
    """In-memory view of the document store, keyed by checksum."""

    documents: dict[str, DocumentRecord] = field(default_factory=dict)

    # --- mutations ---------------------------------------------------------

    def upsert(self, record: DocumentRecord) -> DocumentRecord:
        """Insert or update by checksum; preserve first_seen, refresh last_analyzed."""
        existing = self.documents.get(record.checksum)
        if existing is not None:
            record.first_seen = existing.first_seen or record.first_seen
        record.last_analyzed = _now()
        self.documents[record.checksum] = record
        return record

    def set_status(self, checksum: str, status: DocStatus) -> DocumentRecord | None:
        rec = self.documents.get(checksum)
        if rec is None:
            return None
        rec.status = status
        rec.last_analyzed = _now()
        return rec

    def update_path(
        self, old_path: str, new_path: str, status: DocStatus | None = None
    ) -> DocumentRecord | None:
        """Reconcile a record's location after a move/quarantine (G5).

        Matches the record whose current ``path`` equals ``old_path`` and
        rewrites it to ``new_path`` (optionally flipping status). Returns None
        if no record currently lives at ``old_path`` (registry-optional reconcile).
        """
        for rec in self.documents.values():
            if _same_path(rec.path, old_path):
                rec.path = new_path
                if status is not None:
                    rec.status = status
                rec.last_analyzed = _now()
                return rec
        return None

    # --- queries -----------------------------------------------------------

    def get(self, checksum: str) -> DocumentRecord | None:
        return self.documents.get(checksum)

    def records(self) -> list[DocumentRecord]:
        return sorted(self.documents.values(), key=lambda r: (r.first_seen, r.checksum))

    def find_modified(self) -> list[list[DocumentRecord]]:
        """Groups sharing a normalized title — same title, different content.

        Records are keyed by checksum, so any same-title group inherently has
        differing checksums: the "modifié récemment (même titre, checksum ≠)" case.
        """
        groups: dict[str, list[DocumentRecord]] = {}
        for rec in self.documents.values():
            key = _normalize_title(rec.title)
            if key:
                groups.setdefault(key, []).append(rec)
        return [
            sorted(g, key=lambda r: r.last_analyzed)
            for g in groups.values()
            if len(g) > 1
        ]

    def find_duplicates(self, threshold: float = 0.6) -> list[list[DocumentRecord]]:
        """Fuzzy candidate duplicate clusters for the host to judge.

        Pre-grouping heuristic (server stays free of semantic judgement): cluster
        records by title-token Jaccard similarity within the same document type,
        or on an exact normalized-title match across types. Returns clusters of
        size > 1; the host decides "même information / susceptible d'être remplacé".
        """
        recs = list(self.documents.values())
        parent = list(range(len(recs)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            parent[find(i)] = find(j)

        for i in range(len(recs)):
            ni = _normalize_title(recs[i].title)
            ti = _title_tokens(recs[i].title)
            for j in range(i + 1, len(recs)):
                nj = _normalize_title(recs[j].title)
                if ni and ni == nj:
                    union(i, j)
                    continue
                if recs[i].type == recs[j].type:
                    tj = _title_tokens(recs[j].title)
                    if ti and tj and _jaccard(ti, tj) >= threshold:
                        union(i, j)

        clusters: dict[int, list[DocumentRecord]] = {}
        for idx, rec in enumerate(recs):
            clusters.setdefault(find(idx), []).append(rec)
        return [g for g in clusters.values() if len(g) > 1]

    # --- (de)serialization -------------------------------------------------

    def to_dict(self) -> dict:
        return {"documents": {k: v.to_dict() for k, v in self.documents.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Registry":
        docs = {k: DocumentRecord.from_dict(v) for k, v in d.get("documents", {}).items()}
        return cls(documents=docs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\.[a-z0-9]{1,5}$", "", t)  # drop a trailing extension-like suffix
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def _title_tokens(title: str) -> set[str]:
    return set(_normalize_title(title).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def load(registry_path: Path) -> Registry:
    """Load the registry; returns an empty Registry if the file does not exist."""
    if not registry_path.is_file():
        return Registry()
    return Registry.from_dict(json.loads(registry_path.read_text(encoding="utf-8")))


def save(registry: Registry, registry_path: Path) -> None:
    """Persist the registry to disk (pretty JSON, Unicode preserved)."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(registry.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
