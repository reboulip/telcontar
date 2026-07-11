"""Tests for propose_rename, propose_move, propose_quarantine, and the M1
plan-flow extensions: propose_create_file, propose_update_file, propose_create_dir,
propose_archive_document, propose_compress_quarantine."""

from __future__ import annotations

from pathlib import Path

import pytest

from server import registry as _registry
from server.plan import Plan, load, save
from server.registry import DocumentRecord
from server.tools import (
    propose_archive_document,
    propose_compress_quarantine,
    propose_create_dir,
    propose_create_file,
    propose_move,
    propose_quarantine,
    propose_rename,
    propose_update_file,
)


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def pending_plan(plans_dir: Path) -> Plan:
    p = Plan.new()
    save(p, plans_dir)
    return p


class TestProposeRename:
    def test_appends_op_and_returns_dict(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "old.txt"
        src.write_text("x")
        result = propose_rename(str(src), "new.txt", pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "rename"
        assert result["src"] == str(src)
        assert result["dst"] == "new.txt"
        assert result["status"] == "pending"
        assert result["ops_count"] == 1

    def test_persists_op_to_disk(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "a.txt"
        src.write_text("x")
        propose_rename(str(src), "b.txt", pending_plan.plan_id, plans_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert len(reloaded.ops) == 1
        assert reloaded.ops[0].op_type == "rename"
        assert reloaded.ops[0].dst == "b.txt"

    def test_raises_if_destination_already_exists(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "a.txt"
        src.write_text("x")
        (tmp_path / "b.txt").write_text("existing")
        with pytest.raises(FileExistsError):
            propose_rename(str(src), "b.txt", pending_plan.plan_id, plans_dir)

    def test_raises_if_source_not_found(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        with pytest.raises(FileNotFoundError):
            propose_rename(
                str(tmp_path / "missing.txt"), "new.txt", pending_plan.plan_id, plans_dir
            )

    def test_raises_if_plan_not_pending(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "a.txt"
        src.write_text("x")
        with pytest.raises(ValueError, match="pending"):
            propose_rename(str(src), "b.txt", pending_plan.plan_id, plans_dir)

    def test_multiple_ops_accumulate(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_text("x")
            propose_rename(str(f), f"renamed{i}.txt", pending_plan.plan_id, plans_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert len(reloaded.ops) == 3


class TestProposeMove:
    def test_appends_op_and_returns_dict(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        result = propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "move"
        assert result["src"] == str(src)
        assert result["dst"] == str(dst_dir)
        assert result["status"] == "pending"
        assert result["ops_count"] == 1

    def test_persists_op_to_disk(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert len(reloaded.ops) == 1
        assert reloaded.ops[0].op_type == "move"

    def test_raises_if_destination_already_exists(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        (dst_dir / "file.txt").write_text("existing")
        with pytest.raises(FileExistsError):
            propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)

    def test_raises_if_source_not_a_file(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        with pytest.raises(ValueError, match="Not a file"):
            propose_move(
                str(tmp_path / "missing.txt"), str(dst_dir), pending_plan.plan_id, plans_dir
            )

    def test_raises_if_dest_not_a_directory(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            propose_move(str(src), str(tmp_path / "nonexistent"), pending_plan.plan_id, plans_dir)

    def test_raises_if_plan_not_pending(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        with pytest.raises(ValueError, match="pending"):
            propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)

    def test_allows_move_to_dir_queued_for_creation(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "not-yet-created"
        propose_create_dir(str(dst_dir), pending_plan.plan_id, plans_dir)
        result = propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "move"
        assert result["dst"] == str(dst_dir)
        assert result["ops_count"] == 2


class TestProposeQuarantine:
    def test_appends_op_and_returns_dict(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_quarantine"
        result = propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        assert result["op_type"] == "quarantine"
        assert result["src"] == str(src)
        assert "junk.txt" in result["dst"]
        assert result["status"] == "pending"
        assert result["ops_count"] == 1

    def test_creates_quarantine_dir_if_missing(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "nonexistent_q"
        assert not q_dir.exists()
        propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        assert q_dir.is_dir()

    def test_suffix_on_quarantine_collision(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_q"
        q_dir.mkdir()
        (q_dir / "junk.txt").write_text("already here")
        result = propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        assert result["dst"] == str(q_dir / "junk_1.txt")

    def test_persists_op_to_disk(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_q"
        propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert len(reloaded.ops) == 1
        assert reloaded.ops[0].op_type == "quarantine"

    def test_raises_if_source_not_a_file(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        q_dir = tmp_path / "_q"
        with pytest.raises(ValueError, match="Not a file"):
            propose_quarantine(
                str(tmp_path / "missing.txt"), pending_plan.plan_id, plans_dir, q_dir
            )

    def test_raises_if_plan_not_pending(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_q"
        with pytest.raises(ValueError, match="pending"):
            propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)


class TestProposeCreateFile:
    def test_appends_op_and_returns_dict(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "new.txt"
        result = propose_create_file(str(dest), "hello", pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "create_file"
        assert result["src"] == str(dest)
        assert result["status"] == "pending"
        assert result["ops_count"] == 1

    def test_persists_content_in_params(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "new.txt"
        propose_create_file(str(dest), "hello", pending_plan.plan_id, plans_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert reloaded.ops[0].params == {"content": "hello"}

    def test_raises_if_destination_already_exists(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        with pytest.raises(FileExistsError):
            propose_create_file(str(dest), "new", pending_plan.plan_id, plans_dir)

    def test_raises_if_plan_not_pending(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        with pytest.raises(ValueError, match="pending"):
            propose_create_file(str(tmp_path / "a.txt"), "x", pending_plan.plan_id, plans_dir)


class TestProposeUpdateFile:
    def test_appends_op_for_new_file(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "new.txt"
        result = propose_update_file(str(dest), "hello", pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "update_file"
        assert result["src"] == str(dest)

    def test_persists_content_and_overwrite_in_params(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        propose_update_file(str(dest), "new", pending_plan.plan_id, plans_dir, overwrite=True)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert reloaded.ops[0].params == {"content": "new", "overwrite": True}

    def test_raises_on_collision_without_overwrite(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        with pytest.raises(FileExistsError):
            propose_update_file(str(dest), "new", pending_plan.plan_id, plans_dir)

    def test_allows_collision_with_overwrite_true(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        result = propose_update_file(
            str(dest), "new", pending_plan.plan_id, plans_dir, overwrite=True
        )
        assert result["status"] == "pending"


class TestProposeCreateDir:
    def test_appends_op(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        dest = tmp_path / "newdir"
        result = propose_create_dir(str(dest), pending_plan.plan_id, plans_dir)
        assert result["op_type"] == "create_dir"
        assert result["src"] == str(dest)

    def test_idempotent_on_existing_dir(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        dest = tmp_path / "existing"
        dest.mkdir()
        result = propose_create_dir(str(dest), pending_plan.plan_id, plans_dir)
        assert result["status"] == "pending"

    def test_raises_if_path_is_a_file(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="is a file"):
            propose_create_dir(str(f), pending_plan.plan_id, plans_dir)


class TestProposeArchiveDocument:
    def _seed(self, registry_path: Path, checksum: str, path: Path) -> None:
        reg = _registry.Registry()
        reg.upsert(
            DocumentRecord.new(
                checksum=checksum,
                path=str(path),
                title="T",
                type="notes",
                summary="s",
                provenance="p",
            )
        )
        _registry.save(reg, registry_path)

    def test_appends_op_with_quarantine_dest(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        doc = tmp_path / "doc.pdf"
        doc.write_text("x")
        self._seed(registry_path, "c1", doc)
        q_dir = tmp_path / "_quarantine"

        result = propose_archive_document(
            "c1", "superseded", pending_plan.plan_id, plans_dir, registry_path, q_dir
        )
        assert result["op_type"] == "archive_document"
        assert result["src"] == str(doc)
        assert "doc.pdf" in result["dst"]

    def test_persists_checksum_and_reason_in_params(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        doc = tmp_path / "doc.pdf"
        doc.write_text("x")
        self._seed(registry_path, "c1", doc)
        propose_archive_document(
            "c1", "superseded", pending_plan.plan_id, plans_dir, registry_path, tmp_path / "_q"
        )
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert reloaded.ops[0].params == {"checksum": "c1", "reason": "superseded"}

    def test_raises_if_checksum_not_recorded(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        with pytest.raises(ValueError, match="No document recorded"):
            propose_archive_document(
                "missing", "", pending_plan.plan_id, plans_dir, registry_path, tmp_path / "_q"
            )


class TestProposeCompressQuarantine:
    def test_appends_op(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        q_dir = tmp_path / "_quarantine"
        result = propose_compress_quarantine(pending_plan.plan_id, plans_dir, q_dir)
        assert result["op_type"] == "compress_quarantine"
        assert result["src"] == str(q_dir)

    def test_persists_delete_originals_in_params(
        self, tmp_path: Path, plans_dir: Path, pending_plan: Plan
    ) -> None:
        q_dir = tmp_path / "_quarantine"
        propose_compress_quarantine(pending_plan.plan_id, plans_dir, q_dir, delete_originals=False)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert reloaded.ops[0].params == {"delete_originals": False}


class TestSetPlanRationale:
    def test_persists_and_strips_rationale(self, plans_dir: Path, pending_plan: Plan) -> None:
        from server.tools import set_plan_rationale

        out = set_plan_rationale(pending_plan.plan_id, "  Grouped by phase.  ", plans_dir)
        assert out["rationale"] == "Grouped by phase."
        assert load(pending_plan.plan_id, plans_dir).rationale == "Grouped by phase."

    def test_empty_rationale_is_blank(self, plans_dir: Path, pending_plan: Plan) -> None:
        from server.tools import set_plan_rationale

        out = set_plan_rationale(pending_plan.plan_id, "", plans_dir)
        assert out["rationale"] == ""


class TestSetPlanFolderNotes:
    def test_persists_notes(self, plans_dir: Path, pending_plan: Plan) -> None:
        from server.tools import set_plan_folder_notes

        notes = {"01_decisions": "Formal decision records", "_quarantine": "Duplicates"}
        out = set_plan_folder_notes(pending_plan.plan_id, notes, plans_dir)
        assert out["folder_notes"] == notes
        assert load(pending_plan.plan_id, plans_dir).folder_notes == notes

    def test_strips_and_drops_blank_entries(self, plans_dir: Path, pending_plan: Plan) -> None:
        from server.tools import set_plan_folder_notes

        out = set_plan_folder_notes(
            pending_plan.plan_id,
            {"  reports ": "  Final reports  ", "empty": "   ", "  ": "orphan"},
            plans_dir,
        )
        # keys/values stripped; blank notes and blank keys dropped
        assert out["folder_notes"] == {"reports": "Final reports"}

    def test_empty_notes_is_empty_dict(self, plans_dir: Path, pending_plan: Plan) -> None:
        from server.tools import set_plan_folder_notes

        out = set_plan_folder_notes(pending_plan.plan_id, {}, plans_dir)
        assert out["folder_notes"] == {}
