"""Tests for session persistence (Y2): host/web/sessions.py's home-index/
snapshot logic (plain pytest, no NiceGUI) and host/web/sessions_view.py's
rendering (NiceGUI's headless `user` fixture, shared setup from
tests/web_harness.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nicegui.testing import User

from config import settings as settings_module
from host.web import session as web_session
from host.web import sessions as sessions_store
from host.web.session import RunSession

from tests.web_harness import (  # noqa: F401
    _fast_refresh,
    _preserve_dunder_main,
    _reset_session_registry,
)


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard as tests/test_web_corpus.py/test_web_graph.py —
    resolve_sessions_dir and user_sessions_index_path() both route through
    Settings()/_USER_CONFIG_DIR, which fall back to the developer's real
    ~/.telcontar absent an explicit override."""
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")


def _make_session(tmp_path: Path, run_id: str = "aaaaaaaaaaaaaaaa") -> RunSession:
    session = RunSession(run_id=run_id, target=tmp_path)
    session.add_turn("user", "hello")
    session.add_turn("telcontar", "hi")
    session.add_activity("Reading documents…")
    session.history = [{"role": "user", "content": "hello"}]
    return session


# ── host/web/sessions.py ─────────────────────────────────────────────────────


class TestIsValidRunId:
    def test_accepts_urlsafe_token(self) -> None:
        assert sessions_store.is_valid_run_id("abcDEF123_-xyz")

    def test_rejects_too_short(self) -> None:
        assert not sessions_store.is_valid_run_id("short")

    def test_rejects_path_traversal(self) -> None:
        assert not sessions_store.is_valid_run_id("../../etc/passwd")

    def test_rejects_slash(self) -> None:
        assert not sessions_store.is_valid_run_id("aaaaaaaa/bbbbbbbb")


