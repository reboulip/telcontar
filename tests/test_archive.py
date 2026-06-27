"""Tests for the archived-documents journal (server/archive.py + archive tools)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.archive import ArchiveEntry, all_entries, append
from server.registry import DocumentRecord, Registry
from server.registry import load as load_reg
from server.registry import save as save_reg
from server.tools import archive_document, list_archived, undo_last


# --- ArchiveEntry + module helpers ------------------------------------------


@pytest.fixture()
def archive_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "archive.jsonl"


class TestArchiveModule:
    def test_new_stamps_archived_at(self) -> None:
        e = ArchiveEntry.new("c1", "Titre", "obsolète", "/a.txt", "/q/a.txt")
        assert e.archived_at
        assert e.dst == "/q/a.txt"

    def test_append_creates_and_reads_back(self, archive_path: Path) -> None:
        append(archive_path, ArchiveEntry.new("c1", "T", "r", "/a", "/q/a"))
        assert archive_path.is_file()
        assert all_entries(archive_path)[0]["checksum"] == "c1"

    def test_all_entries_empty_when_missing(self, archive_path: Path) -> None:
        assert all_entries(archive_path) == []

    def test_unicode_preserved(self, archive_path: Path) -> None:
        append(archive_path, ArchiveEntry.new("c1", "Réunion", "périmé", "/é", None))
        line = archive_path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["title"] == "Réunion"


# --- archive_document tool --------------------------------------------------


def _setup(tmp_path: Path, *, with_file: bool = True) -> dict:
    reg_path = tmp_path / ".organizer" / "registry.json"
    quarantine = tmp_path / "_quarantine"
    journal = tmp_path / ".organizer" / "journal.jsonl"
    archive = tmp_path / ".organizer" / "archive.jsonl"
    docfile = tmp_path / "docs" / "a.txt"
    docfile.parent.mkdir(parents=True, exist_ok=True)
    if with_file:
        docfile.write_text("hello", encoding="utf-8")
    rec = DocumentRecord.new(
        checksum="c1",
        path=str(docfile),
        title="Titre A",
        type="notes",
        summary="s",
        provenance="p",
    )
    save_reg(Registry(documents={"c1": rec}), reg_path)
    return {
        "reg_path": reg_path,
        "quarantine": quarantine,
        "journal": journal,
        "archive": archive,
        "docfile": docfile,
    }


def _archive(ctx: dict, reason: str = "obsolète") -> dict:
    return archive_document(
        "c1", reason, ctx["reg_path"], ctx["quarantine"], ctx["journal"], ctx["archive"]
    )


class TestArchiveDocument:
    def test_flips_registry_status(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        _archive(ctx)
        rec = load_reg(ctx["reg_path"]).get("c1")
        assert rec is not None and rec.status == "archived"

    def test_moves_file_to_quarantine(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        out = _archive(ctx)
        assert not ctx["docfile"].exists()
        assert out["moved"] is not None
        assert Path(out["moved"]).is_file()
        assert Path(out["moved"]).parent == ctx["quarantine"]

    def test_updates_record_path_to_quarantine(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        out = _archive(ctx)
        rec = load_reg(ctx["reg_path"]).get("c1")
        assert rec is not None and rec.path == out["moved"]

    def test_writes_archive_log_entry(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        _archive(ctx, reason="doublon")
        entries = list_archived(ctx["archive"])
        assert len(entries) == 1
        assert entries[0]["checksum"] == "c1"
        assert entries[0]["title"] == "Titre A"
        assert entries[0]["reason"] == "doublon"

    def test_journals_quarantine_op_for_undo(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        _archive(ctx)
        lines = [
            json.loads(ln)
            for ln in ctx["journal"].read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert lines[-1]["op_type"] == "quarantine"
        assert lines[-1]["reason"] == "archive_document"

    def test_undo_last_reverses_archive_move(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        _archive(ctx)
        result = undo_last(ctx["journal"], tmp_path / "plans")
        assert result.get("undone")
        assert ctx["docfile"].is_file()  # file restored to original location

    def test_missing_checksum_raises(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path)
        with pytest.raises(ValueError, match="No document recorded"):
            archive_document(
                "nope", "", ctx["reg_path"], ctx["quarantine"], ctx["journal"], ctx["archive"]
            )

    def test_missing_file_still_archives(self, tmp_path: Path) -> None:
        ctx = _setup(tmp_path, with_file=False)
        out = _archive(ctx)
        assert out["moved"] is None
        rec = load_reg(ctx["reg_path"]).get("c1")
        assert rec is not None and rec.status == "archived"
        assert len(list_archived(ctx["archive"])) == 1

    def test_list_archived_empty_when_none(self, tmp_path: Path) -> None:
        assert list_archived(tmp_path / "archive.jsonl") == []
