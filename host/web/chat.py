"""Shared chat-bubble rendering (Y7) for the conversation view — used by both
`host/web/main.py`'s organize-mode run page and `host/web/query_view.py`'s
query mode, which duplicated this rendering logic before Y7 unified it here.

This module renders `TranscriptItem.text` — assistant turns can echo
attacker-planted document text (indirect prompt injection) — via
`ui.markdown`. That makes `render_turn_bubble` the ONE deliberate,
documented exception to telcontar's "never render LLM/corpus-derived text as
markup" rule that every other web UI surface follows (`host/web/docpane.py`,
`corpus_view.py`, `shell.show_detail`'s codemirror view: `ui.label`/
`ui.codemirror` only). It is safe here because:
  - `ui.markdown(sanitize=True)` runs the rendered HTML through a
    client-side, vendored DOMPurify (no network fetch) before it ever
    reaches the DOM — script tags, event handlers, `javascript:` URLs, etc.
    are stripped.
  - The Content-Security-Policy header `_AuthMiddleware` adds in
    `host/web/main.py` includes `img-src 'self' data:`, so even a
    sanitize-surviving `![](http://attacker/...)` markdown image cannot
    beacon out to a remote host — the one thing DOMPurify alone doesn't
    stop, since a plain image load isn't script execution.
Do not reuse `ui.markdown`/`ui.html` for any other corpus-derived value
elsewhere in host/web/ without re-deriving this same reasoning; a single
unsanitized copy defeats the point of unifying this rendering in one place.
"""

from __future__ import annotations

from nicegui import ui

from host.web import session as web_session


def render_turn_bubble(item: web_session.TranscriptItem) -> None:
    """Render one conversation turn as a chat bubble. Must be called with
    the destination column already active (``with conversation_column:``),
    matching how both call sites already scope every other render call."""
    # V13a: `sent=` already picks the right side, but NiceGUI's
    # `.nicegui-column` CSS (`align-items: flex-start`) shrink-wraps every
    # chat-message to its content width, hiding that alignment —
    # `.classes("w-full")` on the message itself is the fix. bg-color/
    # text-color are genuine QChatMessage props (Quasar renders them as
    # `text-<color>` utility classes under the hood, relying on
    # `.q-message-text { background: currentColor }`) resolved against
    # theme.PALETTE via run_web()'s app.colors().
    is_user = item.speaker == "user"
    bubble_props = (
        "bg-color=secondary text-color=dark" if is_user else "bg-color=primary text-color=dark"
    )
    with ui.chat_message(name=item.speaker, sent=is_user).classes("w-full").props(bubble_props):
        ui.markdown(item.text, sanitize=True)
