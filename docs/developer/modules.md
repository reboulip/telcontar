# Module Reference

Detailed breakdown of every Python module in the codebase. For auto-generated API docs (docstrings, signatures), see the [API Reference](../reference/api/server.md).

---

## `config/`

### `config/settings.py`

**Role:** Single source of truth for all runtime configuration. Loads from `.env` (project-local, highest priority) then `~/.telcontar/config.env` (user-level fallback for installed-tool use) via Pydantic Settings; real environment variables override both.

**Key class:** `Settings` — a `BaseSettings` subclass with fields for LLM endpoint, safety, domain profile, document memory, and egress settings. `llm_base_url` and `llm_api_key` default to `""` so `Settings()` can be instantiated before the wizard runs. `target_dir: Path | None = None` holds the directory being organized this run — populated from a `TARGET_DIR` env var set by the host (`mcp_session`) when it launches the server subprocess; `None` outside a real run (e.g. some test harnesses), in which case path-confinement guards fall back to just the server's own working directory (M2). `effective_allowlist_dirs() -> list[Path]` (M7) returns `allowlist_dirs` unchanged if it's non-empty — an explicit operator config always wins outright, never merged with `target_dir` — otherwise defaults to `[target_dir]` if set, else `[]`; `server/main.py`'s `read_file`, `extract_text`, `compare_documents`, and the batch forms `read_file_batch`/`extract_text_batch` (O1) call this instead of the raw `allowlist_dirs` field. `for_target(target: Path) -> Settings` (P2, per-directory memory) returns a copy with `quarantine_dir`/`journal_path`/`events_path`/`plans_dir`/`registry_path`/`graph_path`/`archive_path`/`egress_path` rebased onto `target.resolve()` when they're relative (an already-absolute override passes through unchanged), so a run's memory lives inside the directory being organized rather than telcontar's project root; `profiles_dir` is deliberately left untouched. `load()` calls `for_target(settings.target_dir)` itself whenever `target_dir` is set.

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

**Design note:** This module is deliberately thin — it delegates all logic to `server/tools.py`. Tool parameters injected from config (e.g. `plans_dir`, `journal_path`) are resolved here and passed into the tool functions. `_confinement_roots(cfg)` and `_check_within_root(path, cfg)` (M2) wrap `server/guards.py`'s `check_within_root` and are called at the top of every path-taking tool handler to confine it to `[cfg.target_dir, Path.cwd()]`. The batch tools (O1) apply this — plus `check_allowlist` for the two content tools — per path, before delegating to `server/tools.py`, so a rejected path becomes that entry's `{"error": ...}` instead of aborting the whole call. The `walk_tree` handler additionally passes `hidden_names={".organizer", cfg.quarantine_dir.name}` (P2) into `tools.walk_tree`, so the agent's own memory folder and the quarantine folder are excluded from every discovery result at every depth — now that both live inside `target_dir` (see `config/settings.py`'s `for_target` above). `rehome_documents` (P4) applies `_check_within_root` to every value (new path) in its `paths: dict[str, str]` argument before delegating — same per-path confinement pattern as `record_document_batch`.

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
| Registry | `record_document`, `get_document`, `lookup_documents`, `rehome_documents`, `list_documents`, `get_registry`, `find_duplicates`, `find_modified_documents` |
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

### `server/registry.py`

**Role:** The engine's persistent document memory. Content-addressed (sha256 → `DocumentRecord`). Profile-agnostic — type validation lives in `tools.py`.

**Key types:**
- `DocumentRecord` — one analyzed document. Fields: `checksum`, `path`, `title`, `type`, `summary`, `provenance`, `date`, `entities`, `attributes`, `status`, `first_seen`, `last_analyzed`.
- `Registry` — in-memory view, keyed by checksum. Methods: `upsert`, `get`, `records`, `update_path`, `rehome`, `find_duplicates`, `find_modified`.

**`update_path`:** Called by `execute_plan` after each successful op to reconcile the record's stored path with the file's new location. Matches the record whose *current* `path` equals the op's old path (an O(n) scan), then rewrites it. Normalized path comparison (`os.path.normcase`/`normpath`) for Windows compatibility.

