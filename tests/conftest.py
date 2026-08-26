"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fake_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide required LLM env vars so Settings can be instantiated in any test.

    Also pins PROFILE explicitly: with no project .env, Settings() falls back to
    reading ~/.telcontar/config.env (the real per-machine user config written by
    the setup wizard), so a developer's own onboarding test can silently change
    which profile the suite runs against.
    """
    monkeypatch.setenv("LLM_BASE_URL", "https://fake.api/v1")
    monkeypatch.setenv("LLM_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("PROFILE", "is_it_project")


@pytest.fixture(autouse=True)
def _isolated_user_config_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect config.settings._USER_CONFIG_DIR (and therefore
    user_sessions_index_path(), Y2) to an ephemeral per-test directory.

    Without this, any test driving a real AgentBridge/QueryBridge (Y2's
    checkpointing writes to the home-directory sessions index on every
    on_event) or calling config.settings.save_user_config would write to
    the developer's real ~/.telcontar — the same class of leakage
    _fake_llm_env above already guards against for env vars, discovered
    when a Y2 smoke test found hundreds of pytest tmp_path entries polluting
    a real ~/.telcontar/sessions.json. Several test files already redirect
    this locally (tests/test_settings.py and others); this blanket version
    means no test file can omit it by mistake going forward — the local
    ones are harmless, idempotent duplicates of the same tmp_path value.
    """
    from config import settings as settings_module

    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", tmp_path / ".telcontar")
    monkeypatch.setattr(settings_module, "_USER_CONFIG", tmp_path / ".telcontar" / "config.env")
