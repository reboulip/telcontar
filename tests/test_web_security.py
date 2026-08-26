"""Tests for host/web/security.py's pure decision layer (V2), plus an
ASGI-level integration test for host.web.main._AuthMiddleware wrapped
around a minimal dummy app — deliberately outside NiceGUI's
NICEGUI_USER_SIMULATION-guarded tests, since that guard is exactly what
keeps the middleware out of tests/test_web_ui.py's headless fixture."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from host.web import security
from host.web.main import _AuthMiddleware


@pytest.fixture(autouse=True)
def _isolated_security_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from "not configured" and must call
    security.configure() itself — module-level state would otherwise leak
    between tests depending on run order."""
    monkeypatch.setattr(security, "_token", None)
    monkeypatch.setattr(security, "_port", None)


# ── security.authorize() — pure decision layer ──────────────────────────


def test_new_token_is_url_safe_and_unique() -> None:
    a, b = security.new_token(), security.new_token()
    assert a != b
    assert len(a) > 20
    assert all(c.isalnum() or c in "-_" for c in a)


def test_authorize_denies_when_not_configured() -> None:
    decision = security.authorize(
        host_header="127.0.0.1:8000", origin_header=None, cookie_header=None, query_string=""
    )
    assert decision.allowed is False


def test_authorize_denies_untrusted_host() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="evil.example.com:8000",
        origin_header=None,
        cookie_header=None,
        query_string="token=secret",
    )
    assert decision.allowed is False
    assert "Host" in decision.reason


def test_authorize_allows_valid_query_token_and_requests_a_cookie() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header=None,
        cookie_header=None,
        query_string="token=secret",
    )
    assert decision.allowed is True
    assert decision.set_cookie is True


def test_authorize_allows_valid_cookie_without_requesting_a_new_one() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="localhost:8000",
        origin_header=None,
        cookie_header=f"{security.cookie_name()}=secret",
        query_string="",
    )
    assert decision.allowed is True
    assert decision.set_cookie is False


def test_authorize_denies_wrong_query_token() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header=None,
        cookie_header=None,
        query_string="token=nope",
    )
    assert decision.allowed is False


def test_authorize_denies_wrong_cookie_token() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header=None,
        cookie_header=f"{security.cookie_name()}=nope",
        query_string="",
    )
    assert decision.allowed is False


def test_authorize_denies_missing_token() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000", origin_header=None, cookie_header=None, query_string=""
    )
    assert decision.allowed is False


def test_authorize_allows_matching_origin() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header="http://127.0.0.1:8000",
        cookie_header=None,
        query_string="token=secret",
    )
    assert decision.allowed is True


def test_authorize_denies_mismatched_origin_even_with_a_valid_token() -> None:
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header="http://attacker.example.com",
        cookie_header=None,
        query_string="token=secret",
    )
    assert decision.allowed is False
    assert "Origin" in decision.reason


def test_authorize_allows_absent_origin() -> None:
    # Top-level browser navigations often omit Origin entirely — requiring
    # it would break the very first page load.
    security.configure(token="secret", port=8000)
    decision = security.authorize(
        host_header="127.0.0.1:8000",
        origin_header=None,
        cookie_header=None,
        query_string="token=secret",
    )
    assert decision.allowed is True


def test_cookie_name_is_port_scoped() -> None:
    security.configure(token="secret", port=1234)
    assert security.cookie_name() == "telcontar_auth_1234"
    security.configure(token="secret", port=5678)
    assert security.cookie_name() == "telcontar_auth_5678"


def test_build_cookie_header_is_http_only_and_strict() -> None:
    security.configure(token="secret", port=8000)
    header = security.build_cookie_header()
    assert header.startswith("telcontar_auth_8000=secret;")
    assert "HttpOnly" in header
    assert "SameSite=Strict" in header
    assert "Path=/" in header


# ── _AuthMiddleware — ASGI plumbing ──────────────────────────────────────

_Send = Callable[[dict], Awaitable[None]]
_Receive = Callable[[], Awaitable[dict]]


async def _dummy_app(scope: dict, receive: _Receive, send: _Send) -> None:
    if scope["type"] == "http":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def _wrapped_app() -> _AuthMiddleware:
    security.configure(token="itest-token", port=9999)
    return _AuthMiddleware(_dummy_app)


async def test_middleware_denies_request_without_token(_wrapped_app: _AuthMiddleware) -> None:
    transport = httpx.ASGITransport(app=_wrapped_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as client:
        response = await client.get("/")
    assert response.status_code == 403
    assert b"launch token" in response.content


async def test_middleware_allows_valid_query_token_and_sets_cookie(
    _wrapped_app: _AuthMiddleware,
) -> None:
    transport = httpx.ASGITransport(app=_wrapped_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as client:
        response = await client.get("/?token=itest-token")
    assert response.status_code == 200
    assert response.text == "ok"
    assert "telcontar_auth_9999=itest-token" in response.headers.get("set-cookie", "")


async def test_middleware_reuses_a_valid_cookie_without_resetting_it(
    _wrapped_app: _AuthMiddleware,
) -> None:
    transport = httpx.ASGITransport(app=_wrapped_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:9999",
        cookies={"telcontar_auth_9999": "itest-token"},
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


async def test_middleware_adds_frame_protection_headers(_wrapped_app: _AuthMiddleware) -> None:
    transport = httpx.ASGITransport(app=_wrapped_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as client:
        response = await client.get("/?token=itest-token")
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    # Y7: closes the one thing DOMPurify-sanitized markdown chat messages
    # don't stop — a remote-image beacon via a sanitize-surviving
    # ![](http://attacker/...) tag.
    assert "img-src 'self' data:" in response.headers["content-security-policy"]


async def test_middleware_denies_request_from_wrong_host_header() -> None:
    security.configure(token="itest-token", port=9999)
    middleware = _AuthMiddleware(_dummy_app)
    transport = httpx.ASGITransport(app=middleware)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://evil.example.com:9999"
    ) as client:
        response = await client.get("/?token=itest-token")
    assert response.status_code == 403


async def test_middleware_closes_websocket_scope_without_a_valid_token(
    _wrapped_app: _AuthMiddleware,
) -> None:
    sent: list[dict] = []

    async def _send(message: dict) -> None:
        sent.append(message)

    async def _receive() -> dict:
        return {"type": "websocket.disconnect"}

    scope = {"type": "websocket", "headers": [(b"host", b"127.0.0.1:9999")], "query_string": b""}
    await _wrapped_app(scope, _receive, _send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_middleware_passes_through_non_http_websocket_scopes() -> None:
    calls: list[str] = []

    async def _inner_app(scope: dict, receive: _Receive, send: _Send) -> None:
        calls.append(scope["type"])

    async def _noop_receive() -> dict:
        return {}

    async def _noop_send(message: dict) -> None:
        pass

    middleware = _AuthMiddleware(_inner_app)
    await middleware({"type": "lifespan"}, _noop_receive, _noop_send)
    assert calls == ["lifespan"]
