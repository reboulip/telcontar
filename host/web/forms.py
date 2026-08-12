"""Shared NiceGUI form fragments for the setup wizard (U2) and settings view
(U3) — both need the same URL/key/model credential inputs and the same
warn-then-confirm plaintext-keyring save flow, and per U3's "same behaviour
as the wizard" requirement they must actually share the logic, not just look
alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nicegui import run, ui

from host import configflow


@dataclass
class CredentialInputs:
    url: ui.input
    key: ui.input
    model: ui.input


def credential_inputs(
    *,
    url_hint: str = "",
    url_placeholder: str = "https://…",
    url_value: str = "",
    key_placeholder: str = "Paste your key here",
    key_value: str = "",
    model_hint: str = "",
    model_placeholder: str = "e.g. gpt-5",
    model_value: str = "",
) -> CredentialInputs:
    """Render the URL / API key / model input triple with optional hint text
    above the URL and model fields. A caller with no per-service hints (the
    settings view has no service picker) simply leaves the hint text empty
    — an empty ui.label renders as nothing. ``key_placeholder`` lets the
    settings view explain its blank-key-preserves-existing-key rule inline
    (the wizard has no existing key to preserve, so it uses the default).
    """
    if url_hint:
        ui.label(url_hint).classes("text-caption text-grey")
    ui.label("Web address (URL) of your AI service:")
    url = ui.input(value=url_value, placeholder=url_placeholder).classes("w-full").mark("input-url")

    ui.label("Your API key:")
    key = (
        ui.input(
            value=key_value,
            placeholder=key_placeholder,
            password=True,
            password_toggle_button=True,
        )
        .classes("w-full")
        .mark("input-key")
    )

    if model_hint:
        ui.label(model_hint).classes("text-caption text-grey")
    ui.label("Model name:")
    model = (
        ui.input(value=model_value, placeholder=model_placeholder)
        .classes("w-full")
        .mark("input-model")
    )

    return CredentialInputs(url=url, key=key, model=model)


async def save_with_plaintext_guard(
    build_updates: Callable[[], dict[str, str]],
    *,
    plaintext_confirmed: bool,
    button_label: str,
    recovery_action: str = "go back",
) -> tuple[bool, str]:
    """Attempt config/settings.save_user_config via a *fresh* dict from
    build_updates() — never a cached one. save_user_config pops the API key
    out of its argument dict before raising PlaintextKeyFallbackNeeded, so a
    caller that reused the same dict object across a retry would silently
    save without the key; a builder callable makes that impossible.

    Runs the actual save (file write + OS keyring round-trip) via
    run.io_bound so it never blocks the event loop.

    Returns (success, warning_text) — warning_text is "" on success, or the
    plaintext-fallback warning (already asking the user to press
    button_label again) on the PlaintextKeyFallbackNeeded path. The caller
    owns re-rendering and tracking `plaintext_confirmed` for the next call.
    """
    from config.settings import PlaintextKeyFallbackNeeded, save_user_config

    try:
        await run.io_bound(save_user_config, build_updates(), plaintext_confirmed)
    except PlaintextKeyFallbackNeeded:
        return False, configflow.plaintext_warning(button_label, recovery_action)
    return True, ""
