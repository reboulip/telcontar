"""Tests for review_plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.plan import Plan, PlanOp, save
from server.tools import review_plan


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def empty_plan(plans_dir: Path) -> Plan:
    p = Plan.new()
    save(p, plans_dir)
    return p


class TestReviewPlanBasic:
    def test_empty_plan_is_valid(self, plans_dir: Path, empty_plan: Plan) -> None:
        result = review_plan(empty_plan.plan_id, plans_dir)
        assert result["is_valid"] is True
        assert result["duplicates"] == []
        assert result["missing_sources"] == []
        assert result["total_ops"] == 0

    def test_returns_plan_id(self, plans_dir: Path, empty_plan: Plan) -> None:
        result = review_plan(empty_plan.plan_id, plans_dir)
        assert result["plan_id"] == empty_plan.plan_id

    def test_raises_for_unknown_plan(self, plans_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            review_plan("nonexistent-id", plans_dir)

    def test_total_ops_count(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        for i in range(4):
            p.ops.append(PlanOp.new("rename", str(tmp_path / f"f{i}.txt"), f"g{i}.txt"))
        save(p, plans_dir)
        result = review_plan(p.plan_id, plans_dir)
        assert result["total_ops"] == 4


class TestReviewPlanDuplicates:
    def test_no_duplicates_when_ops_unique(self, tmp_path: Path, plans_dir: Path) -> None:
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        p = Plan.new()
        p.ops.append(PlanOp.new("rename", str(tmp_path / "a.txt"), "b.txt"))
        p.ops.append(PlanOp.new("rename", str(tmp_path / "c.txt"), "d.txt"))
        save(p, plans_dir)
        result = review_plan(p.plan_id, plans_dir)
        assert result["duplicates"] == []
        assert result["is_valid"] is True

    def test_detects_duplicate_rename_ops(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        src = str(tmp_path / "a.txt")
        op1 = PlanOp.new("rename", src, "b.txt")
        op2 = PlanOp.new("rename", src, "c.txt")
        p.ops.extend([op1, op2])
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)

        assert len(result["duplicates"]) == 1
        dup = result["duplicates"][0]
        assert dup["src"] == src
        assert dup["op_type"] == "rename"
        assert set(dup["op_ids"]) == {op1.op_id, op2.op_id}

    def test_detects_duplicate_move_ops(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        src = str(tmp_path / "file.txt")
        dst1 = str(tmp_path / "dir1")
        dst2 = str(tmp_path / "dir2")
        p.ops.extend([PlanOp.new("move", src, dst1), PlanOp.new("move", src, dst2)])
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert len(result["duplicates"]) == 1
        assert result["duplicates"][0]["op_type"] == "move"

    def test_same_src_different_op_types_not_a_duplicate(
        self, tmp_path: Path, plans_dir: Path
    ) -> None:
        p = Plan.new()
        src = str(tmp_path / "file.txt")
        p.ops.append(PlanOp.new("rename", src, "new.txt"))
        p.ops.append(PlanOp.new("quarantine", src, str(tmp_path / "_q" / "file.txt")))
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert result["duplicates"] == []

    def test_three_identical_ops_reported_together(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        src = str(tmp_path / "file.txt")
        ops = [PlanOp.new("rename", src, f"name{i}.txt") for i in range(3)]
        p.ops.extend(ops)
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert len(result["duplicates"]) == 1
        assert len(result["duplicates"][0]["op_ids"]) == 3

    def test_multiple_independent_duplicate_pairs(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        src_a = str(tmp_path / "a.txt")
        src_b = str(tmp_path / "b.txt")
        p.ops.extend(
            [
                PlanOp.new("rename", src_a, "x.txt"),
                PlanOp.new("rename", src_a, "y.txt"),
                PlanOp.new("move", src_b, str(tmp_path / "dir1")),
                PlanOp.new("move", src_b, str(tmp_path / "dir2")),
            ]
        )
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert len(result["duplicates"]) == 2


class TestReviewPlanMissingSources:
    def test_flags_ops_with_missing_src(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        missing_path = str(tmp_path / "nonexistent.txt")
        op = PlanOp.new("rename", missing_path, "new.txt")
        p.ops.append(op)
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert len(result["missing_sources"]) == 1
        assert result["missing_sources"][0]["src"] == missing_path
        assert result["missing_sources"][0]["op_id"] == op.op_id
        assert result["missing_sources"][0]["op_type"] == "rename"

    def test_existing_src_not_flagged(self, tmp_path: Path, plans_dir: Path) -> None:
        src = tmp_path / "real.txt"
        src.write_text("x")
        p = Plan.new()
        p.ops.append(PlanOp.new("rename", str(src), "new.txt"))
        save(p, plans_dir)

        result = review_plan(p.plan_id, plans_dir)
        assert result["missing_sources"] == []

    def test_is_valid_false_when_sources_missing(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        p.ops.append(PlanOp.new("rename", str(tmp_path / "gone.txt"), "new.txt"))
        save(p, plans_dir)
        result = review_plan(p.plan_id, plans_dir)
        assert result["is_valid"] is False

    def test_is_valid_false_when_both_issues_present(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        src = str(tmp_path / "missing.txt")
        p.ops.extend(
            [
                PlanOp.new("rename", src, "a.txt"),
                PlanOp.new("rename", src, "b.txt"),
            ]
        )
        save(p, plans_dir)
        result = review_plan(p.plan_id, plans_dir)
        assert result["is_valid"] is False
        assert len(result["duplicates"]) == 1
        assert len(result["missing_sources"]) == 2

    def test_does_not_modify_plan(self, tmp_path: Path, plans_dir: Path) -> None:
        p = Plan.new()
        p.ops.append(PlanOp.new("rename", str(tmp_path / "missing.txt"), "new.txt"))
        save(p, plans_dir)
        before = p.to_dict()
        review_plan(p.plan_id, plans_dir)
        from server.plan import load

        after = load(p.plan_id, plans_dir).to_dict()
        assert before["ops"] == after["ops"]
        assert before["state"] == after["state"]
        assert before["updated_at"] == after["updated_at"]
