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

    # V11: read-only prompt inspection, deliberately OUTSIDE form()'s
    # @ui.refreshable region — a validation-driven refresh() (e.g. from
    # _save()) must never recompose this on every keystroke. It reflects the
    # currently-saved settings, not in-progress (unsaved) form edits.
    await _build_prompt_inspection()


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


# ── V11: prompt inspection ("What telcontar tells the model") ────────────────


@dataclass
class _PromptInspectionData:
    prompts: dict[str, str]
    resolved_profile: str | None
    configured_profile: str
    analyzer_batch_size: int


def _load_prompt_inspection_data() -> _PromptInspectionData:
    """Blocking work for the prompt-inspection expansion, bundled into one
    `run.io_bound` round trip (TOML profile load + NAMING.md read).

    Builds `config.settings.Settings()` directly — NOT `config.settings.load()`,
    which raises when credentials are absent — so prompt inspection works even
    before the setup wizard has run. `resolved_profile` is None when the
    profile failed to load: `host.agent`'s `_try_load_profile` swallows that
    failure and falls back to a generic "default" profile internally so the
    prompts themselves stay renderable — but a transparency feature must not
    hide the failure the same way, so this surfaces it for display instead.
    """
    from config.settings import Settings
    from host.agent import _ANALYZER_BATCH_SIZE, _resolved_profile_name, composed_system_prompts

    settings = Settings()
    return _PromptInspectionData(
        prompts=composed_system_prompts(settings),
        resolved_profile=_resolved_profile_name(settings),
        configured_profile=settings.profile,
        analyzer_batch_size=_ANALYZER_BATCH_SIZE,
    )


async def _build_prompt_inspection() -> None:
    """Collapsed "What telcontar tells the model" expansion (V11): the three
    read-only composed system prompts (ORGANIZE/QUERY/ANALYZE) telcontar
    actually sends the LLM, so the user can see what it's being told.

    Editing is deliberately out of scope — an editable prompt sits next to
    Phase 12 M10's injection-resistance guardrails and needs its own security
    pass before it's safe to expose as a write path.

    Rendering safety: the composed prompts can embed profile free-text and
    NAMING.md file content, which — like host/web/shell.py's step-detail
    drawer (`show_detail`) — must never be rendered as markup/HTML. Displayed
    via disabled `ui.codemirror` only, never `ui.markdown`/`ui.html`, matching
    that same precedent.
    """
    data = await run.io_bound(_load_prompt_inspection_data)
    if data is None:
        # run.io_bound returns None only when cancelled or the app is
        # shutting down mid-call (NiceGUI's documented interim shape) —
        # nothing to render either way.
        return

    with ui.expansion("What telcontar tells the model").classes("w-full").mark("expansion-prompts"):
        if data.resolved_profile is not None:
            ui.label(f"Domain profile resolved: {data.resolved_profile}").mark(
                "prompt-profile-status"
            )
        else:
            ui.label(
                f"Domain profile {data.configured_profile!r} failed to load — the "
                "prompts below fall back to a generic default, matching what the "
                "model is actually sent in that case."
            ).classes("text-negative").mark("prompt-profile-status")

        ui.label(
            "Not shown here: the corpus digest (built from the analyzed registry) "
            "and any steering instructions you gave before analysis — both are "
            "composed at run time from a live target directory, which this "
            "settings view does not have."
        ).classes("text-caption")

        ui.label("ORGANIZE — sent once, drives the whole organize run").classes("text-caption")
        ui.codemirror(data.prompts.get("organize", "")).classes("w-full").style(
            "max-height: 20rem"
        ).disable().mark("prompt-organize")

        ui.label("QUERY — sent for read-only corpus questions").classes("text-caption")
        ui.codemirror(data.prompts.get("query", "")).classes("w-full").style(
            "max-height: 20rem"
        ).disable().mark("prompt-query")

        ui.label(
            "ANALYZE — sent once per batch of new documents during analysis "
            f"(shown here for a full {data.analyzer_batch_size}-document batch)"
        ).classes("text-caption")
        ui.codemirror(data.prompts.get("analyze", "")).classes("w-full").style(
            "max-height: 20rem"
        ).disable().mark("prompt-analyze")
