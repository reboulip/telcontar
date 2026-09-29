"""AA1: whole-folder quarantine — guards, propose_quarantine_dir, execute_plan, undo."""

from __future__ import annotations

from pathlib import Path

import pytest

from host.agent import _DISCOVERY_SKIP_NAMES
from host.format import fmt_op, plan_tree_diff
from server import plan as _plan
from server.guards import (
    RESIDUE_NAMES,
    check_quarantinable_dir,
    is_residue_name,
    non_residue_files,
    safe_quarantine_dir_path,
)
from server.journal import all_entries
from server.tools import (
    create_plan,
    execute_plan,
    propose_create_dir,
    propose_move,
    propose_quarantine,
    propose_quarantine_dir,
    undo_last,
)


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    target = tmp_path / "corpus"
    target.mkdir()
    (tmp_path / "plans").mkdir()
    return {
        "target": target,
        "plans": tmp_path / "plans",
        "quarantine": target / "_quarantine",
        "journal": target / ".organizer" / "journal.jsonl",
    }


def _plan_id(env: dict) -> str:
    return create_plan(env["plans"])["plan_id"]


def _propose_dir(env: dict, pid: str, folder: Path, reason: str = "old layout") -> dict:
    return propose_quarantine_dir(
        str(folder), pid, env["plans"], env["quarantine"], env["target"], reason
    )


def _approve(env: dict, pid: str) -> None:
    p = _plan.load(pid, env["plans"])
    p.transition("approved")
    _plan.save(p, env["plans"])


def _run(env: dict, pid: str) -> dict:
    return execute_plan(
        pid,
        env["plans"],
        env["journal"],
        quarantine_dir=env["quarantine"],
        target_dir=env["target"],
    )


class TestGuards:
    def test_residue_names_mirror_host_skip_set(self) -> None:
        assert RESIDUE_NAMES == _DISCOVERY_SKIP_NAMES

    def test_dotfiles_are_residue(self) -> None:
        assert is_residue_name(".gitkeep")
        assert not is_residue_name("report.pdf")

    def test_non_residue_files_ignores_residue_and_empty_dirs(self, tmp_path: Path) -> None:
        folder = tmp_path / "f"
        (folder / "empty").mkdir(parents=True)
        (folder / "INDEX.md").write_text("x")
        (folder / ".DS_Store").write_text("x")
        assert non_residue_files(folder) == []
        (folder / "empty" / "a.pdf").write_text("x")
        assert non_residue_files(folder) == [folder / "empty" / "a.pdf"]

    def test_symlink_counts_as_non_residue(self, tmp_path: Path) -> None:
        folder = tmp_path / "f"
        folder.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (folder / "link").symlink_to(outside, target_is_directory=True)
        assert non_residue_files(folder) == [folder / "link"]

    def test_target_root_and_organizer_are_protected(self, env: dict) -> None:
        organizer = env["target"] / ".organizer"
        organizer.mkdir()
        for bad in (env["target"], organizer):
            with pytest.raises(ValueError):
                check_quarantinable_dir(
                    bad,
                    target_root=env["target"],
                    quarantine_dir=env["quarantine"],
                    organizer_dir=organizer,
                )

    def test_reserved_destinations_get_distinct_names(self, tmp_path: Path) -> None:
        q = tmp_path / "_quarantine"
        first = safe_quarantine_dir_path(Path("a/misc"), q)
        second = safe_quarantine_dir_path(Path("b/misc"), q, frozenset({str(first.resolve())}))
        assert first.name == "misc"
        assert second.name == "misc_1"


