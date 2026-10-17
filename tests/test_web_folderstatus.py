"""AB4a — folder-status engine (host/web/folderstatus.py), NiceGUI-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.web import folderstatus as fs
from server.tools import compute_checksum


@pytest.fixture(autouse=True)
def _inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "SCAN_INLINE", True)
    monkeypatch.setattr(fs, "MIN_RESCAN_INTERVAL", 0.0)
    fs.reset()
    yield
    fs.reset()


def _register(root: Path, *files: Path) -> None:
    docs = {}
    for f in files:
        c = compute_checksum(str(f))["checksum"]
        docs[c] = {
            "checksum": c,
            "path": str(f),
            "title": f.name,
            "type": "other",
            "summary": "",
            "provenance": "",
            "date": "",
            "entities": [],
            "status": "active",
            "first_seen": "2026-01-01",
            "last_analyzed": "2026-01-01",
        }
    org = root / ".organizer"
    org.mkdir(exist_ok=True)
    (org / "registry.json").write_text(json.dumps({"documents": docs}), encoding="utf-8")


def test_unmanaged_without_organizer(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    assert fs.state_for(tmp_path)[0] == fs.UNMANAGED


def test_organizer_without_registry_is_unmanaged(tmp_path: Path) -> None:
    (tmp_path / ".organizer").mkdir()
    (tmp_path / "a.txt").write_text("a")
    assert fs.state_for(tmp_path)[0] == fs.UNMANAGED


def test_green_when_everything_registered(tmp_path: Path) -> None:
    a = tmp_path / "sub" / "a.txt"
    a.parent.mkdir()
    a.write_text("a")
    _register(tmp_path, a)
    assert fs.state_for(tmp_path)[0] == fs.UP_TO_DATE
    assert fs.state_for(tmp_path / "sub")[0] == fs.UP_TO_DATE


def test_yellow_rolls_up_to_ancestors_only(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("a")
    new = tmp_path / "sub" / "deep" / "new.txt"
    new.parent.mkdir(parents=True)
    new.write_text("new")
    (tmp_path / "other").mkdir()
    _register(tmp_path, a)
    assert fs.state_for(tmp_path)[0] == fs.NEW_FILES
    assert fs.state_for(tmp_path / "sub")[0] == fs.NEW_FILES
    assert fs.state_for(tmp_path / "sub" / "deep")[0] == fs.NEW_FILES
    assert fs.state_for(tmp_path / "other")[0] == fs.UP_TO_DATE
    assert fs.new_count(tmp_path) == 1


def test_moved_copy_matched_by_checksum_stays_green(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("same content")
    _register(tmp_path, a)
    moved = tmp_path / "x" / "renamed.txt"
    moved.parent.mkdir()
    a.rename(moved)
    assert fs.state_for(tmp_path)[0] == fs.UP_TO_DATE


def test_skips_organizer_quarantine_and_index_files(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("a")
    _register(tmp_path, a)
    (tmp_path / "INDEX.md").write_text("x")
    (tmp_path / "_quarantine").mkdir()
    (tmp_path / "_quarantine" / "old.txt").write_text("old")
    assert fs.state_for(tmp_path)[0] == fs.UP_TO_DATE


def test_corrupt_registry_keeps_pending_not_green(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".organizer").mkdir()
    (tmp_path / ".organizer" / "registry.json").write_text("{not json")
    assert fs.state_for(tmp_path)[0] == fs.PENDING


def test_truncated_scan_is_unknown_not_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tmp_path / "a.txt"
    a.write_text("a")
    _register(tmp_path, a)
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    monkeypatch.setattr(fs, "MAX_FILES", 1)
    assert fs.state_for(tmp_path)[0] in (fs.UNKNOWN, fs.NEW_FILES)


def test_decorate_is_in_place_and_reports_change() -> None:
    child = {
        "id": "/r/c",
        "label": "c",
        "children": [{"id": "/r/c/f", "label": "f", "children": []}],
    }
    nodes = [{"id": "/r", "label": "r", "children": [child]}]
    assert fs.folder_ids(nodes) == ["/r", "/r/c"]
    states = {"/r": fs.UP_TO_DATE, "/r/c": fs.NEW_FILES}
    assert fs.decorate(nodes, states) is True
    assert nodes[0]["children"][0] is child  # same objects, not replaced
    assert child["iconColor"] == "warning"
    assert "icon" not in child["children"][0]
    assert fs.decorate(nodes, states) is False
