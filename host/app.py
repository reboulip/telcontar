"""Textual TUI for the directory organizer host.

Screens
-------
SetupScreen     — first-run configuration wizard (API key, profile)
StartupScreen   — target directory input + entry to ConfigScreen
ConfigScreen    — edit settings at any time
OrganizerScreen — sidebar (static file tree) + agent log + footer status
QueryScreen     — interactive NL Q&A over an analyzed corpus

Modals
------
ApprovalModal   — plan review with per-op checkboxes (inline removal)
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Rule,
    Select,
    Static,
)

from host.agent import AgentEvent, ApprovalResult

# Package root: host/app.py → host/ → project root (or site-packages/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    #ops-scroll {
        max-height: 20;
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

    def __init__(self, plan_id: str, plan_data: dict) -> None:
        super().__init__()
        self._plan_id = plan_id
        self._plan_data = plan_data

    def compose(self) -> ComposeResult:
        ops = self._plan_data.get("ops", [])
        with Container(id="approval-dialog"):
            yield Label(
                f"Plan Review  ·  {self._plan_id[:8]}  ·  {len(ops)} op(s)",
                id="plan-title",
            )
            yield Rule()
            with ScrollableContainer(id="ops-scroll"):
                if ops:
                    for op in ops:
                        yield Checkbox(
                            _fmt_op(op),
                            value=True,
                            name=op.get("op_id", ""),
                        )
                else:
                    yield Label("[dim]No operations in this plan.[/dim]", markup=True)
            yield Rule()
            with Horizontal(id="approval-buttons"):
                yield Button("Approve", variant="success", id="approve-btn")
                yield Button("Reject", variant="error", id="reject-btn")

    @on(Button.Pressed, "#approve-btn")
    def _approve(self) -> None:
        removed = [cb.name for cb in self.query(Checkbox) if not cb.value and cb.name]
        self.dismiss(ApprovalResult(approved=True, removed_op_ids=removed))

    @on(Button.Pressed, "#reject-btn")
    def action_reject(self) -> None:
        self.dismiss(ApprovalResult(approved=False))


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
        color: $text-muted;
        padding-bottom: 1;
    }
    .step-question {
        text-style: bold;
        padding-bottom: 1;
    }
    .step-hint {
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

    BINDINGS = [("escape", "quit", "Quit")]

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
        self._show_step(2)

    @on(Button.Pressed, "#btn-svc-other")
    def _pick_other(self) -> None:
        self._service = "other"
        self.query_one("#url-hint", Label).update(
            "Enter the base URL of any OpenAI-compatible AI service."
        )
        self.query_one("#input-url", Input).placeholder = "https://…"
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
        error = self.query_one("#api-error", Label)
        if not url:
            error.update("Please enter the web address of your AI service.")
            return
        if not key:
            error.update("Please enter your API key.")
            return
        error.update("")
        self._pending_url = url
        self._pending_key = key
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
        error.update("")
        profile = str(select.value)

        updates: dict[str, str] = {
            "llm_base_url": self._pending_url,
            "llm_api_key": self._pending_key,
            "profile": profile,
        }
        if self._service == "azure":
            updates["llm_api_version"] = "2025-01-01-preview"

        from config.settings import save_user_config

        save_user_config(updates)
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
        profile_select = self.query_one("#cfg-profile", Select)
        approval_select = self.query_one("#cfg-approval", Select)

        if not url:
            self.query_one("#cfg-error", Label).update("Please enter the web address.")
            return

        updates: dict[str, str] = {
            "llm_base_url": url,
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

        from config.settings import save_user_config

        save_user_config(updates)
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-cfg-cancel")
    def action_cancel(self) -> None:
        self.app.pop_screen()


# ── Organizer screen ──────────────────────────────────────────────────────────


class OrganizerScreen(Screen):
    """Main screen: static file-tree sidebar + scrollable agent log."""

    DEFAULT_CSS = """
    OrganizerScreen {
        layout: vertical;
    }
    #main-split {
        height: 1fr;
    }
    #file-tree {
        width: 28%;
        border-right: solid $accent-darken-2;
    }
    #agent-log {
        width: 72%;
        padding: 0 1;
    }
    #status-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [("q", "quit", "Quit"), ("g", "query_corpus", "Query corpus")]

    def __init__(self, target: Path) -> None:
        super().__init__()
        self._target = target
        self._status = "Initialising…"
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-split"):
            yield DirectoryTree(str(self._target), id="file-tree")
            yield RichLog(id="agent-log", highlight=True, markup=True, wrap=True)
        yield Static(self._status, id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._log(f"[bold]Target:[/bold] {self._target}")
        self.run_worker(self._agent_worker(), exclusive=True)

    def _log(self, text: str) -> None:
        self.query_one("#agent-log", RichLog).write(text)

    def _set_status(self, text: str) -> None:
        self._status = text
        self.query_one("#status-bar", Static).update(text)

    async def _agent_worker(self) -> None:
        from config.settings import load as load_settings
        from host.agent import run_agent
        from host.llm import make_client

        try:
            settings = load_settings()
        except Exception as exc:
            self._log(f"[bold red]Config error:[/bold red] {exc}")
            self._set_status("Error — check settings")
            return

        llm = make_client(settings)

        def on_event(event: AgentEvent) -> None:
            match event.kind:
                case "thinking":
                    self._set_status(event.text)
                case "tool_call":
                    self._log(f"[yellow]▶ {event.text}[/yellow]")
                case "tool_result":
                    self._log(f"[dim]  {event.text}[/dim]")
                case "plan_ready":
                    self._set_status("Waiting for plan approval…")
                case "done":
                    self._log(f"\n[bold green]✓ Done[/bold green]\n{event.text}")
                    self._set_status("Done")
                case "error":
                    self._log(f"[bold red]✗ {event.text}[/bold red]")
                    self._set_status("Error")

        async def on_approval_needed(plan_id: str, plan_data: dict) -> ApprovalResult:
            self._log(
                f"[bold cyan]Plan ready for review[/bold cyan]  "
                f"({len(plan_data.get('ops', []))} op(s)) — awaiting approval…"
            )
            result: ApprovalResult = await self.app.push_screen_wait(
                ApprovalModal(plan_id, plan_data)
            )
            if result.approved:
                removed = len(result.removed_op_ids)
                self._log(
                    "[green]Approved[/green]" + (f"  ({removed} op(s) removed)" if removed else "")
                )
            else:
                self._log("[red]Rejected[/red] — sending feedback to agent")
            return result

        try:
            await run_agent(
                target=self._target,
                settings=settings,
                llm=llm,
                on_event=on_event,
                on_approval_needed=on_approval_needed,
            )
        except Exception as exc:
            self._log(f"[bold red]Agent error:[/bold red] {exc}")
            self._set_status("Error")
            return

        self._done = True
        self._log(
            "\n[bold]Press [cyan]g[/cyan] to ask questions about this corpus, "
            "or [cyan]q[/cyan] to quit.[/bold]"
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
    #query-log {
        height: 1fr;
        padding: 0 1;
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

    BINDINGS = [("escape", "back", "Back"), ("ctrl+c", "quit", "Quit")]

    def __init__(self, target: Path) -> None:
        super().__init__()
        self._target = target
        self._status = "Connecting…"
        self._questions: asyncio.Queue[str] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="query-log", highlight=True, markup=True, wrap=True)
        yield Static(self._status, id="query-status")
        yield Input(placeholder="Ask a question about this corpus…", id="query-input")
        yield Footer()

    def on_mount(self) -> None:
        self._log(f"[bold]Querying:[/bold] {self._target}")
        self._log("[dim]Type a question and press Enter. Esc to go back.[/dim]")
        self.run_worker(self._query_worker(), exclusive=True)
        self.query_one("#query-input", Input).focus()

    def _log(self, text: str) -> None:
        self.query_one("#query-log", RichLog).write(text)

    def _set_status(self, text: str) -> None:
        self._status = text
        self.query_one("#query-status", Static).update(text)

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
            self._log(f"[bold red]Config error:[/bold red] {exc}")
            self._set_status("Error — check settings")
            return

        llm = make_client(settings)

        def on_event(event: AgentEvent) -> None:
            match event.kind:
                case "thinking":
                    self._set_status(event.text)
                case "tool_call":
                    self._log(f"[yellow]▶ {event.text}[/yellow]")
                case "tool_result":
                    self._log(f"[dim]  {event.text}[/dim]")
                case "error":
                    self._log(f"[bold red]✗ {event.text}[/bold red]")

        history: list[dict] | None = None
        try:
            async with mcp_session(_PROJECT_ROOT) as session:
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
            self._log(f"[bold red]Query error:[/bold red] {exc}")
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
    #target-input {
        margin-bottom: 1;
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

    BINDINGS = [("escape", "quit", "Quit"), ("s", "settings", "Settings")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Container(id="startup-panel"):
                yield Label("Directory Organizer", id="startup-title")
                yield Label("Target directory:")
                yield Input(
                    placeholder="C:\\Users\\me\\Documents\\messy",
                    id="target-input",
                )
                with Horizontal(id="startup-buttons"):
                    yield Button("Organize", variant="primary", id="organize-btn")
                    yield Button("Query", variant="success", id="query-btn")
                    yield Button("⚙ Settings", id="settings-btn")
                yield Label("", id="error-label")
        yield Footer()

    def action_settings(self) -> None:
        self.app.push_screen(ConfigScreen())

    def _get_target(self) -> Path | None:
        raw = self.query_one("#target-input", Input).value.strip()
        if not raw:
            return None
        return Path(raw)

    def _show_error(self, msg: str) -> None:
        self.query_one("#error-label", Label).update(msg)

    @on(Button.Pressed, "#settings-btn")
    def _open_settings(self) -> None:
        self.app.push_screen(ConfigScreen())

    @on(Button.Pressed, "#organize-btn")
    @on(Input.Submitted, "#target-input")
    def _start(self) -> None:
        target = self._get_target()
        if target is None:
            self._show_error("Please enter a directory path.")
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
            self._show_error(f"Config error: {exc}")
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


def _fmt_op(op: dict) -> str:
    op_type = op.get("op_type", "?")
    src = Path(op.get("src", "")).name
    dst = op.get("dst", "")
    match op_type:
        case "rename":
            return f"RENAME   {src}  →  {dst}"
        case "move":
            return f"MOVE     {src}  →  {dst}"
        case "quarantine":
            return f"QUARANTINE  {src}"
        case _:
            return f"{op_type.upper()}  {src}"


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
