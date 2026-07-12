"""Textual TUI for the directory organizer host.

Screens
-------
SetupScreen     — first-run configuration wizard (API key, profile)
StartupScreen   — target directory input + entry to ConfigScreen
ConfigScreen    — edit settings at any time
OrganizerScreen — file tree + chat-transcript conversation pane + status
QueryScreen     — interactive NL Q&A over an analyzed corpus

Modals
------
ApprovalModal      — plan review with per-op checkboxes (inline removal)
ClarificationModal — post-analysis clarifying questions with free-text answers
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    Rule,
    Select,
    Static,
)

from host.agent import AgentEvent, ApprovalResult, ClarificationResult, OptionsResult

# Package root: host/app.py → host/ → project root (or site-packages/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Friendly, plain-language narration for the conversation pane (F10). Maps each
# MCP tool to the macro-task it belongs to; consecutive calls in the same macro
# task collapse to a single line so the pane reads as progress, not a call log.
_TOOL_NARRATION: dict[str, str] = {
    "list_dir": "Scanning the directory…",
    "walk_tree": "Exploring nested folders…",
    "read_file": "Reading documents…",
    "extract_text": "Reading documents…",
    "read_file_batch": "Reading documents…",
    "extract_text_batch": "Reading documents…",
    "compute_checksum": "Computing checksums…",
    "compute_checksum_batch": "Computing checksums…",
    "record_document": "Recording documents in memory…",
    "record_document_batch": "Recording documents in memory…",
    "find_duplicates": "Checking for duplicates…",
    "find_modified_documents": "Checking for newer versions…",
    "compare_documents": "Comparing documents…",
    "create_plan": "Planning changes…",
    "propose_rename": "Planning changes…",
    "propose_move": "Planning changes…",
    "propose_quarantine": "Planning changes…",
    "propose_create_file": "Planning changes…",
    "propose_update_file": "Planning changes…",
    "propose_create_dir": "Planning changes…",
    "propose_archive_document": "Planning changes…",
    "propose_compress_quarantine": "Planning changes…",
    "review_plan": "Reviewing the plan…",
    "set_plan_rationale": "Summarizing the plan…",
    "set_plan_folder_notes": "Describing the target folders…",
    "execute_plan": "Applying the plan…",
    "create_event": "Recording project events…",
    "build_graph": "Building the knowledge graph…",
    "get_graph": "Building the knowledge graph…",
    "get_actors": "Identifying the main actors…",
    "list_events": "Reviewing the timeline…",
    "write_index": "Writing the index…",
    "write_summary": "Writing the summary…",
    "write_folder_readme": "Describing folders…",
    "ask_clarification": "Asking you for clarification…",
    "propose_options": "Reviewing options with you…",
}


# ── Profile helpers ───────────────────────────────────────────────────────────

# Human-readable labels for built-in profiles.
_PROFILE_LABELS: dict[str, str] = {
    "is_it_project": "IS/IT project — technical and business documents",
    "personal_files": "Personal files — invoices, contracts, administrative",
    "research_papers": "Research papers — academic and scientific articles",
}


def _load_profile_options() -> list[tuple[str, str]]:
    """Return [(display_label, profile_id), ...] for the Select widget.

    Reads TOML files from the bundled profiles/ directory.  Falls back to a
    single safe default if the directory cannot be found.
    """
    profiles_dir = _PROJECT_ROOT / "profiles"
    options: list[tuple[str, str]] = []
    try:
        for path in sorted(profiles_dir.glob("*.toml")):
            stem = path.stem
            label = _PROFILE_LABELS.get(stem)
            if label is None:
                try:
                    data = tomllib.loads(path.read_text(encoding="utf-8"))
                    desc = data.get("description") or data.get("name") or stem
                    label = desc
                except Exception:
                    label = stem
            options.append((label, stem))
    except Exception:
        pass
    return options or [("General documents", "is_it_project")]


# ── Approval modal ────────────────────────────────────────────────────────────


class ApprovalModal(ModalScreen[ApprovalResult]):
    """Present plan ops to the user as a checklist; return an ApprovalResult."""

    DEFAULT_CSS = """
    ApprovalModal {
        align: center middle;
    }
    #approval-dialog {
        width: 70%;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #plan-title {
        text-style: bold;
        padding-bottom: 1;
        color: $accent;
    }
    #rationale-disclaimer {
        color: $text-muted;
    }
    #plan-rationale {
        padding: 0 0 1 0;
        color: $text-muted;
    }
    #layout-title {
        text-style: bold;
        color: $accent;
    }
    #layout-notes-disclaimer {
        color: $text-muted;
        padding-bottom: 1;
    }
    #layout-scroll {
        max-height: 10;
        margin-bottom: 1;
    }
    #target-layout {
        color: $text-muted;
    }
    #ops-scroll {
        max-height: 20;
    }
    #ops-json-path {
        color: $text-muted;
        padding-top: 1;
    }
    #refine-label {
        color: $text-muted;
        padding-top: 1;
    }
    #refine-input {
        margin-bottom: 1;
    }
    #approval-buttons {
        align: center middle;
        padding-top: 1;
        height: 3;
    }
    #approval-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [("escape", "reject", "Reject")]

    def __init__(self, plan_id: str, plan_data: dict, target: Path | None = None) -> None:
        super().__init__()
        self._plan_id = plan_id
        self._plan_data = plan_data
        self._target = target

    def compose(self) -> ComposeResult:
        ops = self._plan_data.get("ops", [])
        rationale = (self._plan_data.get("rationale") or "").strip()
        with Container(id="approval-dialog"):
            yield Label(
                f"Plan Review  ·  {self._plan_id[:8]}  ·  {len(ops)} op(s)",
                id="plan-title",
            )
            if rationale:
                yield Label(
                    "[dim]Model-generated rationale — not verified fact:[/dim]",
                    id="rationale-disclaimer",
                    markup=True,
                )
                yield Static(rationale, id="plan-rationale")
            folder_notes = self._plan_data.get("folder_notes") or {}
            layout_lines = _render_target_layout(ops, folder_notes)
            if layout_lines:
                yield Rule()
                yield Label("Target layout", id="layout-title")
                if folder_notes:
                    yield Label(
                        "[dim]Folder notes are model-generated — not verified fact.[/dim]",
                        id="layout-notes-disclaimer",
                        markup=True,
                    )
                with ScrollableContainer(id="layout-scroll"):
                    yield Static("\n".join(layout_lines), id="target-layout", markup=False)
            yield Rule()
            with ScrollableContainer(id="ops-scroll"):
                if ops:
                    for op in ops:
                        yield Checkbox(
                            _fmt_op(op, self._target),
                            value=True,
                            name=op.get("op_id", ""),
                        )
                else:
                    yield Label("[dim]No operations in this plan.[/dim]", markup=True)
            ops_json = (self._plan_data.get("ops_json_path") or "").strip()
            if ops_json:
                yield Label(f"Full ops JSON: {ops_json}", id="ops-json-path")
            yield Rule()
            yield Label(
                "Not quite right? Describe the changes and Refine (e.g. "
                '"merge the drafts into one folder", "don\'t quarantine the specs"):',
                id="refine-label",
            )
            yield Input(
                placeholder="Describe changes to make, then press Refine…", id="refine-input"
            )
            with Horizontal(id="approval-buttons"):
                yield Button("Approve", variant="success", id="approve-btn")
                yield Button("Refine", variant="primary", id="refine-btn")
                yield Button("Reject", variant="error", id="reject-btn")

    @on(Button.Pressed, "#approve-btn")
    def _approve(self) -> None:
        removed = [cb.name for cb in self.query(Checkbox) if not cb.value and cb.name]
        self.dismiss(ApprovalResult(approved=True, removed_op_ids=removed))

    @on(Button.Pressed, "#refine-btn")
    def _refine(self) -> None:
        self._submit_refinement()

    @on(Input.Submitted, "#refine-input")
    def _refine_on_enter(self) -> None:
        self._submit_refinement()

    def _submit_refinement(self) -> None:
        """Send free-text plan feedback to the agent (L6); no-op if the field is blank."""
        text = self.query_one("#refine-input", Input).value.strip()
        if not text:
            return  # nothing to refine — keep the modal open
        self.dismiss(ApprovalResult(approved=False, refinement=text))

    @on(Button.Pressed, "#reject-btn")
    def action_reject(self) -> None:
        self.dismiss(ApprovalResult(approved=False))


