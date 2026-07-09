"""M11 — save_user_config never silently falls back to a plaintext API key (S8).

Patches the module's user-config-dir constants to a tmp_path so these tests
never touch the real ~/.telcontar/config.env.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import settings as settings_module
from config.settings import PlaintextKeyFallbackNeeded, save_user_config


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / ".telcontar"
    config_file = config_dir / "config.env"
    monkeypatch.setattr(settings_module, "_USER_CONFIG_DIR", config_dir)
    monkeypatch.setattr(settings_module, "_USER_CONFIG", config_file)
    return config_file


class TestSaveUserConfigKeyringSucceeds:
    def test_key_stored_via_keyring_not_in_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_user_config: Path
    ) -> None:
        monkeypatch.setattr(settings_module, "_keyring_set", lambda key: True)

        save_user_config({"llm_base_url": "https://example.com", "llm_api_key": "sk-secret"})

        content = _isolated_user_config.read_text(encoding="utf-8")
        assert "sk-secret" not in content
        assert "LLM_BASE_URL=https://example.com" in content


class TestSaveUserConfigKeyringUnavailable:
    def test_raises_instead_of_silently_writing_plaintext(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_user_config: Path
    ) -> None:
        monkeypatch.setattr(settings_module, "_keyring_set", lambda key: False)

        with pytest.raises(PlaintextKeyFallbackNeeded):
            save_user_config({"llm_base_url": "https://example.com", "llm_api_key": "sk-secret"})

        # Nothing plaintext-sensitive was written on the rejected attempt.
        assert not _isolated_user_config.exists() or "sk-secret" not in (
            _isolated_user_config.read_text(encoding="utf-8")
        )

    def test_explicit_opt_in_writes_plaintext_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_user_config: Path
    ) -> None:
        monkeypatch.setattr(settings_module, "_keyring_set", lambda key: False)

        save_user_config(
            {"llm_base_url": "https://example.com", "llm_api_key": "sk-secret"},
            allow_plaintext_fallback=True,
        )

        content = _isolated_user_config.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-secret" in content

    def test_non_sensitive_updates_do_not_require_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_user_config: Path
    ) -> None:
        monkeypatch.setattr(settings_module, "_keyring_set", lambda key: False)

        # No llm_api_key in updates — the keyring is never consulted, so this
        # must succeed even though the keyring itself would fail.
        save_user_config({"profile": "personal_files"})

        content = _isolated_user_config.read_text(encoding="utf-8")
        assert "PROFILE=personal_files" in content
