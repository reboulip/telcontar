"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fake_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide required LLM env vars so Settings can be instantiated in any test."""
    monkeypatch.setenv("LLM_BASE_URL", "https://fake.api/v1")
    monkeypatch.setenv("LLM_API_KEY", "fake-key-for-testing")
