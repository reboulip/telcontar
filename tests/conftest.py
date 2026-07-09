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
