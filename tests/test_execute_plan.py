"""Tests for execute_plan: retry logic, hard-stop, journal integration, state transitions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server import registry as _registry
from server.journal import last as journal_last
from server.plan import Plan, PlanOp, load, save
from server.registry import DocumentRecord
from server.tools import execute_plan


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "journal.jsonl"


@pytest.fixture()
def approved_plan(plans_dir: Path) -> Plan:
    p = Plan.new()
    p.transition("approved")
    save(p, plans_dir)
    return p


def _add_op(plan: Plan, op_type: str, src: str, dst: str, plans_dir: Path) -> PlanOp:
    """Helper: append an op to a plan that may already be in approved state."""
    plan.state = "pending"
    op = PlanOp.new(op_type, src, dst)  # type: ignore[arg-type]
    plan.add_op(op)
    plan.state = "approved"
    save(plan, plans_dir)
    return op


class TestExecutePlanHappyPath:
    def test_rename_op_succeeds(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        src = tmp_path / "old.txt"
        src.write_text("x")
        p = Plan.new()
        p.transition("approved")
        op = PlanOp.new("rename", str(src), "new.txt")
        p.ops.append(op)
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert result["ops_completed"] == 1
        assert result["ops_failed"] == 0
        assert not (tmp_path / "old.txt").exists()
        assert (tmp_path / "new.txt").exists()

    def test_move_op_succeeds(self, tmp_path: Path, plans_dir: Path, journal_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert (dst_dir / "file.txt").exists()
        assert not src.exists()

    def test_quarantine_op_succeeds(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dest = tmp_path / "_q" / "junk.txt"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("quarantine", str(src), str(q_dest)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert q_dest.exists()
        assert not src.exists()

    def test_empty_plan_completes_as_done(
        self, plans_dir: Path, journal_path: Path, approved_plan: Plan
    ) -> None:
        result = execute_plan(approved_plan.plan_id, plans_dir, journal_path)
        assert result["state"] == "done"
        assert result["ops_completed"] == 0

    def test_multiple_ops_all_succeed(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        files = [tmp_path / f"f{i}.txt" for i in range(3)]
        for f in files:
            f.write_text("x")
        p = Plan.new()
        p.transition("approved")
        for f in files:
            p.ops.append(PlanOp.new("move", str(f), str(dst_dir)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)
        assert result["state"] == "done"
        assert result["ops_completed"] == 3


class TestExecutePlanStateTransitions:
    def test_plan_must_be_approved(self, plans_dir: Path, journal_path: Path) -> None:
        p = Plan.new()
        save(p, plans_dir)
        with pytest.raises(ValueError, match="approved"):
            execute_plan(p.plan_id, plans_dir, journal_path)

    def test_transitions_through_executing_to_done(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "f.txt"
        src.write_text("x")
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(src), "g.txt"))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        persisted = load(p.plan_id, plans_dir)
        assert persisted.state == "done"

    def test_partial_failure_ends_in_failed(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        good = tmp_path / "good.txt"
        good.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(good), str(dst_dir)))
        p.ops.append(PlanOp.new("move", str(tmp_path / "missing.txt"), str(dst_dir)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)
        assert result["state"] == "failed"
        assert result["ops_completed"] == 1
        assert result["ops_failed"] == 1


class TestExecutePlanRetry:
    def test_retries_on_oserror_then_succeeds(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)

        call_count = 0
        original_move = __import__("shutil").move

        def flaky_move(src_arg, dst_arg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("temporarily locked")
            return original_move(src_arg, dst_arg)

        with patch("server.tools.shutil.move", side_effect=flaky_move):
            result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert result["ops_completed"] == 1
        assert call_count == 2  # failed once, succeeded on retry

    def test_exhausts_retries_on_persistent_oserror(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)

        with patch("server.tools.shutil.move", side_effect=OSError("always locked")):
            result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "failed"
        assert result["ops_failed"] == 1
        reloaded = load(p.plan_id, plans_dir)
        assert reloaded.ops[0].retries == 3  # 3 failed attempts

    def test_file_not_found_does_not_retry(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(tmp_path / "missing.txt"), "new.txt"))
        save(p, plans_dir)

        call_count = 0
        original_rename = Path.rename

        def counting_rename(self, target):
            nonlocal call_count
            call_count += 1
            return original_rename(self, target)

        with patch.object(Path, "rename", counting_rename):
            result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["ops_failed"] == 1
        assert call_count == 1  # no retries


class TestExecutePlanHardStop:
    def test_hard_stop_after_four_failures(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = Plan.new()
        p.transition("approved")
        for i in range(6):
            p.ops.append(PlanOp.new("rename", str(tmp_path / f"missing_{i}.txt"), f"new_{i}.txt"))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "stopped"
        assert result["hard_stop"] is True
        assert result["ops_failed"] == 4  # stopped exactly at >3

    def test_hard_stop_writes_journal_entry(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = Plan.new()
        p.transition("approved")
        for i in range(5):
            p.ops.append(PlanOp.new("rename", str(tmp_path / f"m_{i}.txt"), f"n_{i}.txt"))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        entry = journal_last(journal_path)
        assert entry is not None
        assert entry["op_type"] == "hard_stop"
        assert entry["plan_id"] == p.plan_id
        assert entry["failed_count"] == 4

    def test_hard_stop_leaves_unprocessed_ops_pending(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        good = tmp_path / "good.txt"
        good.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        for i in range(4):
            p.ops.append(PlanOp.new("rename", str(tmp_path / f"miss_{i}.txt"), f"n_{i}.txt"))
        p.ops.append(PlanOp.new("move", str(good), str(dst_dir)))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        reloaded = load(p.plan_id, plans_dir)
        # The last op (move) should remain pending — hard-stop fired before it ran
        assert reloaded.ops[-1].status == "pending"

    def test_three_failures_does_not_trigger_hard_stop(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = Plan.new()
        p.transition("approved")
        for i in range(3):
            p.ops.append(PlanOp.new("rename", str(tmp_path / f"m_{i}.txt"), f"n_{i}.txt"))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "failed"
        assert result["hard_stop"] is False


class TestExecutePlanJournal:
    def test_journals_each_successful_op(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        files = [tmp_path / f"f{i}.txt" for i in range(3)]
        for f in files:
            f.write_text("x")
        p = Plan.new()
        p.transition("approved")
        for f in files:
            p.ops.append(PlanOp.new("move", str(f), str(dst_dir)))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln]
        entries = [json.loads(ln) for ln in lines]
        assert len(entries) == 3
        assert all(e["op_type"] == "move" for e in entries)
        assert all(e["plan_id"] == p.plan_id for e in entries)

    def test_failed_op_not_journaled(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(tmp_path / "missing.txt"), "new.txt"))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        assert not journal_path.exists() or journal_path.read_text(encoding="utf-8").strip() == ""

    def test_journal_entry_has_required_fields(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        p = Plan.new()
        p.transition("approved")
        op = PlanOp.new("rename", str(src), "renamed.txt")
        p.ops.append(op)
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        entry = journal_last(journal_path)
        assert entry is not None
        for field in ("op_type", "plan_id", "op_id", "src", "dst", "timestamp"):
            assert field in entry, f"missing field: {field}"
        assert entry["op_id"] == op.op_id

    def test_idempotent_skips_completed_ops(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        p = Plan.new()
        p.transition("approved")
        op = PlanOp.new("rename", str(src), "renamed.txt")
        p.ops.append(op)
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)

        # Manually reset plan to approved with op already completed — second call must skip it
        p2 = load(p.plan_id, plans_dir)
        p2.state = "approved"
        save(p2, plans_dir)

        result2 = execute_plan(p.plan_id, plans_dir, journal_path)
        assert result2["ops_completed"] == 0  # already completed op is skipped


class TestExecutePlanRegistryReconcile:
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

    def test_rename_updates_registry_path(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        src = tmp_path / "old.txt"
        src.write_text("x")
        self._seed(registry_path, "c1", src)
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(src), "new.txt"))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        rec = _registry.load(registry_path).get("c1")
        assert rec is not None
        assert rec.path == str(tmp_path / "new.txt")
        assert rec.status == "active"

    def test_move_updates_registry_path(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        self._seed(registry_path, "c1", src)
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        rec = _registry.load(registry_path).get("c1")
        assert rec is not None
        assert rec.path == str(dst_dir / "file.txt")

    def test_quarantine_sets_status_and_path(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dest = tmp_path / "_q" / "junk.txt"
        self._seed(registry_path, "c1", src)
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("quarantine", str(src), str(q_dest)))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        rec = _registry.load(registry_path).get("c1")
        assert rec is not None
        assert rec.path == str(q_dest)
        assert rec.status == "quarantined"

    def test_unrecorded_file_is_noop(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        recorded = tmp_path / "kept.txt"
        recorded.write_text("x")
        self._seed(registry_path, "c1", recorded)
        # Move a DIFFERENT, unrecorded file
        other = tmp_path / "other.txt"
        other.write_text("y")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(other), str(dst_dir)))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        reg = _registry.load(registry_path)
        assert len(reg.records()) == 1
        assert reg.get("c1").path == str(recorded)  # type: ignore[union-attr]

    def test_no_registry_file_is_not_created(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"  # does not exist
        src = tmp_path / "f.txt"
        src.write_text("x")
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(src), "g.txt"))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        assert not registry_path.exists()  # registry-optional: no spurious file


class TestExecutePlanNewOpTypes:
    """M1: create_file/update_file/create_dir/archive_document/compress_quarantine
    are now routed through the plan flow instead of being direct agent tools."""

    def test_create_file_op_succeeds(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dest = tmp_path / "new.txt"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("create_file", str(dest), "", params={"content": "hello"}))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert dest.read_text() == "hello"

    def test_create_file_journaled_and_undoable(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        from server.tools import undo_last

        dest = tmp_path / "new.txt"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("create_file", str(dest), "", params={"content": "hello"}))
        save(p, plans_dir)

        execute_plan(p.plan_id, plans_dir, journal_path)
        assert dest.is_file()

        undo_last(journal_path, plans_dir)
        assert not dest.exists()

    def test_update_file_creates_new_file(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dest = tmp_path / "new.txt"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("update_file", str(dest), "", params={"content": "hello"}))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert dest.read_text() == "hello"

    def test_update_file_without_overwrite_fails_on_collision(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("update_file", str(dest), "", params={"content": "new"}))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "failed"
        assert dest.read_text() == "old"

    def test_update_file_with_overwrite_replaces_content(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        p = Plan.new()
        p.transition("approved")
        p.ops.append(
            PlanOp.new("update_file", str(dest), "", params={"content": "new", "overwrite": True})
        )
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert dest.read_text() == "new"

    def test_create_dir_op_succeeds(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        dest = tmp_path / "newdir"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("create_dir", str(dest), ""))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert dest.is_dir()

    def test_move_into_nonexistent_dir_creates_parent(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "newdir"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert (dst_dir / "file.txt").exists()
        assert not src.exists()

    def test_create_dir_runs_before_move_that_targets_it(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "newdir"
        p = Plan.new()
        p.transition("approved")
        # Authored out of order: the move is listed before the create_dir op
        # that must run first for it to succeed.
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        p.ops.append(PlanOp.new("create_dir", str(dst_dir), ""))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert dst_dir.is_dir()
        assert (dst_dir / "file.txt").exists()

    def test_create_dir_runs_before_file_ops_even_when_listed_last(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        a = tmp_path / "a.txt"
        a.write_text("a")
        b = tmp_path / "b.txt"
        b.write_text("b")
        dst_dir = tmp_path / "subdir"
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(a), str(dst_dir)))
        p.ops.append(PlanOp.new("move", str(b), str(dst_dir)))
        p.ops.append(PlanOp.new("create_dir", str(dst_dir), ""))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert result["ops_completed"] == 3
        assert (dst_dir / "a.txt").exists()
        assert (dst_dir / "b.txt").exists()
        # The create_dir op is journaled first even though it was authored last.
        entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
        create_dir_entries = [e for e in entries if e["op_type"] == "create_dir"]
        move_entries = [e for e in entries if e["op_type"] == "move"]
        assert create_dir_entries and move_entries
        assert entries.index(create_dir_entries[0]) < entries.index(move_entries[0])

    def test_archive_document_op_flips_status_and_quarantines(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        archive_path = tmp_path / ".organizer" / "archive.jsonl"
        quarantine_dir = tmp_path / "_quarantine"
        doc = tmp_path / "doc.pdf"
        doc.write_text("x")

        reg = _registry.Registry()
        reg.upsert(
            DocumentRecord.new(
                checksum="c1", path=str(doc), title="T", type="notes", summary="s", provenance="p"
            )
        )
        _registry.save(reg, registry_path)

        p = Plan.new()
        p.transition("approved")
        p.ops.append(
            PlanOp.new(
                "archive_document",
                str(doc),
                str(quarantine_dir / "doc.pdf"),
                params={"checksum": "c1", "reason": "superseded"},
            )
        )
        save(p, plans_dir)

        result = execute_plan(
            p.plan_id, plans_dir, journal_path, registry_path, quarantine_dir, archive_path
        )

        assert result["state"] == "done"
        assert not doc.exists()
        assert (quarantine_dir / "doc.pdf").exists()
        rec = _registry.load(registry_path).get("c1")
        assert rec is not None
        assert rec.status == "archived"

    def test_archive_document_undoable_via_undo_last(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        from server.tools import undo_last

        registry_path = tmp_path / ".organizer" / "registry.json"
        archive_path = tmp_path / ".organizer" / "archive.jsonl"
        quarantine_dir = tmp_path / "_quarantine"
        doc = tmp_path / "doc.pdf"
        doc.write_text("x")

        reg = _registry.Registry()
        reg.upsert(
            DocumentRecord.new(
                checksum="c1", path=str(doc), title="T", type="notes", summary="s", provenance="p"
            )
        )
        _registry.save(reg, registry_path)

        p = Plan.new()
        p.transition("approved")
        p.ops.append(
            PlanOp.new(
                "archive_document",
                str(doc),
                str(quarantine_dir / "doc.pdf"),
                params={"checksum": "c1", "reason": "superseded"},
            )
        )
        save(p, plans_dir)

        execute_plan(
            p.plan_id, plans_dir, journal_path, registry_path, quarantine_dir, archive_path
        )
        assert not doc.exists()

        undo_last(journal_path, plans_dir)
        assert doc.exists()

    def test_compress_quarantine_op_archives_loose_files(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        quarantine_dir = tmp_path / "_quarantine"
        quarantine_dir.mkdir()
        (quarantine_dir / "junk.txt").write_text("x")

        p = Plan.new()
        p.transition("approved")
        p.ops.append(
            PlanOp.new(
                "compress_quarantine",
                str(quarantine_dir),
                "",
                params={"delete_originals": True},
            )
        )
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path, quarantine_dir=quarantine_dir)

        assert result["state"] == "done"
        assert not (quarantine_dir / "junk.txt").exists()
        archives = list(quarantine_dir.glob("quarantine_*.zip"))
        assert len(archives) == 1


class TestExecutePlanChainedOps:
    """F7: a file relocated by an earlier op is tracked so later ops still resolve."""

    def test_move_after_rename_in_same_run(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        src = tmp_path / "old.txt"
        src.write_text("x")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        # Both ops reference the ORIGINAL path (as the LLM builds them pre-execution).
        p.ops.append(PlanOp.new("rename", str(src), "new.txt"))
        p.ops.append(PlanOp.new("move", str(src), str(dest_dir)))
        save(p, plans_dir)

        result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert result["ops_completed"] == 2
        assert result["ops_failed"] == 0
        # Renamed then moved: lands at dest/new.txt (the renamed name is preserved).
        assert (dest_dir / "new.txt").exists()
        assert not (tmp_path / "new.txt").exists()
        assert not src.exists()

    def test_undo_reverses_chained_rename_then_move(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        from server.tools import undo_last

        src = tmp_path / "old.txt"
        src.write_text("x")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(src), "new.txt"))
        p.ops.append(PlanOp.new("move", str(src), str(dest_dir)))
        save(p, plans_dir)
        execute_plan(p.plan_id, plans_dir, journal_path)
        assert (dest_dir / "new.txt").exists()

        undo_last(journal_path, plans_dir)  # reverse the move
        assert (tmp_path / "new.txt").exists()
        assert not (dest_dir / "new.txt").exists()

        undo_last(journal_path, plans_dir)  # reverse the rename
        assert src.exists()
        assert not (tmp_path / "new.txt").exists()

    def test_registry_reconciles_across_chained_ops(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        registry_path = tmp_path / ".organizer" / "registry.json"
        src = tmp_path / "old.txt"
        src.write_text("hello")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        reg = _registry.Registry()
        reg.upsert(
            DocumentRecord.new(
                checksum="c1", path=str(src), title="T", type="notes", summary="s", provenance="p"
            )
        )
        _registry.save(reg, registry_path)

        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("rename", str(src), "new.txt"))
        p.ops.append(PlanOp.new("move", str(src), str(dest_dir)))
        save(p, plans_dir)
        execute_plan(p.plan_id, plans_dir, journal_path, registry_path)

        rec = _registry.load(registry_path).get("c1")
        assert rec is not None
        assert rec.checksum == "c1"  # identity stays stable across both ops
        assert rec.path == str(dest_dir / "new.txt")  # path tracked through the chain