# ── Clarification modal (K1) ─────────────────────────────────────────────────


class ClarificationModal(ModalScreen[ClarificationResult]):
    """Present the agent's clarifying questions as free-text inputs (K1).

    Shown at most once per run, after the analysis pass. The user may answer any
    subset and Submit, or Skip entirely — an empty result tells the agent to
    proceed with its own best judgement.
    """

    DEFAULT_CSS = """
    ClarificationModal {
        align: center middle;
    }
    #clarify-dialog {
        width: 70%;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #clarify-title {
        text-style: bold;
        padding-bottom: 1;
        color: $accent;
    }
    #clarify-scroll {
        max-height: 20;
    }
    .clarify-q {
        text-style: bold;
        padding-top: 1;
    }
    .clarify-input {
        margin-bottom: 1;
    }
    #clarify-buttons {
        align: center middle;
        padding-top: 1;
        height: 3;
    }
    #clarify-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(self, questions: list[str]) -> None:
        super().__init__()
        self._questions = questions

    def compose(self) -> ComposeResult:
        with Container(id="clarify-dialog"):
            yield Label(
                f"The agent has {len(self._questions)} clarifying question(s)",
                id="clarify-title",
            )
            yield Rule()
            with ScrollableContainer(id="clarify-scroll"):
                for i, question in enumerate(self._questions):
                    yield Label(question, classes="clarify-q")
                    yield Input(
                        placeholder="Your answer (leave blank to skip this one)",
                        id=f"clarify-input-{i}",
                        classes="clarify-input",
                    )
            yield Rule()
            with Horizontal(id="clarify-buttons"):
                yield Button("Submit answers", variant="success", id="clarify-submit")
                yield Button("Skip — best judgement", id="clarify-skip")

    @on(Button.Pressed, "#clarify-submit")
    def _submit(self) -> None:
        answers: dict[str, str] = {}
        for i, question in enumerate(self._questions):
            value = self.query_one(f"#clarify-input-{i}", Input).value.strip()
            if value:
                answers[question] = value
        self.dismiss(ClarificationResult(answers=answers, provided=bool(answers)))

    @on(Button.Pressed, "#clarify-skip")
    def action_skip(self) -> None:
        self.dismiss(ClarificationResult(answers={}, provided=False))


# ── Options modal (L7) ────────────────────────────────────────────────────────


class OptionsModal(ModalScreen[OptionsResult]):
    """Present the agent's competing options as single-choice selections (L7).

    Shown at most once per run. Each question renders as a RadioSet of its options;
    Submit returns the chosen option per question, Skip returns an empty result so
    the agent proceeds with its own best judgement.
    """

    DEFAULT_CSS = """
    OptionsModal {
        align: center middle;
    }
    #options-dialog {
        width: 70%;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #options-title {
        text-style: bold;
        padding-bottom: 1;
        color: $accent;
    }
    #options-scroll {
        max-height: 20;
    }
    .options-q {
        text-style: bold;
        padding-top: 1;
    }
    .options-set {
        margin-bottom: 1;
    }
    #options-buttons {
        align: center middle;
        padding-top: 1;
        height: 3;
    }
    #options-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(self, questions: list[dict]) -> None:
        super().__init__()
        # Each item: {"question": str, "options": [str, ...]}.
        self._questions = questions

    def compose(self) -> ComposeResult:
        with Container(id="options-dialog"):
            yield Label(
                f"The agent proposes options for {len(self._questions)} question(s)",
                id="options-title",
            )
            yield Rule()
            with ScrollableContainer(id="options-scroll"):
                for i, item in enumerate(self._questions):
                    yield Label(item.get("question", ""), classes="options-q")
                    with RadioSet(id=f"options-set-{i}", classes="options-set"):
                        for j, option in enumerate(item.get("options", [])):
                            # First option pre-selected so a plain Submit is valid.
                            yield RadioButton(option, value=(j == 0))
            yield Rule()
            with Horizontal(id="options-buttons"):
                yield Button("Submit choices", variant="success", id="options-submit")
                yield Button("Skip — best judgement", id="options-skip")

    @on(Button.Pressed, "#options-submit")
    def _submit(self) -> None:
        selections: dict[str, str] = {}
        for i, item in enumerate(self._questions):
            radio_set = self.query_one(f"#options-set-{i}", RadioSet)
            pressed = radio_set.pressed_button
            if pressed is not None:
                selections[item.get("question", "")] = str(pressed.label)
        self.dismiss(OptionsResult(selections=selections, provided=bool(selections)))

    @on(Button.Pressed, "#options-skip")
    def action_skip(self) -> None:
        self.dismiss(OptionsResult(selections={}, provided=False))


# ── Setup screen (first-run wizard) ──────────────────────────────────────────


