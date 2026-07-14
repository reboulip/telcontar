"""Tests for the egress audit log (server/egress.py) and its wiring into the
read_file/extract_text/compare_documents MCP handlers (M12/S8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import Settings
from server import main as server_main
from server.egress import EgressEntry, all_entries, append


@pytest.fixture()
def egress_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "egress.jsonl"


class TestEgressEntry:
    def test_new_sets_fields_and_timestamp(self) -> None:
        entry = EgressEntry.new("/a/doc.txt", 123, "read_file")
        assert entry.path == "/a/doc.txt"
        assert entry.size_bytes == 123
        assert entry.tool == "read_file"
        assert entry.timestamp  # ISO timestamp


class TestAppendAllEntries:
    def test_append_creates_file_and_dir(self, egress_path: Path) -> None:
        append(egress_path, EgressEntry.new("/a/doc.txt", 10, "read_file"))
        assert egress_path.is_file()

    def test_all_entries_empty_when_missing(self, egress_path: Path) -> None:
        assert all_entries(egress_path) == []

    def test_append_then_read_back_in_order(self, egress_path: Path) -> None:
        append(egress_path, EgressEntry.new("/a.txt", 10, "read_file"))
        append(egress_path, EgressEntry.new("/b.pdf", 20, "extract_text"))
        entries = all_entries(egress_path)
        assert [e["path"] for e in entries] == ["/a.txt", "/b.pdf"]
        assert [e["tool"] for e in entries] == ["read_file", "extract_text"]

    def test_unicode_preserved(self, egress_path: Path) -> None:
        append(egress_path, EgressEntry.new("/Évry/réunion.txt", 5, "read_file"))
        line = egress_path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["path"] == "/Évry/réunion.txt"


class TestHandlersLogEgress:
    """Spot-check that the three content-egress tools actually log."""

    def test_read_file_logs_egress(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "target"
        target.mkdir()
        doc = target / "doc.txt"
        doc.write_text("hello world")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.read_file(str(doc))

        entries = all_entries(egress_path)
        assert len(entries) == 1
        assert entries[0]["path"] == str(doc)
        assert entries[0]["tool"] == "read_file"
        assert entries[0]["size_bytes"] == len("hello world".encode("utf-8"))

    def test_extract_text_logs_egress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        doc = target / "doc.txt"
        doc.write_text("some content")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.extract_text(str(doc))

        entries = all_entries(egress_path)
        assert len(entries) == 1
        assert entries[0]["tool"] == "extract_text"

    def test_compare_documents_logs_both_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        doc_a = target / "a.txt"
        doc_b = target / "b.txt"
        doc_a.write_text("version one")
        doc_b.write_text("version two")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.compare_documents(str(doc_a), str(doc_b))

        entries = all_entries(egress_path)
        assert {e["path"] for e in entries} == {str(doc_a), str(doc_b)}
        assert all(e["tool"] == "compare_documents" for e in entries)

    def test_read_file_batch_logs_egress_per_file_under_batch_tool_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        a = target / "a.txt"
        b = target / "b.txt"
        a.write_text("hello")
        b.write_text("world")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.read_file_batch([str(a), str(b)])

        entries = all_entries(egress_path)
        assert {e["path"] for e in entries} == {str(a), str(b)}
        assert all(e["tool"] == "read_file_batch" for e in entries)

    def test_extract_text_batch_logs_egress_per_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        doc = target / "doc.txt"
        doc.write_text("some content")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.extract_text_batch([str(doc)])

        entries = all_entries(egress_path)
        assert len(entries) == 1
        assert entries[0]["tool"] == "extract_text_batch"

    def test_batch_error_entries_are_not_logged_as_egress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        good = target / "good.txt"
        good.write_text("ok")
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        server_main.read_file_batch([str(good), str(target / "missing.txt")])

        entries = all_entries(egress_path)
        assert len(entries) == 1
        assert entries[0]["path"] == str(good)

    def test_failed_read_does_not_log_egress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        egress_path = tmp_path / "egress.jsonl"
        cfg = Settings(target_dir=target, egress_path=egress_path)
        monkeypatch.setattr(server_main, "_get_settings", lambda: cfg)

        with pytest.raises(ValueError):
            server_main.read_file(str(target / "missing.txt"))

        assert all_entries(egress_path) == []
