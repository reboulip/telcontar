# Module Reference

Detailed breakdown of every Python module in the codebase. For auto-generated API docs (docstrings, signatures), see the [API Reference](../reference/api/server.md).

---

## `config/`

### `config/settings.py`

**Role:** Single source of truth for all runtime configuration. Loads from `.env` (project-local, highest priority) then `~/.telcontar/config.env` (user-level fallback for installed-tool use) via Pydantic Settings; real environment variables override both.

**Key class:** `Settings` — a `BaseSettings` subclass with fields for LLM endpoint, safety, domain profile, document memory, and egress settings. `llm_base_url` and `llm_api_key` default to `""` so `Settings()` can be instantiated before the wizard runs. `target_dir: Path | None = None` holds the directory being organized this run — populated from a `TARGET_DIR` env var set by the host (`mcp_session`) when it launches the server subprocess; `None` outside a real run (e.g. some test harnesses), in which case path-confinement guards fall back to just the server's own working directory (M2). `effective_allowlist_dirs() -> list[Path]` (M7) returns `allowlist_dirs` unchanged if it's non-empty — an explicit operator config always wins outright, never merged with `target_dir` — otherwise defaults to `[target_dir]` if set, else `[]`; `server/main.py`'s `read_file`, `extract_text`, `compare_documents`, and the batch forms `read_file_batch`/`extract_text_batch` (O1) call this instead of the raw `allowlist_dirs` field.

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

**Design note:** This module is deliberately thin — it delegates all logic to `server/tools.py`. Tool parameters injected from config (e.g. `plans_dir`, `journal_path`) are resolved here and passed into the tool functions. `_confinement_roots(cfg)` and `_check_within_root(path, cfg)` (M2) wrap `server/guards.py`'s `check_within_root` and are called at the top of every path-taking tool handler to confine it to `[cfg.target_dir, Path.cwd()]`. The batch tools (O1) apply this — plus `check_allowlist` for the two content tools — per path, before delegating to `server/tools.py`, so a rejected path becomes that entry's `{"error": ...}` instead of aborting the whole call.

---

### `server/tools.py`

**Role:** All MCP tool implementations. Pure functions with no global state — they receive everything they need as arguments, making them directly testable without spawning an MCP server.

**Groups of functions:**

| Group | Functions |
|---|---|
| Read-only | `list_dir`, `walk_tree`, `read_file`, `extract_text`, `compute_checksum`, `read_file_batch`, `extract_text_batch`, `compute_checksum_batch` |
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

**Design note (O1):** `read_file_batch`/`extract_text_batch`/`compute_checksum_batch` are the batch counterparts of `read_file`/`extract_text`/`compute_checksum` — each loops over its `paths` list, calling the singular function per path and catching any exception into that path's `{"error": str(exc)}` entry rather than letting one bad path abort the whole batch. `server/main.py`'s wrappers apply the same guard sequence per path *before* delegating here, so a guard rejection (allowlist/confinement) also surfaces as a per-path error rather than raising.

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

### `server/guards.py`

**Role:** Guardrail functions enforced before any file operation.

| Function | What it guards |
|---|---|
| `check_no_overwrite(dest)` | Raises `FileExistsError` if `dest` already exists |
| `safe_quarantine_path(src, quarantine_dir)` | Returns a collision-safe path in quarantine (suffixes `_1`, `_2`, …) |
| `check_allowlist(path, allowlist_dirs)` | Raises `PermissionError` if `path` is not under any allowlisted directory. Empty `allowlist_dirs` = no restriction (opt-in). |
| `check_within_root(path, roots)` | Raises `PermissionError` if `path` does not resolve inside any of `roots` (M2). Fail-closed — an empty `roots` list raises rather than allowing everything, the opposite default from `check_allowlist`. Called by `server/main.py`'s `_check_within_root` on every path-taking tool handler, with `roots = [target_dir, Path.cwd()]`. |

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