class SetupScreen(Screen):
    """First-run configuration wizard.

    Guides non-technical users through API endpoint + key selection and
    document-profile choice.  Saves to ~/.telcontar/config.env and the OS
    credential store, then transitions to StartupScreen.
    """

    DEFAULT_CSS = """
    SetupScreen {
        align: center middle;
    }
    #setup-panel {
        width: 72%;
        max-width: 84;
        border: round $accent;
        background: $surface;
        padding: 2 4;
    }
    #setup-title {
        text-style: bold;
        text-align: center;
        color: $accent;
        width: 100%;
        padding-bottom: 1;
    }
    .step-body {
        width: 100%;
        color: $text-muted;
        padding-bottom: 1;
    }
    .step-question {
        width: 100%;
        text-style: bold;
        padding-bottom: 1;
    }
    .step-hint {
        width: 100%;
        color: $text-muted;
        text-style: italic;
        padding-bottom: 1;
    }
    .step-error {
        color: $error;
        height: 1;
        padding-top: 0;
    }
    .step-input {
        margin-bottom: 1;
    }
    .service-btn {
        width: 100%;
        margin-bottom: 1;
    }
    .step-nav {
        height: 3;
        align: right middle;
        padding-top: 1;
    }
    .step-nav Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "app.quit", "Quit")]

    # ── Step indices ──────────────────────────────────────────────────────────
    _STEP_IDS = [
        "step-welcome",
        "step-service",
        "step-api",
        "step-profile",
        "step-done",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._service = "other"
        self._pending_url = ""
        self._pending_key = ""
        self._pending_model = ""
        # S8: set once the user has explicitly confirmed a plaintext-keyring
        # fallback warning — never defaulted to True.
        self._plaintext_confirmed = False

    def compose(self) -> ComposeResult:
        profile_options = _load_profile_options()

        yield Header()
        with VerticalScroll():
            with Container(id="setup-panel"):
                yield Label("Directory Organizer", id="setup-title")

                # ── Step 0: welcome ───────────────────────────────────────────
                with Container(id="step-welcome"):
                    yield Label(
                        "Welcome! Let's get you set up in just a couple of steps.",
                        classes="step-body",
                    )
                    yield Label(
                        "To read and analyze your documents, this app needs to talk to "
                        "an AI service. You'll need:\n\n"
                        "  • The web address of your AI service\n"
                        "  • An API key (your provider gives you this)",
                        classes="step-body",
                    )
                    with Horizontal(classes="step-nav"):
                        yield Button("Get started →", variant="primary", id="btn-welcome-next")

                # ── Step 1: service selection ─────────────────────────────────
                with Container(id="step-service"):
                    yield Label("Which AI service will you use?", classes="step-question")
                    yield Button("Mammouth", classes="service-btn", id="btn-svc-mammouth")
                    yield Button("Azure OpenAI", classes="service-btn", id="btn-svc-azure")
                    yield Button(
                        "Other / I'll enter a URL", classes="service-btn", id="btn-svc-other"
                    )

                # ── Step 2: API details ───────────────────────────────────────
                with Container(id="step-api"):
                    yield Label("", id="url-hint", classes="step-hint")
                    yield Label("Web address (URL) of your AI service:")
                    yield Input(placeholder="https://…", id="input-url", classes="step-input")
                    yield Label("Your API key:")
                    yield Input(
                        placeholder="Paste your key here",
                        id="input-key",
                        password=True,
                        classes="step-input",
                    )
                    yield Label("", id="model-hint", classes="step-hint")
                    yield Label("Model name:")
                    yield Input(placeholder="e.g. gpt-5", id="input-model", classes="step-input")
                    yield Label("", id="api-error", classes="step-error")
                    with Horizontal(classes="step-nav"):
                        yield Button("← Back", id="btn-api-back")
                        yield Button("Next →", variant="primary", id="btn-api-next")

                # ── Step 3: profile selection ─────────────────────────────────
                with Container(id="step-profile"):
                    yield Label(
                        "What kind of documents will you organize?",
                        classes="step-question",
                    )
                    yield Label(
                        "This sets the vocabulary the AI uses to categorize files.",
                        classes="step-body",
                    )
                    yield Select(
                        options=profile_options,
                        value=profile_options[0][1] if profile_options else Select.BLANK,
                        id="select-profile",
                    )
                    yield Label("", id="profile-error", classes="step-error")
                    with Horizontal(classes="step-nav"):
                        yield Button("← Back", id="btn-profile-back")
                        yield Button("Save & continue →", variant="primary", id="btn-profile-next")

                # ── Step 4: done ──────────────────────────────────────────────
                with Container(id="step-done"):
                    yield Label(
                        "You're all set!",
                        id="setup-done-title",
                    )
                    yield Label(
                        "Your settings have been saved securely.\n"
                        "You can update them at any time via the Settings button "
                        "on the main screen.",
                        classes="step-body",
                    )
                    with Horizontal(classes="step-nav"):
                        yield Button("Start Organizing →", variant="success", id="btn-done")

        yield Footer()

    def on_mount(self) -> None:
        self._show_step(0)

    # ── Step navigation ────────────────────────────────────────────────────────

    def _show_step(self, step: int) -> None:
        self._step = step
        for i, step_id in enumerate(self._STEP_IDS):
            self.query_one(f"#{step_id}").display = i == step

    # Welcome → service
    @on(Button.Pressed, "#btn-welcome-next")
    def _next_to_service(self) -> None:
        self._show_step(1)

    # Service selection → API details
    @on(Button.Pressed, "#btn-svc-mammouth")
    def _pick_mammouth(self) -> None:
        self._service = "mammouth"
        self.query_one("#url-hint", Label).update(
            "Mammouth: paste the base URL from your Mammouth account dashboard."
        )
        self.query_one("#input-url", Input).placeholder = "https://api.mammouth.ai/v1"
        self.query_one("#model-hint", Label).update(
            "Check the model list in your Mammouth dashboard — it must match exactly."
        )
        self.query_one("#input-model", Input).placeholder = "e.g. gpt-5"
        self._show_step(2)

    @on(Button.Pressed, "#btn-svc-azure")
    def _pick_azure(self) -> None:
        self._service = "azure"
        self.query_one("#url-hint", Label).update(
            "Azure OpenAI: use your deployment endpoint "
            "(ends with /openai/deployments/<model-name>)."
        )
        self.query_one(
            "#input-url", Input
        ).placeholder = "https://your-resource.openai.azure.com/openai/deployments/gpt-5"
        self.query_one("#model-hint", Label).update(
            "The deployment name you created in Azure OpenAI Studio "
            "(often, but not always, the same as the model)."
        )
        self.query_one("#input-model", Input).placeholder = "e.g. gpt-5"
        self._show_step(2)

    @on(Button.Pressed, "#btn-svc-other")
    def _pick_other(self) -> None:
        self._service = "other"
        self.query_one("#url-hint", Label).update(
            "Enter the base URL of any OpenAI-compatible AI service."
        )
        self.query_one("#input-url", Input).placeholder = "https://…"
        self.query_one("#model-hint", Label).update(
            "The exact model identifier documented by your provider."
        )
        self.query_one("#input-model", Input).placeholder = "e.g. gpt-4o"
        self._show_step(2)

    # API details → back to service / forward to profile
    @on(Button.Pressed, "#btn-api-back")
    def _api_back(self) -> None:
        self.query_one("#api-error", Label).update("")
        self._show_step(1)

    @on(Button.Pressed, "#btn-api-next")
    def _api_next(self) -> None:
        url = self.query_one("#input-url", Input).value.strip()
        key = self.query_one("#input-key", Input).value.strip()
        model = self.query_one("#input-model", Input).value.strip()
        error = self.query_one("#api-error", Label)
        if not url:
            error.update("Please enter the web address of your AI service.")
            return
        if not key:
            error.update("Please enter your API key.")
            return
        if not model:
            error.update("Please enter the model name.")
            return
        error.update("")
        self._pending_url = url
        self._pending_key = key
        self._pending_model = model
        self._show_step(3)

    # Profile → back to API / save and finish
    @on(Button.Pressed, "#btn-profile-back")
    def _profile_back(self) -> None:
        self._show_step(2)

    @on(Button.Pressed, "#btn-profile-next")
    def _profile_next(self) -> None:
        select = self.query_one("#select-profile", Select)
        error = self.query_one("#profile-error", Label)
        if select.value is Select.BLANK:
            error.update("Please choose a document type.")
            return
        profile = str(select.value)

        updates: dict[str, str] = {
            "llm_base_url": self._pending_url,
            "llm_api_key": self._pending_key,
            "llm_model": self._pending_model,
            "profile": profile,
        }
        if self._service == "azure":
            updates["llm_api_version"] = "2025-01-01-preview"

        from config.settings import PlaintextKeyFallbackNeeded, save_user_config

        try:
            save_user_config(updates, allow_plaintext_fallback=self._plaintext_confirmed)
        except PlaintextKeyFallbackNeeded:
            self._plaintext_confirmed = True
            error.update(
                '[bold]Your OS keyring is unavailable.[/bold] Press "Finish" again to '
                "store your API key in PLAINTEXT at ~/.telcontar/config.env, or go back "
                "and fix your keyring first."
            )
            return
        error.update("")
        self._show_step(4)

    # Done → startup
    @on(Button.Pressed, "#btn-done")
    def _go_to_start(self) -> None:
        self.app.push_screen(StartupScreen())


# ── Config screen ─────────────────────────────────────────────────────────────


class ConfigScreen(Screen):
    """Edit telcontar settings at any time after the initial setup."""

    DEFAULT_CSS = """
    ConfigScreen {
        align: center middle;
    }
    #config-panel {
        width: 72%;
        max-width: 84;
        border: round $accent;
        background: $surface;
        padding: 2 4;
    }
    #config-title {
        text-style: bold;
        text-align: center;
        color: $accent;
        width: 100%;
        padding-bottom: 1;
    }
    .cfg-label {
        width: 100%;
        color: $text-muted;
        padding-top: 1;
    }
    .cfg-input {
        margin-bottom: 0;
    }
    #cfg-error {
        color: $error;
        height: 1;
        padding-top: 1;
    }
    #cfg-buttons {
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    #cfg-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self) -> None:
        super().__init__()
        # S8: set once the user has explicitly confirmed a plaintext-keyring
        # fallback warning — never defaulted to True.
        self._plaintext_confirmed = False

    def compose(self) -> ComposeResult:
        from config.settings import read_user_config

        current = read_user_config()
        profile_options = _load_profile_options()
        approval_options: list[tuple[str, str]] = [
            ("Always ask before any changes", "always"),
            ("Only ask before moving or quarantining files", "destructive_only"),
            ("Never ask — full automatic mode", "never"),
        ]

        current_profile = current.get("profile", "is_it_project")
        current_approval = current.get("approval_mode", "always")
        profile_value = (
            current_profile
            if any(v == current_profile for _, v in profile_options)
            else (profile_options[0][1] if profile_options else Select.BLANK)
        )
        approval_value = (
            current_approval
            if any(v == current_approval for _, v in approval_options)
            else "always"
        )

        yield Header()
        with VerticalScroll():
            with Container(id="config-panel"):
                yield Label("Settings", id="config-title")

                yield Label("AI service web address (URL):", classes="cfg-label")
                yield Input(
                    value=current.get("llm_base_url", ""),
                    placeholder="https://…",
                    id="cfg-url",
                    classes="cfg-input",
                )

                yield Label("API key (leave empty to keep the saved key):", classes="cfg-label")
                yield Input(
                    placeholder="Paste a new key, or leave empty to keep the current one",
                    id="cfg-key",
                    password=True,
                    classes="cfg-input",
                )

                yield Label("Model name:", classes="cfg-label")
                yield Input(
                    value=current.get("llm_model", "gpt-5"),
                    placeholder="e.g. gpt-5",
                    id="cfg-model",
                    classes="cfg-input",
                )

                yield Label("Document type:", classes="cfg-label")
                yield Select(
                    options=profile_options,
                    value=profile_value,
                    id="cfg-profile",
                )

                yield Label("How careful should the app be?", classes="cfg-label")
                yield Select(
                    options=approval_options,
                    value=approval_value,
                    id="cfg-approval",
                )

                yield Label("", id="cfg-error")
                with Horizontal(id="cfg-buttons"):
                    yield Button("Save", variant="primary", id="btn-cfg-save")
                    yield Button("Cancel", id="btn-cfg-cancel")

        yield Footer()

    @on(Button.Pressed, "#btn-cfg-save")
    def _save(self) -> None:
        url = self.query_one("#cfg-url", Input).value.strip()
        key = self.query_one("#cfg-key", Input).value.strip()
        model = self.query_one("#cfg-model", Input).value.strip()
        profile_select = self.query_one("#cfg-profile", Select)
        approval_select = self.query_one("#cfg-approval", Select)

        if not url:
            self.query_one("#cfg-error", Label).update("Please enter the web address.")
            return
        if not model:
            self.query_one("#cfg-error", Label).update("Please enter the model name.")
            return

        updates: dict[str, str] = {
            "llm_base_url": url,
            "llm_model": model,
            "profile": (
                str(profile_select.value)
                if profile_select.value is not Select.BLANK
                else "is_it_project"
            ),
            "approval_mode": (
                str(approval_select.value)
                if approval_select.value is not Select.BLANK
                else "always"
            ),
        }
        if key:
            updates["llm_api_key"] = key

        from config.settings import PlaintextKeyFallbackNeeded, save_user_config

        try:
            save_user_config(updates, allow_plaintext_fallback=self._plaintext_confirmed)
        except PlaintextKeyFallbackNeeded:
            self._plaintext_confirmed = True
            self.query_one("#cfg-error", Label).update(
                '[bold]Your OS keyring is unavailable.[/bold] Press "Save" again to '
                "store your API key in PLAINTEXT at ~/.telcontar/config.env, or cancel "
                "and fix your keyring first."
            )
            return
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-cfg-cancel")
    def action_cancel(self) -> None:
        self.app.pop_screen()


