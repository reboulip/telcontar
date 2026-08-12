"""Pure decision layer for V2's local-server auth hardening.

Binding to 127.0.0.1 (host/web/main.py's ui.run(host="127.0.0.1", ...)) keeps
the server off the LAN, but *any* other local process — any other user-level
program running on the same machine — can still reach a loopback port. This
module is the defense-in-depth layer for that gap: a per-launch token (query
param on first navigation, then a cookie) plus a Host-header check (closes
the DNS-rebinding gap an Origin check alone leaves open: a page served from
an attacker-controlled domain that resolves to 127.0.0.1 is same-origin as
far as the browser's Origin header is concerned, so Origin alone proves
nothing without also pinning Host) plus an Origin check for browser-borne
requests.

Kept NiceGUI-free so the decision logic is testable in plain pytest — the
same invariant theme.py/session.py/tree.py/bridge.py already keep.
host/web/main.py wires authorize() into a pure-ASGI middleware.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

_token: str | None = None
_port: int | None = None


def configure(token: str, port: int) -> None:
    """Set once by run_web(), after the per-launch token and port are known."""
    global _token, _port
    _token = token
    _port = port


def new_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_name() -> str:
    """Port-scoped: cookies aren't otherwise, and two concurrent telcontar
    instances on different ports would clobber each other's cookie —
    403-ing whichever window set its cookie last — without this."""
    return f"telcontar_auth_{_port}"


def build_cookie_header() -> str:
    """Set-Cookie value for the current launch's token, sent by the ASGI
    middleware whenever a Decision comes back with set_cookie=True (the
    request authenticated via the query-string token, not an existing
    cookie). HttpOnly blocks JS reads; SameSite=Strict is safe here since
    the app never needs the cookie sent cross-site."""
    return f"{cookie_name()}={_token}; HttpOnly; SameSite=Strict; Path=/"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    set_cookie: bool = False
    """True when the request authenticated via the query-string token
    (first navigation) rather than an already-valid cookie — the caller
    should mint the auth cookie on this response so subsequent in-app
    navigations, which never carry the query string, stay authenticated."""


def _allowed_hosts() -> set[str]:
    return {f"127.0.0.1:{_port}", f"localhost:{_port}"}


def _allowed_origins() -> set[str]:
    return {f"http://127.0.0.1:{_port}", f"http://localhost:{_port}"}


def _token_from_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get(cookie_name())
    return morsel.value if morsel else None


def _token_from_query(query_string: str) -> str | None:
    if not query_string:
        return None
    values = parse_qs(query_string).get("token")
    return values[0] if values else None


def authorize(
    *,
    host_header: str | None,
    origin_header: str | None,
    cookie_header: str | None,
    query_string: str,
) -> Decision:
    """No I/O, no NiceGUI — a pure function of the four request inputs the
    ASGI middleware extracts. Checks, in order: Host (anti-DNS-rebinding),
    Origin (if present — absent is allowed, since top-level browser
    navigations often omit it), then the token (cookie or query string)."""
    if _token is None or _port is None:
        return Decision(allowed=False, reason="server not configured")

    if host_header not in _allowed_hosts():
        return Decision(allowed=False, reason="untrusted Host header")

    if origin_header and origin_header not in _allowed_origins():
        return Decision(allowed=False, reason="untrusted Origin header")

    cookie_token = _token_from_cookie(cookie_header)
    if cookie_token is not None and secrets.compare_digest(cookie_token, _token):
        return Decision(allowed=True)

    query_token = _token_from_query(query_string)
    if query_token is not None and secrets.compare_digest(query_token, _token):
        return Decision(allowed=True, set_cookie=True)

    return Decision(allowed=False, reason="missing or invalid token")
