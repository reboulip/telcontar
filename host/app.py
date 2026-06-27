"""Textual TUI for the directory organizer host.

Screens
-------
StartupScreen   — target directory input
OrganizerScreen — sidebar (static file tree) + agent log + footer status

Modals
------
ApprovalModal   — plan review with per-op checkboxes (inline removal)
"""

from __future__ import annotations

import asyncio
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
    Static,
)

from host.agent import AgentEvent, ApprovalResult

# Repo root — host/app.py → host/ → project root. Used to launch the MCP server.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
            self._set_status("Error — check .env")
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
            self._set_status("Error — check .env")
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

    BINDINGS = [("escape", "quit", "Quit")]

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
                yield Label("", id="error-label")
        yield Footer()

    def _get_target(self) -> Path | None:
        raw = self.query_one("#target-input", Input).value.strip()
        if not raw:
            return None
        return Path(raw)

    def _show_error(self, msg: str) -> None:
        self.query_one("#error-label", Label).update(msg)

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
        self.push_screen(StartupScreen())


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