# ── Journal screen ────────────────────────────────────────────────────────────


def _resolve_journal_path(project_root: Path) -> Path:
    from config.settings import Settings

    journal_path = Settings().journal_path
    if not journal_path.is_absolute():
        journal_path = project_root / journal_path
    return journal_path


def _resolve_plans_dir(project_root: Path) -> Path:
    from config.settings import Settings

    plans_dir = Settings().plans_dir
    if not plans_dir.is_absolute():
        plans_dir = project_root / plans_dir
    return plans_dir


def _fmt_journal_entry(entry: dict) -> str:
    ts = entry.get("timestamp", "?")
    op_type = entry.get("op_type", "?")
    if op_type == "hard_stop":
        reason = entry.get("reason", "")
        failed = entry.get("failed_count", len(entry.get("failed_ops", [])))
        lines = [f"[bold red]{ts}  HARD STOP[/bold red]  ({failed} op(s) failed) — {reason}"]
        for fop in entry.get("failed_ops", []):
            lines.append(
                f"    [red]✗ {fop.get('op_type', '?')}[/red]  {fop.get('src', '?')}"
                f"  — {fop.get('error', '')}"
            )
        return "\n".join(lines)
    src = entry.get("src", "?")
    dst = entry.get("dst")
    target = f"  →  {dst}" if dst else ""
    return f"[dim]{ts}[/dim]  {op_type:<10}  {src}{target}"