**`rehome`** (P4): `rehome(checksum, new_path)` looks a record up directly by checksum (O(1)) and rewrites its `path` — the counterpart `update_path` can't serve, since that method matches by the file's *old* path rather than its identity. Backs the `rehome_documents` MCP tool, used by the deterministic host pre-pass (`host/agent.py`'s `run_prepass`) to reconcile records whose on-disk location no longer matches the registry, independently of any plan/`execute_plan` run. Returns `None` if no record exists for `checksum`.

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

The MCP host package. Drives the agent loop and presents the Textual TUI.

### `host/main.py`

**Role:** CLI entrypoint. Parses arguments and routes to one of the two UIs.

**Entry point:** `main()` is registered as the `telcontar` script in `pyproject.toml`.

**Flags:** `--version` (prints the installed version and exits); `--web` (`store_true` —
launches the NiceGUI web UI instead of the Textual TUI; the TUI remains the default
with no flags); `--target PATH` (only meaningful together with `--web` — skips the
landing page's directory picker and starts a run for that directory immediately;
ignored otherwise). Unrecognized args are tolerated (`parse_known_args`) so a bare
launch keeps working.

**Design note:** As of S6, each UI's dependency import is lazy and scoped to its own
branch — `from host.app import OrganizerApp` for the TUI, `from host.web.main import
run_web` for `--web` — so launching one UI never pays the other's import cost
(`textual` vs. `nicegui`).

---

### `host/tokenlog.py`

**Role:** Append-only JSONL profiling log (R2, GH #27) of per-LLM-call token usage — distinct from the egress log (which records document *content* sent, not token counts) and the undo journal.

**Key type:** `TokenLogEntry` — dataclass with `{ts, run_id, phase, step, call, model, docs, in_, cached_in, out, est_in, total_in, total_out, duration_ms}` (`duration_ms` unwired, always `None`). `.new(...)` is the constructor (stamps `ts`); `.to_dict()` renames the `in_` field to `in` at serialization time only (`in` is a Python keyword).

| Function | Description |
|---|---|
| `append(token_log_path, entry)` | Appends one token-log entry as a JSONL line; creates parent dirs |
| `all_entries(token_log_path)` | Returns all entries in chronological order; empty list if no file |

**Design note:** Written from `host/agent.py`'s `_TokenLedger.record`/`.log_estimate` after each LLM call. Not exposed as an MCP tool — it's a host-side profiling trail for token-spend optimization analysis, always on (no config flag).

---

### `host/agent.py`

**Role:** The async agent loop — both organize and query modes. Fully decoupled from Textual — callers supply callbacks for events and approval so the module can be tested without a TUI.

**Key types:**
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step; `EventKind` includes `"ask_user"` (P8) for the chat checkpoint — merges the former `"question"`/`"options"` kinds — `"progress"` for the O5 document-analysis progress tracker (`data={"analyzed": int, "total": int}`; drives the O6 `OrganizerScreen` progress bar), `"cost_estimate"` for the pre-analysis cost-approval gate (O8/P6), and `"tokens"` for running LLM token-usage updates, alongside `"thinking"`, `"tool_call"`, `"tool_result"`, `"plan_ready"`, `"done"`, `"warning"`, `"error"`. `"warning"` (U8) is non-terminal — currently emitted only when `_analyze_batch` retries once, still fails, and skips a batch: the run continues, unlike the three genuinely-terminal `"error"` emitters (the agent loop's own exception path, and the organize/query max-turns backstops). `"tool_call"` events carry `data={"tool": name}` in both the organize and query loops, so callers can key off the tool name (e.g. `OrganizerScreen._narrate`, F10) without parsing `text`
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str], refinement: str | None}`. `refinement` (L6) carries free-text plan-editing feedback from the `ApprovalModal`'s Refine button; when set, the plan is not executed even though `approved` is `False` — see `_handle_execute_plan` below
- `AskUserResult` (P8) — `{reply: str, provided: bool}`; the user's raw chat reply to an `ask_user` checkpoint call — however many questions/options were asked, the whole reply is one free-text string. `provided` is `False` when no reply was captured (degenerate/no-callback case), in which case the agent proceeds with its own best judgement. Replaces K1's `ClarificationResult` (`{answers: dict[str, str], provided: bool}`) and L7's `OptionsResult` (`{selections: dict[str, str], provided: bool}`)
- `CostApprovalResult` — `{approved: bool}`; the user's yes/no on the pre-ANALYZE cost-estimate gate (O8)
- `PrepassResult` — `{new: list[dict], known: list[dict], rehomed: list[str], errors: list[dict], total_files: int, sizes: dict[str, int]}`; the outcome of `run_prepass` (P4) — `new` is `{path, checksum}` per undiscovered document, `known` is `{path, checksum, record}` per already-registered document, `rehomed` lists checksums whose registry path was corrected, `sizes` maps `path -> size_bytes` for every discovered file (P5, additive — populated straight from `walk_tree`'s entries so the new-docs-only cost estimate below has sizes to work from without a second discovery pass)
- `EventCallback` — `Callable[[AgentEvent], None]`
- `ApprovalCallback` — `Callable[[str, dict], Awaitable[ApprovalResult]]`
- `AskUserCallback` (P8) — `Callable[[list[dict]], Awaitable[AskUserResult]]`; each item is `{"text": str, "options": [str, ...]}` (`"options"` omitted for an open question). Replaces K1's `QuestionsCallback` and L7's `OptionsCallback`
- `CostApprovalCallback` — `Callable[[str, dict], Awaitable[CostApprovalResult]]`; given the summary text plus `{"new": int, "already_analyzed": int, "estimated_tokens": int, "batch_size": int}` (P8 finishes the data-shape migration P6 deferred — the dict originally carried `{"documents": int, "estimated_tokens": int}`; `batch_size` added U5 — previously omitted from `data` entirely, so both UIs' cost dialogs fell back to a hardcoded display default of 10 regardless of `_ANALYZER_BATCH_SIZE`'s real value, and `host/app.py`'s call site didn't even forward the fallback through to `CostEstimateModal`)

**Key constants:**
- `QUERY_ALLOWED_TOOLS` — `frozenset` of read-only tool names exposed to the model in query mode (list/read/inspect tools; no plan, execute, write, build_graph, create_event, or archive tools)
- `ORGANIZE_DENIED_TOOLS` (P6) — `frozenset` of content-fetching/recording tool names EXCLUDED from the model's toolset in organize (ORGANIZE-only) mode: `read_file`, `extract_text`, `read_file_batch`, `extract_text_batch`, `compute_checksum`, `compute_checksum_batch`, `record_document`, `record_document_batch`, `compare_documents`, `lookup_documents`, `rehome_documents`. A denylist (unlike `QUERY_ALLOWED_TOOLS`'s allowlist) since ORGANIZE needs almost every other tool; the corpus is already analyzed by the pre-pass/analyzer before the loop starts, so the model has no legitimate reason to reach these
- `_ASK_USER_TOOL_NAME` / `_ASK_USER_TOOL_SPEC` (P8) — the host-side synthetic tool `ask_user`, merging K1's `ask_clarification` and L7's `propose_options` into one. Never registered with or forwarded to the MCP server; appended to the OpenAI tool list only when an `AskUserCallback` is wired in. Schema takes 1-5 `questions`, each `{text, options?}` — `options` (2-5 mutually-exclusive strings) makes an item multiple-choice, omitting it makes it an open question. No once-per-run cap
- `_PREPASS_CHUNK_SIZE = 300` — round-trip size for `run_prepass`'s `compute_checksum_batch`/`lookup_documents` calls (P4), bounding per-call memory/latency on a large corpus
- `_ANALYZER_EXTRACT_EXTENSIONS` — `frozenset` of `{".pdf", ".docx", ".xlsx", ".pptx", ".msg"}`; the stateless analyzer's (P5) host-side file-type dispatch — these go to `extract_text_batch`, everything else to `read_file_batch` — mirroring the split the old in-loop ANALYZE prompt used to leave to the model's own judgement
- `_ANALYZER_BATCH_SIZE = 10` — the stateless analyzer's (P5) batch size for NEW documents, matching the batch size the old in-loop ANALYZE instructions used
- `_SUBMIT_RECORDS_TOOL_NAME` / `_SUBMIT_RECORDS_TOOL_SPEC` — the host-side-only synthetic tool `submit_document_records` (P5), never forwarded to the MCP server; forced via `tool_choice` on every analyzer LLM call (the only forced-`tool_choice` call site in this codebase — every other call uses `tool_choice="auto"`). Its schema carries only model-derived fields (title/type/summary/provenance/date/entities) — deliberately no `path`/`checksum`, which are host-authoritative and rejoined by position, never trusted from the model's own output
- `_DIGEST_MAX_LISTED_DOCS = 200` (P6) — above this many documents, `_build_digest`'s per-doc listing truncates and points the agent at `list_documents`/`get_registry` instead — a fat digest would defeat its own purpose (avoiding a context blowup) on a large corpus

**Key functions:**
- `run_agent(target, settings, llm, on_event, on_approval_needed, on_ask_user_needed=None, on_cost_approval_needed=None, instructions=None, history=None, message=None) -> tuple[str, list[dict]]` — top-level organize entry; launches the MCP server subprocess via `mcp_session()`, then calls `run_agent_loop`, returning `(final_text, updated_history)`. `mcp_session(project_root, target=None)` sets `TARGET_DIR` on the server subprocess's env whenever `target` is given, so the server can confine path-taking tools to it (M2). `history`/`message` (O7) mirror `run_query_loop`'s shape — see `run_agent_loop` below; for a multi-turn chat, callers should instead keep a single session open and call `run_agent_loop` directly (`run_agent` launches a fresh subprocess per call). Does not accept `message_queue` (P7) — that's only meaningful on a session-holding caller like `run_agent_loop`. `on_ask_user_needed` (P8) replaces the old `on_questions_needed`/`on_options_needed` pair with one unified callback
- `run_agent_loop(target, settings, llm, session, on_event, on_approval_needed, on_ask_user_needed=None, on_cost_approval_needed=None, project_root=None, instructions=None, history=None, message=None, message_queue=None, ledger=None) -> tuple[str, list[dict]]` — the actual LLM tool-calling loop for organize mode (injectable session for testing). `ledger` (R1, GH #27) lets a caller pass the same `_TokenLedger` across a run and its O7 follow-up continuations so the running token totals persist across turns instead of resetting to zero on every call; when `None` (the default — tests, one-shot callers), a fresh ledger is constructed for just this call. **ORGANIZE-only loop, pre-pass + analyzer wiring (P6):** on a fresh run (`history is None`), before any turn happens, this now runs `run_prepass` (P4) to partition the corpus into known/new documents, fires the cost-approval gate (`_handle_cost_approval`, O8/P6) scoped to only the new documents if there are any, runs `_analyze_new_documents` (P5) on approval, then seeds the conversation's first user message with a compact corpus digest (`_build_digest`) instead of blank "please organize" instructions — `instructions` (the user's optional pre-analysis steering text from the `OrganizerScreen` starter pane, L3) is appended to that same seed message when non-empty. The turn loop that follows discovers tools via `_discover_openai_tools(session, denied=ORGANIZE_DENIED_TOOLS)`, structurally excluding content-fetching/recording tools already used by the pre-pass/analyzer, and a defense-in-depth dispatch check rejects any hallucinated call to one of those tools even though none are advertised. `on_ask_user_needed` (P8) wires the unified chat checkpoint, unlimited per run; `on_cost_approval_needed` wires the O8/P6 pre-analysis cost-approval gate, now scoped to new documents only. **Resumable chat (O7):** when `history` is given (the list returned by a previous call), none of the pre-pass/analysis/digest work above repeats — the existing history is reused as-is and `message` — a new free-text user turn — is appended before resuming, so a run that finished, errored, or hit the turn ceiling can be continued with the same ORGANIZE-only toolset. A continuation gets its own fresh per-call turn budget and a fresh, empty `_ProgressTracker` (no new pre-pass happens), so its adaptive budget floors at `_MAX_TURNS` rather than reflecting the initial pass's corpus size. **Live mid-run chat (P7):** when `message_queue` is given, it's drained non-blockingly via `_drain_message_queue` at three points — before the first LLM call, after every turn's tool-call batch, and when the response carries no tool calls (the point that would otherwise end the run) — each drained message is appended as a user turn, and in the last case the loop `continue`s instead of returning if anything was waiting, so a live chat message can redirect an in-progress run. `message_queue=None` (the default) is byte-for-byte the pre-P7 behaviour; the mechanism is independent of and composes with `history`/`message`. `ask_user` (P8) blocks on this same queue for its reply, rather than a modal. The whole turn loop is wrapped in `try`/`except`: an unhandled exception is caught rather than propagating — any tool call left without a matching tool-result message is answered with a synthesized `{"error": ...}` entry (so `messages` stays valid for a follow-up call), an `"error"` event fires, and `(error_text, messages)` is returned
- `_drain_message_queue(message_queue) -> list[str]` (P7) — non-blocking drain of `message_queue` (an `asyncio.Queue[str] | None`): repeatedly calls `get_nowait()` until `asyncio.QueueEmpty`, returning drained messages in arrival order, or `[]` immediately if `message_queue` is `None` or nothing is waiting. Never blocks the turn loop
- `run_query(question, settings, llm, on_event, history, target=None)` — convenience entry for one query, launching its own MCP session; `target` (the analyzed corpus's directory) is passed through to `mcp_session` so the server confines its read-only tools' path arguments (M2)
- `run_query_loop(question, settings, llm, session, on_event, history, project_root, ledger=None)` — read-only tool-calling loop; threads `history` across calls for multi-turn context; returns `(answer, updated_history)`. `ledger` (R1, GH #27) works the same way as `run_agent_loop`'s: pass the same `_TokenLedger` across a chat's questions so the running total persists for the whole `QueryScreen` session instead of resetting per question; `None` (the default) constructs a fresh one for just this call
- `_discover_openai_tools(session, allowed=None, denied=None)` — lists MCP tools and converts to OpenAI function specs; when `allowed` is given, only tools in the set are exposed (used by query mode); when `denied` is given (P6), tools in the set are excluded instead (used by organize/ORGANIZE mode, `denied=ORGANIZE_DENIED_TOOLS`) — the two parameters are independent filters, not mutually exclusive
- `_build_system_prompt(project_root, settings)` — assembles the organize-mode system prompt from the active profile, including one "Optional chat checkpoint" paragraph (P8) referencing `ask_user` — replaces the former separate clarification-checkpoint and multiple-option-checkpoint paragraphs
- `_build_query_system_prompt(project_root, settings)` — assembles the read-only query-mode system prompt from the active profile
- `_handle_execute_plan(...)` — intercepts `execute_plan` calls to insert the approval gate before forwarding to the server. Fetches the plan via `get_plan`, writes its full ops (plan id, rationale, folder notes, ops) to `.organizer/plan_ops.json` via `_write_ops_json` and attaches the path as `ops_json_path` on the event data, then awaits `on_approval_needed`. If the returned `ApprovalResult.refinement` is set (non-blank), the plan is NOT approved or executed — the tool result instead carries the refinement text back to the agent as a note instructing it to revise the plan (ops/rationale/folder notes) and call `execute_plan` again. Otherwise falls back to the plain approved/rejected path
- `_write_ops_json(plan_data, plans_dir)` — writes `{plan_id, rationale, folder_notes, ops}` to `<plans_dir>/../plan_ops.json` (i.e. `.organizer/plan_ops.json`), latest-plan-wins; returns the path, or `None` on an `OSError`
- `_handle_ask_user(*, args, on_event, on_ask_user_needed) -> Any` (P8) — intercepts calls to the host-side `ask_user` tool; merges K1's `_handle_clarification` and L7's `_handle_options` into one handler. Drops malformed/empty items, emits an `"ask_user"` `AgentEvent` with the well-formed questions, and awaits the callback; no once-per-run guard (unlimited calls per run); never raises — degenerate input (no callback wired, no well-formed questions, no reply captured) returns a note telling the agent to proceed with its own best judgement
- `_handle_cost_approval(*, doc_count, already_analyzed, estimated_tokens, settings, on_event, on_cost_approval_needed) -> bool` (O8/P6, `already_analyzed` added P8) — relocated from a mid-loop tool-call interception to a one-time gate run once before `_analyze_new_documents` processes any new documents; emits a `"cost_estimate"` `AgentEvent` with `data={"new": doc_count, "already_analyzed": already_analyzed, "estimated_tokens": estimated_tokens, "batch_size": _ANALYZER_BATCH_SIZE}` (from `_new_docs_cost_estimate`, new-docs-only, plus the known-doc count; `batch_size` added U5 — previously the real `_ANALYZER_BATCH_SIZE` never reached `data` at all, so both UIs' cost dialogs always displayed a hardcoded 10 regardless of the configured value, and the `summary` string interpolated a literal `10` rather than the constant), and — unless `approval_mode == "never"` or no callback is wired — awaits `on_cost_approval_needed`. Returns `True` if analysis should proceed; the event is always emitted, even when auto-approved, for observability
- `_TokenLedger` (dataclass, R2/GH #27, `totals["in"]` accumulation fixed U8) — replaces the old free-standing `_accumulate_tokens`/`token_totals` pair; tracks a run's running token totals and persists a per-call entry to `host/tokenlog.py`'s log as it goes. Fields: `log_path`, `model`, `run_id` (`uuid.uuid4().hex[:12]`), `calls`, `totals` (`{in, out, cached_in}`). `_TokenLedger.new(settings)` builds one from a `Settings` instance, guarding with `isinstance` checks against `token_log_path`/`llm_model` not being a real `Path`/`str` (e.g. a `MagicMock` in tests that stub settings wholesale). `.record(response, *, phase, step, on_event, docs=None, est_in=None)` reads `response.usage.prompt_tokens`/`completion_tokens` (and `prompt_tokens_details.cached_tokens` when present) after an LLM call (organize, query, and analyzer calls alike) and folds `prompt_tokens` into `totals["in"]` via two private accumulators kept separately and always summed together (`totals["in"] = analyze_in + conversation_in`): `analyze_in` sums every `phase="analyze"` call's `prompt_tokens` — analyzer calls are independent, throwaway per-batch conversations with no shared history, so their prompt counts are genuinely additive; `conversation_in` is *replaced* (not summed) on every other phase (`"organize"`, `"query"`) call with that call's `prompt_tokens` — confirmed against a real API journal that within one growing multi-turn conversation the endpoint's `usage.prompt_tokens` is already a cumulative session-wide total (the whole resent history so far), so summing it across turns was compounding an already-cumulative number. The R1 fix above got the per-phase policy right but still replaced the single shared `totals["in"]` outright on an organize/query call, so an analyze phase's accumulated cost visibly vanished from the displayed running total the moment the first organize/query call landed afterward; splitting the two accumulators (U8) closes that gap while keeping R1's per-conversation replace-not-sum reasoning intact. `totals["out"]` always sums (a fresh per-call value) and `totals["cached_in"]` always sums across every call regardless of phase. Appends a `TokenLogEntry` (swallowing `OSError`) and emits a `"tokens"` `AgentEvent` whose text is `_fmt_tokens`'s compact rendering of the running totals, now including the cumulative cached count (e.g. `"42.3K in (5.0K cached) / 5.1K out"`), and whose `data` carries the running `{in, out}` totals, this call's own `cached_in`/`call_in`/`call_out`, and a cumulative `total_cached_in`; a no-op when the endpoint's response omits `usage` or reports non-int counts. `.log_estimate(*, step, docs, est_in)` writes a `phase="estimate"` entry (no event) at the pre-analysis cost-approval gate, so the estimate-vs-actual gap is auditable from the same log. Threaded through as `ledger` in `_analyze_batch`/`_analyze_new_documents` (`phase="analyze"`), `run_agent_loop`'s ORGANIZE loop (`phase="organize"`), and `run_query_loop` (`phase="query"`) — as of R1, callers (`OrganizerScreen`, `QueryScreen`) construct one ledger per screen/session and pass it into every call via the new `ledger` parameter, so the running totals persist for that screen's whole lifetime instead of resetting on each call
- `_fmt_tokens(n)` — compact human-readable token count: `512`, `12K`, `12.3K`, `3.5M`
- `run_prepass(*, session, settings, target, on_event) -> PrepassResult` (P4) — deterministic, LLM-free corpus discovery. Walks `target` to exhaustion (re-walking every `truncated` subdirectory via `_collect_truncated_dirs`), checksums every discovered file in `_PREPASS_CHUNK_SIZE`-sized `compute_checksum_batch` chunks, dedupes by checksum, partitions into known/new by chunked `lookup_documents` calls (P3), and batches a single `rehome_documents` call for any `known` document whose registry path drifted from where it was actually found. Emits exactly one `"progress"` `AgentEvent` once discovery + partitioning finishes. Runs entirely through MCP tool calls, no local file I/O. As of P6, `run_agent_loop` calls this first thing on every fresh run (`history is None`)
- `_collect_truncated_dirs(walk_result)` — recursively collects the paths of every directory a `walk_tree` result marked `truncated` (`children` is `None`, depth limit reached); each needs its own `walk_tree` call to be fully discovered. Used by `run_prepass`'s exhaustive-walk loop
- `_new_docs_cost_estimate(new_docs, sizes, max_snippet_chars) -> tuple[int, int]` (P5) — `(new_doc_count, estimated_input_tokens)` computed from only `new_docs`' sizes (looked up in `PrepassResult.sizes`), mirroring `_ProgressTracker.cost_estimate`'s chars-per-token heuristic but scoped to new documents only; as of P6, feeds `_handle_cost_approval` directly
- `_fetch_batch_content(session, settings, batch, on_event) -> dict` (P5) — fetches content for one analyzer batch, splitting paths into `extract_text_batch` (`_ANALYZER_EXTRACT_EXTENSIONS`) vs. `read_file_batch` calls by extension and merging the two results keyed by path; emits `tool_call`/`tool_result` events for both calls
- `_analyze_batch(*, session, llm, settings, profile, batch, ledger, on_event) -> tuple[list[dict], list[dict]]` (P5) — analyzes one batch (≤10 NEW docs) with a single isolated, forced-`submit_document_records`-tool LLM call. Fetches content via `_fetch_batch_content`, wraps each document's text with `_wrap_untrusted` (S2), builds a throwaway messages list (never threaded into the main conversation), retries the LLM call once on a transient failure then skips the batch (`errors`), and rejoins the model's returned records to the batch's `{path, checksum}` entries strictly **by positional index** — never by any value the model returns — so an under-returning model produces `errors` entries for the unmatched tail rather than a silent misalignment. Returns `(documents, errors)`, where `documents` is ready for `record_document_batch`
- `_analyze_new_documents(*, session, llm, settings, profile, new_docs, ledger, on_event, tracker) -> dict` (P5) — the stateless analyzer's entry point: splits `new_docs` (P4's `PrepassResult.new`) into `_ANALYZER_BATCH_SIZE`-sized batches, calls `_analyze_batch` on each, and persists each batch's successfully-rejoined `documents` via the existing `record_document_batch` tool (no new registry-write code). As of Q2, `tracker` (a required keyword-only `_ProgressTracker`) is updated with each successfully recorded batch's paths and a `"progress"` `AgentEvent` is emitted right after — once per batch, not once for the whole call — so `run_agent_loop` no longer computes/emits progress itself after this returns. Returns `{"recorded": [...], "errors": [...]}` across all batches combined, matching `record_document_batch`'s own shape. As of P6, `run_agent_loop` calls this right after `run_prepass`, gated by `_handle_cost_approval`
- `_build_digest(prepass_result, analysis_result) -> str` (P6) — compact corpus summary seeded into the first ORGANIZE-phase user message in place of blank "please organize" instructions: per-document `title · type · path` line (drawn from `prepass_result.known`'s records and `analysis_result["recorded"]`), plus totals (`N document(s) recorded (K already known, M newly analyzed this run)`) and an error/unanalyzed count when either the pre-pass or the analyzer reported errors. Above `_DIGEST_MAX_LISTED_DOCS` listed documents, the per-doc listing truncates with a pointer to `list_documents`/`get_registry` for the rest. Deliberately NOT full per-document summaries — just enough for the ORGANIZE agent to plan a taxonomy without re-reading content, with the registry read tools available for anything more

**Turn limit:** `run_query_loop` raises an error event if the model has not produced a final (no-tool-call) response within `_MAX_TURNS = 50` turns. `run_agent_loop` (organize mode) instead uses an adaptive budget, `_analysis_turn_budget(total_discovered)` — `max(_MAX_TURNS, min(_MAX_TURN_BUDGET, _TURN_BUDGET_BASE + _TURN_BUDGET_PER_DOCUMENT * total_discovered))`, i.e. floor 50, ceiling `_MAX_TURN_BUDGET = 2000`, `_TURN_BUDGET_BASE = 30` plus `_TURN_BUDGET_PER_DOCUMENT = 3` turns per document discovered so far — recomputed each iteration as the O5 progress tracker's discovered count grows. It's a backstop against a runaway/looping agent, not the primary cost control — that's the O8 pre-ANALYZE cost-approval gate (`_handle_cost_approval`, above), which gates the first real batch-tool call of the run.

---

### `host/app.py`

**Role:** Textual TUI — six screens/modals.

| Class | Role |
|---|---|
| `OrganizerApp` | Root `App`; calls `is_configured()` on mount and routes to `SetupScreen` (first run) or `StartupScreen` (returning user). App-level `Binding("ctrl+s", "open_settings", "Settings", priority=True)` (P9) opens `ConfigScreen` from any screen via `action_open_settings`; no-op if `ConfigScreen` or `SetupScreen` is already the current screen. `priority=True` is required — Textual's non-priority binding-resolution chain stops at the first `ModalScreen` it encounters, so a plain-tuple binding would silently not fire while `ApprovalModal`/`CostEstimateModal` is on screen |
| `SetupScreen` | First-run wizard: welcome → AI service choice → URL + API key → document profile → done. Profile options, credential validation, and the plaintext-keyring warning copy come from `host/configflow.py` (U2, shared with the web UI's `host/web/wizard.py`). Saves via `save_user_config()` / OS keyring. Transitions to `StartupScreen` when complete |
| `ConfigScreen` | Settings panel accessible at any time from `StartupScreen`. Fields: URL, API key (password input), document profile (Select), approval mode (Select with friendly labels, options from `host/configflow.py`'s `APPROVAL_OPTIONS`, U3, shared with the web UI's `host/web/settings.py`). `_save` validates via `configflow.validate_credentials(..., key_required=False)` and builds its update dict via `configflow.build_settings_updates(...)` (U3) — same blank-key-preserves-existing rule as the web settings view — rather than its own inline logic; its plaintext-keyring warning also now comes from `configflow.plaintext_warning("Save", "cancel")`, no behavior change from before U3. Saves back to `~/.telcontar/config.env` via `save_user_config()` |
| `StartupScreen` | Lets the user browse and pick the target folder via a `DirectoryTree` (`#target-tree`, rooted at `Path.home()`); the selected path (defaults to home) is shown in a "Selected: …" label and used by "Organize" and "Query". Offers "Organize", "Query", and "⚙ Settings" buttons. Keybinding `s` opens `ConfigScreen` (the app-level `ctrl+s` binding, P9, also opens it from here and every other screen). "Query" (P2) resolves the corpus via `_find_organizer_root(target)`, walking up from the selected folder through its parents until one containing a `.organizer` is found (a subfolder of a previously-organized tree still resolves to that tree's memory), showing an error if none is found |
| `OrganizerScreen` | Main view. Opens on a **starter pane** (`#starter-pane`, L3) instead of auto-starting the agent: a `Static` rendering `_directory_overview(target)` — a code-generated, deterministic scan of names/structure only (file count, subfolder count, most common extensions; no content read, no LLM), excluding `.organizer` and the quarantine folder (P2, via `_quarantine_basename()`) from its own local `os.walk` the same way `walk_tree` does — plus an `#instructions-input` `Input` for optional free-text steering instructions and a `#proceed-btn` "Start organizing" button (or `Input.Submitted`). `_start_organizing()` hides the starter pane, shows `#main-split` (file-tree sidebar + a single chat-transcript `#conversation-pane`, `VerticalScroll`), and launches `_agent_worker(instructions)` as a Textual worker, passing the typed instructions (if any) through to `run_agent_loop(..., instructions=...)`. `_add_turn(speaker, text)` appends speaker-differentiated turns (`telcontar` / `you`) as styled `Static` widgets — the target line and any typed instructions are shown as the first turns; on each `tool_call` event, `_narrate(tool)` looks up the tool in the module-level `_TOOL_NARRATION` map and, if the macro-task phrase changed, emits a `telcontar` turn (e.g. "Reading documents…", "Planning changes…", "Applying the plan…") — deduping so consecutive calls in the same macro-task collapse to one turn. The raw tool calls/results themselves are appended via `_append_step(line)` into a click-to-expand `Collapsible` ("internal steps") interleaved in the transcript; a new speaker turn closes the currently-open group so the next tool call opens a fresh one. Below `#main-split`, a docked `#ops-journal` `RichLog` (L4, `wrap=False`, horizontally scrollable) renders the file operations recorded in the undo journal — one line per entry, newest last, via `_fmt_journal_entry` (the same formatter `JournalScreen` uses); multi-line hard-stop entries collapse to their summary line. `_refresh_ops_journal()` re-reads `.organizer/journal.jsonl` via `_resolve_journal_path` + `server.journal.all_entries` (swallowing read/config errors so the strip just shows nothing rather than breaking the screen) — `_resolve_journal_path`/`_resolve_plans_dir` (P2) resolve against the run's target directory via `Settings().for_target(target)`, rather than an ad-hoc project-root join; it runs on mount, after any tool in `_JOURNAL_WRITING_TOOLS` (now just `{"execute_plan"}` — the only tool left that can mutate the journal via the agent path, per M1) completes, and again on `done`. Below that, a `#progress-row` (O6, a `#progress-label` plus a Textual `ProgressBar`) renders the O5 `"progress"` event: `_update_progress(data)` reveals the row and updates both widgets, but only once a `total > 0` has been seen (an unknown/`None` total is never shown, avoiding Textual's indeterminate spinner); `_hide_progress()` re-hides it — without first snapping to 100% — on both `"done"` and `"error"`, so it disappears once the ANALYZE phase finishes rather than lingering through ORGANIZE. Status bar shows the current phase plus a running token-usage total (`N in (C cached) / M out`) once the LLM reports it, from a single `_TokenLedger` (R1, GH #27) built once via `_TokenLedger.new(settings)` and threaded through the initial `run_agent_loop` call and every subsequent chat-turn call, so the total accumulates for the screen's whole lifetime instead of resetting each call; keybinding `g` pushes `QueryScreen` once organizing completes, `j` pushes `JournalScreen` (the full modal journal view) via `action_view_journal`, which — as of U6 — runs inside a Textual worker (`self.run_worker(self._view_journal_worker(), exclusive=False)`); `push_screen_wait` requires a worker context and raised `NoActiveWorker` when called directly from the plain key-bound action (a real bug caught by a new test). `_view_journal_worker` awaits the modal and, if it dismissed with `True` (an undo happened), calls `_refresh_ops_journal()` so the bottom strip no longer goes stale after an undo. **Resumable chat (O7):** `_agent_worker` no longer calls the one-shot `run_agent` convenience wrapper — it opens `mcp_session(...)` itself and calls `run_agent_loop(...)` directly, keeping one MCP session (and one subprocess) open across the initial run and every subsequent chat turn. `self._history: list[dict] | None` carries the conversation returned by each call into the next; `self._messages: asyncio.Queue[str]` bridges the synchronous `Input.Submitted` handler on the bottom-docked `#organize-input` into the queue. Submitting echoes the message as a `user`-speaker turn via `_add_turn` before it is queued. `_note_terminal_state()` fires the "press g / keep chatting" cue and the desktop notification only on the *first* `"done"`/`"error"` event (tracked via `self._done`), not on every subsequent chat-turn completion. **Live mid-run chat (P7):** `#organize-input` is enabled right at the start of `_agent_worker`, before the first `run_agent_loop` call, rather than only once a terminal state is reached — both the initial call and every O7 continuation call pass `message_queue=self._messages`, so `run_agent_loop` itself drains and injects queued messages while it runs (see `host/agent.py` above). The worker's own `while True` loop (`await self._messages.get()` then `run_agent_loop(..., history=self._history, message=message, message_queue=self._messages)`) is unchanged in shape and still runs, but now only ever fires for a message that arrives strictly *after* a `run_agent_loop` call has already returned with nothing left pending in the queue — i.e. the agent is fully idle — rather than for every message regardless of timing |
| `QueryScreen` | Chat-style read-only Q&A screen: `RichLog` output + `Input` bar; keeps one MCP session open for the whole chat and threads conversation history across questions; status bar also shows a running token-usage total (`N in (C cached) / M out`) from a single `_TokenLedger` (R1, GH #27) built once and threaded through every question's `run_query_loop` call, so the total persists across the whole chat; settings are resolved via `load_settings().for_target(self._target)` (R1 fix — previously unrebased, so query-mode paths including the token log resolved relative to the process CWD instead of the corpus's own `.organizer/` directory, unlike the organize path); `Esc` pops back to the previous screen |
| `JournalScreen` | Modal view of the full undo journal (newest entries last), opened via `j`. Also the **only place `undo_last` can be triggered** (M1, S1): keybinding `u` calls `server.tools.undo_last` directly — bypassing MCP entirely, same pattern already used to read the journal — and shows a success/error status line; `Esc` or `j` closes it. `ModalScreen[bool]` (U6, was `ModalScreen[None]`): `action_close` dismisses with `self._undone_any` — whether at least one undo succeeded during this screen's lifetime — so the caller can tell whether the bottom ops-journal strip needs refreshing |
| `ApprovalModal` | Plan review: renders the plan's `rationale` (if set via `set_plan_rationale`) as `#plan-rationale`, then — if the plan has any `move`/`quarantine` destinations — a "Target layout" tree (`_render_target_layout`, L5) built from the plan's op destinations with each folder's `folder_notes` purpose note beside it (bare nodes for folders with no note; rename-only plans show no tree), then per-op checkboxes, the `ops_json_path` (if present) shown as a `#ops-json-path` label, a free-text `#refine-input` `Input` for natural-language plan editing (L6), and Approve/Refine/Reject buttons; Approve dismisses with `ApprovalResult(approved=True, removed_op_ids=...)`, Refine (button or `Input.Submitted`) dismisses with `ApprovalResult(approved=False, refinement=<text>)` unless the field is blank (no-op, modal stays open), Reject/Escape dismiss with `ApprovalResult(approved=False)` |
| `CostEstimateModal` | Pre-ANALYZE cost-approval gate (O8/P6/P8): constructor `(new_documents, already_analyzed, estimated_tokens, batch_size=10)`; shows "N new document(s) (M already analyzed, skipped), ~T input tokens estimated, batched in groups of 10 — proceed?" plus a disclaimer that it's a rough file-size estimate, not a real tokenization, and — since R1, GH #27 — that it "Covers analysis only — organizing the corpus afterward adds more", clarifying why this estimate is much smaller than the eventual session-total token count shown on the status bar (the estimate covers only the ANALYZE phase, not the ORGANIZE turn loop that follows). Proceed/Cancel buttons (Escape = Cancel), no op list or refinement. Returns a `CostApprovalResult`. Shown at most once per run, scoped to new documents only, wired via `OrganizerScreen`'s `on_cost_approval_needed` callback |

`ClarificationModal` and `OptionsModal` (K1/L7's separate clarifying-question and multiple-option checkpoints) are **removed as of P8** — no modal replaces them. The unified `ask_user` checkpoint renders as a normal chat-transcript turn and awaits the next chat message on the same live-chat queue P7 wired up; see `_handle_ask_user`/`on_ask_user_needed` above and `OrganizerScreen`'s `on_ask_user_needed` closure below. The `RadioButton`/`RadioSet` widget imports, used only by the deleted `OptionsModal`, are gone too.

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
├─ #organize-input (enabled for the whole run, P7) ───────┤
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

**Worker pattern:** `OrganizerScreen.on_mount` only shows the starter pane and focuses `#instructions-input`; `_start_organizing()` (triggered by `#proceed-btn` or `Input.Submitted` on the instructions field) launches `_agent_worker` as a Textual worker. The worker is `async`, so it can `await` the approval modal via `app.push_screen_wait(ApprovalModal(...))`. As of P8, when the agent calls `ask_user`, there is no modal to await — `on_ask_user_needed` instead renders the question(s)/option(s) as a `telcontar` transcript turn (`_add_turn`) and `await self._messages.get()`, blocking on the same queue live mid-run chat already uses, so the user's next chat message becomes the reply. As of O7, `_agent_worker` opens `mcp_session(...)` itself and calls `run_agent_loop` for the initial run, then enters a `while True` loop that awaits `self._messages.get()` (populated by the synchronous `Input.Submitted` handler on `#organize-input`) and calls `run_agent_loop(..., history=self._history, message=message, message_queue=self._messages)` — all on the same session, so the subprocess is never restarted between turns. As of P7, `#organize-input` is enabled once, right at the start of `_agent_worker`, and stays enabled for the whole run rather than being toggled per iteration of that loop — both the initial call and every continuation call pass `message_queue=self._messages`, so `run_agent_loop` drains and injects queued messages itself while it runs; the `while True` loop above now only fires for a message that arrives after a call has already returned with the queue empty. `QueryScreen` uses the same `asyncio.Queue` bridging pattern to feed the synchronous `Input.Submitted` event handler into the async `_query_worker` that drives `run_query_loop`.

---

### `host/llm.py` (~18 lines)

**Role:** Factory function for the OpenAI-compatible client.

**Key function:** `make_client(settings) -> AsyncOpenAI` — creates an `AsyncOpenAI` instance pointed at `settings.llm_base_url`. For Azure, it also injects `default_query={"api-version": ...}` so the Azure API version parameter is sent on every request.

**Design note:** No provider-specific code is needed for most endpoints — only Azure requires the extra `api-version` query parameter; any other OpenAI-compatible provider (Mammouth, OpenAI, etc.) works with just the `base_url` and `api_key` overrides.

---

### `host/configflow.py`

**Role:** Framework-agnostic (no `nicegui`, no `textual`) configuration-flow logic shared by the Textual TUI's `SetupScreen`/`ConfigScreen` and the NiceGUI web UI's setup wizard (U2) and settings view (U3) — one source of truth for profile options, per-service hints, credential validation, approval-mode options, and the plaintext-keyring-fallback warning copy.

**Key functions:**

| Function | Description |
|---|---|
| `profile_options() -> list[tuple[str, str]]` | `[(display_label, profile_id), ...]` for a Select/dropdown; reads TOML files from `profiles/`, falling back to `[("General documents", "is_it_project")]` if the directory can't be found. Moved here from `host/app.py`'s old `_load_profile_options`/`_PROFILE_LABELS` (now gone from that module); `host/app.py` imports it aliased as `_load_profile_options` at its two existing call sites. |
| `validate_credentials(url, key, model, *, key_required) -> str \| None` | Validates url → key → model in that frozen order, returning the first error message or `None`. `key_required=True` is the wizard's stricter first-run case (a blank key is itself an error); `key_required=False` (U3) is the settings view's case — a blank key there means "keep the saved key" — which also changes the URL error's wording to match each screen's existing, test-pinned copy. |
| `build_wizard_updates(url, key, model, profile, service) -> dict[str, str]` | Builds the settings-update dict for the wizard's save step; always includes the API key (the wizard requires one). Adds `llm_api_version` when `service == "azure"`. |
| `build_settings_updates(url, key, model, profile, approval_mode) -> dict[str, str]` | (U3) The settings view's counterpart to `build_wizard_updates` — includes `llm_api_key` only when `key` is non-empty (the blank-key-preserves-existing rule) and, unlike the wizard's dict, carries `approval_mode` instead of a service/`llm_api_version` field (the settings view has no service picker). |
| `plaintext_warning(button_label, recovery_action="go back") -> str` | The shared, plain-text (no Rich/HTML markup — this module is UI-agnostic) warning shown when the OS keyring is unavailable and the user must explicitly confirm a plaintext fallback. `button_label` must match the actual button the user is told to press again — fixes U8's copy bug, where the TUI wizard said `Press "Finish" again` while its button read "Save & continue →". |

**Other exports:** `AZURE_API_VERSION` (`"2025-01-01-preview"`); `SERVICE_HINTS: dict[str, dict[str, str]]` — per-service URL/model hint and placeholder text for `"openai_compatible"` vs `"azure"`, consumed by both the TUI's API-details step and the web wizard's API-details step; `APPROVAL_OPTIONS: list[tuple[str, str]]` (U3) — the three `(label, value)` approval-mode choices ("Always ask before any changes"/`always`, "Only ask before moving or quarantining files"/`destructive_only`, "Never ask — full automatic mode"/`never`), moved out of `host/app.py`'s `ConfigScreen` so both `ConfigScreen` and `host/web/settings.py` share one list.

---

### `host/web/` (Phase 18, extended by Phase 19 T2/T3/T5/T6/T7, Phase 20 U2-U7)

**Role:** NiceGUI-based web UI package — the first piece of a planned
Textual→NiceGUI migration. As of S6, `telcontar --web` (`host/main.py`, lazy import)
launches it in place of the Textual TUI, which stays the default with no flags. As
of U2 it has its own first-run setup wizard, at parity with the TUI's; U3 a settings
view reachable from every screen; U4 a TUI-faithful approval dialog plus a
sidebar tree that refreshes itself after `execute_plan`; U5 a TUI-faithful cost
estimate dialog; U6 a journal view + undo, with a visible toolbar affordance; and
U7 a read-only query view (`/query/{run_id}`), reached via a "Query this corpus"
button on the organize view once a run finishes. It still does not have a
dedicated startup screen offering direct Organize/Query/Settings entry points the
way the TUI's `StartupScreen` does (Phase 20 item U1, still open) — see
`host/main.py`, above.

**`host/web/session.py`** — framework-agnostic per-run state, no `nicegui` import.
Key types: `RunSession` (`run_id`, `target`, `mode: Literal["organize", "query"] =
"organize"` (U7), `transcript`, `steps`, `activity`, `status`, `tokens`,
`progress`, `pending: PendingRequest | None`, `messages: asyncio.Queue`,
`history: list[dict] | None`, `narrator`, `task`, `fs_revision: int`),
`TranscriptItem` (`seq`/`speaker`/`text`), `StepRecord`
(`seq`/`tool`/`summary`/`args`/`detail`/`status: "running"|"ok"|"error"`),
`PendingRequest` (`request_id`/`kind`/`payload`/`future: asyncio.Future`). `mode`
(U7) is one `RunSession` type/registry serving both organize and query runs
rather than a parallel `QuerySession` type — query mode needs the exact same
`add_turn`/`open_step`/`close_step`/`status`/`tokens` primitives, and
`pending`/`progress` simply stay unused for query sessions.

As of T5/T6, `transcript: list[TranscriptItem]` is turns-only — genuine
user↔telcontar exchanges (chat, ask_user, approval/cost outcomes, done/error) —
and `TranscriptItem` no longer carries a `kind`/`lines` discriminator; tool
activity lives instead in `steps: list[StepRecord]`, sharing the same `_seq`
counter as `transcript` for a stable relative ordering. `add_turn(speaker, text)`
appends a turn. `open_step(tool, summary, args=None)` starts a step "running" and
tracks it as the session's one currently-open step; `close_step(result, *, ok) ->
StepRecord | None` pairs it with its result as pretty-printed JSON
(`{"args": step.args, "result": result}`, capped at `_MAX_STEP_DETAIL_CHARS =
20_000` chars with a "(truncated)" suffix — a batch read/extract result can be
megabytes of document text, and this is a display cap, distinct from the egress
cap `MAX_SNIPPET_CHARS` already enforces upstream) and marks it `"ok"`/`"error"`,
returning the closed `StepRecord` (`None` if none was open, U4 — was previously
`None` unconditionally) so a caller like `AgentBridge` can inspect `.tool` to
decide whether the call just mutated the tree.
A step left "running" forever (the run errored out mid-call before its matching
`tool_result` arrived) is the intentional, correct visual, not a bug — it shows
exactly where things stopped. These replace the old `append_step`/`_steps_item`
group-building logic that mirrored `OrganizerScreen`'s `_add_turn`/`_append_step`.
`new_pending`/`resolve_pending` manage the one in-flight approval/cost request per
session; as of U4, `resolve_pending(result, *, request_id=None)` takes an optional
`request_id` that, when given, must match the *current* `pending.request_id` or the
call is silently ignored — stops a stale dialog (another browser tab, or one left
over after a reload) from resolving a different, newer pending request than the one
it was actually shown. `request_id` is optional so the app-shutdown hook (which has
no dialog and just rejects whatever is pending) keeps working unchanged.
`bump_fs_revision()` (U4) increments `fs_revision`, a counter signalling "the
target directory's contents changed on disk" — bumped by `AgentBridge` after a
tree-mutating tool result (see below) and, as of U6, also called directly by
the journal dialog's own undo-confirm handler (an undo changes what's on disk
too) — consumed by `run_page`'s sidebar-tree refresh and journal-count refresh,
so the poll loop can rebuild the tree/refresh the count only when something
actually changed instead of on every tick. `has_open_step() -> bool` (U6) reports
whether `_open_step` is set — true while a tool call is still running — and
gates `build_journal_dialog`'s Undo button: undo must be blocked in this state,
since `server.journal.pop_last` rewrites the whole journal file while the MCP
server subprocess may still be appending to it, and racing them can silently
drop audit records.
Module-level registry `create(target, *, mode="organize") -> RunSession` /
`get(run_id)` / `close(run_id)` / `all_sessions()`, keyed by a
`secrets.token_urlsafe(16)` run id —
deliberately unit-testable in plain pytest, since a page (`host/web/main.py`) only
polls and mutates this data rather than deciding how it's drawn. `get_sidebar_width()
-> int` / `set_sidebar_width(width: int) -> int` (T4) manage one in-memory
sidebar-width preference (`SIDEBAR_WIDTH_DEFAULT`/`_MIN`/`_MAX` = 380/240/720px) for
the process's lifetime — a module-level global rather than a `RunSession` field,
since it must also apply on the picker route where no `RunSession` exists yet, and
telcontar is single-user so there's no other viewer's preference it could clobber.
`set_sidebar_width` clamps to `[SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX]` and returns
the clamped value actually stored.

**`host/web/bridge.py`** — `AgentBridge(session)`, also `nicegui`-free. Implements
the same callback contract `host.app.OrganizerScreen` uses:
`on_event`/`on_approval_needed`/`on_cost_approval_needed`/`on_ask_user_needed` (the
last awaits the next message on `session.messages`, mirroring P8's live-chat
`ask_user` checkpoint). `start(instructions: str | None = None)` launches
`run(instructions)` as a detached `asyncio.Task` owned by the `RunSession` (not by
any one NiceGUI client); `run()` is a near-verbatim port of
`OrganizerScreen._agent_worker` onto a plain `asyncio.Task` — loads settings, opens
`mcp_session`, calls `run_agent_loop`, then loops on `session.messages.get()` for
O7-style follow-up continuations, threading one `_TokenLedger` across all of them.
`instructions` (S5) is the starter pane's optional steering text; it is passed
only to the first `run_agent_loop` call, never to a continuation, matching
`host/app.py`'s `_agent_worker(instructions=...)` contract.

As of T5/T6, `on_event`'s `tool_call`/`tool_result` handling no longer appends a
chat turn — that fixed the "telcontar talking to itself in bubbles" issue T5 was
written to address. `tool_call` narrates via `session.narrator.narrate(tool)` into
`session.activity` (the log zone's "current activity" line) and opens a step —
`session.open_step(tool, event.text, args)` — reading `tool`/`args` off
`event.data`; `tool_result` closes it — `session.close_step(result, ok=ok)` —
inferring `ok` from whether `event.data`'s `"result"` value is a dict containing
an `"error"` key. This relies on `host/agent.py`'s 5
`AgentEvent("tool_call"/"tool_result", ...)` emission sites (the pre-pass
analyzer's `_fetch_batch_content`, `_analyze_new_documents`'s
`record_document_batch` call, and the ORGANIZE/QUERY dispatch loops in
`run_agent_loop`/`run_query_loop`) now carrying structured `data`:
`{"tool": name, "args": args}` for `tool_call` (previously just the tool name,
no args) and `{"result": result}` for `tool_result` (previously no data at all).
Purely additive — no `run_agent_loop`/`run_query_loop` signature change, since
adding a kwarg there breaks explicit-signature `fake_run_agent` test doubles.

As of U4, `on_event`'s `tool_result` case also calls `session.bump_fs_revision()`
whenever `close_step` returns a closed step (see `session.py` above) whose `.tool`
is in the module-level `_TREE_MUTATING_TOOLS = frozenset({"execute_plan",
"write_index", "write_summary", "write_folder_readme"})` and the result was ok —
these are the only tools that change what's on disk under the target directory.
This is what drives `run_page`'s sidebar-tree refresh.

**`host/web/bridge.py` also exports `QueryBridge(session)`** (U7) — the same shape
(`on_event`/`start`/`run`) as `AgentBridge`, but driving `host.agent.run_query_loop`
instead of `run_agent_loop`. It has no `on_approval_needed`/`on_cost_approval_needed`/
`on_ask_user_needed` methods at all — their absence is itself the safety property,
since query mode is read-only by construction (`host.agent.QUERY_ALLOWED_TOOLS`).
`on_event` handles `thinking`/`tool_call`/`tool_result`/`tokens`/`warning`/`error`
the same way `AgentBridge` does (narration is skipped — `tool_call` just opens a
step, no `Narrator` call), but deliberately has no `"done"` case: `run_query_loop`
both emits a `"done"` event and returns the answer text, and the TUI's own
`QueryScreen.on_event` renders only from the return value — `QueryBridge.run()`
mirrors that, calling `session.add_turn` with the returned answer after each
`run_query_loop` call rather than reacting to the event (handling both would
render every answer twice). `start()` kicks the query worker off immediately (TUI
parity: `QueryScreen` starts its worker in `on_mount`, no explicit "start"
button). `run()` is a near-verbatim port of `QueryScreen._query_worker`: one MCP
session and one `_TokenLedger` for the whole chat, threading `history` across
questions for multi-turn context. `done`/`error` here are per-question, not
per-session: `QueryBridge` never sets `session.done`.

**`host/web/dialogs.py`** (U4, extended by U6) — one builder per `PendingRequest` kind, replacing
the dialog-building code that used to live inline in `run_page`'s
`_show_pending_dialog` closure. `build_approval_dialog(session, pending) ->
ui.dialog` is a faithful port of the TUI's `ApprovalModal`: title (plan id +
op count), the rationale (if any) with its "model-generated — not verified fact"
disclaimer, the target-layout preview (`host.format.render_target_layout`) with
its own folder-notes disclaimer when folder notes are present, per-op checkboxes
(`fmt_op(op, session.target, markup=False)`, defaulting checked), the
`ops_json_path` label when present, a free-text refine input, and
Approve/Refine/Reject buttons. Approve resolves with `ApprovalResult(approved=True,
removed_op_ids=[...])` for every unchecked op; Refine resolves with
`ApprovalResult(approved=False, refinement=text)` only if the field is non-blank
(otherwise a no-op, dialog stays open) — refinement therefore always takes
priority over approval, since Refine and Approve can never both fire from the same
click; Reject resolves with `ApprovalResult(approved=False)`. `build_cost_dialog(session,
pending) -> ui.dialog` (U4 placeholder, made a faithful port of the TUI's
`CostEstimateModal` in U5) shows the title "Analyze this corpus?", a summary
line composed from the engine-side `pending.payload["data"]` dict
(`new`/`already_analyzed`/`estimated_tokens`/`batch_size` — `data` is the
source of truth, matching the approval dialog's `plan_data`-driven approach;
`pending.payload["summary"]` is used only as a fallback when `data` is empty,
a caller-convenience case exercised by one existing test), the same
"rough estimate from file sizes" disclaimer as the TUI, and Proceed/Cancel
buttons. Both dialogs are
`.props("persistent")` (no backdrop-click or Esc dismissal) and resolve via
`session.resolve_pending(result, request_id=pending.request_id)` — fixing a live
bug where the previous plain `ui.dialog()` could be dismissed without resolving
its future, permanently deadlocking the run with no visible symptom (the same
failure class as the reload-orphaning issue ROADMAP.md's "Break 1" spike found,
closed here for the dialog-dismissal path instead).

`build_journal_dialog(session) -> ui.dialog` (U6) is the toolbar-triggered
journal viewer — the web UI's counterpart to the TUI's `JournalScreen`. Unlike
the approval/cost dialogs above, it isn't resolving a `PendingRequest` (nothing
is waiting on a future), so it's a plain, dismissible `ui.dialog()` — Esc/
backdrop-close just closes the viewer. Lists entries via
`host.web.journal.load_entries` rendered through `host.format.fmt_journal_entry`
inside a `@ui.refreshable body()`, with an "Undo last operation" button gated
behind a separate sibling confirm dialog (built once up front, not nested
inside `body`'s refreshable, so its buttons bind once rather than re-binding on
every `body.refresh()`); confirming calls `host.web.journal.do_undo`, refreshes
`body`, and — on success — calls `session.bump_fs_revision()` so the sidebar
tree and the toolbar's own journal count pick up the change. While
`session.has_open_step()` is true, the Undo button is replaced with an
explanatory label — undo is blocked while a tree-mutating step is in flight,
since `server.journal.pop_last` rewrites the whole journal file while the MCP
server subprocess may be appending to it. `load_entries`/`do_undo` are called
**synchronously**, not via `run.io_bound` — a deliberate deviation from every
other blocking-I/O call site in `host/web/`; see `host/web/journal.py` below
for why.

**`host/web/journal.py`** (U6) — journal load/undo logic, `nicegui`-free,
mirroring how `host/web/tree.py` relates to `host/web/shell.py`: this module
owns the filesystem/MCP-adjacent logic, `host/web/dialogs.py` owns the
rendering. `load_entries(target) -> list[dict]` wraps `server.journal.all_entries`
via `host.paths.resolve_journal_path`, wrapped in a defensive `try`/`except`
that returns `[]` on any error — mirrors `host.app.JournalScreen`'s existing
defensive handling, so a broken `Settings()`/config never blanks the view.
`do_undo(target) -> dict` wraps `server.tools.undo_last` via
`host.paths.resolve_journal_path`/`resolve_plans_dir`, returning its raw result
dict verbatim. Both `server.journal`/`server.tools` imports are late (inside
the functions), matching the TUI's own existing discipline for these same
imports — avoids dragging their heavier dependency chains in at module import
time. Undo stays user-only and out of MCP by design: this module calls
`server.tools.undo_last` directly (a local function call, same machine), the
same way `host.app.JournalScreen` does; there is no agent-reachable path to it.

**`host/web/steplog.py`** (U4) — the internal-step log-strip rendering lifted out
of `run_page`'s closure, originally intended so later screens (a journal view,
a query view) could reuse the same "one compact line per step, toggle opens
full detail in the shell's drawer" idiom instead of re-deriving it. In the
event, U6's journal dialog (`host/web/dialogs.py`'s `build_journal_dialog`)
didn't need it — journal entries render as plain formatted lines
(`host.format.fmt_journal_entry`), not as steps — so this module's reuse case
is still open, pending U7's query view. Still imports `nicegui` (renders
`ui.row`/`ui.label`/`ui.button`), unlike `session.py`/`bridge.py`/`tree.py` —
only `fmt_step_line(step) -> str` (`f"{glyph} {step.summary}"`, `_STEP_GLYPHS` — ▶
running / · ok / ✗ error) has no framework dependency. `StepLogState` is the
per-client render cursor (`step_seq`, `step_rows: dict[int, tuple[ui.row,
ui.label]]` keyed by step seq) — the same shape `_RenderState.step_rows` used to
carry inline in `main.py`. `render_step_row(log_column, shell, step)` renders one
row with its "code"-icon detail button (`shell.show_detail(...)`); `prune_log(state)`
caps the DOM at `_MAX_LOG_ROWS = 500`, deleting the oldest row first;
`sync_steps(log_column, shell, state, steps)` renders any step newer than
`state.step_seq` and refreshes the text of already-rendered rows whose
status/summary changed (a "running" step is updated in place once it closes) —
`run_page`'s `_refresh()` now just calls this once per tick.

**`host/web/shell.py`** (Phase 19 T2, extended by T3, T6, and Phase 20 U3) —
`app_shell(*, target: Path | None = None, on_select: Callable[[Path], None] | None
= None) -> Iterator[Shell]`, a `@contextmanager` mounted by every `@ui.page` route in
`host/web/main.py`, including the early-return branches (not-configured,
run-not-found), so the sidebar is visible on every screen instead of being
assembled per-page. As of U3, the drawer always renders an unconditional
"Settings" button (`.mark("btn-sidebar-settings")`) right below the "telcontar"
label, navigating to `/settings` — reachable from every route, mirroring the
TUI's app-level `ctrl+s` `action_open_settings` binding (`host/app.py`). Builds a `ui.left_drawer` as a direct child of the page body —
NiceGUI's `require_top_level_layout` raises `RuntimeError` if the drawer is nested
inside another container — containing a `ui.tree` sourced from
`host.web.tree.build_nodes` (`.props("dense no-connectors")`), plus the page's
main content column. As of T6 it also creates a `ui.right_drawer` alongside the
left one (both top-level layout elements, subject to the same constraint), and
yields a `Shell` dataclass (`drawer`, `tree`, `content`, `detail_drawer`, `target`,
`selected`). `Shell.show_detail(title: str, detail: str)` (T6) populates and opens
the right drawer with one internal step's full payload, via
`ui.codemirror(detail, language="JSON").disable()` — deliberately never
`ui.code`/`ui.markdown`, since both render through a markdown fenced-code path and
step detail can carry untrusted document content that must never be interpreted
as markup; `ui.codemirror` takes the content as a plain value/prop instead, with
`.disable()` making it read-only display, not an editor. `host/web/main.py`'s
`run_page` is the only caller, reached via a per-step "code" icon button in the
log zone. The tree's `on_select` handler ignores placeholder-node clicks and
otherwise sets
`shell.selected` and calls the optional `on_select` callback; its `on_expand`
handler is async — the first time a real directory node is expanded it calls
`host.web.tree.load_children` via `run.io_bound`, splices the result into
`tree.props["nodes"]` in place (found via `host.web.tree.find_node`), and calls
`tree.update()`. `Shell.refresh_tree(root)` (T3) re-roots the tree at `root` and
updates `Shell.target` to match — used by the picker's "go up one level" button and
a Windows-only drive-root `ui.select` dropdown, both rendered only when `on_select`
is passed in (the picker route, `/`; hidden on `/run/{run_id}`, where the tree is
for verification only). `app_shell`'s signature is frozen: later Phase 20/21 work
is expected to mount through it unchanged. `_apply_theme()` is a deliberately empty
hook for T7/T8's future `host/web/theme.py`. `host/web/shell.py` now shares
nicegui-importing duties with `host/web/main.py`.

The drawer's width (T4) comes from `web_session.get_sidebar_width()` and is applied
via the Quasar `width` prop (`drawer.props(f"width={width}")`), never raw CSS,
because Quasar also offsets `.q-page-container` from that same prop — a CSS-only
width would leave the page content overlapped. A 6px `div.tc-sidebar-resize` handle
on the drawer's right edge is wired, once per page build, by a small injected JS
snippet (`_RESIZE_JS`, run via `ui.run_javascript`) that tracks
mousedown/mousemove/mouseup on `document` rather than just the handle (so the
pointer can leave the 6px strip mid-drag) and live-resizes the drawer's CSS width
for visual feedback. Only on mouseup does it emit a custom `tc_sidebar_resized`
event (via NiceGUI's `emitEvent`/`ui.on` bus) carrying the final pixel width; the
Python-side `_handle_resize` (registered with `ui.on("tc_sidebar_resized", ...,
throttle=0.05)`) is the only point that actually persists the preference, via
`web_session.set_sidebar_width()`, and re-applies the real Quasar `width` prop. The
drag itself is DOM-only and writes nothing to `session.py` until mouseup.

**`host/web/tree.py`** (Phase 19 T2, fleshed out by T3) — NiceGUI-free, mirroring
`session.py`/`bridge.py`'s invariant so it stays testable in plain pytest.
`build_nodes(root: Path) -> list[dict]` builds the top-level node list `ui.tree`
expects (`{"id": <absolute path str>, "label": <basename>, "children": [...]}`, id
always an absolute path string so it's a stable key across a page reload); the
root's own immediate children are loaded eagerly (one directory listing) so the
sidebar shows useful content on first render, while deeper levels stay lazy behind
a placeholder-child scheme — a not-yet-expanded directory gets one placeholder
child whose id ends in `PLACEHOLDER_SUFFIX` (a null byte + ellipsis, never a real
path), so `ui.tree` shows an expand arrow without this module walking into it.
`load_children(path) -> list[dict]` lists one directory's immediate entries —
files and folders both shown, since the sidebar's job includes letting the user
verify files actually moved/renamed, not just picking folders — sorted folders
before files, then alphabetically. It hides dotfiles (`.organizer`, `.git`, ...)
but deliberately *not* `_quarantine` (the only removal path — the user must be
able to see what landed there), never follows symlinks/junctions (Windows profile
directories like "Application Data" can loop back on themselves), and never
raises: a permission-denied or vanished directory yields an empty list rather than
blanking the whole tree. `find_node(nodes, id)` depth-first searches the nested
node list for a placeholder's real parent; `needs_loading(node)` reports whether a
node still carries the placeholder rather than real children — both support
`shell.py`'s expand handler. `rebuild_nodes(root, expanded_ids: set[str]) ->
list[dict]` (U4) is a non-destructive alternative to `build_nodes`: it rebuilds
the whole node list the same way, but for every directory id in `expanded_ids` it
eagerly loads real children — recursively, via the private `_rebuild_children`
helper — instead of leaving the lazy-load placeholder, so refreshing the sidebar
after a tree-mutating tool call doesn't collapse whatever the user had expanded. A
directory that no longer exists (renamed/moved away by the very op that triggered
the refresh) is silently dropped, the same tolerance `load_children` already has.
`list_drive_roots() -> list[Path]` wraps
`os.listdrives()` (Python 3.12+, Windows-only) so the picker can reach outside the
home directory, returning an empty list (never raising) on any other platform,
Python version, or enumeration error.

**`host/web/theme.py`** (T7, extended by T8) — product-identity helpers,
`nicegui`-free like `session.py`/`bridge.py`/`tree.py`. `window_title(target: Path
| None = None) -> str` returns `"telcontar"` with no target, or `f"telcontar —
{target.name}"` once one is selected, falling back to the full path string when
`.name` is empty (a Windows drive root, e.g. `Path("C:\\")`, so the title never
ends in a dangling "— "). T8 adds telcontar's visual identity — a
Númenórean/human-king (Aragorn's Quenya name) motif, gold and silver on a dark
base:

- `PALETTE: dict[str, str]` — exactly the 9 keyword names `nicegui.app.colors()`
  accepts (`primary`/`secondary`/`accent`/`dark`/`dark_page`/`positive`/
  `negative`/`info`/`warning`). Gold `primary` (`#C8A951`), mithril-silver
  `secondary` (`#AEB6C4`), `dark_page`/`dark` as the page background and
  elevated-surface dark tones. `positive`/`negative` stay in their own
  desaturated green/red hue families, deliberately never re-hued gold/silver —
  the approval dialog's Approve/Reject buttons are the highest-trust screen in
  the product and must stay unmistakable.
- `FAVICON_SVG` — an inline SVG string (Elendil's seven-pointed star, gold on
  the dark base) passed straight to `ui.run(favicon=...)`, which NiceGUI inlines
  as a data URL — no file, no network request.
- `font_face_css(font_dir: Path | None = None) -> str` — emits an `@font-face`
  rule for the vendored Cinzel woff2 (`host/web/assets/fonts/`) only if the file
  actually exists on disk; returns `""` otherwise, so a missing font is silently
  a plainer heading, never a 404.
- `css(font_dir: Path | None = None) -> str` — the one small CSS layer: binds the
  display typeface (Cinzel, falling back to "Trajan Pro" / "Palatino Linotype" /
  "Book Antiqua" / Georgia / serif — always present regardless of whether the
  font file exists) directly onto Quasar's own `.text-h1`...`.text-h6` heading
  classes, so every existing heading picks it up with no per-component class
  sprinkling, plus a mandatory contrast fix (`.q-btn.bg-primary { color:
  #0E1116 !important; }` — Quasar renders a filled `color="primary"` button with
  white label text by default, and white-on-gold is ~2.2:1 contrast, unreadable).
- `FONT_DIR`, `FONT_URL_PATH` (`/tc-fonts`) — the static-assets directory and its
  `app.add_static_files` mount point, both consumed by `run_web()`.

**`host/web/forms.py`** (U2, extended by U3) — shared NiceGUI form fragments for
the setup wizard and the settings view; unlike `session.py`/`bridge.py`/`tree.py`/
`theme.py` it does import `nicegui`, since it renders actual UI elements.
`credential_inputs(...) -> CredentialInputs` renders the URL / API-key / model
input triple, with optional per-service hint text above the URL and model fields
(empty hint text renders as nothing rather than an empty caption), each element
`.mark()`ed (`input-url`/`input-key`/`input-model`) for NiceGUI's headless `user`
test fixture. As of U3, it also takes a `key_placeholder` parameter (default
`"Paste your key here"`, the wizard's copy) so `host/web/settings.py` can pass
`"Paste a new key, or leave empty to keep the current one"` instead — the
blank-key-preserves-existing rule spelled out inline in the field itself.
`save_with_plaintext_guard(build_updates, *, plaintext_confirmed,
button_label, recovery_action="go back") -> tuple[bool, str]` calls
`config.settings.save_user_config` via `run.io_bound` (so the file write + OS
keyring round-trip never blocks the event loop) using a *fresh* dict from
`build_updates()` on every call — never a cached one, since `save_user_config` pops
the API key out of its argument dict before raising `PlaintextKeyFallbackNeeded`,
so a caller reusing the same dict object across a retry would silently save without
the key. Returns `(success, warning_text)` — `""` on success, or
`host.configflow.plaintext_warning(...)`'s text on the fallback path; the caller
owns re-rendering and tracking `plaintext_confirmed` for the next call.

**`host/web/wizard.py`** (U2) — the setup wizard itself: `build_setup_wizard(*,
on_finish)`, a 1:1 port of `host/app.py`'s `SetupScreen` — same 5 steps (welcome,
service choice, API details, document profile, done), same validation
order/strings (via `host/configflow.py`), same plaintext-keyring
warn-then-confirm flow (via `forms.save_with_plaintext_guard`). State is a
page-closure `_WizardState` dataclass, never `app.storage` or a URL param — the
API key must never touch either. A `@ui.refreshable` `steps()` function keyed on
`state.step` renders only the active step and rebuilds on transition — real
per-step routing, NiceGUI's natural equivalent of the TUI's
mount-all-five-and-toggle-`.display` approach (`_show_step` in `host/app.py`).
Deliberately lives outside `host/web/main.py` — `main.py`'s `@ui.page` decorators
stay thin shells; the view-building logic lives in per-screen modules like this
one. `host/web/main.py` mounts it at `@ui.page("/setup")`, calling
`build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))`, through the existing
`app_shell()`.

**`host/web/settings.py`** (U3) — the settings view, a NiceGUI port of
`host/app.py`'s `ConfigScreen`: `build_settings_view(*, on_done)` fetches
`configflow.profile_options()` and `config.settings.read_user_config()` via
`run.io_bound` (off the event loop, same S5 discipline as the wizard), then
renders URL / API-key / model (`forms.credential_inputs`, with the
key-preserves-existing placeholder above) plus document-profile and
approval-mode `ui.select`s (`.mark("select-profile")`/`.mark("select-approval")`)
through a single `@ui.refreshable` form — one page, Save/Cancel, unlike the
wizard's multi-step routing, since there's no first-run narrative to walk
through. `_save()` validates via `configflow.validate_credentials(...,
key_required=False)`, builds the update dict via
`configflow.build_settings_updates(url, key, model, profile, approval_mode)` —
which omits `llm_api_key` entirely when `key` is blank, the same
blank-key-preserves-existing rule as `host/app.py`'s `ConfigScreen` — and saves
through the shared `forms.save_with_plaintext_guard(..., button_label="Save",
recovery_action="cancel")`. `host/web/main.py` mounts it at `@ui.page("/settings")`,
calling `build_settings_view(on_done=lambda: ui.navigate.back())` inside
`app_shell()`; the sidebar's Settings button (`host/web/shell.py`) is what
routes here from any screen.

**`host/web/query_view.py`** (U7) — the query page's UI, `nicegui`-importing (like
`dialogs.py`/`steplog.py`/`shell.py`). `build_query_view(shell, session)` renders a
conversation column (`ui.chat_message`, reusing the same idiom `run_page` already
uses for organize turns) plus a question input/Ask button
(`.mark("query-input")`/`.mark("btn-query-ask")`) that echoes the question as a
`user`-speaker turn (`session.add_turn`) and pushes it onto `session.messages` —
and, below a separator, a step-log strip
(`host.web.steplog.sync_steps`/`StepLogState`, the same T5/T6 idiom `run_page`
established) instead of the TUI `QueryScreen`'s side-by-side dual-`RichLog` split.
Phase 20 is parity with a cleaner surface, not a redesign, and the log strip
already *is* the web UI's "tool timeline". No approval/cost dialog wiring at all —
query mode is read-only by construction (`QUERY_ALLOWED_TOOLS`), so there is
nothing to gate. A `ui.timer` (same `web_session.REFRESH_INTERVAL` cadence as
`run_page`) drives `_refresh()`, which renders new transcript turns, syncs the
step log, and updates the status/token line.

**`host/web/main.py`** (extended by T5/T6/T7/T8, U2/U3/U4/U6/U7) — now shares nicegui-importing duties
with `host/web/shell.py` (T2). Pages are registered at import time (`@ui.page("/")`,
`@ui.page("/run/{run_id}")`, `@ui.page("/query/{run_id}")` (U7)) but nothing binds a port until `run_web(target: Path |
None = None)` is called, so importing the module is side-effect-free. Each
connected browser tab polls `RunSession`/`TranscriptItem`/`StepRecord` state with
its own `ui.timer`, rather than the bridge touching NiceGUI elements directly —
this is what lets a page reload re-attach to an in-flight approval/cost dialog (via
`session.pending`) instead of orphaning it. The browser tab title (T7) comes from
`host.web.theme.window_title`: `run_web`'s `ui.run(...)` call passes it (with no
target) as the global default title, and `run_page` calls
`ui.page_title(theme.window_title(session.target))` from inside the page body —
not via `@ui.page(title=...)`, which is bound at decoration/import time and can't
see the per-request session's target — so the run's target directory lands in the
title of the very first HTML response. `index_page` (the picker route) never calls
`ui.page_title()`, since no directory is "selected" until a run exists.

Both page bodies now open with `with app_shell(...) as shell:` (T2), mounting the
persistent sidebar before any page-specific content. The landing page (`/`, S5)
checks `config.settings.is_configured()` first: if unconfigured, it navigates to
`/setup` (`host/web/wizard.py`'s `build_setup_wizard`, U2) instead of any picker,
rather than the pre-U2 plain message pointing the user at the Textual TUI.
`/settings` (`host/web/settings.py`'s `build_settings_view`, U3) is registered
the same thin-shell way, reachable from the sidebar on every route. As of U7,
`/query/{run_id}` is registered the same thin-shell way too: it looks up the
query-mode `RunSession`, calls `QueryBridge(session).start()` on first mount if
not already started (TUI parity: `QueryScreen.on_mount` auto-starts its worker
too, no explicit "start" button), and delegates rendering to
`host.web.query_view.build_query_view`. Once configured, folder selection is the
sidebar tree, which now doubles as the collapsible directory picker (T3,
superseding the flat browse-view half of Phase 20's planned U1 — see the ROADMAP
note there): clicking a node sets `shell.selected`, which may now be a file since
the tree lists files as well as folders, so the "Use selected directory" button
only starts a run when `shell.selected.is_dir()`. This replaced S4/S5's flat
one-button-per-folder `_list_subdirs` browser (a `Path.iterdir()` walk offloaded
via `run.io_bound`), which T2 had already removed outright pending T3's real tree.

The run page (`/run/{run_id}`, S5) opens on a starter pane — hidden once
`session.started` — showing a directory overview (`host.paths.directory_overview`,
also dispatched via `run.io_bound`) and an optional free-text steering-instructions
input mirroring the Textual TUI's pre-analysis steering box. Only its
"Start organizing" button constructs `AgentBridge(session)` and calls
`start(instructions=...)`, which is what actually launches the agent task; S4's
version began the run as soon as a directory was picked. Once `session.started`
flips, the starter pane hides and the main view (status/progress-bar/chat-input/
approval-cost-dialog) takes over.

As of T5/T6, that main view splits telcontar's own tool activity out of the chat
stream into two independent zones: `conversation_column` (turns only,
`ui.chat_message`, rendering `session.transcript` exactly as S4 did) and, below a
separator, an `activity_label` (`session.activity`, the current narration line)
plus a pinned-bottom, scrolling `log_column` (`max-height: 25vh`). As of U4, the
log column's rendering (row-building, glyphs, the truncating cap) is no longer
inline here — it's `host/web/steplog.py`'s `sync_steps`/`StepLogState`, above;
`run_page` just owns a `steplog.StepLogState()` instance and calls
`steplog.sync_steps(log_column, shell, step_log_state, session.steps)` once per
tick. `run_page`'s `with app_shell(...) as shell:` captures the `Shell` handle so
`steplog.render_step_row` can reach `shell.show_detail()`.

**Journal toolbar affordance (U6):** `run_page` renders a "Journal (N)" button
(`.mark("btn-open-journal")`) above `starter_column`, so it's usable before a
run even starts — TUI parity with the `j` keybinding, which works from the
moment `OrganizerScreen` mounts. Clicking it opens `host.web.dialogs.build_journal_dialog(session)`.
Its label's count is read via `host.web.journal.load_entries` — synchronously,
not via `run.io_bound` (see `host/web/dialogs.py` above) — both on initial
render and again inside `_refresh()`'s `fs_revision`-changed branch, alongside
the existing sidebar-tree rebuild, so the count refreshes whenever the target
directory's contents change (including from an undo, which also bumps
`fs_revision`).

**Query button (U7):** the run page's main view also renders a "Query this
corpus" button (`.mark("btn-query-corpus")`), hidden until `session.done` —
mirroring the TUI's `OrganizerScreen`'s `g` keybinding, which is gated the same
way. Clicking it creates a new query-mode session
(`web_session.create(session.target, mode="query")`) and navigates to
`/query/{run_id}`.

**Dialogs (U4):** `_show_pending_dialog`'s inline checkbox/button-building code is
gone too — it now just tracks which `pending.request_id` has already been shown
(`_RenderState.shown_request_id`) and, on a new one, calls
`host.web.dialogs.build_approval_dialog`/`build_cost_dialog` and `.open()`s the
result; the dialog's own Approve/Refine/Reject buttons resolve
`session.pending` directly (see `dialogs.py` above).

**Sidebar tree refresh (U4):** `_RenderState` gained an `fs_revision: int` field
(the step log's own render cursor moved out to `steplog.StepLogState`, above).
`run_page`'s `_refresh()` is now `async`; on each tick, if `session.fs_revision`
has changed since `render_state.fs_revision`, it reads the tree's currently-
expanded node ids off its Quasar `expanded` prop (`shell.tree.props.get(
"expanded")`, kept in sync by `shell.py`'s `on_expand` handler), rebuilds the node
list via `await run.io_bound(web_tree.rebuild_nodes, session.target, expanded)`,
and — guarding against `run.io_bound` returning `None` on shutdown/cancel —
replaces `shell.tree.props["nodes"]` and calls `shell.tree.update()`. This only
runs when a tree-mutating tool actually closed since the last tick (see
`bridge.py`'s `_TREE_MUTATING_TOOLS` above), never on every 0.5s poll, and never
collapses whatever the user had expanded.

`_pick_port()` binds an ephemeral `127.0.0.1` port. `run_web` calls
`ui.run(host="127.0.0.1", port=port, show=True, reload=False, dark=True,
favicon=theme.FAVICON_SVG)` — `reload=False` is required, not stylistic:
`reload=True` forces uvicorn onto a `SelectorEventLoop` on Windows, where
`asyncio.create_subprocess_exec` (the MCP server subprocess launch) raises
`NotImplementedError`. `dark=True` is likewise load-bearing (T8): Quasar only
honours the `dark`/`dark_page` palette tokens in dark mode. Before `ui.run()`,
`run_web` applies the visual identity globally and exactly once: `app.colors(
**theme.PALETTE)` (never a per-page `ui.colors()`, which would silently
override this and fragment the identity across routes), `app.add_static_files(
theme.FONT_URL_PATH, theme.FONT_DIR)` when the vendored-fonts directory exists,
and `ui.add_css(theme.css(), shared=True)`. As of U7, `run_web`'s
`@app.on_shutdown` hook also calls `session.task.cancel()` for every session,
alongside its existing pending-future rejection — an organize or query session's
MCP server subprocess previously had no lifecycle at all past shutdown, since
nothing ever calls `web_session.close()`. A full lifecycle/reaper is still future
work; this is minimal hardening only.

Note: the ROADMAP text for S5 also names `_load_profile_options` (now
`host.configflow.profile_options`), journal reads, and `server.tools.undo_last` as
blocking calls to move off the event loop. As of U2, `profile_options()`'s one call
site — `host/web/wizard.py`'s `build_setup_wizard` — goes through
`await run.io_bound(configflow.profile_options)`, closing that gap. As of U6,
journal reads (`host.web.journal.load_entries`) and `undo_last`
(`host.web.journal.do_undo`) get their first real call sites — the Journal
toolbar button and its dialog above — but deliberately **do not** go through
`run.io_bound`/`asyncio.to_thread`: under NiceGUI's headless test harness, an
executor-callback continuation invoked from inside a click handler on a dialog
opened from *another* dialog's own click handler never resumes (confirmed by
direct experiment; documented as gotcha #6 in `tests/test_web_ui.py`'s module
docstring). Both operations are fast (a single small JSONL file) and rare (an
explicit, deliberate user click, never the poll timer), so a brief synchronous
stall is imperceptible — unlike this section's original motivating cases (a
full directory walk, a Windows keyring round-trip that can take seconds). See
`build_journal_dialog`'s docstring in `host/web/dialogs.py` for the same
rationale in place.
