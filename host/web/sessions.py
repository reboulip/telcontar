"""Session persistence (Y2) — home-directory index (metadata only) +
per-target snapshots (transcript/activity log/LLM history), so a session
survives a process restart.

Two tiers, deliberately: the home-directory index
(``config.settings.user_sessions_index_path()``) lives outside every
allowlist/egress boundary this project's security model reasons about, so
it must never carry corpus-derived text (a document title, a summary, an
entity name mentioned in the conversation) — only ``run_id``/``target``/
``mode``/timestamps/``status``. The per-target snapshot
(``Settings.sessions_dir``, under ``.organizer/``) lives inside the same
boundary the registry/journal/graph already trust, so the transcript,
activity log, and LLM message history — all derived from the user's own
documents — belong there instead.

NiceGUI-free — host/web/sessions_view.py owns rendering, the same split as
host/web/corpus.py / corpus_view.py. server.*/config.settings imports are
late (inside the functions) to avoid dragging in their dependency chain at
module import time, matching that same discipline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from host.paths import resolve_sessions_dir
from host.web.session import ActivityEntry, RunSession, TranscriptItem

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def is_valid_run_id(run_id: str) -> bool:
    """True if ``run_id`` is safe to join into a filesystem path — every
    route that takes a run_id from the URL must check this before it
    reaches ``_snapshot_path``/``load_snapshot`` below; the index itself is
    a user-writable JSON file, so its ``target`` values are untrusted too."""
    return bool(_RUN_ID_RE.match(run_id))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _snapshot_path(target: Path, run_id: str) -> Path | None:
    if not is_valid_run_id(run_id):
        return None
    return resolve_sessions_dir(target) / f"{run_id}.json"


def _home_index_path() -> Path:
    from config.settings import user_sessions_index_path

    return user_sessions_index_path()


def _read_index() -> list[dict]:
    path = _home_index_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_index(entries: list[dict]) -> None:
    _atomic_write(_home_index_path(), json.dumps(entries, indent=2, ensure_ascii=False))


def _upsert_index_entry(entry: dict) -> None:
    entries = _read_index()
    for i, existing in enumerate(entries):
        if existing.get("run_id") == entry["run_id"]:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    _write_index(entries)


def _status_of(session: RunSession) -> str:
    if session.error:
        return "error"
    if session.done:
        return "done"
    return "running"


def record_started(session: RunSession) -> None:
    """Upsert this session's home-index entry — called once at the start of
    a run (fresh or resumed), so it appears in the sessions list
    immediately rather than only after the first checkpoint. Preserves an
    existing entry's ``created_at`` on resume. Never raises."""
    try:
        entries = _read_index()
        existing = next((e for e in entries if e.get("run_id") == session.run_id), None)
        entry = {
            "run_id": session.run_id,
            "target": str(session.target),
            "mode": session.mode,
            "created_at": (existing or {}).get("created_at") or _now(),
            "last_active_at": _now(),
            "status": "running",
        }
        _upsert_index_entry(entry)
    except Exception:
        pass


def snapshot(session: RunSession) -> None:
    """Persist this session's transcript/activity_log/history to its
    per-target snapshot file, and refresh its home-index entry. Never
    raises — a failed checkpoint must never break the run it's
    checkpointing (mirrors host/llmlog.py's same contract)."""
    try:
        status = _status_of(session)
        payload = {
            "run_id": session.run_id,
            "target": str(session.target),
            "mode": session.mode,
            "status": status,
            "last_active_at": _now(),
            "transcript": [asdict(item) for item in session.transcript],
            "activity_log": [asdict(item) for item in session.activity_log],
            "history": session.history or [],
        }
        path = _snapshot_path(session.target, session.run_id)
        if path is not None:
            _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))

        entries = _read_index()
        existing = next((e for e in entries if e.get("run_id") == session.run_id), None)
        entry = {
            "run_id": session.run_id,
            "target": str(session.target),
            "mode": session.mode,
            "created_at": (existing or {}).get("created_at") or _now(),
            "last_active_at": _now(),
            "status": status,
        }
        _upsert_index_entry(entry)
    except Exception:
        pass


def list_index() -> list[dict]:
    """Every known session's metadata, newest-active first. [] on any error."""
    entries = _read_index()
    return sorted(entries, key=lambda e: e.get("last_active_at", ""), reverse=True)


def load_snapshot(run_id: str, target: Path) -> dict | None:
    """The full per-target snapshot for ``run_id`` under ``target``, or None
    if missing/unreadable/invalid. Never raises."""
    path = _snapshot_path(target, run_id)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def restore_session(snapshot_data: dict) -> RunSession:
    """Build a fresh RunSession from a loaded snapshot, ready to hand to
    ``host.web.session.register()`` and a bridge's resume path. Keeps the
    persisted run_id so existing ``/run/{run_id}`` links keep working."""
    transcript = [
        TranscriptItem(seq=t["seq"], speaker=t["speaker"], text=t["text"])
        for t in snapshot_data.get("transcript") or []
    ]
    activity_log = [
        ActivityEntry(seq=a["seq"], text=a["text"]) for a in snapshot_data.get("activity_log") or []
    ]
    mode_raw = snapshot_data.get("mode", "organize")
    mode: Literal["organize", "query"] = "query" if mode_raw == "query" else "organize"

    session = RunSession(
        run_id=snapshot_data["run_id"],
        target=Path(snapshot_data["target"]),
        mode=mode,
        transcript=transcript,
        activity_log=activity_log,
        history=snapshot_data.get("history") or None,
        started=True,
    )
    max_transcript_seq = max((item.seq for item in transcript), default=0)
    max_activity_seq = max((item.seq for item in activity_log), default=0)
    session.seed_seq(max(max_transcript_seq, max_activity_seq))
    return session