class JournalScreen(ModalScreen[None]):
    """Read-only view of the full undo journal, newest entries last — and the
    one place ``undo_last`` can be triggered from (S1).

    Reads `.organizer/journal.jsonl` directly from disk and calls
    ``server.tools.undo_last`` directly (local file/function, same machine)
    rather than through an MCP tool — undo is deliberately an explicit user
    action here, never something the agent can call itself.
    """

    DEFAULT_CSS = """
    JournalScreen {
        align: center middle;
    }
    #journal-dialog {
        width: 90%;
        max-height: 85%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #journal-title {
        width: 100%;
        text-style: bold;
        padding-bottom: 1;
        color: $accent;
    }
    #journal-scroll {
        max-height: 25;
    }
    #journal-status {
        padding-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("j", "close", "Close"),
        ("u", "undo", "Undo last"),
    ]

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self._project_root = project_root
        self._status = ""

    def compose(self) -> ComposeResult:
        from server.journal import all_entries

        journal_path = _resolve_journal_path(self._project_root)
        entries = all_entries(journal_path) if journal_path.is_file() else []

        with Container(id="journal-dialog"):
            yield Label(f"Operation journal  ·  {journal_path}", id="journal-title")
            yield Rule()
            with VerticalScroll(id="journal-scroll"):
                if entries:
                    for entry in entries:
                        yield Static(_fmt_journal_entry(entry), markup=True)
                else:
                    yield Label("[dim]No operations recorded yet.[/dim]", markup=True)
            yield Rule()
            if self._status:
                yield Label(self._status, id="journal-status", markup=True)
            yield Label(
                "[dim]Press Esc or j to close, u to undo the last operation.[/dim]", markup=True
            )

    def action_close(self) -> None:
        self.dismiss(None)

    async def action_undo(self) -> None:
        from server.tools import undo_last

        journal_path = _resolve_journal_path(self._project_root)
        plans_dir = _resolve_plans_dir(self._project_root)
        result = undo_last(journal_path, plans_dir)
        if result.get("undone") is not None:
            self._status = "[green]Undone the last operation.[/green]"
        else:
            self._status = f"[red]Undo failed: {result.get('error', 'unknown error')}[/red]"
        await self.recompose()


# ── Organizer screen ──────────────────────────────────────────────────────────


def _directory_overview(target: Path, max_entries: int = 5000) -> str:
    """Code-generated, deterministic one-glance summary of a directory (L3).

    Reads only names and structure — no file contents, no LLM, no latency —
    counting files, subfolders and the most common file types. Bounded by
    ``max_entries`` so a huge tree cannot stall the UI (the count then reads
    ``N+``). Shown as the opening telcontar turn before ANALYZE so the user can
    steer the run instead of it auto-organizing.
    """
    file_count = 0
    dir_count = 0
    ext_counts: dict[str, int] = {}
    truncated = False
    for _root, dirs, files in os.walk(target):
        dir_count += len(dirs)
        for name in files:
            if file_count >= max_entries:
                truncated = True
                break
            file_count += 1
            ext = Path(name).suffix.lower() or "(no extension)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        if truncated:
            break

    count_label = f"{file_count}{'+' if truncated else ''}"
    lines = [
        f"Target directory: {target}",
        f"{count_label} file(s) across {dir_count} subfolder(s).",
    ]
    if ext_counts:
        top = sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        breakdown = ", ".join(f"{n}× {ext}" for ext, n in top)
        lines.append(f"Most common types: {breakdown}.")
    else:
        lines.append("No files found at or below this folder.")
    return "\n".join(lines)


class OrganizerScreen(Screen):
    """Main screen: file-tree sidebar + a single chat-transcript conversation pane.

    The transcript reads as a conversation: speaker-differentiated turns
    (``telcontar`` / ``you``) carry the plain-language narrative, while the
    machinery (raw tool calls + results) is grouped into click-to-expand
    ``internal steps`` Collapsibles interleaved at the point they happened.
    """

    DEFAULT_CSS = """
    OrganizerScreen {
        layout: vertical;
    }
    #main-split {
        height: 1fr;
    }
    #file-tree {
        width: 22%;
        border-right: solid $accent-darken-2;
    }
    #conversation-pane {
        width: 1fr;
        padding: 0 1;
    }
    #starter-pane {
        height: 1fr;
        padding: 1 2;
    }
    #starter-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    #dir-overview {
        padding: 1 1;
        background: $panel;
        color: $text;
    }
    #instructions-label {
        padding-top: 1;
        color: $text-muted;
    }
    #instructions-input {
        margin: 1 0;
    }
    #starter-buttons {
        height: 3;
        align: left middle;
    }
    .turn {
        margin-top: 1;
        padding: 0 1;
    }
    .turn-telcontar {
        color: $text;
        border-left: thick $accent;
    }
    .turn-user {
        color: $accent;
        border-left: thick $success;
    }
    .internal-steps {
        margin: 0 0 0 2;
        color: $text-muted;
    }
    .steps-log {
        color: $text-muted;
        padding: 0 1;
    }
    #ops-journal {
        height: 5;
        background: $panel;
        color: $text-muted;
        border-top: solid $accent-darken-2;
        padding: 0 1;
        scrollbar-size-horizontal: 1;
    }
    #status-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("g", "query_corpus", "Query corpus"),
        ("j", "view_journal", "Journal"),
    ]

    _SPEAKERS = {"telcontar": "telcontar", "user": "you"}
    # Tools whose completion mutates the undo journal — a result from one of these
    # refreshes the bottom operations panel. archive_document/compress_quarantine
    # are no longer directly agent-callable (S1) — they only run inside
    # execute_plan now, via propose_archive_document/propose_compress_quarantine.
    _JOURNAL_WRITING_TOOLS = frozenset({"execute_plan"})

    def __init__(self, target: Path) -> None:
        super().__init__()
        self._target = target
        self._status = "Initialising…"
        self._tokens = ""
        self._last_narration = ""
        self._done = False
        self._started = False
        # Name of the tool whose result we're awaiting — lets a tool_result event
        # (which carries no tool name) know whether to refresh the ops journal.
        self._current_tool = ""
        # The currently-open "internal steps" group: a Static we append raw tool
        # lines to, plus its running text. None between groups (a speaker turn
        # closes the group so the next tool call opens a fresh Collapsible).
        self._steps_widget: Static | None = None
        self._steps_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        # Starter pane (L3): a code-generated overview + optional steering
        # instructions, shown before ANALYZE instead of auto-organizing.
        with VerticalScroll(id="starter-pane"):
            yield Label("Here's what I found", id="starter-title")
            yield Static(_directory_overview(self._target), id="dir-overview", markup=False)
            yield Label(
                "Tell me how you'd like it organized (optional) — e.g. \"group by "
                'workstream", "keep the 2024 invoices together", "don\'t quarantine drafts":',
                id="instructions-label",
            )
            yield Input(
                placeholder="Steering instructions — leave blank to use my best judgement",
                id="instructions-input",
            )
            with Horizontal(id="starter-buttons"):
                yield Button("Start organizing", variant="primary", id="proceed-btn")
        with Horizontal(id="main-split"):
            yield DirectoryTree(str(self._target), id="file-tree")
            yield VerticalScroll(id="conversation-pane")
        # Operations journal (L4): a compact bottom strip of the file operations
        # recorded in the undo journal, one line per entry, horizontally scrollable.
        yield RichLog(id="ops-journal", markup=True, highlight=False, wrap=False)
        yield Static(self._status, id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Show the starter pane first; the transcript/agent starts on "proceed".
        self.query_one("#main-split").display = False
        self._set_status("Review the overview, add any instructions, then Start organizing.")
        self._refresh_ops_journal()
        self.query_one("#instructions-input", Input).focus()

    @on(Button.Pressed, "#proceed-btn")
    def _proceed_button(self) -> None:
        self._start_organizing()

    @on(Input.Submitted, "#instructions-input")
    def _proceed_on_enter(self) -> None:
        self._start_organizing()

    def _start_organizing(self) -> None:
        """Leave the starter pane and launch the agent with any steering text (L3)."""
        if self._started:
            return
        self._started = True
        instructions = self.query_one("#instructions-input", Input).value.strip()
        self.query_one("#starter-pane").display = False
        self.query_one("#main-split").display = True
        # Move focus off the (now-hidden) input so screen key bindings work again.
        self.query_one("#file-tree", DirectoryTree).focus()
        self._add_turn("telcontar", f"Target: {self._target}")
        if instructions:
            self._add_turn("user", instructions)
        self.run_worker(self._agent_worker(instructions), exclusive=True)

    # ── Transcript helpers ────────────────────────────────────────────────────

    def _pane(self) -> VerticalScroll:
        return self.query_one("#conversation-pane", VerticalScroll)

    def _scroll_end(self) -> None:
        try:
            self._pane().scroll_end(animate=False)
        except NoMatches:
            pass

    def _add_turn(self, speaker: str, text: str) -> None:
        """Append a speaker-tagged turn, closing any open internal-steps group.

        ``speaker`` is ``telcontar`` or ``user``; the label is rendered inline so
        the transcript reads as a conversation. Adding a turn ends the current
        steps group, so a following tool call opens a fresh Collapsible.
        """
        self._steps_widget = None
        label = self._SPEAKERS.get(speaker, speaker)
        turn = Static(
            f"[b]{label}[/b]  {text}",
            markup=True,
            classes=f"turn turn-{speaker}",
        )
        try:
            self._pane().mount(turn)
        except NoMatches:
            return
        self._scroll_end()

    def _append_step(self, line: str) -> None:
        """Append a raw tool line to the current internal-steps Collapsible.

        Consecutive tool activity accumulates in one collapsed group until a
        speaker turn (e.g. a narration line) closes it — so the transcript reads
        as narrative with the machinery tucked into expandable steps.
        """
        if self._steps_widget is None:
            self._steps_lines = []
            static = Static("", markup=True, classes="steps-log")
            collapsible = Collapsible(static, title="internal steps", collapsed=True)
            collapsible.add_class("internal-steps")
            self._steps_widget = static
            try:
                self._pane().mount(collapsible)
            except NoMatches:
                self._steps_widget = None
                return
        self._steps_lines.append(line)
        self._steps_widget.update("\n".join(self._steps_lines))
        self._scroll_end()

    def _set_status(self, text: str) -> None:
        self._status = text
        self._refresh_status_bar()

    def _set_tokens(self, usage: str) -> None:
        self._tokens = usage
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        line = self._status
        if self._tokens:
            line = f"{self._status}   ·   ⬍ {self._tokens}"
        self.query_one("#status-bar", Static).update(line)

    def _refresh_ops_journal(self) -> None:
        """Re-render the bottom operations journal from the undo journal (L4).

        One line per entry, newest last, drawn straight from
        ``.organizer/journal.jsonl`` via the same formatter the modal
        JournalScreen uses. Multi-line entries (a hard stop lists its failed ops)
        are collapsed to their summary line so the strip stays one-line-per-op;
        press ``j`` for the full detail. Horizontally scrollable (wrap=False).
        """
        from server.journal import all_entries

        try:
            log = self.query_one("#ops-journal", RichLog)
        except NoMatches:
            return
        log.clear()
        try:
            journal_path = _resolve_journal_path(_PROJECT_ROOT)
            entries = all_entries(journal_path) if journal_path.is_file() else []
        except Exception:
            # A config/read error must never break the screen — just show nothing.
            entries = []
        if not entries:
            log.write("[dim]No operations yet.[/dim]")
            return
        for entry in entries:
            log.write(_fmt_journal_entry(entry).split("\n", 1)[0])

    def _narrate(self, tool: str) -> None:
        """Add a plain-language macro-task turn to the transcript (F10).

        Consecutive calls in the same macro-task collapse to one turn, so the pane
        reads as telcontar-voice progress rather than a raw tool-call log. Emitting
        the turn also closes the open internal-steps group, so the detailed calls
        for this macro-task collect under it.
        """
        phrase = _TOOL_NARRATION.get(tool)
        if phrase and phrase != self._last_narration:
            self._last_narration = phrase
            self._add_turn("telcontar", f"[dim italic]{phrase}[/dim italic]")

    def action_view_journal(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        self.app.push_screen(JournalScreen(project_root))

    async def _agent_worker(self, instructions: str | None = None) -> None:
        from config.settings import load as load_settings
        from host.agent import run_agent
        from host.llm import make_client

        try:
            settings = load_settings()
        except Exception as exc:
            self._add_turn("telcontar", f"[bold red]Config error:[/bold red] {_fmt_exc(exc)}")
            self._set_status("Error — check settings")
            return

        llm = make_client(settings)

        def on_event(event: AgentEvent) -> None:
            match event.kind:
                case "thinking":
                    self._set_status(event.text)
                case "tool_call":
                    # Narrate first so the telcontar turn precedes (and opens a
                    # fresh group for) the detailed call it announces.
                    self._current_tool = (event.data or {}).get("tool", "")
                    self._narrate(self._current_tool)
                    self._append_step(f"[yellow]▶ {event.text}[/yellow]")
                case "tool_result":
                    self._append_step(f"[dim]  {event.text}[/dim]")
                    # A file-mutating tool just finished → refresh the ops journal.
                    if self._current_tool in self._JOURNAL_WRITING_TOOLS:
                        self._refresh_ops_journal()
                    self._current_tool = ""
                case "plan_ready":
                    self._set_status("Waiting for plan approval…")
                case "question":
                    self._set_status("Awaiting your answers…")
                case "options":
                    self._set_status("Awaiting your choice…")
                case "tokens":
                    self._set_tokens(event.text)
                case "done":
                    self._add_turn("telcontar", f"[bold green]✓ Done[/bold green]\n{event.text}")
                    self._refresh_ops_journal()
                    self._set_status("Done")
                case "error":
                    self._add_turn(
                        "telcontar",
                        f"[bold red]✗ {event.text}[/bold red]\n"
                        "[dim]Press j to view the operation journal for details.[/dim]",
                    )
                    self._set_status("Error")

        async def on_approval_needed(plan_id: str, plan_data: dict) -> ApprovalResult:
            self._add_turn(
                "telcontar",
                f"[bold cyan]Plan ready for review[/bold cyan]  "
                f"({len(plan_data.get('ops', []))} op(s)) — awaiting approval…",
            )
            result: ApprovalResult = await self.app.push_screen_wait(
                ApprovalModal(plan_id, plan_data, self._target)
            )
            if result.approved:
                removed = len(result.removed_op_ids)
                self._add_turn(
                    "user",
                    "[green]Approved[/green]" + (f"  ({removed} op(s) removed)" if removed else ""),
                )
            elif result.refinement:
                self._add_turn("user", f"[yellow]Refine:[/yellow] {result.refinement}")
            else:
                self._add_turn("user", "[red]Rejected[/red] — sending feedback to agent")
            return result

        async def on_questions_needed(questions: list[str]) -> ClarificationResult:
            self._add_turn(
                "telcontar",
                f"[bold cyan]The agent has {len(questions)} clarifying question(s)[/bold cyan] "
                "— awaiting your input…",
            )
            answer: ClarificationResult = await self.app.push_screen_wait(
                ClarificationModal(questions)
            )
            if answer.provided:
                self._add_turn("user", f"[green]Answered {len(answer.answers)} question(s)[/green]")
            else:
                self._add_turn("user", "[dim]Skipped — the agent will use its best judgement[/dim]")
            return answer

        async def on_options_needed(questions: list[dict]) -> OptionsResult:
            self._add_turn(
                "telcontar",
                f"[bold cyan]The agent proposes options for {len(questions)} "
                "question(s)[/bold cyan] — awaiting your choice…",
            )
            choice: OptionsResult = await self.app.push_screen_wait(OptionsModal(questions))
            if choice.provided:
                self._add_turn("user", f"[green]Chose {len(choice.selections)} option(s)[/green]")
            else:
                self._add_turn("user", "[dim]Skipped — the agent will use its best judgement[/dim]")
            return choice

        try:
            await run_agent(
                target=self._target,
                settings=settings,
                llm=llm,
                on_event=on_event,
                on_approval_needed=on_approval_needed,
                on_questions_needed=on_questions_needed,
                on_options_needed=on_options_needed,
                instructions=instructions,
            )
        except Exception as exc:
            self._add_turn(
                "telcontar",
                f"[bold red]Agent error:[/bold red] {_fmt_exc(exc)}\n"
                "[dim]Press j to view the operation journal for details.[/dim]",
            )
            self._set_status("Error")
            return

        self._done = True
        self._add_turn(
            "telcontar",
            "[bold]Press [cyan]g[/cyan] to ask questions about this corpus, "
            "or [cyan]q[/cyan] to quit.[/bold]",
        )
        _send_notification(self._target)

    def action_query_corpus(self) -> None:
        """Switch into interactive query mode over the just-organized corpus."""
        if not self._done:
            self._set_status("Query mode available once organizing completes.")
            return
        self.app.push_screen(QueryScreen(self._target))


# ── Query screen ──────────────────────────────────────────────────────────────


class QueryScreen(Screen):
    """Interactive read-only Q&A over an already-analyzed corpus.

    Keeps a single MCP session open for the whole chat and threads the
    conversation history across questions for multi-turn context.
    """

    DEFAULT_CSS = """
    QueryScreen {
        layout: vertical;
    }
    #query-split {
        height: 1fr;
    }
    #query-log {
        width: 65%;
        padding: 0 1;
        border-right: solid $accent-darken-2;
    }
    #query-timeline {
        width: 35%;
        padding: 0 1;
        color: $text-muted;
    }
    #query-input {
        dock: bottom;
        margin: 0 1 1 1;
    }
    #query-status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [("escape", "back", "Back"), ("ctrl+c", "app.quit", "Quit")]

    def __init__(self, target: Path) -> None:
        super().__init__()
        self._target = target
        self._status = "Connecting…"
        self._tokens = ""
        self._questions: asyncio.Queue[str] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="query-split"):
            yield RichLog(id="query-log", highlight=True, markup=True, wrap=True)
            yield RichLog(id="query-timeline", highlight=True, markup=True, wrap=True)
        yield Static(self._status, id="query-status")
        yield Input(placeholder="Ask a question about this corpus…", id="query-input")
        yield Footer()

    def on_mount(self) -> None:
        self._log(f"[bold]Querying:[/bold] {self._target}")
        self._log("[dim]Type a question and press Enter. Esc to go back.[/dim]")
        self.run_worker(self._query_worker(), exclusive=True)
        self.query_one("#query-input", Input).focus()

    def _log(self, text: str) -> None:
        # A background query worker can outlive the screen (e.g. the user pressed
        # Esc to go back mid-query), so the widget may already be gone.
        try:
            self.query_one("#query-log", RichLog).write(text)
        except NoMatches:
            pass

    def _log_tool(self, text: str) -> None:
        try:
            self.query_one("#query-timeline", RichLog).write(text)
        except NoMatches:
            pass

    def _set_status(self, text: str) -> None:
        self._status = text
        self._refresh_status_bar()

    def _set_tokens(self, usage: str) -> None:
        self._tokens = usage
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        line = self._status
        if self._tokens:
            line = f"{self._status}   ·   ⬍ {self._tokens}"
        try:
            self.query_one("#query-status", Static).update(line)
        except NoMatches:
            pass

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Input.Submitted, "#query-input")
    def _submit(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question:
            return
        self.query_one("#query-input", Input).value = ""
        self._log(f"\n[bold cyan]❯ {question}[/bold cyan]")
        self._questions.put_nowait(question)

    async def _query_worker(self) -> None:
        from config.settings import load as load_settings
        from host.agent import mcp_session, run_query_loop
        from host.llm import make_client

        try:
            settings = load_settings()
        except Exception as exc:
            self._log(f"[bold red]Config error:[/bold red] {_fmt_exc(exc)}")
            self._set_status("Error — check settings")
            return

        llm = make_client(settings)

        def on_event(event: AgentEvent) -> None:
            match event.kind:
                case "thinking":
                    self._set_status(event.text)
                case "tool_call":
                    self._log_tool(f"[yellow]▶ {event.text}[/yellow]")
                case "tool_result":
                    self._log_tool(f"[dim]  {event.text}[/dim]")
                case "tokens":
                    self._set_tokens(event.text)
                case "error":
                    self._log(f"[bold red]✗ {event.text}[/bold red]")

        history: list[dict] | None = None
        try:
            async with mcp_session(_PROJECT_ROOT, target=self._target) as session:
                self._set_status("Ready — ask a question.")
                while True:
                    question = await self._questions.get()
                    self._set_status("Thinking…")
                    answer, history = await run_query_loop(
                        question=question,
                        settings=settings,
                        llm=llm,
                        session=session,
                        on_event=on_event,
                        history=history,
                        project_root=_PROJECT_ROOT,
                    )
                    self._log(f"[green]{answer}[/green]")
                    self._set_status("Ready — ask a question.")
        except Exception as exc:
            self._log(f"[bold red]Query error:[/bold red] {_fmt_exc(exc)}")
            self._set_status("Error")


# ── Startup screen ────────────────────────────────────────────────────────────


class StartupScreen(Screen):
    """Collect the target directory from the user before launching the agent."""

    DEFAULT_CSS = """
    StartupScreen {
        align: center middle;
    }
    #startup-panel {
        width: 60%;
        border: round $accent;
        background: $surface;
        padding: 2 4;
    }
    #startup-title {
        text-style: bold;
        text-align: center;
        padding-bottom: 1;
        color: $accent;
    }
    #target-tree {
        height: 12;
        border: round $panel;
        margin-bottom: 1;
    }
    #selected-label {
        color: $text-muted;
        padding-bottom: 1;
    }
    #startup-buttons {
        height: 3;
        align: center middle;
    }
    #startup-buttons Button {
        margin: 0 1;
    }
    #error-label {
        color: $error;
        height: 1;
        padding-top: 1;
    }
    """

    BINDINGS = [("escape", "app.quit", "Quit"), ("s", "settings", "Settings")]

    def __init__(self) -> None:
        super().__init__()
        self._selected: Path = Path.home()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Container(id="startup-panel"):
                yield Label("Directory Organizer", id="startup-title")
                yield Label("Choose the folder to organize:")
                yield DirectoryTree(str(Path.home()), id="target-tree")
                yield Label(f"Selected: {self._selected}", id="selected-label")
                with Horizontal(id="startup-buttons"):
                    yield Button("Organize", variant="primary", id="organize-btn")
                    yield Button("Query", variant="success", id="query-btn")
                    yield Button("⚙ Settings", id="settings-btn")
                yield Label("", id="error-label")
        yield Footer()

    def action_settings(self) -> None:
        self.app.push_screen(ConfigScreen())

    def _get_target(self) -> Path | None:
        return self._selected

    def _show_error(self, msg: str) -> None:
        self.query_one("#error-label", Label).update(msg)

    @on(DirectoryTree.DirectorySelected, "#target-tree")
    def _on_dir_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._selected = Path(event.path)
        self.query_one("#selected-label", Label).update(f"Selected: {self._selected}")

    @on(Button.Pressed, "#settings-btn")
    def _open_settings(self) -> None:
        self.app.push_screen(ConfigScreen())

    @on(Button.Pressed, "#organize-btn")
    def _start(self) -> None:
        target = self._get_target()
        if target is None:
            self._show_error("Please choose a folder to organize.")
            return
        if not target.is_dir():
            self._show_error(f"Not a directory: {target}")
            return
        self.app.push_screen(OrganizerScreen(target))

    @on(Button.Pressed, "#query-btn")
    def _query(self) -> None:
        # Query mode runs over the project-scoped registry (resolved from
        # settings, relative paths are anchored at the project root). The target
        # directory is optional here and used only as a display label.
        from config.settings import load as load_settings

        try:
            settings = load_settings()
        except Exception as exc:
            self._show_error(f"Config error: {_fmt_exc(exc)}")
            return
        registry = settings.registry_path
        if not registry.is_absolute():
            registry = _PROJECT_ROOT / registry
        if not registry.is_file():
            self._show_error(f"No analyzed corpus yet (missing {registry}). Run Organize first.")
            return
        target = self._get_target() or _PROJECT_ROOT
        self.app.push_screen(QueryScreen(target))


# ── App ───────────────────────────────────────────────────────────────────────


class OrganizerApp(App):
    """Root Textual application."""

    TITLE = "Directory Organizer"
    SUB_TITLE = "Powered by GPT-5 + MCP"

    def on_mount(self) -> None:
        from config.settings import is_configured

        if is_configured():
            self.push_screen(StartupScreen())
        else:
            self.push_screen(SetupScreen())


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fmt_exc(exc: BaseException) -> str:
    """Format an exception with its type so errors are actionable, not just a message.

    anyio/asyncio TaskGroups (the MCP session, the LLM HTTP client) wrap any child
    failure in an ExceptionGroup whose own message is just "unhandled errors in a
    TaskGroup (N sub-exception(s))" — useless on its own. Drill into `.exceptions`
    (recursively, since groups can nest) to surface the real leaf error(s) instead.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_fmt_exc(sub) for sub in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def _is_op_out_of_scope(op: dict, target: Path | None) -> bool:
    """Best-effort UI check: does this op's source resolve outside ``target``?

    Advisory only — the server's own `check_within_root` guard (M2) is the real
    enforcement boundary. This just makes the existing risk visible to the
    approver. Defensive: any resolution error (missing src, bad path) reads as
    in-scope rather than raising, so a malformed op never crashes the modal.
    """
    if target is None:
        return False
    src = op.get("src") or ""
    if not src:
        return False
    try:
        Path(src).resolve().relative_to(target.resolve())
        return False
    except (ValueError, OSError):
        return True


