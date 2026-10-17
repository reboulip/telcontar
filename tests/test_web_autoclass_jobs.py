"""AB4b — web auto-class job runner (host/web/autoclass_jobs.py), NiceGUI-free."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from host import autoclass
from host.autoclass import AutoClassReport
from host.web import autoclass_jobs as jobs
from host.web import folderstatus
from host.web import session as web_session


@pytest.fixture(autouse=True)
def _clean() -> None:
    jobs.reset()
    web_session._SESSIONS.clear()
    yield
    jobs.reset()
    web_session._SESSIONS.clear()


async def test_job_runs_with_record_in_place_and_keeps_report(tmp_path: Path) -> None:
    fake = AsyncMock(return_value=AutoClassReport(candidates=2))
    with patch.object(autoclass, "run_auto_class_for_target", fake):
        job = jobs.start(tmp_path)
        assert job is not None and job.running
        await job.task

    assert fake.await_args.kwargs["record_in_place"] is True
    assert not job.running and job.error is None
    assert "new document" in job.report_text


async def test_setup_error_is_the_job_error(tmp_path: Path) -> None:
    fake = AsyncMock(side_effect=autoclass.AutoClassSetupError("no setup"))
    with patch.object(autoclass, "run_auto_class_for_target", fake):
        job = jobs.start(tmp_path)
        await job.task
    assert job.error == "no setup" and not job.running


async def test_only_progress_style_events_are_stored(tmp_path: Path) -> None:
    async def fake(root: Path, *, on_event, **_kw) -> AutoClassReport:
        on_event(SimpleNamespace(kind="tool_result", text="SECRET DOCUMENT BODY"))
        on_event(SimpleNamespace(kind="progress", text="Batch 1 of 2"))
        on_event(SimpleNamespace(kind="warning", text="careful"))
        return AutoClassReport()

    with patch.object(autoclass, "run_auto_class_for_target", fake):
        job = jobs.start(tmp_path)
        await job.task
    assert job.lines == ["Batch 1 of 2", "warning: careful"]


async def test_job_end_invalidates_folder_status(tmp_path: Path) -> None:
    with (
        patch.object(
            autoclass, "run_auto_class_for_target", AsyncMock(return_value=AutoClassReport())
        ),
        patch.object(folderstatus, "invalidate") as invalidate,
    ):
        job = jobs.start(tmp_path)
        await job.task
    invalidate.assert_called_once_with(tmp_path)


async def test_overlapping_job_is_blocked_until_it_ends(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def slow(root: Path, **_kw) -> AutoClassReport:
        await gate.wait()
        return AutoClassReport()

    with patch.object(autoclass, "run_auto_class_for_target", slow):
        job = jobs.start(tmp_path)
        assert jobs.blocking_reason(tmp_path / "sub") is not None
        assert jobs.blocking_reason(tmp_path.parent) is not None
        assert jobs.start(tmp_path) is None
        gate.set()
        await job.task
    assert jobs.blocking_reason(tmp_path) is None


def test_live_organize_session_blocks_but_unstarted_one_does_not(tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    assert jobs.blocking_reason(tmp_path) is None
    session.started = True
    assert "organize session" in (jobs.blocking_reason(tmp_path) or "")
    assert jobs.blocking_reason(tmp_path.parent / "elsewhere") is None
