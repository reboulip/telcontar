"""Local, redacted debug log for outbound LLM endpoint calls (Y5) — request and
response metadata only, never message content or credentials, to help diagnose
corporate/Azure connectivity issues without adding this app's own message
content to the egress surface documented in docs/developer/security-model.md.

Deliberately does NOT enable the OpenAI SDK's own `OPENAI_LOG`/logger — that
dumps full request bodies, including extracted document text, to whatever
handler is configured. This module only ever writes counts, statuses, and
(on an HTTP error response) the error body Azure/the provider returned, which
carries no document content.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

_REDACT_QUERY_KEYS = {"api-key", "key", "code", "subscription-key"}
_REQUEST_ID_HEADERS = ("x-request-id", "apim-request-id", "x-ms-request-id")
_MAX_ERROR_BODY_CHARS = 2000
_START_TIME_KEY = "_llmlog_start"


@dataclass(frozen=True)
class LLMLogEntry:
    """One debug-log line: a client/request/response/error event."""

    ts: str
    kind: str  # "client" | "request" | "response" | "error"
    detail: dict

    @classmethod
    def new(cls, kind: str, detail: dict) -> "LLMLogEntry":
        return cls(ts=datetime.now(timezone.utc).isoformat(), kind=kind, detail=detail)

    def to_dict(self) -> dict:
        return asdict(self)


def append(path: Path, entry: LLMLogEntry) -> None:
    """Append a single log entry; creates parent dirs if needed. Never raises —
    a debug log must never break the run it's diagnosing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        pass


def all_entries(path: Path) -> list[dict]:
    """Read back every logged entry, oldest first. `[]` if the file is missing
    or unreadable."""
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return entries


def _redact_url(url: httpx.URL) -> str:
    """Return the URL with any credential-bearing query value masked. Other
    query params (notably `api-version`, the whole diagnostic point) survive."""
    parts = urlsplit(str(url))
    q = [
        (k, "***" if k.lower() in _REDACT_QUERY_KEYS else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def log_client(
    path: Path,
    *,
    client_class: str,
    endpoint: str,
    api_version: str,
    deployment: str,
    model: str,
    auth_header: str,
) -> None:
    """Log the resolved client shape once, right after `make_client` builds it —
    this single line is enough to diagnose a future Azure/endpoint misconfiguration
    without reproducing the failure first."""
    append(
        path,
        LLMLogEntry.new(
            "client",
            {
                "client_class": client_class,
                "endpoint": endpoint,
                "api_version": api_version,
                "deployment": deployment,
                "model": model,
                "auth_header": auth_header,
            },
        ),
    )


async def _on_request(path: Path, request: httpx.Request) -> None:
    try:
        request.extensions[_START_TIME_KEY] = time.monotonic()
        message_count = None
        try:
            body = request.read()
            if body:
                parsed = json.loads(body)
                if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
                    message_count = len(parsed["messages"])
        except (ValueError, UnicodeDecodeError):
            pass
        append(
            path,
            LLMLogEntry.new(
                "request",
                {
                    "method": request.method,
                    "url": _redact_url(request.url),
                    "messages": message_count,
                    "bytes": len(request.content) if request.content else 0,
                },
            ),
        )
    except Exception:
        return


async def _on_response(path: Path, response: httpx.Response) -> None:
    try:
        start = response.request.extensions.get(_START_TIME_KEY)
        duration_ms = int((time.monotonic() - start) * 1000) if start is not None else None
        request_id = next(
            (response.headers.get(h) for h in _REQUEST_ID_HEADERS if response.headers.get(h)),
            None,
        )
        detail: dict = {
            "status": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "url": _redact_url(response.request.url),
        }
        if response.status_code >= 400:
            try:
                await response.aread()
                detail["body"] = response.text[:_MAX_ERROR_BODY_CHARS]
            except Exception:
                pass
        append(path, LLMLogEntry.new("response", detail))
    except Exception:
        return


class _LoggingTransport(httpx.AsyncBaseTransport):
    """Wraps the default transport to log transport-level failures (connect
    errors, timeouts, TLS/proxy errors) — the one class of failure httpx event
    hooks never see, since they only fire once a request/response actually
    completes a stage."""

    def __init__(self, wrapped: httpx.AsyncBaseTransport, log_path: Path) -> None:
        self._wrapped = wrapped
        self._log_path = log_path

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._wrapped.handle_async_request(request)
        except Exception as exc:
            try:
                append(
                    self._log_path,
                    LLMLogEntry.new(
                        "error",
                        {
                            "url": _redact_url(request.url),
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:_MAX_ERROR_BODY_CHARS],
                        },
                    ),
                )
            except Exception:
                pass
            raise

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def build_http_client(log_path: Path) -> httpx.AsyncClient:
    """An httpx.AsyncClient wired to log every LLM HTTP call to ``log_path``,
    with the openai SDK's own transport defaults preserved explicitly (a bare
    ``httpx.AsyncClient()`` defaults to a 5s timeout, which would break every
    long analyze call)."""
    transport = _LoggingTransport(httpx.AsyncHTTPTransport(), log_path)
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(600, connect=5.0),
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
        follow_redirects=True,
        event_hooks={
            "request": [lambda request: _on_request(log_path, request)],
            "response": [lambda response: _on_response(log_path, response)],
        },
    )