def _fmt_op(op: dict, target: Path | None = None) -> str:
    op_type = op.get("op_type", "?")
    src = Path(op.get("src", "")).name
    dst = op.get("dst", "")
    match op_type:
        case "rename":
            label = f"RENAME   {src}  →  {dst}"
        case "move":
            label = f"MOVE     {src}  →  {dst}"
        case "quarantine":
            label = f"QUARANTINE  {src}"
        case "update_file":
            # Subtle, not alarming (M4's discreet-styling convention): the
            # overwrite flag matters to the approver but isn't a red-banner risk.
            # Parens, not square brackets — Textual's markup parser treats
            # `[...]` as a style tag and silently drops unrecognized names.
            overwrite_flag = (
                "  [dim](overwrite)[/dim]" if (op.get("params") or {}).get("overwrite") else ""
            )
            label = f"UPDATE   {src}{overwrite_flag}"
        case _:
            label = f"{op_type.upper()}  {src}"
    if _is_op_out_of_scope(op, target):
        # Same discreet convention as the overwrite flag above — a quiet cue,
        # not a red banner, per the explicit "keep this subtle" instruction (S4).
        label += "  [dim](outside target)[/dim]"
    return label


# ── Plan target-layout preview (L5) ──────────────────────────────────────────


def _target_folders(ops: list[dict]) -> list[str]:
    """Distinct target folders a plan will populate (move dests + quarantine dir)."""
    folders: set[str] = set()
    for op in ops:
        dst = op.get("dst") or ""
        if not dst:
            continue
        if op.get("op_type") == "move":
            folders.add(dst)
        elif op.get("op_type") == "quarantine":
            folders.add(str(Path(dst).parent))
    return sorted(folders)


