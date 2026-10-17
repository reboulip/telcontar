"""Web auto-class jobs (AB4b, #76) — NiceGUI-free.

Runs the AA2 headless auto-class (`host.autoclass.run_auto_class_for_target`,
with ``record_in_place=True``) as a background task per folder, so the landing
page's "Auto-class" button can bring a yellow folder to green. A job keeps only
progress / cost / warning / error *text*: tool-call and tool-result events
carry whole document records and are never stored. When a job ends it
invalidates the folder-status cache so the indicator re-checks at once.

Concurrency: an auto-class job and an organize session both spawn an MCP
server that writes the same registry and journal, so `blocking_reason` reports
any overlapping running job or live organize session; the UI uses it to
disable the button, Organize, and journal undo.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from host import autoclass
from host.web import folderstatus
from host.web import session as web_session

_TEXT_KINDS = ("progress", "cost_estimate", "warning", "error")


@dataclass
class AutoClassJob:
    root: Path
    running: bool = True
    lines: list[str] = field(default_factory=list)
    report_text: str = ""
    error: str | None = None
    task: asyncio.Task | None = None

    def on_event(self, event: object) -> None:
        kind = getattr(event, "kind", "")
        text = getattr(event, "text", "")
        if kind in _TEXT_KINDS and text:
            self.lines.append(f"{kind}: {text}" if kind in ("warning", "error") else str(text))
            del self.lines[:-200]


_jobs: dict[str, AutoClassJob] = {}


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _overlaps(a: Path | str, b: Path | str) -> bool:
    ka, kb = _key(a), _key(b)
    return ka == kb or ka.startswith(kb + os.sep) or kb.startswith(ka + os.sep)


def reset() -> None:
    cancel_all()
    _jobs.clear()


def get(root: Path) -> AutoClassJob | None:
    return _jobs.get(_key(root))


def all_jobs() -> list[AutoClassJob]:
    return list(_jobs.values())


def is_running(root: Path) -> bool:
    job = get(root)
    return job is not None and job.running


def running_overlapping(root: Path) -> AutoClassJob | None:
    for job in _jobs.values():
        if job.running and _overlaps(job.root, root):
            return job
    return None


def blocking_reason(root: Path) -> str | None:
    """Why auto-class / organize / undo must not start on ``root`` right now,
    or None when nothing overlapping is in flight."""
    job = running_overlapping(root)
    if job is not None:
        return f"Auto-class is already running on {job.root}."
    for session in web_session.all_sessions():
        live = session.mode == "organize" and session.started
        if live and (session.task is None or not session.task.done()):
            if _overlaps(session.target, root):
                return f"An organize session is open on {session.target}."
    return None


async def _run(job: AutoClassJob) -> None:
    try:
        report = await autoclass.run_auto_class_for_target(
            job.root, record_in_place=True, on_event=job.on_event
        )
        job.report_text = autoclass.format_report(report)
    except autoclass.AutoClassSetupError as exc:
        job.error = str(exc)
    except asyncio.CancelledError:
        job.error = "Cancelled."
        raise
    except Exception as exc:  # noqa: BLE001 - shown in the job panel
        job.error = f"Auto-class stopped: {exc}"
    finally:
        job.running = False
        folderstatus.invalidate(job.root)


def start(root: Path) -> AutoClassJob | None:
    """Start a job for ``root``; None when `blocking_reason` says no."""
    if blocking_reason(root) is not None:
        return None
    job = AutoClassJob(root=root)
    _jobs[_key(root)] = job
    job.task = asyncio.create_task(_run(job))
    return job


def cancel_all() -> None:
    for job in _jobs.values():
        if job.task is not None and not job.task.done():
            job.task.cancel()
