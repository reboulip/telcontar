# Module Reference

Detailed breakdown of every Python module in the codebase. For auto-generated API docs (docstrings, signatures), see the [API Reference](../reference/api/server.md).

---

## `config/`

### `config/settings.py` (~44 lines)

**Role:** Single source of truth for all runtime configuration. Loads from `.env` (or environment variables) via Pydantic Settings.

**Key class:** `Settings` — a `BaseSettings` subclass with fields for LLM endpoint, safety, domain profile, document memory, and egress settings.

**Key function:** `load() -> Settings` — instantiates and validates settings; called once per process.

**Why it's structured this way:** Both the host and the server import this module independently (they run in different processes). There is no shared singleton across the stdio boundary.

---

## `server/`

The MCP server package. Launched as a subprocess by the host; communicates via stdio. Owns all file I/O, guardrails, and persistent state.

### `server/main.py` (~270 lines)

**Role:** MCP server entrypoint. Registers all tools with FastMCP and wires each handler to `server/tools.py`. Lazy-initialises `Settings` and the active `Profile` on first use.

**Key object:** `mcp = FastMCP("directory-organizer")` — the FastMCP server instance.

**Entrypoint:** `main()` calls `mcp.run(transport="stdio")`.

**Design note:** This module is deliberately thin — it delegates all logic to `server/tools.py`. Tool parameters injected from config (e.g. `plans_dir`, `journal_path`) are resolved here and passed into the tool functions.

---

### `server/tools.py` (~634 lines)

**Role:** All MCP tool implementations. Pure functions with no global state — they receive everything they need as arguments, making them directly testable without spawning an MCP server.

**Groups of functions:**

| Group | Functions |
|---|---|
| Read-only | `list_dir`, `read_file`, `extract_text`, `compute_checksum` |
| Direct file ops | `move_file`, `rename_file`, `create_file`, `update_file` |
| Plan management | `create_plan`, `get_plan`, `list_plans`, `review_plan`, `approve_plan` |
| Plan-building | `propose_rename`, `propose_move`, `propose_quarantine` |
| Gated execution | `execute_plan`, `write_index`, `write_summary` |
| Recovery | `undo_last` |
| Registry | `record_document`, `get_document`, `list_documents`, `get_registry`, `find_duplicates`, `find_modified_documents` |
| Event journal | `create_event`, `list_events` |
| Knowledge graph | `build_graph`, `get_graph`, `get_actors` |
| Archive | `archive_document`, `list_archived` |

**Internal helpers:** `_apply_op` executes a single `PlanOp` against the filesystem; `_reconcile_op` updates the registry record's path/status after execution.

---

### `server/plan.py` (~138 lines)

**Role:** Plan data model and disk persistence. Defines the state machine, serialization, and plan/op CRUD.

**Key types:**
- `PlanState` — `Literal["pending", "approved", "executing", "done", "failed", "stopped"]`
- `OpType` — `Literal["rename", "move", "quarantine"]`
- `PlanOp` — dataclass with `op_id` (UUID), `op_type`, `src`, `dst`, `status`, `error`, `retries`
- `Plan` — dataclass with `plan_id`, `state`, `ops: list[PlanOp]`, timestamps

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

### `server/extract.py` (~18 lines)

**Role:** Thin wrapper around markitdown for text extraction from binary formats.

**Key function:** `extract(path, max_chars) -> str` — calls `MarkItDown().convert(path)`, returns `result.text_content` truncated to `max_chars`.

**Single module-level instance:** `_md = MarkItDown()` — markitdown is initialized once per server process.

---

## `host/`

The MCP host package. Drives the GPT-5 agent loop and presents the Textual TUI.

### `host/main.py` (~9 lines)

**Role:** CLI entrypoint. Instantiates `OrganizerApp` and calls `.run()`.

**Entry point:** `main()` is registered as the `organizer-host` script in `pyproject.toml`.

---

### `host/agent.py` (~397 lines)

**Role:** The async agent loop. Fully decoupled from Textual — callers supply callbacks for events and approval so the module can be tested without a TUI.

**Key types:**
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str]}`
- `EventCallback` — `Callable[[AgentEvent], None]`
- `ApprovalCallback` — `Callable[[str, dict], Awaitable[ApprovalResult]]`

**Key functions:**
- `run_agent(target, settings, llm, on_event, on_approval_needed)` — top-level entry; launches the MCP server subprocess via `mcp_session()`, then calls `run_agent_loop`
- `run_agent_loop(target, settings, llm, session, ...)` — the actual GPT-5 tool-calling loop (injectable session for testing)
- `_build_system_prompt(project_root, settings)` — assembles the system prompt from the active profile (document types, role taxonomy, naming instructions, extraction rules)
- `_handle_execute_plan(...)` — intercepts `execute_plan` calls to insert the approval gate before forwarding to the server

**Turn limit:** `_MAX_TURNS = 50` — the loop raises an error event if the agent has not produced a final (no-tool-call) response within 50 turns.

---

### `host/app.py` (~346 lines)

**Role:** Textual TUI — three screens and one modal.

| Class | Role |
|---|---|
| `OrganizerApp` | Root `App`; pushes `StartupScreen` on mount |
| `StartupScreen` | Collects the target directory path from the user |
| `OrganizerScreen` | Main view: file-tree sidebar + scrollable agent log; runs the agent in a Textual worker |
| `ApprovalModal` | Plan review: per-op checkboxes, Approve/Reject buttons; returns an `ApprovalResult` |

**TUI layout (OrganizerScreen):**

```
┌─ Header ───────────────────────────────────────────────┐
│ DirectoryTree (28%)  │  RichLog (72%)                  │
│                      │  (agent event stream)           │
├─ Status bar ───────────────────────────────────────────┤
│ Footer (key bindings)                                  │
└────────────────────────────────────────────────────────┘
```

**Worker pattern:** `OrganizerScreen.on_mount` launches `_agent_worker` as a Textual worker. The worker is `async`, so it can `await` the approval modal via `app.push_screen_wait(ApprovalModal(...))`.

---

### `host/llm.py` (~18 lines)

**Role:** Factory function for the OpenAI-compatible client.

**Key function:** `make_client(settings) -> AsyncOpenAI` — creates an `AsyncOpenAI` instance pointed at `settings.llm_base_url`. For Azure, it also injects `default_query={"api-version": ...}` so the Azure API version parameter is sent on every request.

**Design note:** No Mammouth-specific code is needed — Mammouth is OpenAI-compatible and only requires the `base_url` and `api_key` overrides.
