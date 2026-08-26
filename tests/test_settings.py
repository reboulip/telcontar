"""P2 — Settings.for_target rebases per-directory memory paths onto a target dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import settings as settings_module
from config.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")


class TestForTarget:
    def test_rebases_relative_memory_paths_onto_target(self, tmp_path: Path) -> None:
        target = tmp_path / "corpus"
        target.mkdir()
        settings = Settings()

        rebased = settings.for_target(target)

        assert rebased.target_dir == target.resolve()
        assert rebased.quarantine_dir == target.resolve() / "_quarantine"
        assert rebased.journal_path == target.resolve() / ".organizer" / "journal.jsonl"
        assert rebased.events_path == target.resolve() / ".organizer" / "events.jsonl"
        assert rebased.plans_dir == target.resolve() / ".organizer" / "plans"
        assert rebased.registry_path == target.resolve() / ".organizer" / "registry.json"
        assert rebased.graph_path == target.resolve() / ".organizer" / "graph.json"
        assert rebased.archive_path == target.resolve() / ".organizer" / "archive.jsonl"
        assert rebased.egress_path == target.resolve() / ".organizer" / "egress.jsonl"
        assert rebased.token_log_path == target.resolve() / ".organizer" / "tokens.jsonl"
        assert rebased.llm_debug_log_path == target.resolve() / ".organizer" / "llm-debug.jsonl"

    def test_absolute_paths_pass_through_unchanged(self, tmp_path: Path) -> None:
        target = tmp_path / "corpus"
        target.mkdir()
        explicit_journal = tmp_path / "elsewhere" / "journal.jsonl"
        settings = Settings(journal_path=explicit_journal)

        rebased = settings.for_target(target)

        assert rebased.journal_path == explicit_journal

    def test_profiles_dir_is_not_rebased(self, tmp_path: Path) -> None:
        target = tmp_path / "corpus"
        target.mkdir()
        settings = Settings()

        rebased = settings.for_target(target)

        assert rebased.profiles_dir == settings.profiles_dir

    def test_original_settings_object_is_unchanged(self, tmp_path: Path) -> None:
        target = tmp_path / "corpus"
        target.mkdir()
        settings = Settings()

        settings.for_target(target)

        assert settings.target_dir is None
        assert settings.journal_path == Path(".organizer/journal.jsonl")


class TestLoadRebasesWhenTargetDirSet:
    def test_load_rebases_paths_when_target_dir_env_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "corpus"
        target.mkdir()
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
        monkeypatch.setenv("LLM_API_KEY", "sk-secret")
        monkeypatch.setenv("TARGET_DIR", str(target))

        loaded = settings_module.load()

        assert loaded.target_dir == target.resolve()
        assert loaded.registry_path == target.resolve() / ".organizer" / "registry.json"

    def test_load_leaves_paths_relative_default_when_no_target_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
        monkeypatch.setenv("LLM_API_KEY", "sk-secret")
        monkeypatch.delenv("TARGET_DIR", raising=False)

        loaded = settings_module.load()

        assert loaded.target_dir is None
        assert loaded.registry_path == Path(".organizer/registry.json")
