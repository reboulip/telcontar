"""Tests for the project event journal (server/events.py + the tool wrappers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.events import Event, all_events, append
from server.tools import create_event, list_events


@pytest.fixture()
def events_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "events.jsonl"


# --- Event dataclass --------------------------------------------------------


class TestEvent:
    def test_new_assigns_id_and_created_at(self) -> None:
        ev = Event.new("Lancé le projet", "2024-01-15")
        assert ev.event_id
        assert ev.sentence == "Lancé le projet"
        assert ev.date == "2024-01-15"
        assert ev.created_at  # ISO timestamp

    def test_new_allows_null_date(self) -> None:
        ev = Event.new("Décidé d'archiver les drafts")
        assert ev.date is None

    def test_roundtrip_dict(self) -> None:
        ev = Event.new("Validé le cadrage", "2024-02-01")
        restored = Event.from_dict(ev.to_dict())
        assert restored == ev

    def test_from_dict_backfills_missing_event_id(self) -> None:
        restored = Event.from_dict({"sentence": "Ouvert le COPIL", "date": None})
        assert restored.event_id
        assert restored.created_at == ""


# --- module-level append / all_events ---------------------------------------


class TestAppendAllEvents:
    def test_append_creates_file_and_dir(self, events_path: Path) -> None:
        append(events_path, Event.new("Initié le projet", "2024-01-01"))
        assert events_path.is_file()

    def test_all_events_empty_when_missing(self, events_path: Path) -> None:
        assert all_events(events_path) == []

    def test_append_then_read_back_in_order(self, events_path: Path) -> None:
        append(events_path, Event.new("Première étape", "2024-01-01"))
        append(events_path, Event.new("Deuxième étape", "2024-02-01"))
        evs = all_events(events_path)
        assert [e.sentence for e in evs] == ["Première étape", "Deuxième étape"]

    def test_unicode_preserved(self, events_path: Path) -> None:
        append(events_path, Event.new("Réunion de cadrage à Évry", "2024-03-01"))
        line = events_path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["sentence"] == "Réunion de cadrage à Évry"


# --- tool wrappers ----------------------------------------------------------


class TestCreateEventTool:
    def test_create_event_persists(self, events_path: Path) -> None:
        out = create_event("Approuvé le budget", "2024-04-01", events_path)
        assert out["sentence"] == "Approuvé le budget"
        assert out["date"] == "2024-04-01"
        assert events_path.is_file()

    def test_create_event_strips_whitespace(self, events_path: Path) -> None:
        out = create_event("  Clôturé la phase 1  ", None, events_path)
        assert out["sentence"] == "Clôturé la phase 1"
        assert out["date"] is None

    def test_create_event_rejects_blank_sentence(self, events_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            create_event("   ", "2024-01-01", events_path)

    def test_list_events_empty(self, events_path: Path) -> None:
        assert list_events(events_path) == []

    def test_list_events_returns_all_in_order(self, events_path: Path) -> None:
        create_event("Lancé le chantier", "2024-01-01", events_path)
        create_event("Livré le lot 1", "2024-02-01", events_path)
        evs = list_events(events_path)
        assert [e["sentence"] for e in evs] == ["Lancé le chantier", "Livré le lot 1"]
        assert all("event_id" in e for e in evs)