class TestRecordStartedAndSnapshot:
    def test_record_started_creates_an_index_entry(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)

        sessions_store.record_started(session)

        entries = sessions_store.list_index()
        assert len(entries) == 1
        assert entries[0]["run_id"] == session.run_id
        assert entries[0]["target"] == str(tmp_path)
        assert entries[0]["status"] == "running"

    def test_record_started_preserves_created_at_on_second_call(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        sessions_store.record_started(session)
        first_created_at = sessions_store.list_index()[0]["created_at"]

        sessions_store.record_started(session)

        assert sessions_store.list_index()[0]["created_at"] == first_created_at

    def test_snapshot_writes_a_per_target_file(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)

        sessions_store.snapshot(session)

        data = sessions_store.load_snapshot(session.run_id, tmp_path)
        assert data is not None
        assert data["run_id"] == session.run_id
        assert [t["text"] for t in data["transcript"]] == ["hello", "hi"]
        assert data["history"] == [{"role": "user", "content": "hello"}]

    def test_snapshot_updates_index_status(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        sessions_store.record_started(session)

        session.done = True
        sessions_store.snapshot(session)

        assert sessions_store.list_index()[0]["status"] == "done"

    def test_snapshot_status_is_error_when_session_errored(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        session.error = "boom"

        sessions_store.snapshot(session)

        data = sessions_store.load_snapshot(session.run_id, tmp_path)
        assert data is not None
        assert data["status"] == "error"

    def test_snapshot_never_raises_on_a_bad_target(self, tmp_path: Path) -> None:
        # A file where a directory is needed makes resolve_sessions_dir's
        # mkdir fail — snapshot() must swallow this, never propagate.
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        session = RunSession(run_id="bbbbbbbbbbbbbbbb", target=blocker / "sub")

        sessions_store.snapshot(session)  # must not raise


class TestListIndex:
    def test_empty_when_no_index_file(self) -> None:
        assert sessions_store.list_index() == []

    def test_sorted_newest_active_first(self, tmp_path: Path) -> None:
        older = _make_session(tmp_path, run_id="aaaaaaaaaaaaaaaa")
        sessions_store.record_started(older)
        newer = _make_session(tmp_path, run_id="bbbbbbbbbbbbbbbb")
        sessions_store.record_started(newer)
        # Force distinct last_active_at ordering deterministically.
        sessions_store.snapshot(newer)

        entries = sessions_store.list_index()

        assert entries[0]["run_id"] == newer.run_id


class TestLoadSnapshot:
    def test_none_when_missing(self, tmp_path: Path) -> None:
        assert sessions_store.load_snapshot("aaaaaaaaaaaaaaaa", tmp_path) is None

    def test_none_for_invalid_run_id(self, tmp_path: Path) -> None:
        assert sessions_store.load_snapshot("../../etc/passwd", tmp_path) is None

    def test_none_on_corrupt_file(self, tmp_path: Path) -> None:
        from host.paths import resolve_sessions_dir

        path = resolve_sessions_dir(tmp_path) / "aaaaaaaaaaaaaaaa.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")

        assert sessions_store.load_snapshot("aaaaaaaaaaaaaaaa", tmp_path) is None


class TestRestoreSession:
    def test_rebuilds_transcript_activity_and_history(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        sessions_store.snapshot(session)
        data = sessions_store.load_snapshot(session.run_id, tmp_path)
        assert data is not None

        restored = sessions_store.restore_session(data)

        assert restored.run_id == session.run_id
        assert restored.target == tmp_path
        assert [t.text for t in restored.transcript] == ["hello", "hi"]
        assert [a.text for a in restored.activity_log] == ["Reading documents…"]
        assert restored.history == [{"role": "user", "content": "hello"}]
        assert restored.started is True

    def test_seq_continues_after_restore(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        sessions_store.snapshot(session)
        data = sessions_store.load_snapshot(session.run_id, tmp_path)
        assert data is not None

        restored = sessions_store.restore_session(data)
        restored.add_turn("user", "continuing")

        # The new turn's seq must be strictly greater than every seq already
        # persisted, or it would collide when merged into the thread.
        assert restored.transcript[-1].seq > max(
            item.seq for item in (*session.transcript, *session.activity_log)
        )


# ── host/web/session.py::register ───────────────────────────────────────────


class TestRegister:
    def test_register_inserts_under_its_own_run_id(self, tmp_path: Path) -> None:
        session = RunSession(run_id="cccccccccccccccc", target=tmp_path)

        web_session.register(session)

        assert web_session.get("cccccccccccccccc") is session

    def test_register_sets_active_for_organize_mode(self, tmp_path: Path) -> None:
        session = RunSession(run_id="dddddddddddddddd", target=tmp_path, mode="organize")

        web_session.register(session)

        assert web_session.get_active() is session


# ── host/web/sessions_view.py ────────────────────────────────────────────────


async def test_sessions_page_shows_empty_state(user: User) -> None:
    await user.open("/sessions")

    await user.should_see(marker="sessions-empty")


async def test_sessions_page_lists_a_dead_session(user: User, tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    sessions_store.snapshot(session)

    await user.open("/sessions")

    await user.should_see(marker="session-row")
    await user.should_see(tmp_path.name)


async def test_sessions_page_shows_live_session_as_open(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    sessions_store.record_started(session)

    await user.open("/sessions")

    await user.should_see(marker="btn-session-open")


async def test_session_detail_page_shows_transcript_and_resume(user: User, tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    sessions_store.snapshot(session)

    await user.open(f"/sessions/{session.run_id}")

    await user.should_see(marker="sessions-transcript")
    await user.should_see("hello")
    await user.should_see("hi")
    await user.should_see(marker="btn-session-resume")


async def test_session_detail_page_invalid_run_id(user: User) -> None:
    # A single path segment with disallowed characters — long enough that
    # the router doesn't 404 it outright, so it actually reaches
    # is_valid_run_id()'s check.
    await user.open("/sessions/not-a-real-run-id!!")

    await user.should_see(marker="sessions-invalid")


async def test_session_detail_page_not_found(user: User) -> None:
    await user.open("/sessions/aaaaaaaaaaaaaaaa")

    await user.should_see(marker="sessions-not-found")


async def test_session_detail_page_live_session_links_to_run(user: User, tmp_path: Path) -> None:
    session = web_session.create(tmp_path)
    sessions_store.record_started(session)

    await user.open(f"/sessions/{session.run_id}")

    await user.should_see(marker="btn-session-open-live")


async def test_resume_registers_session_and_navigates_to_run(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from host.web import bridge as bridge_module

    session = _make_session(tmp_path)
    sessions_store.snapshot(session)

    started = {}

    def _fake_start_resumed(self: object) -> None:
        started["called"] = True
        return None

    monkeypatch.setattr(bridge_module.AgentBridge, "start_resumed", _fake_start_resumed)

    await user.open(f"/sessions/{session.run_id}")
    await user.should_see(marker="btn-session-resume")

    user.find(marker="btn-session-resume").click()

    assert web_session.get(session.run_id) is not None
    assert started.get("called") is True


async def test_sessions_nav_tab_always_enabled(user: User, tmp_path: Path) -> None:
    await user.open("/")
    await user.should_see(marker="nav-sessions")

    [sessions_tab] = user.find(marker="nav-sessions").elements
    assert sessions_tab.enabled is True
