# Module Reference

Detailed breakdown of every Python module in the codebase. For auto-generated API docs (docstrings, signatures), see the [API Reference](../reference/api/server.md).

---

## `config/`

### `config/settings.py` (~44 lines)

**Role:** Single source of truth for all runtime configuration. Loads from `.env` (project-local, highest priority) then `~/.telcontar/config.env` (user-level fallback for installed-tool use) via Pydantic Settings; real environment variables override both.

**Key class:** `Settings` — a `BaseSettings` subclass with fields for LLM endpoint, safety, domain profile, document memory, and egress settings. `llm_base_url` and `llm_api_key` default to `""` so `Settings()` can be instantiated before the wizard runs.

**Public functions:**

| Function | Description |
|---|---|
| `load() -> Settings` | Instantiates `Settings`, injects the API key from the OS keyring if not in env/files, then validates that both `llm_base_url` and `llm_api_key` are present. Called once per process by the agent/query workers. |
| `is_configured() -> bool` | Returns `True` if the minimum required settings (URL + API key from env, file, or keyring) are present. Called by `OrganizerApp.on_mount` to choose the startup screen. |
| `save_user_config(updates: dict[str, str]) -> None` | Writes non-sensitive keys to `~/.telcontar/config.env`; stores the API key in the OS keyring (falls back to the config file if keyring is unavailable). |
| `read_user_config() -> dict[str, str]` | Returns the raw key→value pairs from `~/.telcontar/config.env` (lowercase keys, no API key). |

**Why it's structured this way:** Both the host and the server import this module independently (they run in different processes). There is no shared singleton across the stdio boundary.

---

## `server/`

The MCP server package. Launched as a subprocess by the host; communicates via stdio. Owns all file I/O, guardrails, and persistent state.

### `server/main.py`

**Role:** MCP server entrypoint. Registers all tools with FastMCP and wires each handler to `server/tools.py`. Lazy-initialises `Settings` and the active `Profile` on first use.

**Key object:** `mcp = FastMCP("directory-organizer")` — the FastMCP server instance.

**Entrypoint:** `main()` calls `mcp.run(transport="stdio")`.

**Design note:** This module is deliberately thin — it delegates all logic to `server/tools.py`. Tool parameters injected from config (e.g. `plans_dir`, `journal_path`) are resolved here and passed into the tool functions.

---

### `server/tools.py`

**Role:** All MCP tool implementations. Pure functions with no global state — they receive everything they need as arguments, making them directly testable without spawning an MCP server.

**Groups of functions:**

| Group | Functions |
|---|---|
| Read-only | `list_dir`, `walk_tree`, `read_file`, `extract_text`, `compute_checksum` |
| Plan management | `create_plan`, `get_plan`, `list_plans`, `review_plan`, `approve_plan`, `set_plan_rationale`, `set_plan_folder_notes` |
| Plan-building | `propose_rename`, `propose_move`, `propose_quarantine`, `propose_create_file`, `propose_update_file`, `propose_create_dir`, `propose_archive_document`, `propose_compress_quarantine` |
| Gated execution | `execute_plan`, `write_index`, `write_summary` |
| Recovery (not MCP tools) | `undo_last` — no longer registered as an MCP tool (M1); called directly by the TUI's `JournalScreen` |
| Registry | `record_document`, `get_document`, `list_documents`, `get_registry`, `find_duplicates`, `find_modified_documents` |
| Event journal | `create_event`, `list_events` |
| Knowledge graph | `build_graph`, `get_graph`, `get_actors` |
| Archive | `archive_document` (no longer an MCP tool; called by `execute_plan` for `archive_document` ops), `list_archived` |
| Quarantine compression | `compress_quarantine` (no longer an MCP tool; called by `execute_plan` for `compress_quarantine` ops) |

**Internal helpers:** `_apply_op` executes a single `PlanOp` against the filesystem, dispatching `rename`/`move`/`quarantine`/`create_file`/`update_file`/`create_dir` directly (`archive_document`/`compress_quarantine` ops are handled inline in `execute_plan` itself, calling the standalone functions above); `_reconcile_op` updates the registry record's path/status after execution; `_load_pending_plan` loads a plan and raises unless it is still `pending`, shared by all five newer `propose_*` functions.

---

### `server/plan.py`

**Role:** Plan data model and disk persistence. Defines the state machine, serialization, and plan/op CRUD.

