# Module Reference

Detailed breakdown of every Python module in the codebase. For auto-generated API docs (docstrings, signatures), see the [API Reference](../../reference/api/server.md).

This page covers `config/`, `server/`, and `host/`'s core (non-web) modules. For the NiceGUI web UI package (`host/web/`), see [Module Reference — Web UI](web-ui.md).

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

**Design note:** This module is deliberately thin — it delegates all logic to `server/tools.py`. Tool parameters injected from config (e.g. `plans_dir`, `journal_path`) are resolved here and passed into the tool functions. `_confinement_roots(cfg)` and `_check_within_root(path, cfg)` (M2) wrap `server/guards.py`'s `check_within_root` and are called at the top of every path-taking tool handler to confine it to `[cfg.target_dir, Path.cwd()]`. The batch tools (O1) apply this — plus `check_allowlist` for the two content tools — per path, before delegating to `server/tools.py`, so a rejected path becomes that entry's `{"error": ...}` instead of aborting the whole call. The `walk_tree` handler additionally passes `hidden_names={os.path.normcase(".organizer"), os.path.normcase(cfg.quarantine_dir.name)}` (P2, case-normalized as of X8) into `tools.walk_tree`, so the agent's own memory folder and the quarantine folder are excluded from every discovery result at every depth regardless of on-disk casing — now that both live inside `target_dir` (see `config/settings.py`'s `for_target` above); `server/tools.py`'s own hidden-name comparison normcases each entry's `name` to match. `rehome_documents` (P4) applies `_check_within_root` to every value (new path) in its `paths: dict[str, str]` argument before delegating — same per-path confinement pattern as `record_document_batch`. `_check_not_quarantine(path, cfg)` (X8) wraps `server/guards.py`'s `check_not_quarantine_collision` (above) and is called by `propose_rename` (on the computed new path), `propose_move` (on `dest_dir` only — deliberately never the move's own source, since un-quarantining a file is a legitimate move that must stay legal), and `propose_create_dir` (on `path`) — a proposed taxonomy folder can never collide with the server-managed quarantine folder.

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
| Recovery (not MCP tools) | `undo_last` — no longer registered as an MCP tool (M1); called directly by the web UI's journal dialog (`host/web/journal.py`) |
| Registry | `record_document`, `get_document`, `lookup_documents`, `rehome_documents`, `list_documents`, `get_registry`, `find_duplicates`, `find_modified_documents` |
| Event journal | `create_event`, `list_events` |
| Knowledge graph | `build_graph`, `get_graph`, `get_actors` |
| Archive | `archive_document` (no longer an MCP tool; called by `execute_plan` for `archive_document` ops), `list_archived` |
| Quarantine compression | `compress_quarantine` (no longer an MCP tool; called by `execute_plan` for `compress_quarantine` ops) |

**Internal helpers:** `_apply_op` executes a single `PlanOp` against the filesystem, dispatching `rename`/`move`/`quarantine`/`create_file`/`update_file`/`create_dir` directly (`archive_document`/`compress_quarantine` ops are handled inline in `execute_plan` itself, calling the standalone functions above); `_reconcile_op` updates the registry record's path/status after execution; `_load_pending_plan` loads a plan and raises unless it is still `pending`, shared by all five newer `propose_*` functions.

**Design note (O1):** `read_file_batch`/`extract_text_batch`/`compute_checksum_batch` are the batch counterparts of `read_file`/`extract_text`/`compute_checksum` — each loops over its `paths` list, calling the singular function per path and catching any exception into that path's `{"error": str(exc)}` entry rather than letting one bad path abort the whole batch. `server/main.py`'s wrappers apply the same guard sequence per path *before* delegating here, so a guard rejection (allowlist/confinement) also surfaces as a per-path error rather than raising.

**Design note (V10):** `propose_quarantine` takes a new optional `reason: str = ""`, stored stripped in the op's `params` (`{"reason": ...}`) rather than validated — an empty string is accepted the same as any other. The concrete-reason requirement ("duplicate of X", not "unreadable" alone) is enforced only by the ORGANIZE system prompt in `host/agent.py`, not by the server. `host/format.py`'s `quarantine_reason(op)`/`fmt_op` render it at approval time, defaulting to "no reason given" when blank.

---

### `server/plan.py`

**Role:** Plan data model and disk persistence. Defines the state machine, serialization, and plan/op CRUD.

