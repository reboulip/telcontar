"""Setup wizard (U2) — NiceGUI port of host.app.SetupScreen's 5 steps.

Ports the TUI 1:1: same steps, same validation order/strings, same
plaintext-keyring warn-then-confirm flow (see host/configflow.py and
host/web/forms.py, which this and the settings view (U3) share). Lives
outside host/web/main.py on purpose — this sprint's main.py stays a thin
@ui.page shell per screen; the actual view-building logic lives here so the
file isn't the single serialization point for every Phase 20 item.

State is a page-closure dataclass (_WizardState), never app.storage or a URL
param — the API key must never touch either.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nicegui import run, ui

from host import configflow
from host.web import forms


@dataclass
class _WizardState:
    step: int = 0
    service: str = "openai_compatible"
    pending_url: str = ""
    pending_key: str = ""
    pending_model: str = ""
    plaintext_confirmed: bool = False
    error: str = ""


async def build_setup_wizard(*, on_finish: Callable[[], None]) -> None:
    state = _WizardState()
    # Blocking I/O (glob + TOML parse) — off the event loop (Phase 18 S5's
    # deferred offload; sprint-brief.md's cross-cutting decision #5).
    options = await run.io_bound(configflow.profile_options)

    ui.label("Directory Organizer").classes("text-h5 text-center w-full")

    @ui.refreshable
    def steps() -> None:
        if state.step == 0:
            _step_welcome(state, steps.refresh)
        elif state.step == 1:
            _step_service(state, steps.refresh)
        elif state.step == 2:
            _step_api(state, steps.refresh)
        elif state.step == 3:
            _step_profile(state, steps.refresh, options)
        else:
            _step_done(on_finish)

    steps()


def _step_welcome(state: _WizardState, refresh: Callable[[], None]) -> None:
    ui.label("Welcome! Let's get you set up in just a couple of steps.")
    ui.label(
        "To read and analyze your documents, this app needs to talk to "
        "an AI service. You'll need:\n\n"
        "  • The web address of your AI service\n"
        "  • An API key (your provider gives you this)"
    ).style("white-space: pre-line")

    def _next() -> None:
        state.step = 1
        refresh()

    ui.button("Get started →", on_click=_next, color="primary").mark("btn-welcome-next")


def _step_service(state: _WizardState, refresh: Callable[[], None]) -> None:
    ui.label("Which AI service will you use?").classes("text-subtitle1")

    def _pick(service: str) -> None:
        state.service = service
        state.step = 2
        refresh()

    ui.button(
        "Any OpenAI-compatible service",
        on_click=lambda: _pick("openai_compatible"),
    ).classes("w-full").mark("btn-svc-compatible")
    ui.button("Azure OpenAI", on_click=lambda: _pick("azure")).classes("w-full").mark(
        "btn-svc-azure"
    )


def _step_api(state: _WizardState, refresh: Callable[[], None]) -> None:
    hints = configflow.SERVICE_HINTS[state.service]
    inputs = forms.credential_inputs(
        url_hint=hints["url_hint"],
        url_placeholder=hints["url_placeholder"],
        url_value=state.pending_url,
        key_value=state.pending_key,
        model_hint=hints["model_hint"],
        model_placeholder=hints["model_placeholder"],
        model_value=state.pending_model,
    )
    if state.error:
        ui.label(state.error).classes("text-negative").mark("api-error")

    def _back() -> None:
        state.error = ""
        state.step = 1
        refresh()

    def _next() -> None:
        url = inputs.url.value.strip()
        key = inputs.key.value.strip()
        model = inputs.model.value.strip()
        error = configflow.validate_credentials(url, key, model, key_required=True)
        if error:
            state.error = error
            refresh()
            return
        state.pending_url = url
        state.pending_key = key
        state.pending_model = model
        state.error = ""
        state.step = 3
        refresh()

    with ui.row():
        ui.button("← Back", on_click=_back).mark("btn-api-back")
        ui.button("Next →", on_click=_next, color="primary").mark("btn-api-next")


def _step_profile(
    state: _WizardState, refresh: Callable[[], None], options: list[tuple[str, str]]
) -> None:
    ui.label("What kind of documents will you organize?").classes("text-subtitle1")
    ui.label("This sets the vocabulary the AI uses to categorize files.").classes(
        "text-caption text-grey"
    )
    select_options = {value: label for label, value in options}
    default_value = options[0][1] if options else None
    select = ui.select(select_options, value=default_value).classes("w-full").mark("select-profile")
    if state.error:
        ui.label(state.error).classes("text-negative").mark("profile-error")

    def _back() -> None:
        state.error = ""
        state.step = 2
        refresh()

    async def _save() -> None:
        if select.value is None:
            state.error = "Please choose a document type."
            refresh()
            return
        profile = str(select.value)

        def build_updates() -> dict[str, str]:
            return configflow.build_wizard_updates(
                state.pending_url, state.pending_key, state.pending_model, profile, state.service
            )

        ok, warning = await forms.save_with_plaintext_guard(
            build_updates,
            plaintext_confirmed=state.plaintext_confirmed,
            button_label="Save & continue →",
        )
        if ok:
            state.error = ""
            state.step = 4
        else:
            state.plaintext_confirmed = True
            state.error = warning
        refresh()

    with ui.row():
        ui.button("← Back", on_click=_back).mark("btn-profile-back")
        ui.button("Save & continue →", on_click=_save, color="primary").mark("btn-profile-next")


def _step_done(on_finish: Callable[[], None]) -> None:
    ui.label("You're all set!").classes("text-h6")
    ui.label(
        "Your settings have been saved securely.\nYou can update them at any time via Settings."
    ).style("white-space: pre-line")
    ui.button("Start Organizing →", on_click=on_finish, color="positive").mark("btn-done")
