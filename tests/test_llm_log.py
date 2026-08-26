"""Tests for the LLM debug log (host/llmlog.py) — Y5, GH #60."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from host import llmlog
from host.llmlog import LLMLogEntry


@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "llm-debug.jsonl"


class TestAppendAndAllEntries:
    def test_append_creates_parent_dirs_and_writes_jsonl(self, log_path: Path) -> None:
        llmlog.append(log_path, LLMLogEntry.new("client", {"a": 1}))
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["kind"] == "client"

    def test_all_entries_returns_empty_list_for_missing_file(self, log_path: Path) -> None:
        assert llmlog.all_entries(log_path) == []

    def test_all_entries_round_trips_multiple_appends(self, log_path: Path) -> None:
        llmlog.append(log_path, LLMLogEntry.new("request", {"n": 1}))
        llmlog.append(log_path, LLMLogEntry.new("response", {"n": 2}))
        entries = llmlog.all_entries(log_path)
        assert [e["kind"] for e in entries] == ["request", "response"]

    def test_append_swallows_oserror(self, tmp_path: Path) -> None:
        # A path with a file where a directory is needed raises OSError on mkdir.
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        bad_path = blocker / "sub" / "llm-debug.jsonl"
        llmlog.append(bad_path, LLMLogEntry.new("client", {}))  # must not raise


class TestRedactUrl:
    def test_redacts_api_key_query_param(self) -> None:
        url = httpx.URL("https://example.com/foo?api-key=SECRET&api-version=2024-01-01")
        redacted = llmlog._redact_url(url)
        assert "SECRET" not in redacted
        assert "api-version=2024-01-01" in redacted

    def test_url_with_no_query_is_unchanged_shape(self) -> None:
        url = httpx.URL("https://example.com/foo")
        redacted = llmlog._redact_url(url)
        assert redacted == "https://example.com/foo"


class TestLogClient:
    def test_logs_a_single_client_entry_with_no_key(self, log_path: Path) -> None:
        llmlog.log_client(
            log_path,
            client_class="AsyncAzureOpenAI",
            endpoint="https://res.openai.azure.com",
            api_version="2024-12-01-preview",
            deployment="gpt-5",
            model="gpt-5",
            auth_header="api-key",
        )
        entries = llmlog.all_entries(log_path)
        assert len(entries) == 1
        assert entries[0]["detail"]["client_class"] == "AsyncAzureOpenAI"
        assert entries[0]["detail"]["auth_header"] == "api-key"


class TestLoggingTransportError:
    async def test_transport_logs_error_entry_and_reraises(self, log_path: Path) -> None:
        class _FailingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("boom", request=request)

        transport = llmlog._LoggingTransport(_FailingTransport(), log_path)
        request = httpx.Request("POST", "https://example.com/chat/completions")

        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(request)

        entries = llmlog.all_entries(log_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "error"
        assert entries[0]["detail"]["error_type"] == "ConnectError"


class TestRequestResponseHooks:
    async def test_request_and_response_hooks_log_metadata_not_content(
        self, log_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(
            transport=transport,
            event_hooks={
                "request": [lambda request: llmlog._on_request(log_path, request)],
                "response": [lambda response: llmlog._on_response(log_path, response)],
            },
        )
        async with client:
            await client.post(
                "https://example.com/chat/completions",
                json={"messages": [{"role": "user", "content": "top secret document text"}]},
            )

        entries = llmlog.all_entries(log_path)
        kinds = [e["kind"] for e in entries]
        assert kinds == ["request", "response"]
        request_entry, response_entry = entries
        assert request_entry["detail"]["messages"] == 1
        assert response_entry["detail"]["status"] == 200
        assert response_entry["detail"]["duration_ms"] is not None
        # Never logs message content.
        blob = json.dumps(entries)
        assert "top secret document text" not in blob

    async def test_error_status_response_logs_truncated_body(self, log_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "NotFound"}})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(
            transport=transport,
            event_hooks={
                "response": [lambda response: llmlog._on_response(log_path, response)],
            },
        )
        async with client:
            await client.get("https://example.com/chat/completions")

        entries = llmlog.all_entries(log_path)
        assert entries[0]["detail"]["status"] == 404
        assert "NotFound" in entries[0]["detail"]["body"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
