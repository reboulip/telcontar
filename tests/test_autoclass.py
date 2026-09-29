"""AA2: headless auto-class — preconditions, placement contract, move-only plan."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from host.agent import PrepassResult
from host.autoclass import (
    AutoClassReport,
    check_preconditions,
    format_report,
    run_auto_class,
    run_auto_class_cli,
)

_ALLOWED_TOOLS = {
    "walk_tree",
    "compute_checksum_batch",
    "lookup_documents",
    "rehome_documents",
    "read_file_batch",
    "extract_text_batch",
    "record_document_batch",
    "create_plan",
    "propose_move",
    "set_plan_rationale",
    "approve_plan",
    "execute_plan",
    "write_index",
    "write_summary",
    "list_documents",
}


def _result(data: Any) -> MagicMock:
    content = MagicMock()
    content.text = json.dumps(data)
    result = MagicMock()
    result.content = [content]
    return result


def _settings() -> MagicMock:
    s = MagicMock()
    s.llm_model = "test-model"
    s.max_snippet_chars = 4000
    s.approval_mode = "always"  # the flag must override this
    return s


def _llm(placements: list[dict] | Exception | list) -> MagicMock:
    """Fake client: the placement call returns `placements`; any other call
    (the SUMMARY.md composition) returns plain text."""
    llm = MagicMock()

    async def _create(**kwargs: Any) -> Any:
        if kwargs.get("tools"):
            if isinstance(placements, Exception):
                raise placements
            call = SimpleNamespace(
                function=SimpleNamespace(
                    name="submit_placements", arguments=json.dumps({"placements": placements})
                )
            )
            msg = SimpleNamespace(tool_calls=[call], content=None)
        else:
            msg = SimpleNamespace(tool_calls=None, content="# Summary")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    llm.chat.completions.create = AsyncMock(side_effect=_create)
    return llm


def _session(
    *, propose_error: dict[str, str] | None = None, failed_ops: list[dict] | None = None
) -> tuple[MagicMock, list[tuple[str, dict]]]:
    calls: list[tuple[str, dict]] = []
    s = MagicMock()

    async def _call(name: str, args: dict | None = None) -> MagicMock:
        args = args or {}
        calls.append((name, args))
        if name == "create_plan":
            return _result({"plan_id": "p1"})
        if name == "propose_move" and propose_error and Path(args["path"]).name in propose_error:
            bad = _result(propose_error[Path(args["path"]).name])
            bad.isError = True
            return bad
        if name == "execute_plan":
            return _result({"ops": failed_ops or []})
        if name == "list_documents":
            return _result([{"title": "T", "type": "report", "summary": "s"}])
        return _result({"ok": True})

    s.call_tool = AsyncMock(side_effect=_call)
    return s, calls


def _setup(tmp_path: Path, names: tuple[str, ...] = ("a.pdf", "b.pdf")) -> tuple[Path, Any, Any]:
    target = tmp_path / "corpus"
    (target / "Plans").mkdir(parents=True)
    (target / "Reports").mkdir()
    new = [{"path": str(target / n), "checksum": f"c-{n}"} for n in names]
    known = [
        {
            "path": str(target / "Plans" / "old.pdf"),
            "checksum": "c-old",
            "record": {"type": "plan", "title": "Old plan"},
        }
    ]
    prepass = PrepassResult(
        new=new,
        known=known,
        total_files=len(new) + 1,
        dirs=[str(target / "Plans"), str(target / "Reports")],
        root_markers=["INDEX.md"],
    )
    recorded = [
        {"path": d["path"], "checksum": d["checksum"], "title": d["path"], "type": "report"}
        for d in new
    ]
    return target, prepass, recorded


async def _run(
    target: Path, prepass: PrepassResult, recorded: list, llm: Any, session: Any, **kw: Any
) -> AutoClassReport:
    with (
        patch("host.autoclass.run_prepass", AsyncMock(return_value=prepass)),
        patch(
            "host.autoclass._analyze_new_documents",
            AsyncMock(return_value={"recorded": recorded, "errors": []}),
        ),
        patch("host.autoclass._analyze_batch", AsyncMock(return_value=(recorded, []))),
    ):
        return await run_auto_class(
            target=target,
            settings=_settings(),
            llm=llm,
            session=session,
            on_event=lambda _e: None,
            project_root=target,
            **kw,
        )


class TestPreconditions:
    def test_missing_setup_is_reported(self, tmp_path: Path) -> None:
        msg = check_preconditions(tmp_path)
        assert msg and "INDEX.md" in msg and ".organizer" in msg

    def test_registry_is_required(self, tmp_path: Path) -> None:
        (tmp_path / ".organizer").mkdir()
        (tmp_path / "INDEX.md").write_text("x")
        msg = check_preconditions(tmp_path)
        assert msg and "registry.json" in msg

    def test_complete_setup_passes(self, tmp_path: Path) -> None:
        (tmp_path / ".organizer").mkdir()
        (tmp_path / ".organizer" / "registry.json").write_text("{}")
        (tmp_path / "INDEX.md").write_text("x")
        assert check_preconditions(tmp_path) is None

    def test_cli_exits_2_and_creates_nothing_on_failed_precondition(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("host.llm.make_client") as make_client:
            code = run_auto_class_cli(tmp_path)
        assert code == 2
        assert not (tmp_path / ".organizer").exists()
        make_client.assert_not_called()
        assert "no telcontar setup" in capsys.readouterr().err


class TestRunAutoClass:
    async def test_files_documents_with_move_only_plan_and_refreshes_outputs(
        self, tmp_path: Path
    ) -> None:
        target, prepass, recorded = _setup(tmp_path)
        llm = _llm(
            [
                {"folder": "Plans", "reason": "a plan"},
                {"folder": "Nope/Invented", "reason": "guess"},
            ]
        )
        session, calls = _session()

        report = await _run(target, prepass, recorded, llm, session)

        moves = [a for n, a in calls if n == "propose_move"]
        assert moves == [
            {"path": str(target / "a.pdf"), "dest_dir": str(target / "Plans"), "plan_id": "p1"}
        ]
        assert report.filed == [("a.pdf", "Plans")]
        assert [n for n, _ in report.left] == ["b.pdf"]
        names = {n for n, _ in calls}
        assert names <= _ALLOWED_TOOLS
        assert {"approve_plan", "execute_plan", "write_index", "write_summary"} <= names
        assert report.index_written and report.summary_written
        assert report.exit_code == 0

    async def test_model_gets_no_mcp_tools_only_forced_placement_call(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path)
        llm = _llm([{"folder": None, "reason": "none fits"}] * 2)
        session, _ = _session()

        await _run(target, prepass, recorded, llm, session)

        placement_calls = [
            c.kwargs for c in llm.chat.completions.create.await_args_list if c.kwargs.get("tools")
        ]
        assert len(placement_calls) == 1
        tools = placement_calls[0]["tools"]
        assert [t["function"]["name"] for t in tools] == ["submit_placements"]

    async def test_new_documents_outside_root_are_not_touched(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path, names=("a.pdf",))
        prepass.new.append({"path": str(target / "Plans" / "deep.pdf"), "checksum": "c-deep"})
        llm = _llm([{"folder": "Reports", "reason": "r"}])
        session, calls = _session()

        report = await _run(target, prepass, recorded, llm, session)

        assert report.candidates == 1
        assert report.outside_root == 1
        assert len([n for n, _ in calls if n == "propose_move"]) == 1

    async def test_dry_run_stages_and_writes_nothing(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path)
        llm = _llm([{"folder": "Plans", "reason": "p"}, {"folder": "Reports", "reason": "r"}])
        session, calls = _session()

        with patch("host.autoclass._analyze_new_documents") as record_path:
            report = await _run(target, prepass, recorded, llm, session, dry_run=True)

        record_path.assert_not_called()  # dry run must not record documents
        assert calls == []  # the pre-pass is patched; nothing else touched the server
        assert report.dry_run
        assert report.filed == [("a.pdf", "Plans"), ("b.pdf", "Reports")]
        assert "Would file 2 of 2" in format_report(report)

    async def test_name_collision_leaves_document_in_place(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path)
        llm = _llm([{"folder": "Plans", "reason": "p"}] * 2)
        session, _ = _session(propose_error={"a.pdf": "Destination already exists"})

        report = await _run(target, prepass, recorded, llm, session)

        assert report.filed == [("b.pdf", "Plans")]
        assert report.left[0][0] == "a.pdf"
        assert "already exists" in report.left[0][1]

    async def test_failed_move_gives_exit_code_1(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path, names=("a.pdf",))
        llm = _llm([{"folder": "Plans", "reason": "p"}])
        session, _ = _session(
            failed_ops=[{"src": str(target / "a.pdf"), "status": "failed", "error": "locked"}]
        )

        report = await _run(target, prepass, recorded, llm, session)

        assert report.filed == []
        assert report.move_errors == ["a.pdf: locked"]
        assert report.exit_code == 1

    async def test_failed_placement_call_leaves_batch_in_place(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path)
        llm = _llm(RuntimeError("429"))
        session, calls = _session()

        report = await _run(target, prepass, recorded, llm, session)

        assert len(report.left) == 2
        assert "propose_move" not in {n for n, _ in calls}
        assert llm.chat.completions.create.await_count == 2  # one retry

    async def test_no_existing_folders_means_nothing_is_moved(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path)
        prepass.dirs = []
        prepass.known = []
        llm = _llm([])
        session, calls = _session()

        report = await _run(target, prepass, recorded, llm, session)

        assert [why for _, why in report.left] == ["no existing folders"] * 2
        assert calls == []

    async def test_nothing_new_at_root_is_a_clean_no_op(self, tmp_path: Path) -> None:
        target, prepass, recorded = _setup(tmp_path, names=())
        llm = _llm([])
        session, calls = _session()

        report = await _run(target, prepass, [], llm, session)

        assert report.candidates == 0
        assert report.exit_code == 0
        assert calls == []
        llm.chat.completions.create.assert_not_awaited()