**Key types:**
- `PlanState` — `Literal["pending", "approved", "executing", "done", "failed", "stopped"]`
- `OpType` — `Literal["rename", "move", "quarantine", "create_file", "update_file", "create_dir", "archive_document", "compress_quarantine"]`
- `PlanOp` — dataclass with `op_id` (UUID), `op_type`, `src`, `dst`, `status`, `error`, `retries`, `params: dict | None` (op-specific data that doesn't fit `src`/`dst` — e.g. `{"content": ...}` for `create_file`/`update_file`, `{"checksum": ..., "reason": ...}` for `archive_document`, `{"delete_originals": ...}` for `compress_quarantine`)
- `Plan` — dataclass with `plan_id`, `state`, `ops: list[PlanOp]`, timestamps, `rationale: str = ""` (agent's plain-language explanation, set via `set_plan_rationale`), `folder_notes: dict[str, str] = {}` (agent-supplied per-folder purpose notes for the approval-view target-layout preview, set via `set_plan_folder_notes`) — both round-trip through `to_dict`/`from_dict`, backward-compatible with older plan files via `d.get`

**State machine:** `_VALID_TRANSITIONS` dict enforces which state transitions are legal. `Plan.transition()` validates and applies.

**Persistence:** One JSON file per plan at `{plans_dir}/{plan_id}.json`. `save()`, `load()`, `list_all()`.

---

### `server/registry.py` (~243 lines)

**Role:** The engine's persistent document memory. Content-addressed (sha256 → `DocumentRecord`). Profile-agnostic — type validation lives in `tools.py`.

**Key types:**
- `DocumentRecord` — one analyzed document. Fields: `checksum`, `path`, `title`, `type`, `summary`, `provenance`, `date`, `entities`, `attributes`, `status`, `first_seen`, `last_analyzed`.
- `Registry` — in-memory view, keyed by checksum. Methods: `upsert`, `get`, `records`, `update_path`, `find_duplicates`, `find_modified`.

**`update_path`:** Called by `execute_plan` after each successful op to reconcile the record's stored path with the file's new location. Matches by normalized path comparison (`os.path.normcase`/`normpath`) for Windows compatibility.

**`find_duplicates`:** Union-find clustering by title-token Jaccard similarity (threshold 0.6) within the same type, or exact normalized-title match across types.

**Persistence:** Single JSON file at `registry_path`. `load()` returns an empty `Registry` if the file doesn't exist; `save()` writes pretty JSON with Unicode preserved.

---

### `server/profile.py` (~122 lines)

**Role:** Load and validate a domain profile TOML file. Expose typed accessors used by the tools layer and the host's system prompt builder.

**Key types:**
- `DocumentType` — `{id, label, description}`
- `Profile` — fully parsed profile with accessors: `document_type_ids()`, `entity_roles()`, `extraction_fields()`, `naming()`

**`load_profile(name, profiles_dir)`:** Reads `{profiles_dir}/{name}.toml`, parses with `tomllib`, validates required fields (name, at least one document type, no duplicate type IDs), and returns a `Profile`.

---

### `server/guards.py` (~43 lines)

**Role:** Three guardrail functions enforced before any file operation.

| Function | What it guards |
|---|---|
| `check_no_overwrite(dest)` | Raises `FileExistsError` if `dest` already exists |
| `safe_quarantine_path(src, quarantine_dir)` | Returns a collision-safe path in quarantine (suffixes `_1`, `_2`, …) |
| `check_allowlist(path, allowlist_dirs)` | Raises `PermissionError` if `path` is not under any allowlisted directory |

**Why separate:** These rules are invariants that must hold across multiple tools. Centralising them in one module makes them easy to audit and test independently.

---

### `server/journal.py` (~48 lines)

**Role:** Append-only JSONL helpers for the undo journal.

| Function | Description |
|---|---|
| `append(journal_path, entry)` | Appends one JSON entry + newline; creates parent dirs |
| `last(journal_path)` | Returns the last entry without removing it; `None` if empty |
| `all_entries(journal_path)` | Returns all entries in chronological order |
| `pop_last(journal_path)` | Removes and returns the last entry; rewrites the file |

**Design note:** `pop_last` rewrites the entire file minus the last line. For typical journal sizes (hundreds of entries) this is fine; for very large corpora a more efficient structure could be introduced later.

---

### `server/archive.py` (~64 lines)

**Role:** Append-only JSONL log of documents withdrawn from active memory — the "retirer de la mémoire" audit trail. Distinct from the undo journal (which records reversible file ops) and the event journal (project narrative).

**Key type:** `ArchiveEntry` — dataclass with `{checksum, title, reason, src, dst, archived_at}`. `dst` is `null` when the file was already absent at archive time.

| Function | Description |
|---|---|
| `append(archive_path, entry)` | Appends one archive entry as a JSONL line; creates parent dirs |
| `all_entries(archive_path)` | Returns all entries in chronological order; empty list if no file |

**Design note:** `archive_document` in `server/tools.py` coordinates the status flip in the registry, the quarantine move (journaled in the undo log for reversibility), and the append here. This module owns only the serialization.

---

### `server/sinks.py` (~76 lines)

**Role:** Output-sink abstraction — defines where the engine's synthesized Markdown artifacts are emitted.

**Key types:**
- `Sink` — `runtime_checkable` Protocol with attributes `name: str`, `external: bool` and methods `write_summary(target_dir, content) -> dict`, `write_folder_readme(folder, content) -> dict`.
- `LocalMarkdownSink` — the built-in sink (`name="local_markdown"`, `external=False`). Delegates to `tools.write_summary` and `tools.write_folder_readme`; writes files to the local filesystem.

**Key function:** `resolve_sinks(names, *, allow_external) -> list[Sink]` — instantiates the sinks named in the profile's `[sinks] default` list. Built-in sinks are created directly. Any unrecognised name is treated as an external sink: raises `PermissionError` if `allow_external=False`, or `NotImplementedError` if `True` (external sinks are separate MCP integrations, not built into this codebase).

**Design note:** `server/main.py` calls `resolve_sinks` inside `write_summary` and `write_folder_readme` handlers, passing `egress_allow_external_sinks` from `Settings`. A single-sink result is unwrapped; multiple sinks return `{"sinks": [...]}`.

---

### `server/extract.py` (~18 lines)

**Role:** Thin wrapper around markitdown for text extraction from binary formats.

**Key function:** `extract(path, max_chars) -> str` — calls `MarkItDown().convert(path)`, returns `result.text_content` truncated to `max_chars`.

**Single module-level instance:** `_md = MarkItDown()` — markitdown is initialized once per server process.

---

## `host/`

The MCP host package. Drives the GPT-5 agent loop and presents the Textual TUI.

### `host/main.py` (~9 lines)

**Role:** CLI entrypoint. Instantiates `OrganizerApp` and calls `.run()`.

**Entry point:** `main()` is registered as the `telcontar` script in `pyproject.toml`.

---

### `host/agent.py`

**Role:** The async agent loop — both organize and query modes. Fully decoupled from Textual — callers supply callbacks for events and approval so the module can be tested without a TUI.

**Key types:**
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step; `EventKind` includes `"question"` for the post-analysis clarification checkpoint, `"options"` for the multiple-option checkpoint (L7), and `"tokens"` for running LLM token-usage updates, alongside `"thinking"`, `"tool_call"`, `"tool_result"`, `"plan_ready"`, `"done"`, `"error"`. `"tool_call"` events carry `data={"tool": name}` in both the organize and query loops, so callers can key off the tool name (e.g. `OrganizerScreen._narrate`, F10) without parsing `text`
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str], refinement: str | None}`. `refinement` (L6) carries free-text plan-editing feedback from the `ApprovalModal`'s Refine button; when set, the plan is not executed even though `approved` is `False` — see `_handle_execute_plan` below
- `ClarificationResult` — `{answers: dict[str, str], provided: bool}`; answers from the post-analysis clarification checkpoint. `provided` is `False` when the user skipped / had nothing to add, in which case the agent proceeds with its own best judgement
- `OptionsResult` — `{selections: dict[str, str], provided: bool}`; the user's picks from the multiple-option checkpoint (L7), one selected option per question. `provided` is `False` when the user skipped, in which case the agent proceeds with its own best judgement
- `EventCallback` — `Callable[[AgentEvent], None]`
- `ApprovalCallback` — `Callable[[str, dict], Awaitable[ApprovalResult]]`
- `QuestionsCallback` — `Callable[[list[str]], Awaitable[ClarificationResult]]`
- `OptionsCallback` — `Callable[[list[dict]], Awaitable[OptionsResult]]`; each item is `{"question": str, "options": [str, ...]}`

**Key constants:**
- `QUERY_ALLOWED_TOOLS` — `frozenset` of read-only tool names exposed to the model in query mode (list/read/inspect tools; no plan, execute, write, build_graph, create_event, or archive tools)
- `_CLARIFY_TOOL_NAME` / `_CLARIFY_TOOL_SPEC` — the host-side synthetic tool `ask_clarification`. Never registered with or forwarded to the MCP server; appended to the OpenAI tool list only when a `QuestionsCallback` is wired in
- `_OPTIONS_TOOL_NAME` / `_OPTIONS_TOOL_SPEC` — the host-side synthetic tool `propose_options` (L7), mirroring `ask_clarification`. Never registered with or forwarded to the MCP server; appended to the OpenAI tool list only when an `OptionsCallback` is wired in. Each question requires 2-5 mutually-exclusive options

**Key functions:**
- `run_agent(target, settings, llm, on_event, on_approval_needed, on_questions_needed=None, on_options_needed=None, instructions=None)` — top-level organize entry; launches the MCP server subprocess via `mcp_session()`, then calls `run_agent_loop`
- `run_agent_loop(target, settings, llm, session, on_event, on_approval_needed, on_questions_needed=None, on_options_needed=None, project_root=None, instructions=None)` — the actual GPT-5 tool-calling loop for organize mode (injectable session for testing). `instructions` carries the user's optional pre-analysis steering text from the `OrganizerScreen` starter pane (L3); when non-empty it is appended to the seed user message ("Please organize the directory: …") before the loop starts. `on_options_needed` wires the L7 multiple-option checkpoint
- `run_query(question, settings, llm, on_event, history)` — convenience entry for one query, launching its own MCP session
- `run_query_loop(question, settings, llm, session, on_event, history, project_root)` — read-only tool-calling loop; threads `history` across calls for multi-turn context; returns `(answer, updated_history)`
- `_discover_openai_tools(session, allowed)` — lists MCP tools and converts to OpenAI function specs; when `allowed` is given, only tools in the set are exposed (used by query mode)
- `_build_system_prompt(project_root, settings)` — assembles the organize-mode system prompt from the active profile, including an optional clarification-checkpoint note and an optional multiple-option-checkpoint note between the ANALYZE and ORGANIZE sections
- `_build_query_system_prompt(project_root, settings)` — assembles the read-only query-mode system prompt from the active profile
- `_handle_execute_plan(...)` — intercepts `execute_plan` calls to insert the approval gate before forwarding to the server. Fetches the plan via `get_plan`, writes its full ops (plan id, rationale, folder notes, ops) to `.organizer/plan_ops.json` via `_write_ops_json` and attaches the path as `ops_json_path` on the event data, then awaits `on_approval_needed`. If the returned `ApprovalResult.refinement` is set (non-blank), the plan is NOT approved or executed — the tool result instead carries the refinement text back to the agent as a note instructing it to revise the plan (ops/rationale/folder notes) and call `execute_plan` again. Otherwise falls back to the plain approved/rejected path
- `_write_ops_json(plan_data, plans_dir)` — writes `{plan_id, rationale, folder_notes, ops}` to `<plans_dir>/../plan_ops.json` (i.e. `.organizer/plan_ops.json`), latest-plan-wins; returns the path, or `None` on an `OSError`
- `_handle_clarification(args, on_event, on_questions_needed, already_used)` — intercepts calls to the host-side `ask_clarification` tool; enforces the at-most-once-per-run rule and the "nothing to add → proceed" path; emits a `"question"` `AgentEvent` and awaits the callback; never raises — degenerate input (no callback wired, already used, no questions) returns a note telling the agent to proceed with its own best judgement
- `_handle_options(args, on_event, on_options_needed, already_used)` — intercepts calls to the host-side `propose_options` tool (L7); mirrors `_handle_clarification`: enforces the at-most-once-per-run rule, drops any question lacking a non-empty prompt or with fewer than two options, emits an `"options"` `AgentEvent`, and awaits the callback; never raises — degenerate input (no callback wired, already used, no well-formed questions) returns a note telling the agent to proceed with its own best judgement
- `_accumulate_tokens(response, totals, on_event)` — reads `response.usage.prompt_tokens` / `completion_tokens` after each LLM call (both organize and query loops), adds them to the run's running total, and emits a `"tokens"` `AgentEvent` whose text is the compact rendering from `_fmt_tokens` (e.g. `"42.3K in / 5.1K out"`) and whose `data` carries the raw `{in, out}` totals; a no-op when the endpoint's response omits `usage`
- `_fmt_tokens(n)` — compact human-readable token count: `512`, `12K`, `12.3K`, `3.5M`

**Turn limit:** `_MAX_TURNS = 50` — both loops raise an error event if the model has not produced a final (no-tool-call) response within 50 turns.

---

### `host/app.py`

**Role:** Textual TUI — six screens/modals.

| Class | Role |
|---|---|
| `OrganizerApp` | Root `App`; calls `is_configured()` on mount and routes to `SetupScreen` (first run) or `StartupScreen` (returning user) |
| `SetupScreen` | First-run wizard: welcome → AI service choice → URL + API key → document profile → done. Saves via `save_user_config()` / OS keyring. Transitions to `StartupScreen` when complete |
| `ConfigScreen` | Settings panel accessible at any time from `StartupScreen`. Fields: URL, API key (password input), document profile (Select), approval mode (Select with friendly labels). Saves back to `~/.telcontar/config.env` via `save_user_config()` |
| `StartupScreen` | Lets the user browse and pick the target folder via a `DirectoryTree` (`#target-tree`, rooted at `Path.home()`); the selected path (defaults to home) is shown in a "Selected: …" label and used by "Organize" and "Query". Offers "Organize", "Query", and "⚙ Settings" buttons. Keybinding `s` opens `ConfigScreen`. "Query" validates that `settings.registry_path` exists before proceeding |
| `OrganizerScreen` | Main view. Opens on a **starter pane** (`#starter-pane`, L3) instead of auto-starting the agent: a `Static` rendering `_directory_overview(target)` — a code-generated, deterministic scan of names/structure only (file count, subfolder count, most common extensions; no content read, no LLM) — plus an `#instructions-input` `Input` for optional free-text steering instructions and a `#proceed-btn` "Start organizing" button (or `Input.Submitted`). `_start_organizing()` hides the starter pane, shows `#main-split` (file-tree sidebar + a single chat-transcript `#conversation-pane`, `VerticalScroll`), and launches `_agent_worker(instructions)` as a Textual worker, passing the typed instructions (if any) through to `run_agent(..., instructions=...)`. `_add_turn(speaker, text)` appends speaker-differentiated turns (`telcontar` / `you`) as styled `Static` widgets — the target line and any typed instructions are shown as the first turns; on each `tool_call` event, `_narrate(tool)` looks up the tool in the module-level `_TOOL_NARRATION` map and, if the macro-task phrase changed, emits a `telcontar` turn (e.g. "Reading documents…", "Planning changes…", "Applying the plan…") — deduping so consecutive calls in the same macro-task collapse to one turn. The raw tool calls/results themselves are appended via `_append_step(line)` into a click-to-expand `Collapsible` ("internal steps") interleaved in the transcript; a new speaker turn closes the currently-open group so the next tool call opens a fresh one. Below `#main-split`, a docked `#ops-journal` `RichLog` (L4, `wrap=False`, horizontally scrollable) renders the file operations recorded in the undo journal — one line per entry, newest last, via `_fmt_journal_entry` (the same formatter `JournalScreen` uses); multi-line hard-stop entries collapse to their summary line. `_refresh_ops_journal()` re-reads `.organizer/journal.jsonl` via `_resolve_journal_path` + `server.journal.all_entries` (swallowing read/config errors so the strip just shows nothing rather than breaking the screen); it runs on mount, after any tool in `_JOURNAL_WRITING_TOOLS` (now just `{"execute_plan"}` — the only tool left that can mutate the journal via the agent path, per M1) completes, and again on `done`. Status bar shows the current phase plus a running token-usage total (`N in / M out`) once the LLM reports it; keybinding `g` pushes `QueryScreen` once organizing completes, `j` pushes `JournalScreen` (the full modal journal view) |
| `QueryScreen` | Chat-style read-only Q&A screen: `RichLog` output + `Input` bar; keeps one MCP session open for the whole chat and threads conversation history across questions; status bar also shows a running token-usage total (`N in / M out`); `Esc` pops back to the previous screen |
| `JournalScreen` | Modal view of the full undo journal (newest entries last), opened via `j`. Also the **only place `undo_last` can be triggered** (M1, S1): keybinding `u` calls `server.tools.undo_last` directly — bypassing MCP entirely, same pattern already used to read the journal — and shows a success/error status line; `Esc` or `j` closes it |
| `ApprovalModal` | Plan review: renders the plan's `rationale` (if set via `set_plan_rationale`) as `#plan-rationale`, then — if the plan has any `move`/`quarantine` destinations — a "Target layout" tree (`_render_target_layout`, L5) built from the plan's op destinations with each folder's `folder_notes` purpose note beside it (bare nodes for folders with no note; rename-only plans show no tree), then per-op checkboxes, the `ops_json_path` (if present) shown as a `#ops-json-path` label, a free-text `#refine-input` `Input` for natural-language plan editing (L6), and Approve/Refine/Reject buttons; Approve dismisses with `ApprovalResult(approved=True, removed_op_ids=...)`, Refine (button or `Input.Submitted`) dismisses with `ApprovalResult(approved=False, refinement=<text>)` unless the field is blank (no-op, modal stays open), Reject/Escape dismiss with `ApprovalResult(approved=False)` |
| `ClarificationModal` | Post-analysis clarifying questions: one free-text `Input` per question, "Submit answers" / "Skip — best judgement" buttons; returns a `ClarificationResult`. Shown at most once per run, wired via `OrganizerScreen`'s `on_questions_needed` callback |
| `OptionsModal` | Multiple-option checkpoint (L7): one `RadioSet` per question, each with 2-5 `RadioButton` options (first pre-selected), "Submit choices" / "Skip — best judgement" buttons; returns an `OptionsResult` mapping each question to its chosen option. Shown at most once per run, wired via `OrganizerScreen`'s `on_options_needed` callback |