class TestProposeQuarantineDir:
    def test_stages_op_for_empty_folder(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        (old / "INDEX.md").write_text("x")
        result = _propose_dir(env, _plan_id(env), old)
        assert result["target_kind"] == "dir"
        p = _plan.load(result["plan_id"], env["plans"])
        assert p.ops[0].params == {"reason": "old layout", "target_kind": "dir"}
        assert Path(p.ops[0].dst).parent == env["quarantine"]

    def test_rejects_folder_with_unstaged_document(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        (old / "a.pdf").write_text("x")
        with pytest.raises(ValueError, match="still holds documents"):
            _propose_dir(env, _plan_id(env), old)

    def test_accepts_folder_whose_documents_are_all_staged_to_leave(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        (old / "a.pdf").write_text("x")
        (old / "b.pdf").write_text("x")
        new = env["target"] / "New"
        pid = _plan_id(env)
        propose_create_dir(str(new), pid, env["plans"])
        propose_move(str(old / "a.pdf"), str(new), pid, env["plans"])
        propose_quarantine(str(old / "b.pdf"), pid, env["plans"], env["quarantine"], "dup of a")
        result = _propose_dir(env, pid, old)
        assert result["ops_count"] == 4

    def test_rejects_folder_the_new_taxonomy_reuses(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        pid = _plan_id(env)
        propose_create_dir(str(old / "Sub"), pid, env["plans"])
        with pytest.raises(ValueError, match="needs that folder"):
            _propose_dir(env, pid, old)

    def test_rejects_folder_that_receives_a_move(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        doc = env["target"] / "loose.pdf"
        doc.write_text("x")
        pid = _plan_id(env)
        propose_move(str(doc), str(old), pid, env["plans"])
        with pytest.raises(ValueError, match="inside it"):
            _propose_dir(env, pid, old)

    def test_rejects_duplicate_and_root(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        pid = _plan_id(env)
        _propose_dir(env, pid, old)
        with pytest.raises(ValueError, match="already staged"):
            _propose_dir(env, pid, old)
        with pytest.raises(ValueError, match="protected"):
            _propose_dir(env, pid, env["target"])

    def test_same_named_folders_get_distinct_destinations(self, env: dict) -> None:
        a, b = env["target"] / "a" / "misc", env["target"] / "b" / "misc"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        pid = _plan_id(env)
        first = _propose_dir(env, pid, a)
        second = _propose_dir(env, pid, b)
        assert first["dst"] != second["dst"]


class TestExecuteDirQuarantine:
    def test_runs_after_moves_even_when_staged_first(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        doc = old / "a.pdf"
        doc.write_text("x")
        (old / "README.md").write_text("r")
        new = env["target"] / "New"
        pid = _plan_id(env)
        propose_create_dir(str(new), pid, env["plans"])
        propose_move(str(doc), str(new), pid, env["plans"])
        _propose_dir(env, pid, old)
        # Reorder so the folder op comes before the move in plan order.
        p = _plan.load(pid, env["plans"])
        p.ops.insert(0, p.ops.pop())
        _plan.save(p, env["plans"])
        _approve(env, pid)

        result = _run(env, pid)

        assert result["state"] == "done"
        assert (new / "a.pdf").exists()
        assert not old.exists()
        assert (env["quarantine"] / "Old" / "README.md").exists()
        entries = all_entries(env["journal"])
        folder_entry = next(e for e in entries if e.get("target_kind") == "dir")
        assert folder_entry["reason"] == "old layout"

    def test_nested_folders_quarantined_deepest_first_and_parent_swept(self, env: dict) -> None:
        outer = env["target"] / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        pid = _plan_id(env)
        _propose_dir(env, pid, outer)
        _propose_dir(env, pid, inner)
        _approve(env, pid)
        result = _run(env, pid)
        assert result["state"] == "done"
        assert not outer.exists()
        assert (env["quarantine"] / "inner").is_dir()

    def test_unticked_move_fails_closed_and_keeps_folder(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        doc = old / "a.pdf"
        doc.write_text("x")
        new = env["target"] / "New"
        pid = _plan_id(env)
        propose_create_dir(str(new), pid, env["plans"])
        move = propose_move(str(doc), str(new), pid, env["plans"])
        _propose_dir(env, pid, old)
        p = _plan.load(pid, env["plans"])
        for op in p.ops:
            if op.op_id == move["op_id"]:
                op.status = "skipped"
        p.state = "pending"
        p.transition("approved")
        _plan.save(p, env["plans"])

        result = _run(env, pid)

        assert result["state"] == "failed"
        assert doc.exists()
        failed = [o for o in result["ops"] if o["status"] == "failed"]
        assert "still holds documents" in failed[0]["error"]

    def test_undo_restores_folder_with_its_leftovers(self, env: dict) -> None:
        old = env["target"] / "Old"
        old.mkdir()
        (old / "README.md").write_text("r")
        pid = _plan_id(env)
        _propose_dir(env, pid, old)
        _approve(env, pid)
        _run(env, pid)
        assert not old.exists()

        undo_last(env["journal"], env["plans"])

        assert (old / "README.md").read_text() == "r"


class TestDisplay:
    def _op(self) -> dict:
        return {
            "op_type": "quarantine",
            "src": "/t/Old",
            "dst": "/t/_quarantine/Old",
            "params": {"reason": "old layout", "target_kind": "dir"},
        }

    def test_fmt_op_marks_folder(self) -> None:
        label = fmt_op(self._op(), markup=False)
        assert label.startswith("QUARANTINE FOLDER  Old/")
        assert "old layout" in label

    def test_folder_op_goes_to_other_ops(self) -> None:
        before, after, other = plan_tree_diff([self._op()])
        assert other == [self._op()]
        assert not before and not after
