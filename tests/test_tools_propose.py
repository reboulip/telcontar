"""Tests for propose_rename, propose_move, propose_quarantine."""
from __future__ import annotations

from pathlib import Path

import pytest

from server.plan import Plan, load, save
from server.tools import propose_move, propose_quarantine, propose_rename


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
    def test_appends_op_and_returns_dict(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
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

    def test_raises_if_destination_already_exists(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "a.txt"
        src.write_text("x")
        (tmp_path / "b.txt").write_text("existing")
        with pytest.raises(FileExistsError):
            propose_rename(str(src), "b.txt", pending_plan.plan_id, plans_dir)

    def test_raises_if_source_not_found(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        with pytest.raises(FileNotFoundError):
            propose_rename(str(tmp_path / "missing.txt"), "new.txt", pending_plan.plan_id, plans_dir)

    def test_raises_if_plan_not_pending(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "a.txt"
        src.write_text("x")
        with pytest.raises(ValueError, match="pending"):
            propose_rename(str(src), "b.txt", pending_plan.plan_id, plans_dir)

    def test_multiple_ops_accumulate(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_text("x")
            propose_rename(str(f), f"renamed{i}.txt", pending_plan.plan_id, plans_dir)
        reloaded = load(pending_plan.plan_id, plans_dir)
        assert len(reloaded.ops) == 3


class TestProposeMove:
    def test_appends_op_and_returns_dict(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
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

    def test_raises_if_destination_already_exists(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        (dst_dir / "file.txt").write_text("existing")
        with pytest.raises(FileExistsError):
            propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)

    def test_raises_if_source_not_a_file(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        with pytest.raises(ValueError, match="Not a file"):
            propose_move(str(tmp_path / "missing.txt"), str(dst_dir), pending_plan.plan_id, plans_dir)

    def test_raises_if_dest_not_a_directory(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "file.txt"
        src.write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            propose_move(str(src), str(tmp_path / "nonexistent"), pending_plan.plan_id, plans_dir)

    def test_raises_if_plan_not_pending(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dest"
        dst_dir.mkdir()
        with pytest.raises(ValueError, match="pending"):
            propose_move(str(src), str(dst_dir), pending_plan.plan_id, plans_dir)


class TestProposeQuarantine:
    def test_appends_op_and_returns_dict(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_quarantine"
        result = propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        assert result["op_type"] == "quarantine"
        assert result["src"] == str(src)
        assert "junk.txt" in result["dst"]
        assert result["status"] == "pending"
        assert result["ops_count"] == 1

    def test_creates_quarantine_dir_if_missing(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "nonexistent_q"
        assert not q_dir.exists()
        propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
        assert q_dir.is_dir()

    def test_suffix_on_quarantine_collision(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
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

    def test_raises_if_source_not_a_file(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        q_dir = tmp_path / "_q"
        with pytest.raises(ValueError, match="Not a file"):
            propose_quarantine(str(tmp_path / "missing.txt"), pending_plan.plan_id, plans_dir, q_dir)

    def test_raises_if_plan_not_pending(self, tmp_path: Path, plans_dir: Path, pending_plan: Plan) -> None:
        pending_plan.transition("approved")
        save(pending_plan, plans_dir)
        src = tmp_path / "junk.txt"
        src.write_text("x")
        q_dir = tmp_path / "_q"
        with pytest.raises(ValueError, match="pending"):
            propose_quarantine(str(src), pending_plan.plan_id, plans_dir, q_dir)