### `server/egress.py`

**Role:** Append-only JSONL audit trail (S8/M12) of document content sent to the LLM endpoint — distinct from the undo journal, event journal, and archive log.

**Key type:** `EgressEntry` — dataclass with `{path, size_bytes, tool, timestamp}`.

| Function | Description |
|---|---|
| `append(egress_path, entry)` | Appends one egress entry as a JSONL line; creates parent dirs |
| `all_entries(egress_path)` | Returns all entries in chronological order; empty list if no file |

**Design note:** Logged from `server/main.py`'s `read_file`/`extract_text`/`compare_documents` handlers via `_log_egress`/`_log_egress_from_disk`, after a successful call. Not exposed as an MCP tool — it's an audit trail of the agent's own information exposure, meant for the operator, not the agent.

---

### `server/sinks.py` (~76 lines)

**Role:** Output-sink abstraction — defines where the engine's synthesized Markdown artifacts are emitted.

**Key types:**
- `Sink` — `runtime_checkable` Protocol with attributes `name: str`, `external: bool` and methods `write_summary(target_dir, content) -> dict`, `write_folder_readme(folder, content) -> dict`.
- `LocalMarkdownSink` — the built-in sink (`name="local_markdown"`, `external=False`). Delegates to `tools.write_summary` and `tools.write_folder_readme`; writes files to the local filesystem.

**Key function:** `resolve_sinks(names, *, allow_external) -> list[Sink]` — instantiates the sinks named in the profile's `[sinks] default` list. Built-in sinks are created directly. Any unrecognised name is treated as an external sink: raises `PermissionError` if `allow_external=False`, or `NotImplementedError` if `True` (external sinks are separate MCP integrations, not built into this codebase).

**Design note:** `server/main.py` calls `resolve_sinks` inside `write_summary` and `write_folder_readme` handlers, passing `egress_allow_external_sinks` from `Settings`. A single-sink result is unwrapped; multiple sinks return `{"sinks": [...]}`.

---

### `server/extract.py`

**Role:** Bounded wrapper around markitdown (and, for `.msg`, `extract-msg`) for text extraction from binary formats — S5 hardening: a crash/DoS/zip-bomb guard, not a sandbox.

**Key function:** `extract(path, max_chars, max_file_bytes=200_000_000, timeout_secs=30.0) -> str` — rejects the input with `ValueError` if it exceeds `max_file_bytes`, runs `_check_not_a_zip_bomb` for zip-based formats (`.docx`/`.xlsx`/`.pptx`/`.zip`), then inside a `ThreadPoolExecutor` bounded by `timeout_secs` (raises `TimeoutError` on expiry) either calls `_extract_msg(path)` for a `.msg` suffix or `MarkItDown().convert(path)` otherwise, and returns the resulting text truncated to `max_chars`.

**Key helper:** `_check_not_a_zip_bomb(path)` — for zip-based suffixes, opens the archive and raises `ValueError` if any entry's uncompressed:compressed ratio exceeds 100x while its uncompressed size is at least 10MB; an invalid zip despite the extension is let through silently so markitdown's own parser reports the real error. `.msg` files are OLE compound documents, not zip containers, so this check does not apply to them.

