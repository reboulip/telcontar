"""Tests for the token profiling log (host/tokenlog.py) — R2, GH #27."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.tokenlog import TokenLogEntry, all_entries, append


@pytest.fixture()
def token_log_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "tokens.jsonl"


class TestTokenLogEntry:
    def test_new_sets_fields_and_timestamp(self) -> None:
        entry = TokenLogEntry.new(
            run_id="r1",
            phase="analyze",
            step=0,
            call=1,
            model="gpt-5",
            docs=3,
            in_=100,
            cached_in=20,
            out=15,
            est_in=90,
            total_in=100,
            total_out=15,
        )
        assert entry.run_id == "r1"
        assert entry.phase == "analyze"
        assert entry.step == 0
        assert entry.call == 1
        assert entry.model == "gpt-5"
        assert entry.docs == 3
        assert entry.in_ == 100
        assert entry.cached_in == 20
        assert entry.out == 15
        assert entry.est_in == 90
        assert entry.total_in == 100
        assert entry.total_out == 15
        assert entry.duration_ms is None
        assert entry.ts  # ISO timestamp

    def test_to_dict_renames_in__to_in(self) -> None:
        entry = TokenLogEntry.new(
            run_id="r1",
            phase="organize",
            step=0,
            call=1,
            model="gpt-5",
            docs=None,
            in_=500,
            cached_in=0,
            out=100,
            est_in=None,
            total_in=500,
            total_out=100,
        )
        d = entry.to_dict()
        assert d["in"] == 500
        assert "in_" not in d


class TestAppendAllEntries:
    def test_append_creates_file_and_dir(self, token_log_path: Path) -> None:
        append(
            token_log_path,
            TokenLogEntry.new(
                run_id="r1",
                phase="analyze",
                step=0,
                call=1,
                model="gpt-5",
                docs=1,
                in_=10,
                cached_in=0,
                out=5,
                est_in=10,
                total_in=10,
                total_out=5,
            ),
        )
        assert token_log_path.is_file()

    def test_all_entries_empty_when_missing(self, token_log_path: Path) -> None:
        assert all_entries(token_log_path) == []

    def test_append_then_read_back_in_order(self, token_log_path: Path) -> None:
        append(
            token_log_path,
            TokenLogEntry.new(
                run_id="r1",
                phase="analyze",
                step=0,
                call=1,
                model="gpt-5",
                docs=1,
                in_=10,
                cached_in=0,
                out=5,
                est_in=10,
                total_in=10,
                total_out=5,
            ),
        )
        append(
            token_log_path,
            TokenLogEntry.new(
                run_id="r1",
                phase="analyze",
                step=1,
                call=2,
                model="gpt-5",
                docs=1,
                in_=20,
                cached_in=0,
                out=8,
                est_in=20,
                total_in=30,
                total_out=13,
            ),
        )
        entries = all_entries(token_log_path)
        assert [e["step"] for e in entries] == [0, 1]
        assert [e["in"] for e in entries] == [10, 20]
        assert [e["total_in"] for e in entries] == [10, 30]

    def test_unicode_preserved(self, token_log_path: Path) -> None:
        append(
            token_log_path,
            TokenLogEntry.new(
                run_id="Évry-réunion",
                phase="query",
                step=0,
                call=1,
                model="gpt-5",
                docs=None,
                in_=5,
                cached_in=0,
                out=2,
                est_in=None,
                total_in=5,
                total_out=2,
            ),
        )
        line = token_log_path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["run_id"] == "Évry-réunion"
