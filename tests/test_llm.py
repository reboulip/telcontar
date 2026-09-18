"""Tests for the LLM client factory (host/llm.py) — Y4, GH #61."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI

from config.settings import Settings
from host.configflow import AZURE_API_VERSION
from host.llm import (
    AzureTarget,
    ReloadingClient,
    make_client,
    make_reloading_client,
    resolve_azure_target,
)


class TestResolveAzureTarget:
    def test_blank_base_url_is_not_azure(self) -> None:
        assert resolve_azure_target("", "gpt-5", "") is None

    def test_mammouth_url_with_no_api_version_is_not_azure(self) -> None:
        assert resolve_azure_target("https://api.mammouth.ai/v1", "gpt-5", "") is None

    def test_plain_openai_compatible_url_is_not_azure(self) -> None:
        assert resolve_azure_target("https://api.openai.com/v1", "gpt-5", "") is None

    def test_bare_resource_root_with_explicit_api_version(self) -> None:
        target = resolve_azure_target(
            "https://resourcename.openai.azure.com", "gpt-5.6-luna", "2024-12-01-preview"
        )
        assert target == AzureTarget(
            endpoint="https://resourcename.openai.azure.com",
            deployment="gpt-5.6-luna",
            api_version="2024-12-01-preview",
        )

    def test_resource_root_with_trailing_slash(self) -> None:
        target = resolve_azure_target(
            "https://resourcename.openai.azure.com/", "gpt-5", "2024-12-01-preview"
        )
        assert target is not None
        assert target.endpoint == "https://resourcename.openai.azure.com"

    def test_azure_host_with_blank_api_version_falls_back_to_default(self) -> None:
        target = resolve_azure_target("https://resourcename.openai.azure.com", "gpt-5", "")
        assert target is not None
        assert target.api_version == AZURE_API_VERSION

    def test_openai_suffix_is_stripped_to_resource_root(self) -> None:
        target = resolve_azure_target(
            "https://resourcename.openai.azure.com/openai", "gpt-5", "2024-12-01-preview"
        )
        assert target is not None
        assert target.endpoint == "https://resourcename.openai.azure.com"
        assert target.deployment == "gpt-5"

    def test_deployment_segment_is_extracted_and_wins_over_model(self) -> None:
        target = resolve_azure_target(
            "https://resourcename.openai.azure.com/openai/deployments/gpt-5-deployed",
            "gpt-5-different-model-name",
            "2024-12-01-preview",
        )
        assert target is not None
        assert target.endpoint == "https://resourcename.openai.azure.com"
        assert target.deployment == "gpt-5-deployed"

    def test_openai_v1_path_is_never_azure_even_with_api_version(self) -> None:
        # Real-world regression (issue #61): a dated api-version alongside
        # /openai/v1 produces Azure's "API version not supported" error.
        # /openai/v1 must always take the plain-client path.
        assert (
            resolve_azure_target(
                "https://resourcename.openai.azure.com/openai/v1",
                "gpt-5.6-luna",
                "2025-04-01-preview",
            )
            is None
        )

    def test_openai_v1_path_with_no_api_version_is_not_azure(self) -> None:
        # The reporter's own final working configuration.
        assert (
            resolve_azure_target(
                "https://resourcename.openai.azure.com/openai/v1", "gpt-5.6-luna", ""
            )
            is None
        )


class TestMakeClient:
    def test_non_azure_config_builds_plain_async_openai(self) -> None:
        settings = Settings(
            llm_base_url="https://api.mammouth.ai/v1",
            llm_api_key="k",
            llm_model="gpt-5",
        )
        client = make_client(settings)
        try:
            assert type(client) is AsyncOpenAI
        finally:
            pass

    def test_azure_config_builds_async_azure_openai(self) -> None:
        settings = Settings(
            llm_base_url="https://resourcename.openai.azure.com",
            llm_api_key="k",
            llm_model="gpt-5.6-luna",
            llm_api_version="2024-12-01-preview",
        )
        client = make_client(settings)
        assert isinstance(client, AsyncAzureOpenAI)

    def test_azure_v1_config_builds_plain_async_openai(self) -> None:
        settings = Settings(
            llm_base_url="https://resourcename.openai.azure.com/openai/v1",
            llm_api_key="k",
            llm_model="gpt-5.6-luna",
        )
        client = make_client(settings)
        assert type(client) is AsyncOpenAI

    def test_make_client_logs_a_client_entry(self, tmp_path) -> None:
        from host import llmlog

        log_path = tmp_path / "llm-debug.jsonl"
        settings = Settings(
            llm_base_url="https://resourcename.openai.azure.com",
            llm_api_key="SECRETKEY123",
            llm_model="gpt-5.6-luna",
            llm_api_version="2024-12-01-preview",
            llm_debug_log_path=log_path,
        )
        make_client(settings)
        entries = llmlog.all_entries(log_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "client"
        assert entries[0]["detail"]["client_class"] == "AsyncAzureOpenAI"
        assert entries[0]["detail"]["deployment"] == "gpt-5.6-luna"
        # No API key anywhere in the logged detail.
        assert "SECRETKEY123" not in str(entries[0]["detail"].values())


# ── ReloadingClient / make_reloading_client (Z2, #67) ─────────────────────────


def _settings(model: str = "gpt-5") -> Settings:
    return Settings(llm_base_url="https://api.mammouth.ai/v1", llm_api_key="k", llm_model=model)


def _stub_make_client(monkeypatch: pytest.MonkeyPatch) -> list[AsyncMock]:
    """Replace host.llm.make_client with a stub returning a fresh AsyncMock
    client each call, so identity/close() assertions don't depend on real
    network client construction."""
    built: list[AsyncMock] = []

    def fake_make_client(settings: Settings) -> AsyncMock:
        client = AsyncMock()
        built.append(client)
        return client

    monkeypatch.setattr("host.llm.make_client", fake_make_client)
    return built


class TestReloadingClient:
    async def test_pins_the_chat_completions_create_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: if host/agent.py ever reaches for another
        client member, this must fail loudly rather than silently no-op."""
        _stub_make_client(monkeypatch)
        handle = make_reloading_client(_settings())
        assert callable(handle.chat.completions.create)

    async def test_ensure_current_noop_when_revision_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_make_client(monkeypatch)
        load_calls: list[int] = []
        monkeypatch.setattr(
            "config.settings.load",
            lambda: load_calls.append(1) or _settings(),  # type: ignore[func-returns-value]
        )

        handle = ReloadingClient(_settings())
        await handle.ensure_current()

        assert load_calls == []

    async def test_ensure_current_reloads_and_rebuilds_client_on_revision_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = _stub_make_client(monkeypatch)
        revision = {"n": 0}
        monkeypatch.setattr("config.settings.config_revision", lambda: revision["n"])
        monkeypatch.setattr("config.settings.load", lambda: _settings(model="gpt-5-new"))

        settings = _settings()
        handle = ReloadingClient(settings)
        first_client = built[0]
        revision["n"] = 1  # simulate a save happened

        await handle.ensure_current()

        assert settings.llm_model == "gpt-5-new"
        assert len(built) == 2
        first_client.close.assert_awaited_once()

    async def test_ensure_current_leaves_non_reloadable_fields_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """approval_mode (and every path field) must never hot-swap — a live
        run's already-consented-to approval gate must not silently relax."""
        _stub_make_client(monkeypatch)
        revision = {"n": 0}
        monkeypatch.setattr("config.settings.config_revision", lambda: revision["n"])
        fresh = _settings(model="gpt-5-new")
        fresh.approval_mode = "never"
        monkeypatch.setattr("config.settings.load", lambda: fresh)

        settings = _settings()
        settings.approval_mode = "always"
        handle = ReloadingClient(settings)
        revision["n"] = 1

        await handle.ensure_current()

        assert settings.llm_model == "gpt-5-new"
        assert settings.approval_mode == "always"

    async def test_ensure_current_calls_on_reload_with_model_and_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_make_client(monkeypatch)
        revision = {"n": 0}
        monkeypatch.setattr("config.settings.config_revision", lambda: revision["n"])
        monkeypatch.setattr(
            "config.settings.load",
            lambda: Settings(
                llm_base_url="https://new.endpoint.example/v1",
                llm_api_key="k",
                llm_model="gpt-5-new",
            ),
        )

        seen: list[tuple[str, str]] = []
        handle = ReloadingClient(
            _settings(), on_reload=lambda msg, model: seen.append((msg, model))
        )
        revision["n"] = 1

        await handle.ensure_current()

        assert len(seen) == 1
        message, model = seen[0]
        assert model == "gpt-5-new"
        assert "gpt-5-new" in message
        assert "new.endpoint.example" in message
        assert "k" not in message  # never leak the API key

    async def test_ensure_current_keeps_existing_client_when_reload_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = _stub_make_client(monkeypatch)
        revision = {"n": 0}
        monkeypatch.setattr("config.settings.config_revision", lambda: revision["n"])

        def _boom() -> Settings:
            raise ValueError("LLM endpoint not configured")

        monkeypatch.setattr("config.settings.load", _boom)

        handle = ReloadingClient(_settings())
        revision["n"] = 1

        await handle.ensure_current()  # must not raise

        assert len(built) == 1  # no new client was built
        built[0].close.assert_not_awaited()

    async def test_ensure_current_noop_when_reloaded_settings_are_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A revision bump from an unrelated settings save (e.g. only the
        profile changed) must not rebuild the client or fire on_reload."""
        built = _stub_make_client(monkeypatch)
        revision = {"n": 0}
        monkeypatch.setattr("config.settings.config_revision", lambda: revision["n"])
        monkeypatch.setattr("config.settings.load", lambda: _settings())

        seen: list[tuple[str, str]] = []
        handle = ReloadingClient(
            _settings(), on_reload=lambda msg, model: seen.append((msg, model))
        )
        revision["n"] = 1

        await handle.ensure_current()

        assert len(built) == 1
        assert seen == []

    async def test_create_delegates_to_the_current_underlying_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = _stub_make_client(monkeypatch)
        handle = make_reloading_client(_settings())

        await handle.chat.completions.create(model="gpt-5", messages=[])

        built[0].chat.completions.create.assert_awaited_once_with(model="gpt-5", messages=[])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