**Key types:**
- `PlanState` — `Literal["pending", "approved", "executing", "done", "failed", "stopped"]`
- `OpType` — `Literal["rename", "move", "quarantine", "create_file", "update_file", "create_dir", "archive_document", "compress_quarantine"]`
- `PlanOp` — dataclass with `op_id` (UUID), `op_type`, `src`, `dst`, `status`, `error`, `retries`, `params: dict | None` (op-specific data that doesn't fit `src`/`dst` — e.g. `{"content": ...}` for `create_file`/`update_file`, `{"reason": ...}` for `quarantine` (V10), `{"checksum": ..., "reason": ...}` for `archive_document`, `{"delete_originals": ...}` for `compress_quarantine`)
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

### `server/profile.py`

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
| `normalize_dir_name(name)` | (X8) Folds a directory name to a locale/case-insensitive comparison key: NFKD-decomposes and drops combining marks (accented variants fold together), casefolds, strips a leading ordering prefix (`"01_"`, `"2. "`), trims, and collapses inner separator runs to a single underscore. |
| `is_quarantine_like_name(name, quarantine_dir_name)` | (X8) True if `name` normalizes to the configured quarantine folder's name, or to a known quarantine/discard alias — `_QUARANTINE_ALIASES`, a fixed set covering French/Spanish/English discard words (quarantine, quarantaine, quarantena, cuarentena, trash, corbeille, poubelle, a_supprimer, to_delete, a_jeter, junk). Whole-normalized-basename match only, never substring — a real taxonomy folder like `"quarantaine_sanitaire"` or `"archives"` stays usable. |
| `check_not_quarantine_collision(dest, quarantine_dir)` | (X8) Raises `ValueError` if `dest`'s basename is quarantine-like (`is_quarantine_like_name`) or `dest` resolves inside `quarantine_dir` itself (catches a nested taxonomy folder proposed *under* quarantine). Proposal-time only — never wraps `propose_quarantine`/`propose_archive_document`/`propose_compress_quarantine`, which legitimately target the quarantine dir. Called by `server/main.py`'s `_check_not_quarantine` from `propose_rename`, `propose_move` (`dest_dir` only, never the move's source), and `propose_create_dir`. |

**Why separate:** These rules are invariants that must hold across multiple tools. Centralising them in one module makes them easy to audit and test independently.

---

### `server/journal.py`

**Role:** Append-only JSONL helpers for the undo journal.

| Function | Description |
|---|---|
| `append(journal_path, entry)` | Appends one JSON entry + newline; creates parent dirs |
| `last(journal_path)` | Returns the last entry without removing it; `None` if empty |
| `all_entries(journal_path)` | Returns all entries in chronological order |
| `pop_last(journal_path)` | Removes and returns the last entry; rewrites the file |

**Design note:** `pop_last` rewrites the entire file minus the last line. For typical journal sizes (hundreds of entries) this is fine; for very large corpora a more efficient structure could be introduced later.

---

### `server/archive.py`

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

### `server/sinks.py`

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

The MCP host package. Drives the agent loop and presents the web UI.

### `host/main.py`

**Role:** CLI entrypoint. Parses arguments and launches the web UI.

**Entry point:** `main()` is registered as the `telcontar` script in `pyproject.toml`.

**Flags:** `--version` (prints the installed version and exits); `--target PATH`
(skips the landing page's directory picker and starts a run for that directory
immediately); `--browser` (`store_true`, V1 — launches the web UI in the system
browser instead of a native window). Unrecognized args are tolerated
(`parse_known_args`) so a bare launch keeps working.

**Design note:** The web UI's `from host.web.main import run_web` import is
deferred until after `main()` prints "Loading telcontar…", so the user sees
something immediately instead of a frozen terminal during the ~1s cost of its
heavier imports (`nicegui`, `mcp`, `openai`, …). As of V1, `main()` passes
`native=not args.browser` to `run_web`, which opens a native `pywebview` window
by default (Windows only, falling back to the system browser otherwise or if
`pywebview` isn't installed — see `host/web/main.py`, [Module Reference — Web UI](web-ui.md)). As of Phase 22
(W1), the Textual TUI (`host/app.py`) and its `--tui` flag were deleted
outright — telcontar now always launches the web UI, with no flag to opt out.

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

**Role:** The async agent loop — both organize and query modes. Fully decoupled from any UI framework — callers supply callbacks for events and approval so the module can be tested without a UI.

**Key types:**
- `AgentEvent` — `{kind: EventKind, text, data}` emitted at each step; `EventKind` includes `"ask_user"` (P8) for the chat checkpoint — merges the former `"question"`/`"options"` kinds — `"progress"` for the O5 document-analysis progress tracker (`data={"analyzed": int, "total": int, "current": list[str]}`; drives the web UI's progress bar (V14, see `host/web/main.py`, [Module Reference — Web UI](web-ui.md)) — `"current"` added V8a, basename(s) of the document(s) the in-flight analyzer batch is currently processing, `[]` when nothing is in flight or on the pre-pass's own snapshot event, which omits the key entirely; `host.format.fmt_progress` renders the dict to a short status string but isn't wired into the web UI, which surfaces `current` itself (V8b) via its own inline formatting in `host/web/main.py`'s `_refresh()` (a `progress-current` label), not through this function), `"cost_estimate"` for the pre-analysis cost-approval gate (O8/P6), and `"tokens"` for running LLM token-usage updates, alongside `"thinking"`, `"tool_call"`, `"tool_result"`, `"plan_ready"`, `"done"`, `"warning"`, `"error"`. `"warning"` (U8) is non-terminal — currently emitted only when `_analyze_batch` retries once, still fails, and skips a batch: the run continues, unlike the three genuinely-terminal `"error"` emitters (the agent loop's own exception path, and the organize/query max-turns backstops). `"tool_call"` events carry `data={"tool": name}` in both the organize and query loops, so callers can key off the tool name (e.g. `host/narration.py`'s `Narrator.narrate`, F10) without parsing `text`
- `ApprovalResult` — `{approved: bool, removed_op_ids: list[str], refinement: str | None}`. `refinement` (L6) carries free-text plan-editing feedback from the approval dialog's Refine button (`build_approval_dialog`, `host/web/dialogs.py`); when set, the plan is not executed even though `approved` is `False` — see `_handle_execute_plan` below
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
- `run_agent_loop(target, settings, llm, session, on_event, on_approval_needed, on_ask_user_needed=None, on_cost_approval_needed=None, project_root=None, instructions=None, history=None, message=None, message_queue=None, ledger=None) -> tuple[str, list[dict]]` — the actual LLM tool-calling loop for organize mode (injectable session for testing). `ledger` (R1, GH #27) lets a caller pass the same `_TokenLedger` across a run and its O7 follow-up continuations so the running token totals persist across turns instead of resetting to zero on every call; when `None` (the default — tests, one-shot callers), a fresh ledger is constructed for just this call. **ORGANIZE-only loop, pre-pass + analyzer wiring (P6):** on a fresh run (`history is None`), before any turn happens, this now runs `run_prepass` (P4) to partition the corpus into known/new documents, fires the cost-approval gate (`_handle_cost_approval`, O8/P6) scoped to only the new documents if there are any, runs `_analyze_new_documents` (P5) on approval, then seeds the conversation's first user message with a compact corpus digest (`_build_digest`) instead of blank "please organize" instructions — `instructions` (the user's optional pre-analysis steering text from the organize view's starter pane, L3) is appended to that same seed message when non-empty. The turn loop that follows discovers tools via `_discover_openai_tools(session, denied=ORGANIZE_DENIED_TOOLS)`, structurally excluding content-fetching/recording tools already used by the pre-pass/analyzer, and a defense-in-depth dispatch check rejects any hallucinated call to one of those tools even though none are advertised. `on_ask_user_needed` (P8) wires the unified chat checkpoint, unlimited per run; `on_cost_approval_needed` wires the O8/P6 pre-analysis cost-approval gate, now scoped to new documents only. **Resumable chat (O7):** when `history` is given (the list returned by a previous call), none of the pre-pass/analysis/digest work above repeats — the existing history is reused as-is and `message` — a new free-text user turn — is appended before resuming, so a run that finished, errored, or hit the turn ceiling can be continued with the same ORGANIZE-only toolset. A continuation gets its own fresh per-call turn budget and a fresh, empty `_ProgressTracker` (no new pre-pass happens), so its adaptive budget floors at `_MAX_TURNS` rather than reflecting the initial pass's corpus size. **Live mid-run chat (P7):** when `message_queue` is given, it's drained non-blockingly via `_drain_message_queue` at three points — before the first LLM call, after every turn's tool-call batch, and when the response carries no tool calls (the point that would otherwise end the run) — each drained message is appended as a user turn, and in the last case the loop `continue`s instead of returning if anything was waiting, so a live chat message can redirect an in-progress run. `message_queue=None` (the default) is byte-for-byte the pre-P7 behaviour; the mechanism is independent of and composes with `history`/`message`. `ask_user` (P8) blocks on this same queue for its reply, rather than a modal. The whole turn loop is wrapped in `try`/`except`: an unhandled exception is caught rather than propagating — any tool call left without a matching tool-result message is answered with a synthesized `{"error": ...}` entry (so `messages` stays valid for a follow-up call), an `"error"` event fires, and `(error_text, messages)` is returned
- `_drain_message_queue(message_queue) -> list[str]` (P7) — non-blocking drain of `message_queue` (an `asyncio.Queue[str] | None`): repeatedly calls `get_nowait()` until `asyncio.QueueEmpty`, returning drained messages in arrival order, or `[]` immediately if `message_queue` is `None` or nothing is waiting. Never blocks the turn loop
- `run_query(question, settings, llm, on_event, history, target=None)` — convenience entry for one query, launching its own MCP session; `target` (the analyzed corpus's directory) is passed through to `mcp_session` so the server confines its read-only tools' path arguments (M2)
- `run_query_loop(question, settings, llm, session, on_event, history, project_root, ledger=None)` — read-only tool-calling loop; threads `history` across calls for multi-turn context; returns `(answer, updated_history)`. `ledger` (R1, GH #27) works the same way as `run_agent_loop`'s: pass the same `_TokenLedger` across a chat's questions so the running total persists for the whole query session instead of resetting per question; `None` (the default) constructs a fresh one for just this call
- `_discover_openai_tools(session, allowed=None, denied=None)` — lists MCP tools and converts to OpenAI function specs; when `allowed` is given, only tools in the set are exposed (used by query mode); when `denied` is given (P6), tools in the set are excluded instead (used by organize/ORGANIZE mode, `denied=ORGANIZE_DENIED_TOOLS`) — the two parameters are independent filters, not mutually exclusive
- `_build_system_prompt(project_root, settings)` — assembles the organize-mode system prompt from the active profile, including one "Optional chat checkpoint" paragraph (P8) referencing `ask_user` — replaces the former separate clarification-checkpoint and multiple-option-checkpoint paragraphs. As of X8, rendering (`_render_system_prompt(profile, project_root, quarantine_name)`, the shared step `composed_system_prompts` also reuses) fills a `{quarantine_name}` placeholder with `_resolve_quarantine_name(settings)` — the configured quarantine folder's basename, falling back to `"_quarantine"` on any lookup failure (e.g. a bare `MagicMock` settings stub in tests) — so the taxonomy-design step's quarantine-collision prohibition and the folder-notes example's quarantine key match the real configured name instead of assuming the default (see `server/guards.py`'s `check_not_quarantine_collision`, above, which the prompt text now describes). Drive-by fix (X8): `_should_skip_discovery`'s quarantine-name comparison is now case-normalized (`os.path.normcase`) on both sides — previously case-sensitive, so a differently-cased configured quarantine dir could leak quarantined files back into corpus discovery on Windows
- `_build_query_system_prompt(project_root, settings)` — assembles the read-only query-mode system prompt from the active profile
- `composed_system_prompts(settings, project_root=None) -> dict[str, str]` (V11) — read-only introspection for the Settings "What telcontar tells the model" panel: renders and returns all three system prompts (`{"organize": ..., "query": ..., "analyze": ...}`) from a single profile load, reusing the same rendering steps `_build_system_prompt`/`_build_query_system_prompt`/`_analyze_batch` already use (factored into `_render_system_prompt`/`_render_query_system_prompt`/`_build_analyzer_system_prompt`) rather than duplicating them or loading the profile three times. `project_root` defaults to the exact expression `run_agent_loop` uses when omitted (`Path(__file__).resolve().parent.parent`) — load-bearing, since `_load_naming_conventions` reads `.organizer/NAMING.md` relative to the repo root, not any run's target directory, so a different default would silently display a prompt telcontar does not actually send. The ANALYZE prompt is rendered for a full `_ANALYZER_BATCH_SIZE`-document batch, illustrative rather than any real run's actual (often smaller) batch size. Deliberately does not reflect the two things composed at runtime from a live run — the corpus digest and the user's own pre-analysis steering instructions — since this view is target-free and must work before any directory has been analyzed
- `_resolved_profile_name(settings, project_root=None) -> str | None` (V11) — the active profile's real name once loaded, or `None` on a load failure. `_try_load_profile` swallows that same failure and prompt-building falls back to a generic "default" profile name so the prompts stay renderable — convenient for the LLM-facing text, but it would hide the failure from a transparency view; this surfaces the same pass/fail outcome instead. Same `project_root` default as `composed_system_prompts`
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

### `host/llm.py`

**Role:** Factory function for the OpenAI-compatible client.

**Key function:** `make_client(settings) -> AsyncOpenAI` — creates an `AsyncOpenAI` instance pointed at `settings.llm_base_url`. For Azure, it also injects `default_query={"api-version": ...}` so the Azure API version parameter is sent on every request.

**Design note:** No provider-specific code is needed for most endpoints — only Azure requires the extra `api-version` query parameter; any other OpenAI-compatible provider (Mammouth, OpenAI, etc.) works with just the `base_url` and `api_key` overrides.

---

### `host/configflow.py`

**Role:** Framework-agnostic (no `nicegui`) configuration-flow logic used by the web UI's setup wizard (U2) and settings view (U3) — one source of truth for profile options, per-service hints, credential validation, approval-mode options, and the plaintext-keyring-fallback warning copy. Originally factored out of the (now-deleted) Textual TUI's `SetupScreen`/`ConfigScreen` so both UIs could share it; as of Phase 22 (W1), the web UI is its only consumer.

**Key functions:**

| Function | Description |
|---|---|
| `profile_options() -> list[tuple[str, str]]` | `[(display_label, profile_id), ...]` for a Select/dropdown; reads TOML files from `profiles/`, falling back to `[("General documents", "is_it_project")]` if the directory can't be found. Originally moved here from the deleted `host/app.py`'s old `_load_profile_options`/`_PROFILE_LABELS`. |
| `validate_credentials(url, key, model, *, key_required) -> str \| None` | Validates url → key → model in that frozen order, returning the first error message or `None`. `key_required=True` is the wizard's stricter first-run case (a blank key is itself an error); `key_required=False` (U3) is the settings view's case — a blank key there means "keep the saved key" — which also changes the URL error's wording to match each screen's existing, test-pinned copy. |
| `build_wizard_updates(url, key, model, profile, service) -> dict[str, str]` | Builds the settings-update dict for the wizard's save step; always includes the API key (the wizard requires one). Adds `llm_api_version` when `service == "azure"`. |
| `build_settings_updates(url, key, model, profile, approval_mode) -> dict[str, str]` | (U3) The settings view's counterpart to `build_wizard_updates` — includes `llm_api_key` only when `key` is non-empty (the blank-key-preserves-existing rule) and, unlike the wizard's dict, carries `approval_mode` instead of a service/`llm_api_version` field (the settings view has no service picker). |
| `plaintext_warning(button_label, recovery_action="go back") -> str` | The shared, plain-text (no Rich/HTML markup — this module is UI-agnostic) warning shown when the OS keyring is unavailable and the user must explicitly confirm a plaintext fallback. `button_label` must match the actual button the user is told to press again — fixes U8's copy bug, where the TUI wizard said `Press "Finish" again` while its button read "Save & continue →". |

**Other exports:** `AZURE_API_VERSION` (`"2025-01-01-preview"`); `SERVICE_HINTS: dict[str, dict[str, str]]` — per-service URL/model hint and placeholder text for `"openai_compatible"` vs `"azure"`, consumed by the web wizard's API-details step; `APPROVAL_OPTIONS: list[tuple[str, str]]` (U3) — the three `(label, value)` approval-mode choices ("Always ask before any changes"/`always`, "Only ask before moving or quarantining files"/`destructive_only`, "Never ask — full automatic mode"/`never`), originally moved out of the deleted `host/app.py`'s `ConfigScreen`, now shared by `host/web/settings.py`.

---

## `host/web/`

The NiceGUI-based web UI package (`host/web/`) has its own dedicated per-module reference, since it makes up the large majority of the host codebase — see [Module Reference — Web UI](web-ui.md).
