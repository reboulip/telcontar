"""Tests for the plan data model and disk persistence."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from server.plan import Plan, PlanOp, load, save, list_all


class TestPlanOp:
    def test_new_assigns_unique_ids(self) -> None:
        a = PlanOp.new("rename", "/src/a.txt", "b.txt")
        b = PlanOp.new("rename", "/src/a.txt", "b.txt")
        assert a.op_id != b.op_id

    def test_new_defaults(self) -> None:
        op = PlanOp.new("move", "/src/a.txt", "/dst/")
        assert op.status == "pending"
        assert op.retries == 0
        assert op.error is None

    def test_from_dict_roundtrip(self) -> None:
        op = PlanOp.new("quarantine", "/src/junk.txt", "/q/junk.txt")
        op.status = "completed"
        op.retries = 1
        restored = PlanOp.from_dict(asdict(op))
        assert restored.op_id == op.op_id
        assert restored.op_type == op.op_type
        assert restored.src == op.src
        assert restored.dst == op.dst
        assert restored.status == op.status
        assert restored.retries == op.retries
        assert restored.error is None

    def test_from_dict_with_error(self) -> None:
        op = PlanOp.new("rename", "/src/a.txt", "b.txt")
        op.status = "failed"
        op.error = "permission denied"
        restored = PlanOp.from_dict(asdict(op))
        assert restored.error == "permission denied"
        assert restored.status == "failed"

    def test_from_dict_missing_optional_fields_use_defaults(self) -> None:
        d = {"op_id": "x", "op_type": "move", "src": "/a", "dst": "/b"}
        op = PlanOp.from_dict(d)
        assert op.status == "pending"
        assert op.retries == 0
        assert op.error is None


class TestPlan:
    def test_new_is_pending(self) -> None:
        p = Plan.new()
        assert p.state == "pending"

    def test_new_assigns_uuid(self) -> None:
        a = Plan.new()
        b = Plan.new()
        assert a.plan_id != b.plan_id
        assert len(a.plan_id) == 36  # UUID4 canonical string length

    def test_new_ops_empty(self) -> None:
        p = Plan.new()
        assert p.ops == []

    def test_new_timestamps_set(self) -> None:
        p = Plan.new()
        assert p.created_at
        assert p.updated_at
        assert p.created_at == p.updated_at

    def test_add_op_appends(self) -> None:
        p = Plan.new()
        op = PlanOp.new("rename", "/src/a.txt", "b.txt")
        p.add_op(op)
        assert len(p.ops) == 1
        assert p.ops[0].op_id == op.op_id

    def test_add_op_updates_updated_at(self) -> None:
        p = Plan.new()
        original_updated = p.updated_at
        time.sleep(0.001)
        p.add_op(PlanOp.new("move", "/src/a.txt", "/dst/"))
        assert p.updated_at >= original_updated

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        p = Plan.new()
        p.add_op(PlanOp.new("rename", "/src/a.txt", "b.txt"))
        p.add_op(PlanOp.new("quarantine", "/src/old.log", "/q/old.log"))
        restored = Plan.from_dict(p.to_dict())
        assert restored.plan_id == p.plan_id
        assert restored.state == p.state
        assert len(restored.ops) == 2
        assert restored.ops[0].op_id == p.ops[0].op_id
        assert restored.ops[1].op_type == "quarantine"

    def test_to_dict_is_json_serializable(self) -> None:
        p = Plan.new()
        p.add_op(PlanOp.new("move", "/src/a.txt", "/dst/"))
        json.dumps(p.to_dict())  # must not raise


class TestPlanTransitions:
    def test_pending_to_approved(self) -> None:
        p = Plan.new()
        p.transition("approved")
        assert p.state == "approved"

    def test_pending_to_stopped(self) -> None:
        p = Plan.new()
        p.transition("stopped")
        assert p.state == "stopped"

    def test_approved_to_executing(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        assert p.state == "executing"

    def test_approved_to_stopped(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("stopped")
        assert p.state == "stopped"

    def test_executing_to_done(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        p.transition("done")
        assert p.state == "done"

    def test_executing_to_failed(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        p.transition("failed")
        assert p.state == "failed"

    def test_executing_to_stopped(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        p.transition("stopped")
        assert p.state == "stopped"

    def test_done_is_terminal(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        p.transition("done")
        with pytest.raises(ValueError, match="terminal"):
            p.transition("pending")

    def test_failed_is_terminal(self) -> None:
        p = Plan.new()
        p.transition("approved")
        p.transition("executing")
        p.transition("failed")
        with pytest.raises(ValueError, match="Invalid plan state transition"):
            p.transition("executing")

    def test_stopped_is_terminal(self) -> None:
        p = Plan.new()
        p.transition("stopped")
        with pytest.raises(ValueError, match="Invalid plan state transition"):
            p.transition("pending")

    def test_invalid_skip_raises(self) -> None:
        p = Plan.new()
        with pytest.raises(ValueError, match="Invalid plan state transition"):
            p.transition("executing")  # skip approved

    def test_transition_updates_updated_at(self) -> None:
        p = Plan.new()
        before = p.updated_at
        time.sleep(0.001)
        p.transition("approved")
        assert p.updated_at >= before


class TestPlanPersistence:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        p = Plan.new()
        plans_dir = tmp_path / "plans"
        save(p, plans_dir)
        assert (plans_dir / f"{p.plan_id}.json").is_file()

    def test_save_creates_plans_dir(self, tmp_path: Path) -> None:
        p = Plan.new()
        plans_dir = tmp_path / "nested" / "plans"
        assert not plans_dir.exists()
        save(p, plans_dir)
        assert plans_dir.is_dir()

    def test_load_roundtrip(self, tmp_path: Path) -> None:
        p = Plan.new()
        p.add_op(PlanOp.new("rename", "/src/a.txt", "b.txt"))
        p.transition("approved")
        plans_dir = tmp_path / "plans"
        save(p, plans_dir)
        restored = load(p.plan_id, plans_dir)
        assert restored.plan_id == p.plan_id
        assert restored.state == "approved"
        assert len(restored.ops) == 1
        assert restored.ops[0].op_type == "rename"

    def test_plan_id_stable_across_save_load(self, tmp_path: Path) -> None:
        p = Plan.new()
        plans_dir = tmp_path / "plans"
        save(p, plans_dir)
        loaded = load(p.plan_id, plans_dir)
        assert loaded.plan_id == p.plan_id

    def test_load_raises_for_unknown_id(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Plan not found"):
            load("nonexistent-id", tmp_path / "plans")

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        p = Plan.new()
        plans_dir = tmp_path / "plans"
        save(p, plans_dir)
        p.transition("approved")
        save(p, plans_dir)
        loaded = load(p.plan_id, plans_dir)
        assert loaded.state == "approved"

    def test_list_all_empty_dir(self, tmp_path: Path) -> None:
        assert list_all(tmp_path / "nonexistent") == []

    def test_list_all_multiple_plans_sorted_by_created_at(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / "plans"
        a = Plan.new()
        time.sleep(0.001)
        b = Plan.new()
        time.sleep(0.001)
        c = Plan.new()
        for p in (a, b, c):
            save(p, plans_dir)
        result = list_all(plans_dir)
        assert [r.plan_id for r in result] == [a.plan_id, b.plan_id, c.plan_id]

    def test_list_all_skips_corrupted_files(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        good = Plan.new()
        save(good, plans_dir)
        (plans_dir / "bad.json").write_text("not json", encoding="utf-8")
        (plans_dir / "missing-key.json").write_text('{"plan_id": "x"}', encoding="utf-8")
        result = list_all(plans_dir)
        assert len(result) == 1
        assert result[0].plan_id == good.plan_id