**TUI layout (OrganizerScreen):**

```
┌─ Header ───────────────────────────────────────────────┐
│ DirectoryTree (22%)  │  #conversation-pane (1fr)       │
│                      │  telcontar/you turns, with      │
│                      │  collapsed "internal steps"     │
│                      │  groups interleaved             │
├─ #ops-journal (RichLog, height 5, h-scroll) ────────────┤
│ 12:03:04  rename      draft.docx  →  Report_2024.docx  │
│ 12:03:05  move        Report_2024.docx  →  Reports/…   │
├─ Status bar ───────────────────────────────────────────┤
│ Footer  [q] Quit  [g] Query corpus  [j] Journal         │
└────────────────────────────────────────────────────────┘
```

**TUI layout (QueryScreen):**

```
┌─ Header ───────────────────────────────────────────────┐
│ RichLog (1fr)                                          │
│ (answer stream + tool call log)                        │
├─ Status bar ───────────────────────────────────────────┤
│ Input  "Ask a question about this corpus…"             │
│ Footer  [Esc] Back  [Ctrl+C] Quit                      │
└────────────────────────────────────────────────────────┘
```

**Worker pattern:** `OrganizerScreen.on_mount` only shows the starter pane and focuses `#instructions-input`; `_start_organizing()` (triggered by `#proceed-btn` or `Input.Submitted` on the instructions field) launches `_agent_worker` as a Textual worker. The worker is `async`, so it can `await` the approval modal via `app.push_screen_wait(ApprovalModal(...))`, and — when the agent calls `ask_clarification` or `propose_options` — the clarification modal via `app.push_screen_wait(ClarificationModal(questions))` or the options modal via `app.push_screen_wait(OptionsModal(questions))`. `QueryScreen` uses a `asyncio.Queue` to bridge the synchronous `Input.Submitted` event handler into the async `_query_worker` that drives `run_query_loop`.

---

### `host/llm.py` (~18 lines)

**Role:** Factory function for the OpenAI-compatible client.

**Key function:** `make_client(settings) -> AsyncOpenAI` — creates an `AsyncOpenAI` instance pointed at `settings.llm_base_url`. For Azure, it also injects `default_query={"api-version": ...}` so the Azure API version parameter is sent on every request.

**Design note:** No Mammouth-specific code is needed — Mammouth is OpenAI-compatible and only requires the `base_url` and `api_key` overrides.