**Key helper:** `_extract_msg(path)` — parses an Outlook `.msg` file via `extract_msg.openMsg`, returning `From`/`To`/`Cc`/`Bcc` (when present)/`Date`/`Subject` headers followed by a blank line and the message body, instead of markitdown's lossy conversion.

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
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step; `EventKind` includes `"question"` for the post-analysis clarification checkpoint, `"options"` for the multiple-option checkpoint (L7), `"progress"` for the O5 document-analysis progress tracker (`data={"analyzed": int, "total": int}`; drives the O6 `OrganizerScreen` progress bar), `"cost_estimate"` for the pre-ANALYZE cost-approval gate (O8), and `"tokens"` for running LLM token-usage updates, alongside `"thinking"`, `"tool_call"`, `"tool_result"`, `"plan_ready"`, `"done"`, `"error"`. `"tool_call"` events carry `data={"tool": name}` in both the organize and query loops, so callers can key off the tool name (e.g. `OrganizerScreen._narrate`, F10) without parsing `text`
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str], refinement: str | None}`. `refinement` (L6) carries free-text plan-editing feedback from the `ApprovalModal`'s Refine button; when set, the plan is not executed even though `approved` is `False` — see `_handle_execute_plan` below
- `ClarificationResult` — `{answers: dict[str, str], provided: bool}`; answers from the post-analysis clarification checkpoint. `provided` is `False` when the user skipped / had nothing to add, in which case the agent proceeds with its own best judgement
- `OptionsResult` — `{selections: dict[str, str], provided: bool}`; the user's picks from the multiple-option checkpoint (L7), one selected option per question. `provided` is `False` when the user skipped, in which case the agent proceeds with its own best judgement
- `CostApprovalResult` — `{approved: bool}`; the user's yes/no on the pre-ANALYZE cost-estimate gate (O8)
- `EventCallback` — `Callable[[AgentEvent], None]`
- `ApprovalCallback` — `Callable[[str, dict], Awaitable[ApprovalResult]]`
- `QuestionsCallback` — `Callable[[list[str]], Awaitable[ClarificationResult]]`
- `OptionsCallback` — `Callable[[list[dict]], Awaitable[OptionsResult]]`; each item is `{"question": str, "options": [str, ...]}`
- `CostApprovalCallback` — `Callable[[str, dict], Awaitable[CostApprovalResult]]`; given the summary text plus `{"documents": int, "estimated_tokens": int}`

**Key constants:**
- `QUERY_ALLOWED_TOOLS` — `frozenset` of read-only tool names exposed to the model in query mode (list/read/inspect tools; no plan, execute, write, build_graph, create_event, or archive tools)
- `_CLARIFY_TOOL_NAME` / `_CLARIFY_TOOL_SPEC` — the host-side synthetic tool `ask_clarification`. Never registered with or forwarded to the MCP server; appended to the OpenAI tool list only when a `QuestionsCallback` is wired in
- `_OPTIONS_TOOL_NAME` / `_OPTIONS_TOOL_SPEC` — the host-side synthetic tool `propose_options` (L7), mirroring `ask_clarification`. Never registered with or forwarded to the MCP server; appended to the OpenAI tool list only when an `OptionsCallback` is wired in. Each question requires 2-5 mutually-exclusive options
- `_COST_GATED_BATCH_TOOLS` — `frozenset` naming the four O1/O2 batch document tools (`extract_text_batch`, `read_file_batch`, `compute_checksum_batch`, `record_document_batch`) whose first call in a run triggers the O8 cost-approval gate; their singular counterparts are not gated

**Key functions:**
- `run_agent(target, settings, llm, on_event, on_approval_needed, on_questions_needed=None, on_options_needed=None, on_cost_approval_needed=None, instructions=None, history=None, message=None) -> tuple[str, list[dict]]` — top-level organize entry; launches the MCP server subprocess via `mcp_session()`, then calls `run_agent_loop`, returning `(final_text, updated_history)`. `mcp_session(project_root, target=None)` sets `TARGET_DIR` on the server subprocess's env whenever `target` is given, so the server can confine path-taking tools to it (M2). `history`/`message` (O7) mirror `run_query_loop`'s shape — see `run_agent_loop` below; for a multi-turn chat, callers should instead keep a single session open and call `run_agent_loop` directly (`run_agent` launches a fresh subprocess per call)
- `run_agent_loop(target, settings, llm, session, on_event, on_approval_needed, on_questions_needed=None, on_options_needed=None, on_cost_approval_needed=None, project_root=None, instructions=None, history=None, message=None) -> tuple[str, list[dict]]` — the actual GPT-5 tool-calling loop for organize mode (injectable session for testing). `instructions` carries the user's optional pre-analysis steering text from the `OrganizerScreen` starter pane (L3); when non-empty it is appended to the seed user message ("Please organize the directory: …") before the loop starts. `on_options_needed` wires the L7 multiple-option checkpoint; `on_cost_approval_needed` wires the O8 pre-ANALYZE cost-approval gate. **Resumable chat (O7):** when `history` is `None` (default), a fresh run is seeded from `target`/`instructions` as before; when `history` is given (the list returned by a previous call), it is reused as-is and `message` — a new free-text user turn — is appended before resuming, so a run that finished, errored, or hit the turn ceiling can be continued with the same mutating toolset. A continuation gets its own fresh per-call turn budget and a fresh, empty `_ProgressTracker` (no new `walk_tree` survey happens), so its adaptive budget floors at `_MAX_TURNS` rather than reflecting the initial pass's corpus size. The whole turn loop is wrapped in `try`/`except`: an unhandled exception is caught rather than propagating — any tool call left without a matching tool-result message is answered with a synthesized `{"error": ...}` entry (so `messages` stays valid for a follow-up call), an `"error"` event fires, and `(error_text, messages)` is returned
- `run_query(question, settings, llm, on_event, history, target=None)` — convenience entry for one query, launching its own MCP session; `target` (the analyzed corpus's directory) is passed through to `mcp_session` so the server confines its read-only tools' path arguments (M2)
- `run_query_loop(question, settings, llm, session, on_event, history, project_root)` — read-only tool-calling loop; threads `history` across calls for multi-turn context; returns `(answer, updated_history)`
- `_discover_openai_tools(session, allowed)` — lists MCP tools and converts to OpenAI function specs; when `allowed` is given, only tools in the set are exposed (used by query mode)
- `_build_system_prompt(project_root, settings)` — assembles the organize-mode system prompt from the active profile, including an optional clarification-checkpoint note and an optional multiple-option-checkpoint note between the ANALYZE and ORGANIZE sections
- `_build_query_system_prompt(project_root, settings)` — assembles the read-only query-mode system prompt from the active profile
- `_handle_execute_plan(...)` — intercepts `execute_plan` calls to insert the approval gate before forwarding to the server. Fetches the plan via `get_plan`, writes its full ops (plan id, rationale, folder notes, ops) to `.organizer/plan_ops.json` via `_write_ops_json` and attaches the path as `ops_json_path` on the event data, then awaits `on_approval_needed`. If the returned `ApprovalResult.refinement` is set (non-blank), the plan is NOT approved or executed — the tool result instead carries the refinement text back to the agent as a note instructing it to revise the plan (ops/rationale/folder notes) and call `execute_plan` again. Otherwise falls back to the plain approved/rejected path
- `_write_ops_json(plan_data, plans_dir)` — writes `{plan_id, rationale, folder_notes, ops}` to `<plans_dir>/../plan_ops.json` (i.e. `.organizer/plan_ops.json`), latest-plan-wins; returns the path, or `None` on an `OSError`
- `_handle_clarification(args, on_event, on_questions_needed, already_used)` — intercepts calls to the host-side `ask_clarification` tool; enforces the at-most-once-per-run rule and the "nothing to add → proceed" path; emits a `"question"` `AgentEvent` and awaits the callback; never raises — degenerate input (no callback wired, already used, no questions) returns a note telling the agent to proceed with its own best judgement
- `_handle_options(args, on_event, on_options_needed, already_used)` — intercepts calls to the host-side `propose_options` tool (L7); mirrors `_handle_clarification`: enforces the at-most-once-per-run rule, drops any question lacking a non-empty prompt or with fewer than two options, emits an `"options"` `AgentEvent`, and awaits the callback; never raises — degenerate input (no callback wired, already used, no well-formed questions) returns a note telling the agent to proceed with its own best judgement
- `_handle_cost_approval(tracker, settings, on_event, on_cost_approval_needed)` — intercepts the first call to any `_COST_GATED_BATCH_TOOLS` tool (O8); computes `tracker.cost_estimate(settings.max_snippet_chars)`, emits a `"cost_estimate"` `AgentEvent`, and — unless `approval_mode == "never"` or no callback is wired — awaits `on_cost_approval_needed`. Returns `(error_or_None, shown=True)`; rejecting yields an error dict telling the agent to stop and report back instead of the tool result
- `_accumulate_tokens(response, totals, on_event)` — reads `response.usage.prompt_tokens` / `completion_tokens` after each LLM call (both organize and query loops), adds them to the run's running total, and emits a `"tokens"` `AgentEvent` whose text is the compact rendering from `_fmt_tokens` (e.g. `"42.3K in / 5.1K out"`) and whose `data` carries the raw `{in, out}` totals; a no-op when the endpoint's response omits `usage`
- `_fmt_tokens(n)` — compact human-readable token count: `512`, `12K`, `12.3K`, `3.5M`

**Turn limit:** `run_query_loop` raises an error event if the model has not produced a final (no-tool-call) response within `_MAX_TURNS = 50` turns. `run_agent_loop` (organize mode) instead uses an adaptive budget, `_analysis_turn_budget(total_discovered)` — `max(_MAX_TURNS, min(_MAX_TURN_BUDGET, _TURN_BUDGET_BASE + _TURN_BUDGET_PER_DOCUMENT * total_discovered))`, i.e. floor 50, ceiling `_MAX_TURN_BUDGET = 2000`, `_TURN_BUDGET_BASE = 30` plus `_TURN_BUDGET_PER_DOCUMENT = 3` turns per document discovered so far — recomputed each iteration as the O5 progress tracker's discovered count grows. It's a backstop against a runaway/looping agent, not the primary cost control — that's the O8 pre-ANALYZE cost-approval gate (`_handle_cost_approval`, above), which gates the first real batch-tool call of the run.

---

### `host/app.py`

**Role:** Textual TUI — six screens/modals.

| Class | Role |
|---|---|
| `OrganizerApp` | Root `App`; calls `is_configured()` on mount and routes to `SetupScreen` (first run) or `StartupScreen` (returning user) |
| `SetupScreen` | First-run wizard: welcome → AI service choice → URL + API key → document profile → done. Saves via `save_user_config()` / OS keyring. Transitions to `StartupScreen` when complete |
| `ConfigScreen` | Settings panel accessible at any time from `StartupScreen`. Fields: URL, API key (password input), document profile (Select), approval mode (Select with friendly labels). Saves back to `~/.telcontar/config.env` via `save_user_config()` |
| `StartupScreen` | Lets the user browse and pick the target folder via a `DirectoryTree` (`#target-tree`, rooted at `Path.home()`); the selected path (defaults to home) is shown in a "Selected: …" label and used by "Organize" and "Query". Offers "Organize", "Query", and "⚙ Settings" buttons. Keybinding `s` opens `ConfigScreen`. "Query" validates that `settings.registry_path` exists before proceeding |
| `OrganizerScreen` | Main view. Opens on a **starter pane** (`#starter-pane`, L3) instead of auto-starting the agent: a `Static` rendering `_directory_overview(target)` — a code-generated, deterministic scan of names/structure only (file count, subfolder count, most common extensions; no content read, no LLM) — plus an `#instructions-input` `Input` for optional free-text steering instructions and a `#proceed-btn` "Start organizing" button (or `Input.Submitted`). `_start_organizing()` hides the starter pane, shows `#main-split` (file-tree sidebar + a single chat-transcript `#conversation-pane`, `VerticalScroll`), and launches `_agent_worker(instructions)` as a Textual worker, passing the typed instructions (if any) through to `run_agent_loop(..., instructions=...)`. `_add_turn(speaker, text)` appends speaker-differentiated turns (`telcontar` / `you`) as styled `Static` widgets — the target line and any typed instructions are shown as the first turns; on each `tool_call` event, `_narrate(tool)` looks up the tool in the module-level `_TOOL_NARRATION` map and, if the macro-task phrase changed, emits a `telcontar` turn (e.g. "Reading documents…", "Planning changes…", "Applying the plan…") — deduping so consecutive calls in the same macro-task collapse to one turn. The raw tool calls/results themselves are appended via `_append_step(line)` into a click-to-expand `Collapsible` ("internal steps") interleaved in the transcript; a new speaker turn closes the currently-open group so the next tool call opens a fresh one. Below `#main-split`, a docked `#ops-journal` `RichLog` (L4, `wrap=False`, horizontally scrollable) renders the file operations recorded in the undo journal — one line per entry, newest last, via `_fmt_journal_entry` (the same formatter `JournalScreen` uses); multi-line hard-stop entries collapse to their summary line. `_refresh_ops_journal()` re-reads `.organizer/journal.jsonl` via `_resolve_journal_path` + `server.journal.all_entries` (swallowing read/config errors so the strip just shows nothing rather than breaking the screen); it runs on mount, after any tool in `_JOURNAL_WRITING_TOOLS` (now just `{"execute_plan"}` — the only tool left that can mutate the journal via the agent path, per M1) completes, and again on `done`. Below that, a `#progress-row` (O6, a `#progress-label` plus a Textual `ProgressBar`) renders the O5 `"progress"` event: `_update_progress(data)` reveals the row and updates both widgets, but only once a `total > 0` has been seen (an unknown/`None` total is never shown, avoiding Textual's indeterminate spinner); `_hide_progress()` re-hides it — without first snapping to 100% — on both `"done"` and `"error"`, so it disappears once the ANALYZE phase finishes rather than lingering through ORGANIZE. Status bar shows the current phase plus a running token-usage total (`N in / M out`) once the LLM reports it; keybinding `g` pushes `QueryScreen` once organizing completes, `j` pushes `JournalScreen` (the full modal journal view). **Resumable chat (O7):** `_agent_worker` no longer calls the one-shot `run_agent` convenience wrapper — it opens `mcp_session(...)` itself and calls `run_agent_loop(...)` directly, keeping one MCP session (and one subprocess) open across the initial run and every subsequent chat turn. `self._history: list[dict] | None` carries the conversation returned by each call into the next; `self._messages: asyncio.Queue[str]` bridges the synchronous `Input.Submitted` handler on the bottom-docked `#organize-input` (disabled by default, enabled once the run reaches its first terminal state, disabled again while a turn is running) into the worker's `while True` loop, which awaits the queue and calls `run_agent_loop(..., history=self._history, message=message)` again for each submitted message. Submitting echoes the message as a `user`-speaker turn via `_add_turn` before it is queued. `_note_terminal_state()` fires the "press g / keep chatting" cue and the desktop notification only on the *first* `"done"`/`"error"` event (tracked via `self._done`), not on every subsequent chat-turn completion |
| `QueryScreen` | Chat-style read-only Q&A screen: `RichLog` output + `Input` bar; keeps one MCP session open for the whole chat and threads conversation history across questions; status bar also shows a running token-usage total (`N in / M out`); `Esc` pops back to the previous screen |
| `JournalScreen` | Modal view of the full undo journal (newest entries last), opened via `j`. Also the **only place `undo_last` can be triggered** (M1, S1): keybinding `u` calls `server.tools.undo_last` directly — bypassing MCP entirely, same pattern already used to read the journal — and shows a success/error status line; `Esc` or `j` closes it |
| `ApprovalModal` | Plan review: renders the plan's `rationale` (if set via `set_plan_rationale`) as `#plan-rationale`, then — if the plan has any `move`/`quarantine` destinations — a "Target layout" tree (`_render_target_layout`, L5) built from the plan's op destinations with each folder's `folder_notes` purpose note beside it (bare nodes for folders with no note; rename-only plans show no tree), then per-op checkboxes, the `ops_json_path` (if present) shown as a `#ops-json-path` label, a free-text `#refine-input` `Input` for natural-language plan editing (L6), and Approve/Refine/Reject buttons; Approve dismisses with `ApprovalResult(approved=True, removed_op_ids=...)`, Refine (button or `Input.Submitted`) dismisses with `ApprovalResult(approved=False, refinement=<text>)` unless the field is blank (no-op, modal stays open), Reject/Escape dismiss with `ApprovalResult(approved=False)` |
| `ClarificationModal` | Post-analysis clarifying questions: one free-text `Input` per question, "Submit answers" / "Skip — best judgement" buttons; returns a `ClarificationResult`. Shown at most once per run, wired via `OrganizerScreen`'s `on_questions_needed` callback |
| `OptionsModal` | Multiple-option checkpoint (L7): one `RadioSet` per question, each with 2-5 `RadioButton` options (first pre-selected), "Submit choices" / "Skip — best judgement" buttons; returns an `OptionsResult` mapping each question to its chosen option. Shown at most once per run, wired via `OrganizerScreen`'s `on_options_needed` callback |
| `CostEstimateModal` | Pre-ANALYZE cost-approval gate (O8): shows the estimated document count and input-token count ("~N documents, ~M input tokens estimated, batched in groups of 10 — proceed?"), Proceed/Cancel buttons (Escape = Cancel), no op list or refinement. Returns a `CostApprovalResult`. Shown at most once per run, on the first batch document-content tool call, wired via `OrganizerScreen`'s `on_cost_approval_needed` callback |

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
├─ #progress-row (hidden until first known total) ───────┤
│ 12 / 47 documents analyzed  [████████░░░░░░░░░░]       │
├─ Status bar ───────────────────────────────────────────┤
├─ #organize-input (disabled until first terminal state) ┤
│ "Once done, keep chatting to refine…"                  │
├──────────────────────────────────────────────────────────┤
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

**Worker pattern:** `OrganizerScreen.on_mount` only shows the starter pane and focuses `#instructions-input`; `_start_organizing()` (triggered by `#proceed-btn` or `Input.Submitted` on the instructions field) launches `_agent_worker` as a Textual worker. The worker is `async`, so it can `await` the approval modal via `app.push_screen_wait(ApprovalModal(...))`, and — when the agent calls `ask_clarification` or `propose_options` — the clarification modal via `app.push_screen_wait(ClarificationModal(questions))` or the options modal via `app.push_screen_wait(OptionsModal(questions))`. As of O7, `_agent_worker` opens `mcp_session(...)` itself, calls `run_agent_loop` for the initial run, then enters a `while True` loop that enables `#organize-input`, awaits `self._messages.get()` (populated by the synchronous `Input.Submitted` handler on `#organize-input`), disables the input again, and calls `run_agent_loop(..., history=self._history, message=message)` — all on the same session, so the subprocess is never restarted between turns. `QueryScreen` uses the same `asyncio.Queue` bridging pattern to feed the synchronous `Input.Submitted` event handler into the async `_query_worker` that drives `run_query_loop`.

---

### `host/llm.py` (~18 lines)

**Role:** Factory function for the OpenAI-compatible client.

**Key function:** `make_client(settings) -> AsyncOpenAI` — creates an `AsyncOpenAI` instance pointed at `settings.llm_base_url`. For Azure, it also injects `default_query={"api-version": ...}` so the Azure API version parameter is sent on every request.

**Design note:** No Mammouth-specific code is needed — Mammouth is OpenAI-compatible and only requires the `base_url` and `api_key` overrides.