def _note_for(folder: str, notes: dict) -> str:
    """Best-effort match of a (possibly absolute) folder path to a folder note.

    The agent may key notes by a short relative folder name (``"01_decisions"``)
    while the plan ops carry absolute destinations, so match on the full path, its
    forward-slashed form, its basename, or a path suffix.
    """
    if not notes:
        return ""
    norm = folder.replace("\\", "/").rstrip("/")
    name = Path(folder).name
    for key in (folder, norm, name):
        if key in notes:
            return str(notes[key])
    for key, val in notes.items():
        k = str(key).replace("\\", "/").strip("/")
        if k and (norm == k or norm.endswith("/" + k) or name == Path(k).name):
            return str(val)
    return ""


def _render_target_layout(ops: list[dict], folder_notes: dict) -> list[str]:
    """Render the plan's target folder tree with per-folder purpose notes (L5).

    Returns tree lines (box-drawing connectors) or an empty list when the plan
    has no folder destinations. Folders with no note render as bare nodes.
    """
    folders = _target_folders(ops)
    if not folders:
        return []
    norm = [f.replace("\\", "/").rstrip("/") for f in folders]
    try:
        base = (
            str(Path(norm[0]).parent).replace("\\", "/")
            if len(norm) == 1
            else os.path.commonpath(norm).replace("\\", "/")
        )
    except ValueError:  # e.g. paths on different drives — no common base
        base = ""

    tree: dict = {}
    note_at: dict[tuple[str, ...], str] = {}
    for folder in norm:
        rel = folder[len(base) :].strip("/") if base and folder.startswith(base) else folder
        parts = [p for p in rel.split("/") if p]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        note = _note_for(folder, folder_notes)
        if note and parts:
            note_at[tuple(parts)] = note

    lines: list[str] = [f"{Path(base).name or base or 'target'}/"]

    def _walk(node: dict, prefix: str, acc: tuple[str, ...]) -> None:
        items = sorted(node.items())
        for i, (name, children) in enumerate(items):
            last = i == len(items) - 1
            connector = "└── " if last else "├── "
            new_acc = acc + (name,)
            note = note_at.get(new_acc, "")
            suffix = f"  — {note}" if note else ""
            lines.append(f"{prefix}{connector}{name}/{suffix}")
            _walk(children, prefix + ("    " if last else "│   "), new_acc)

    _walk(tree, "", ())
    return lines


def _send_notification(target: Path) -> None:
    try:
        from plyer import notification  # type: ignore[import-untyped]

        notification.notify(
            title="Directory Organizer",
            message=f"Done organizing {target.name}",
            app_name="Telcontar",
            timeout=5,
        )
    except Exception:
        pass  # non-fatal; TUI already shows the done message
