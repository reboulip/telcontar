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

The MCP host package. Drives the GPT-5 agent loop and presents the Textual TUI.

### `host/main.py` (~9 lines)

**Role:** CLI entrypoint. Instantiates `OrganizerApp` and calls `.run()`.

**Entry point:** `main()` is registered as the `telcontar` script in `pyproject.toml`.

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
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step; `EventKind` includes `"ask_user"` (P8) for the chat checkpoint — merges the former `"question"`/`"options"` kinds — `"progress"` for the O5 document-analysis progress tracker (`data={"analyzed": int, "total": int}`; drives the O6 `OrganizerScreen` progress bar), `"cost_estimate"` for the pre-analysis cost-approval gate (O8/P6), and `"tokens"` for running LLM token-usage updates, alongside `"thinking"`, `"tool_call"`, `"tool_result"`, `"plan_ready"`, `"done"`, `"error"`. `"tool_call"` events carry `data={"tool": name}` in both the organize and query loops, so callers can key off the tool name (e.g. `OrganizerScreen._narrate`, F10) without parsing `text`
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str], refinement: str | None}`. `refinement` (L6) carries free-text plan-editing feedback from the `ApprovalModal`'s Refine button; when set, the plan is not executed even though `approved` is `False` — see `_handle_execute_plan` below
- `AskUserResult` (P8) — `{reply: str, provided: bool}`; the user's raw chat reply to an `ask_user` checkpoint call — however many questions/options were asked, the whole reply is one free-text string. `provided` is `False` when no reply was captured (degenerate/no-callback case), in which case the agent proceeds with its own best judgement. Replaces K1's `ClarificationResult` (`{answers: dict[str, str], provided: bool}`) and L7's `OptionsResult` (`{selections: dict[str, str], provided: bool}`)
- `CostApprovalResult` — `{approved: bool}`; the user's yes/no on the pre-ANALYZE cost-estimate gate (O8)
- `PrepassResult` — `{new: list[dict], known: list[dict], rehomed: list[str], errors: list[dict], total_files: int, sizes: dict[str, int]}`; the outcome of `run_prepass` (P4) — `new` is `{path, checksum}` per undiscovered document, `known` is `{path, checksum, record}` per already-registered document, `rehomed` lists checksums whose registry path was corrected, `sizes` maps `path -> size_bytes` for every discovered file (P5, additive — populated straight from `walk_tree`'s entries so the new-docs-only cost estimate below has sizes to work from without a second discovery pass)
- `EventCallback` — `Callable[[AgentEvent], None]`
- `ApprovalCallback` — `Callable[[str, dict], Awaitable[ApprovalResult]]`
- `AskUserCallback` (P8) — `Callable[[list[dict]], Awaitable[AskUserResult]]`; each item is `{"text": str, "options": [str, ...]}` (`"options"` omitted for an open question). Replaces K1's `QuestionsCallback` and L7's `OptionsCallback`
- `CostApprovalCallback` — `Callable[[str, dict], Awaitable[CostApprovalResult]]`; given the summary text plus `{"new": int, "already_analyzed": int, "estimated_tokens": int}` (P8 finishes the data-shape migration P6 deferred — the dict originally carried `{"documents": int, "estimated_tokens": int}`)

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
- `run_agent_loop(target, settings, llm, session, on_event, on_approval_needed, on_ask_user_needed=None, on_cost_approval_needed=None, project_root=None, instructions=None, history=None, message=None, message_queue=None, ledger=None) -> tuple[str, list[dict]]` — the actual GPT-5 tool-calling loop for organize mode (injectable session for testing). `ledger` (R1, GH #27) lets a caller pass the same `_TokenLedger` across a run and its O7 follow-up continuations so the running token totals persist across turns instead of resetting to zero on every call; when `None` (the default — tests, one-shot callers), a fresh ledger is constructed for just this call. **ORGANIZE-only loop, pre-pass + analyzer wiring (P6):** on a fresh run (`history is None`), before any turn happens, this now runs `run_prepass` (P4) to partition the corpus into known/new documents, fires the cost-approval gate (`_handle_cost_approval`, O8/P6) scoped to only the new documents if there are any, runs `_analyze_new_documents` (P5) on approval, then seeds the conversation's first user message with a compact corpus digest (`_build_digest`) instead of blank "please organize" instructions — `instructions` (the user's optional pre-analysis steering text from the `OrganizerScreen` starter pane, L3) is appended to that same seed message when non-empty. The turn loop that follows discovers tools via `_discover_openai_tools(session, denied=ORGANIZE_DENIED_TOOLS)`, structurally excluding content-fetching/recording tools already used by the pre-pass/analyzer, and a defense-in-depth dispatch check rejects any hallucinated call to one of those tools even though none are advertised. `on_ask_user_needed` (P8) wires the unified chat checkpoint, unlimited per run; `on_cost_approval_needed` wires the O8/P6 pre-analysis cost-approval gate, now scoped to new documents only. **Resumable chat (O7):** when `history` is given (the list returned by a previous call), none of the pre-pass/analysis/digest work above repeats — the existing history is reused as-is and `message` — a new free-text user turn — is appended before resuming, so a run that finished, errored, or hit the turn ceiling can be continued with the same ORGANIZE-only toolset. A continuation gets its own fresh per-call turn budget and a fresh, empty `_ProgressTracker` (no new pre-pass happens), so its adaptive budget floors at `_MAX_TURNS` rather than reflecting the initial pass's corpus size. **Live mid-run chat (P7):** when `message_queue` is given, it's drained non-blockingly via `_drain_message_queue` at three points — before the first LLM call, after every turn's tool-call batch, and when the response carries no tool calls (the point that would otherwise end the run) — each drained message is appended as a user turn, and in the last case the loop `continue`s instead of returning if anything was waiting, so a live chat message can redirect an in-progress run. `message_queue=None` (the default) is byte-for-byte the pre-P7 behaviour; the mechanism is independent of and composes with `history`/`message`. `ask_user` (P8) blocks on this same queue for its reply, rather than a modal. The whole turn loop is wrapped in `try`/`except`: an unhandled exception is caught rather than propagating — any tool call left without a matching tool-result message is answered with a synthesized `{"error": ...}` entry (so `messages` stays valid for a follow-up call), an `"error"` event fires, and `(error_text, messages)` is returned
- `_drain_message_queue(message_queue) -> list[str]` (P7) — non-blocking drain of `message_queue` (an `asyncio.Queue[str] | None`): repeatedly calls `get_nowait()` until `asyncio.QueueEmpty`, returning drained messages in arrival order, or `[]` immediately if `message_queue` is `None` or nothing is waiting. Never blocks the turn loop
- `run_query(question, settings, llm, on_event, history, target=None)` — convenience entry for one query, launching its own MCP session; `target` (the analyzed corpus's directory) is passed through to `mcp_session` so the server confines its read-only tools' path arguments (M2)
- `run_query_loop(question, settings, llm, session, on_event, history, project_root, ledger=None)` — read-only tool-calling loop; threads `history` across calls for multi-turn context; returns `(answer, updated_history)`. `ledger` (R1, GH #27) works the same way as `run_agent_loop`'s: pass the same `_TokenLedger` across a chat's questions so the running total persists for the whole `QueryScreen` session instead of resetting per question; `None` (the default) constructs a fresh one for just this call
- `_discover_openai_tools(session, allowed=None, denied=None)` — lists MCP tools and converts to OpenAI function specs; when `allowed` is given, only tools in the set are exposed (used by query mode); when `denied` is given (P6), tools in the set are excluded instead (used by organize/ORGANIZE mode, `denied=ORGANIZE_DENIED_TOOLS`) — the two parameters are independent filters, not mutually exclusive
- `_build_system_prompt(project_root, settings)` — assembles the organize-mode system prompt from the active profile, including one "Optional chat checkpoint" paragraph (P8) referencing `ask_user` — replaces the former separate clarification-checkpoint and multiple-option-checkpoint paragraphs
- `_build_query_system_prompt(project_root, settings)` — assembles the read-only query-mode system prompt from the active profile
- `_handle_execute_plan(...)` — intercepts `execute_plan` calls to insert the approval gate before forwarding to the server. Fetches the plan via `get_plan`, writes its full ops (plan id, rationale, folder notes, ops) to `.organizer/plan_ops.json` via `_write_ops_json` and attaches the path as `ops_json_path` on the event data, then awaits `on_approval_needed`. If the returned `ApprovalResult.refinement` is set (non-blank), the plan is NOT approved or executed — the tool result instead carries the refinement text back to the agent as a note instructing it to revise the plan (ops/rationale/folder notes) and call `execute_plan` again. Otherwise falls back to the plain approved/rejected path
- `_write_ops_json(plan_data, plans_dir)` — writes `{plan_id, rationale, folder_notes, ops}` to `<plans_dir>/../plan_ops.json` (i.e. `.organizer/plan_ops.json`), latest-plan-wins; returns the path, or `None` on an `OSError`
- `_handle_ask_user(*, args, on_event, on_ask_user_needed) -> Any` (P8) — intercepts calls to the host-side `ask_user` tool; merges K1's `_handle_clarification` and L7's `_handle_options` into one handler. Drops malformed/empty items, emits an `"ask_user"` `AgentEvent` with the well-formed questions, and awaits the callback; no once-per-run guard (unlimited calls per run); never raises — degenerate input (no callback wired, no well-formed questions, no reply captured) returns a note telling the agent to proceed with its own best judgement
- `_handle_cost_approval(*, doc_count, already_analyzed, estimated_tokens, settings, on_event, on_cost_approval_needed) -> bool` (O8/P6, `already_analyzed` added P8) — relocated from a mid-loop tool-call interception to a one-time gate run once before `_analyze_new_documents` processes any new documents; emits a `"cost_estimate"` `AgentEvent` with `data={"new": doc_count, "already_analyzed": already_analyzed, "estimated_tokens": estimated_tokens}` (from `_new_docs_cost_estimate`, new-docs-only, plus the known-doc count), and — unless `approval_mode == "never"` or no callback is wired — awaits `on_cost_approval_needed`. Returns `True` if analysis should proceed; the event is always emitted, even when auto-approved, for observability
- `_TokenLedger` (dataclass, R2/GH #27) — replaces the old free-standing `_accumulate_tokens`/`token_totals` pair; tracks a run's running token totals and persists a per-call entry to `host/tokenlog.py`'s log as it goes. Fields: `log_path`, `model`, `run_id` (`uuid.uuid4().hex[:12]`), `calls`, `totals` (`{in, out, cached_in}`). `_TokenLedger.new(settings)` builds one from a `Settings` instance, guarding with `isinstance` checks against `token_log_path`/`llm_model` not being a real `Path`/`str` (e.g. a `MagicMock` in tests that stub settings wholesale). `.record(response, *, phase, step, on_event, docs=None, est_in=None)` reads `response.usage.prompt_tokens`/`completion_tokens` (and `prompt_tokens_details.cached_tokens` when present) after an LLM call (organize, query, and analyzer calls alike), folds them into the running totals with a **phase-aware policy for `totals["in"]`** (R1, GH #27): for `phase="analyze"` each batch's `prompt_tokens` is still summed — analyzer calls are independent, throwaway per-batch conversations with no shared history, so their prompt counts are genuinely additive; for every other phase (`"organize"`, `"query"`) `totals["in"]` is instead **replaced** with the latest call's `prompt_tokens` — confirmed against a real API journal that within one growing multi-turn conversation the endpoint's `usage.prompt_tokens` is already a cumulative session-wide total (the whole resent history so far), so summing it across turns was compounding an already-cumulative number. `totals["out"]` always sums (a fresh per-call value) and `totals["cached_in"]` always sums across every call regardless of phase. Appends a `TokenLogEntry` (swallowing `OSError`) and emits a `"tokens"` `AgentEvent` whose text is `_fmt_tokens`'s compact rendering of the running totals, now including the cumulative cached count (e.g. `"42.3K in (5.0K cached) / 5.1K out"`), and whose `data` carries the running `{in, out}` totals, this call's own `cached_in`/`call_in`/`call_out`, and a cumulative `total_cached_in`; a no-op when the endpoint's response omits `usage` or reports non-int counts. `.log_estimate(*, step, docs, est_in)` writes a `phase="estimate"` entry (no event) at the pre-analysis cost-approval gate, so the estimate-vs-actual gap is auditable from the same log. Threaded through as `ledger` in `_analyze_batch`/`_analyze_new_documents` (`phase="analyze"`), `run_agent_loop`'s ORGANIZE loop (`phase="organize"`), and `run_query_loop` (`phase="query"`) — as of R1, callers (`OrganizerScreen`, `QueryScreen`) construct one ledger per screen/session and pass it into every call via the new `ledger` parameter, so the running totals persist for that screen's whole lifetime instead of resetting on each call
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
| `SetupScreen` | First-run wizard: welcome → AI service choice → URL + API key → document profile → done. Saves via `save_user_config()` / OS keyring. Transitions to `StartupScreen` when complete |
| `ConfigScreen` | Settings panel accessible at any time from `StartupScreen`. Fields: URL, API key (password input), document profile (Select), approval mode (Select with friendly labels). Saves back to `~/.telcontar/config.env` via `save_user_config()` |
| `StartupScreen` | Lets the user browse and pick the target folder via a `DirectoryTree` (`#target-tree`, rooted at `Path.home()`); the selected path (defaults to home) is shown in a "Selected: …" label and used by "Organize" and "Query". Offers "Organize", "Query", and "⚙ Settings" buttons. Keybinding `s` opens `ConfigScreen` (the app-level `ctrl+s` binding, P9, also opens it from here and every other screen). "Query" (P2) resolves the corpus via `_find_organizer_root(target)`, walking up from the selected folder through its parents until one containing a `.organizer` is found (a subfolder of a previously-organized tree still resolves to that tree's memory), showing an error if none is found |
| `OrganizerScreen` | Main view. Opens on a **starter pane** (`#starter-pane`, L3) instead of auto-starting the agent: a `Static` rendering `_directory_overview(target)` — a code-generated, deterministic scan of names/structure only (file count, subfolder count, most common extensions; no content read, no LLM), excluding `.organizer` and the quarantine folder (P2, via `_quarantine_basename()`) from its own local `os.walk` the same way `walk_tree` does — plus an `#instructions-input` `Input` for optional free-text steering instructions and a `#proceed-btn` "Start organizing" button (or `Input.Submitted`). `_start_organizing()` hides the starter pane, shows `#main-split` (file-tree sidebar + a single chat-transcript `#conversation-pane`, `VerticalScroll`), and launches `_agent_worker(instructions)` as a Textual worker, passing the typed instructions (if any) through to `run_agent_loop(..., instructions=...)`. `_add_turn(speaker, text)` appends speaker-differentiated turns (`telcontar` / `you`) as styled `Static` widgets — the target line and any typed instructions are shown as the first turns; on each `tool_call` event, `_narrate(tool)` looks up the tool in the module-level `_TOOL_NARRATION` map and, if the macro-task phrase changed, emits a `telcontar` turn (e.g. "Reading documents…", "Planning changes…", "Applying the plan…") — deduping so consecutive calls in the same macro-task collapse to one turn. The raw tool calls/results themselves are appended via `_append_step(line)` into a click-to-expand `Collapsible` ("internal steps") interleaved in the transcript; a new speaker turn closes the currently-open group so the next tool call opens a fresh one. Below `#main-split`, a docked `#ops-journal` `RichLog` (L4, `wrap=False`, horizontally scrollable) renders the file operations recorded in the undo journal — one line per entry, newest last, via `_fmt_journal_entry` (the same formatter `JournalScreen` uses); multi-line hard-stop entries collapse to their summary line. `_refresh_ops_journal()` re-reads `.organizer/journal.jsonl` via `_resolve_journal_path` + `server.journal.all_entries` (swallowing read/config errors so the strip just shows nothing rather than breaking the screen) — `_resolve_journal_path`/`_resolve_plans_dir` (P2) resolve against the run's target directory via `Settings().for_target(target)`, rather than an ad-hoc project-root join; it runs on mount, after any tool in `_JOURNAL_WRITING_TOOLS` (now just `{"execute_plan"}` — the only tool left that can mutate the journal via the agent path, per M1) completes, and again on `done`. Below that, a `#progress-row` (O6, a `#progress-label` plus a Textual `ProgressBar`) renders the O5 `"progress"` event: `_update_progress(data)` reveals the row and updates both widgets, but only once a `total > 0` has been seen (an unknown/`None` total is never shown, avoiding Textual's indeterminate spinner); `_hide_progress()` re-hides it — without first snapping to 100% — on both `"done"` and `"error"`, so it disappears once the ANALYZE phase finishes rather than lingering through ORGANIZE. Status bar shows the current phase plus a running token-usage total (`N in (C cached) / M out`) once the LLM reports it, from a single `_TokenLedger` (R1, GH #27) built once via `_TokenLedger.new(settings)` and threaded through the initial `run_agent_loop` call and every subsequent chat-turn call, so the total accumulates for the screen's whole lifetime instead of resetting each call; keybinding `g` pushes `QueryScreen` once organizing completes, `j` pushes `JournalScreen` (the full modal journal view). **Resumable chat (O7):** `_agent_worker` no longer calls the one-shot `run_agent` convenience wrapper — it opens `mcp_session(...)` itself and calls `run_agent_loop(...)` directly, keeping one MCP session (and one subprocess) open across the initial run and every subsequent chat turn. `self._history: list[dict] | None` carries the conversation returned by each call into the next; `self._messages: asyncio.Queue[str]` bridges the synchronous `Input.Submitted` handler on the bottom-docked `#organize-input` into the queue. Submitting echoes the message as a `user`-speaker turn via `_add_turn` before it is queued. `_note_terminal_state()` fires the "press g / keep chatting" cue and the desktop notification only on the *first* `"done"`/`"error"` event (tracked via `self._done`), not on every subsequent chat-turn completion. **Live mid-run chat (P7):** `#organize-input` is enabled right at the start of `_agent_worker`, before the first `run_agent_loop` call, rather than only once a terminal state is reached — both the initial call and every O7 continuation call pass `message_queue=self._messages`, so `run_agent_loop` itself drains and injects queued messages while it runs (see `host/agent.py` above). The worker's own `while True` loop (`await self._messages.get()` then `run_agent_loop(..., history=self._history, message=message, message_queue=self._messages)`) is unchanged in shape and still runs, but now only ever fires for a message that arrives strictly *after* a `run_agent_loop` call has already returned with nothing left pending in the queue — i.e. the agent is fully idle — rather than for every message regardless of timing |
| `QueryScreen` | Chat-style read-only Q&A screen: `RichLog` output + `Input` bar; keeps one MCP session open for the whole chat and threads conversation history across questions; status bar also shows a running token-usage total (`N in (C cached) / M out`) from a single `_TokenLedger` (R1, GH #27) built once and threaded through every question's `run_query_loop` call, so the total persists across the whole chat; settings are resolved via `load_settings().for_target(self._target)` (R1 fix — previously unrebased, so query-mode paths including the token log resolved relative to the process CWD instead of the corpus's own `.organizer/` directory, unlike the organize path); `Esc` pops back to the previous screen |
| `JournalScreen` | Modal view of the full undo journal (newest entries last), opened via `j`. Also the **only place `undo_last` can be triggered** (M1, S1): keybinding `u` calls `server.tools.undo_last` directly — bypassing MCP entirely, same pattern already used to read the journal — and shows a success/error status line; `Esc` or `j` closes it |
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

**Design note:** No Mammouth-specific code is needed — Mammouth is OpenAI-compatible and only requires the `base_url` and `api_key` overrides.
