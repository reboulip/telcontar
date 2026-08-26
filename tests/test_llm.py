"""Tests for the LLM client factory (host/llm.py) — Y4, GH #61."""

from __future__ import annotations

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI

from config.settings import Settings
from host.configflow import AZURE_API_VERSION
from host.llm import AzureTarget, make_client, resolve_azure_target


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
