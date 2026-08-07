"""Settings view (U3) — NiceGUI port of host.app.ConfigScreen.

Same behaviour as the setup wizard (host/web/wizard.py), including the
blank-key-preserves-existing-key rule — reuses the same shared scaffolding
(host/configflow.py, host/web/forms.py) rather than re-deriving it, per U3's
literal "same behaviour as the wizard" requirement.

Unlike the wizard, this is not multi-step: one form, Save or Cancel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nicegui import run, ui

from host import configflow
from host.web import forms


@dataclass
class _SettingsState:
    plaintext_confirmed: bool = False
    error: str = ""


async def build_settings_view(*, on_done: Callable[[], None]) -> None:
    state = _SettingsState()
    # Blocking I/O (glob + TOML parse) and the config-file read — off the
    # event loop (Phase 18 S5's deferred offload; sprint-brief.md's
    # cross-cutting decision #5).
    profile_choices = await run.io_bound(configflow.profile_options)
    from config.settings import read_user_config

    current = await run.io_bound(read_user_config)

    ui.label("Settings").classes("text-h5 text-center w-full")

    @ui.refreshable
    def form() -> None:
        _render_form(state, form.refresh, on_done, profile_choices, current)

    form()


def _render_form(
    state: _SettingsState,
    refresh: Callable[[], None],
    on_done: Callable[[], None],
    profile_choices: list[tuple[str, str]],
    current: dict[str, str],
) -> None:
    profile_values = {value for _label, value in profile_choices}
    current_profile = current.get("profile", "is_it_project")
    profile_default = current_profile if current_profile in profile_values else "is_it_project"

    approval_values = {value for _label, value in configflow.APPROVAL_OPTIONS}
    current_approval = current.get("approval_mode", "always")
    approval_default = current_approval if current_approval in approval_values else "always"

    inputs = forms.credential_inputs(
        url_value=current.get("llm_base_url", ""),
        key_placeholder="Paste a new key, or leave empty to keep the current one",
        model_value=current.get("llm_model", "gpt-5"),
    )

    ui.label("Document type:")
    profile_select = (
        ui.select({v: label for label, v in profile_choices}, value=profile_default)
        .classes("w-full")
        .mark("select-profile")
    )

    ui.label("How careful should the app be?")
    approval_select = (
        ui.select({v: label for label, v in configflow.APPROVAL_OPTIONS}, value=approval_default)
        .classes("w-full")
        .mark("select-approval")
    )

    if state.error:
        ui.label(state.error).classes("text-negative").mark("settings-error")

    async def _save() -> None:
        url = inputs.url.value.strip()
        key = inputs.key.value.strip()
        model = inputs.model.value.strip()
        error = configflow.validate_credentials(url, key, model, key_required=False)
        if error:
            state.error = error
            refresh()
            return

        profile = str(profile_select.value) if profile_select.value else "is_it_project"
        approval_mode = str(approval_select.value) if approval_select.value else "always"

        def build_updates() -> dict[str, str]:
            return configflow.build_settings_updates(url, key, model, profile, approval_mode)

        ok, warning = await forms.save_with_plaintext_guard(
            build_updates,
            plaintext_confirmed=state.plaintext_confirmed,
            button_label="Save",
            recovery_action="cancel",
        )
        if ok:
            on_done()
        else:
            state.plaintext_confirmed = True
            state.error = warning
            refresh()

    def _cancel() -> None:
        on_done()

    with ui.row():
        ui.button("Save", on_click=_save, color="primary").mark("btn-settings-save")
        ui.button("Cancel", on_click=_cancel).mark("btn-settings-cancel")
