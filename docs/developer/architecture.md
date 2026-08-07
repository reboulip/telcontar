# Architecture

Telcontar is a locally-run AI directory organizer built on the **Model Context Protocol (MCP)**. Two Python processes communicate over stdio: a **host** that runs the agent loop and a **server** that owns all file operations.

---

## Component overview

```
User
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  MCP Host  (host/)                                  │
│  ┌──────────────┐   ┌────────────────────────────┐  │
│  │ Textual TUI  │   │  Agent loop (host/agent.py)│  │
│  │ host/app.py  │←→│  - builds system prompt     │  │
│  │              │   │  - tool-calling loop        │  │
│  │ Startup/     │   │  - approval gate            │  │
│  │ Organizer/   │   │  - query loop (read-only)  │  │
│  │ Query/       │   │  - MCP client (stdio)       │  │
│  │ Approval     │   └────────────┬───────────────┘  │
│  │ screens      │                │ stdio transport   │
│  └──────────────┘                ▼                   │
└──────────────────────────────────┼──────────────────┘
                                   │ stdio transport
┌──────────────────────────────────▼──────────────────┐
│  MCP Server  (server/)                              │
│  ┌─────────────────────────────────────────────────┐│
│  │  FastMCP server (server/main.py)                ││
│  │  tool handlers → server/tools.py                ││
│  │                                                 ││
│  │  server/plan.py      plan state machine         ││
│  │  server/registry.py  content-addressed memory   ││
│  │  server/profile.py   domain profile loader      ││
│  │  server/guards.py    no-overwrite / allowlist /  ││
│  │                      target-dir confinement    ││
│  │  server/journal.py   append-only undo log       ││
│  │  server/events.py    project event journal      ││
│  │  server/graph.py     knowledge graph projection ││
  │  │  server/archive.py   archived-documents log ││
│  │  server/sinks.py    output-sink abstraction    ││
│  │  server/extract.py   markitdown text extraction ││
│  └─────────────────────────────────────────────────┘│
│                          │                          │
│              ┌───────────▼──────────┐               │
│              │  Local filesystem    │               │
│              │  .organizer/ state   │               │
│              └──────────────────────┘               │
└─────────────────────────────────────────────────────┘
                          ▲
                          │ API calls
┌─────────────────────────┴───────────────────────────┐
│  OpenAI-compatible endpoint                         │
│  chat completions with tool use                     │
└─────────────────────────────────────────────────────┘
```

---

## Design decisions

### MCP over stdio

The server and host communicate via the [Model Context Protocol](https://modelcontextprotocol.io/) over stdio. This means:

- The server can be replaced or extended without touching the host
- Tests can inject a mock `ClientSession` instead of spawning a real subprocess
- The host discovers available tools dynamically from the server at startup (`session.list_tools()`)

### Content-addressed registry

Documents are identified by their sha256 checksum, not their path. This means:

- Renaming or moving a file does not lose its analysis metadata
- `execute_plan` reconciles paths in the registry as files move
- Duplicate detection is checksum-exact (same content) + title-token fuzzy (similar content)
- `rehome_documents` (P4) offers a second, plan-independent reconciliation path: given `{checksum: new_path}`, it looks a record up directly by checksum and rewrites its path — used by the deterministic host pre-pass (below) to fix up a record whose on-disk location drifted outside any `execute_plan` run

### Plan state machine

File operations are never executed speculatively. The server enforces:

```
pending → approved → executing → done
                               → failed
                   → stopped
```

The host can only call `execute_plan` on a plan in `approved` state. The `approved` transition requires an explicit `approve_plan` call, which the host gates on user approval in the TUI.

### No delete, ever

The MCP server has no delete tool. The `propose_quarantine` / `quarantine` path is the only way to remove files from the working tree. Quarantined files are moved to `QUARANTINE_DIR` and journaled — they can be recovered manually or via `undo_last` (see below).

`compress_quarantine` is the only other operation that removes bytes from disk (the original loose files in `QUARANTINE_DIR`, after a verified archive is produced) — staged via `propose_compress_quarantine` and applied only through `execute_plan`, like every other mutation. It is still fully reversible: `undo_last` restores each original from the archive and deletes the zip. No bytes leave the machine — compression only reclaims space within the local quarantine folder.

### Every mutation goes through the plan flow (M1)

As of the security-hardening pass that closed finding S1, there is no MCP tool that touches the filesystem directly. `move_file`, `rename_file`, `create_file`, `update_file`, `create_dir`, `archive_document`, and `compress_quarantine` were removed as standalone tools; their functionality is reachable only by staging a `propose_create_file` / `propose_update_file` / `propose_create_dir` / `propose_archive_document` / `propose_compress_quarantine` op onto a plan and applying it via `execute_plan` — exactly the same path `propose_rename` / `propose_move` / `propose_quarantine` already used. `execute_plan`'s internal `_apply_op` dispatcher now handles all these op types directly, except `archive_document` and `compress_quarantine`, which are delegated to the pre-existing standalone functions of the same name (avoiding duplicated logic) and self-journal under their own `op_type` rather than the generic per-op entry.

`undo_last` was removed from the MCP tool surface entirely — it is no longer callable by the agent under any circumstance. It survives only as a plain function in `server/tools.py`, invoked directly (bypassing MCP) by the TUI's `JournalScreen` when the user presses **u** — undo is now a deliberate, user-only action, never something the agent itself can trigger.

### Path confinement on every path-taking tool (M2)

As of the security-hardening pass that closed finding S3, `server/guards.py` exposes
`check_within_root(path, roots)`, a fail-closed sibling of `check_allowlist`: an
*empty* `roots` list raises `PermissionError` (the opposite of `check_allowlist`,
where empty means unrestricted). `server/main.py` calls it — via the
`_check_within_root` helper — in every path-taking tool handler (`list_dir`,
`walk_tree`, `read_file`, `extract_text`, `compute_checksum`, `compare_documents`,
`read_file_batch`, `extract_text_batch`, `compute_checksum_batch`, every `propose_*`
tool, `write_index`, `write_summary`, `write_folder_readme`, `record_document`,
`record_document_batch`, `rehome_documents`).
The batch tools apply the check per path, before that path is read/extracted, so
one disallowed path in a batch surfaces as `{"error": ...}` for that entry rather
than failing the whole call. `_confinement_roots(cfg)` builds the allowed roots as
`[settings.target_dir, Path.cwd()]` (target_dir omitted when unset). `target_dir`
is populated from a `TARGET_DIR` env var that `host/agent.py`'s `mcp_session` sets
on the server subprocess whenever a `target` is passed in — i.e. on every real
organize (`run_agent`) or query (`run_query`) session — and `Path.cwd()` remains
in the root list as a floor for the case `target_dir` is unset. As of P2 (below),
`.organizer/*` and the quarantine dir themselves live *inside* `target_dir` for
every real run, so `target_dir` is where the confinement boundary and the run's
own memory now coincide; `Path.cwd()` no longer plays a special role in housing
them. Both `.resolve()`-normalize the candidate path first, so an
absolute escape and a `..` traversal are rejected identically. For the tools
that already run `check_allowlist` (`read_file`, `extract_text`,
`compare_documents`, and the batch forms `read_file_batch`/`extract_text_batch`),
`check_within_root` runs *after* it — the allowlist remains a stricter, opt-in
bound; target-dir confinement is the always-on floor underneath it.

### Injection-resistance delimiter for document content (M10)

Document text returned by `read_file`/`extract_text`, and the `diff` field of
`compare_documents`, is untrusted input sharing the LLM's context with telcontar's
own instructions — a crafted file can embed text that reads as a command (e.g.
"SYSTEM OVERRIDE: ..."). `host/agent.py`'s `_wrap_untrusted_content(result, tool_name)`
wraps that text between `_UNTRUSTED_CONTENT_BEGIN`/`_UNTRUSTED_CONTENT_END` delimiter
markers before it is JSON-serialized into the tool-result message: the whole string
for `_DOCUMENT_CONTENT_TOOLS = frozenset({"read_file", "extract_text"})`, and only the
`diff` field of a `compare_documents` dict result (its other fields — `path_a`,
`path_b`, `identical` — are metadata, left untouched). Any other tool, or an
unexpected result shape, passes through unchanged. It is called at both
tool-result-append sites — `run_agent_loop` (organize mode) and `run_query_loop`
(query mode) — since both modes expose these document-reading tools. The system
prompt's Safety rules section explicitly tells the model that content between the
markers is data, never an instruction, regardless of phrasing. This is a
mitigation, not a sandboxed boundary — it raises the bar against indirect prompt
injection rather than eliminating it; see `docs/developer/security-model.md` (S2).

The batch counterparts `read_file_batch`/`extract_text_batch` (O1) return
`{path: text | {"error": ...}}` rather than a bare string, so wrapping the whole
result would either miss the per-file content or wrap error dicts nonsensically.
`_wrap_untrusted_content` handles `_DOCUMENT_CONTENT_BATCH_TOOLS =
frozenset({"read_file_batch", "extract_text_batch"})` by wrapping each successful
(`str`) entry individually and passing error dicts through unchanged, so every
file's content in a batch carries the same delimiter as a singular `read_file`/
`extract_text` call would. `compute_checksum_batch` is deliberately excluded, same
as the singular `compute_checksum` — a checksum is not untrusted content.

The stateless analyzer (P5/P6, `_analyze_batch`) also wraps each document's fetched
text with the same `_wrap_untrusted` helper directly, since its per-batch messages
list is a throwaway construction outside the two tool-result-append sites above.
Until P6, its system prompt (`_ANALYZER_SYSTEM_PROMPT_TEMPLATE`) wrapped content in
the delimiter but never explained to the model what the delimiter *means* or that
content inside it must never be treated as an instruction — that explanation lived
only in the old ORGANIZE-phase system prompt, which the analyzer's throwaway
messages list never saw. `_ANALYZER_SYSTEM_PROMPT_TEMPLATE` now includes that
explanation directly, closing the gap between the analyzer's mitigation and the
organize/query loops' — see `docs/developer/security-model.md` (S2).

### Recursive tree exploration

`walk_tree(path, max_depth=3)` complements `list_dir` (a single level): it returns a bounded recursive directory listing, where each directory entry carries a nested `children` list until `max_depth` is reached — deeper directories come back with `children: null` and `truncated: true`, signalling the agent to call `walk_tree` again on that subpath to descend further. Files carry `size`/`mtime` like `list_dir`; unreadable entries are marked `type: "unknown"`.

Full-coverage document discovery now happens host-side, in `run_prepass` (P4/P6, above), which re-walks every `truncated` subdirectory to exhaustion before the ORGANIZE-phase model ever runs — this used to be a system-prompt instruction to the model ("you MUST call `walk_tree` again... repeat until no truncated directory remains", O3) for the old in-loop ANALYZE phase, which no longer exists. The ORGANIZE-phase prompt still exposes `walk_tree` (it is not in `ORGANIZE_DENIED_TOOLS`) but now only for the agent to check the *current on-disk layout* while designing its target taxonomy — "call walk_tree if you need to see the current on-disk layout" — and still permits the agent to redesign the existing nested layout entirely, not just what sits at the root.

`server/tools.py`'s `walk_tree` implementation takes an optional `hidden_names: frozenset[str] | None` (P2) that excludes entries by basename at every level; the `server/main.py` MCP wrapper always passes `{".organizer", cfg.quarantine_dir.name}`, so now that both live inside the target directory (see "Per-directory memory (P2)" below), the agent never sees or proposes moving/quarantining its own memory. `list_dir` (the single-level tool) is unaffected — it has no `hidden_names` parameter.

### Per-directory memory (P2)

Each run's memory — the undo journal, event journal, plans, document registry, knowledge graph, archive log, egress log, and the quarantine folder — lives inside the directory being organized (`<target>/.organizer/...`, `<target>/_quarantine`) rather than at telcontar's own project root. `Settings.for_target(target)` (`config/settings.py`) is the one place this rebasing happens: it anchors each of `quarantine_dir`, `journal_path`, `events_path`, `plans_dir`, `registry_path`, `graph_path`, `archive_path`, and `egress_path` at `target.resolve()` when the path is relative, and leaves an already-absolute path (an explicit operator override) untouched. `profiles_dir` and `.organizer/NAMING.md` are deliberately *not* rebased — they are cross-corpus, project-level conventions rather than per-run memory.

- **Server:** `config.settings.load()` calls `for_target(settings.target_dir)` whenever `target_dir` is set — which it always is for a real run, since `TARGET_DIR` is set on the server subprocess by `mcp_session` (see [Path confinement](#path-confinement-on-every-path-taking-tool-m2)). The server's own working directory is unchanged (still telcontar's project root); only the settings paths become absolute and target-anchored.
- **Host:** `host/app.py` re-derives its own settings the same way before starting a run (`load_settings().for_target(self._target)` in `_agent_worker`), and the Journal screen / ops-journal panel / undo action resolve `journal_path`/`plans_dir` against the run's target directory via `_resolve_journal_path`/`_resolve_plans_dir`, both thin wrappers around `Settings().for_target(target)`.
- **Query mode:** since a `.organizer` no longer has one fixed project-root location, `StartupScreen._query` resolves it by calling `_find_organizer_root(target)`, which walks up from the selected folder through its parent directories until it finds one containing a `.organizer`, or reaches the filesystem root without finding one (in which case the user is asked to run Organize first). This means picking a subfolder of a previously-organized tree still resolves to that tree's memory.
- **Discovery hiding:** because `.organizer`/quarantine now live physically inside the organized tree, they must be hidden from the agent so it doesn't propose moving or quarantining its own memory — see `walk_tree`'s `hidden_names` above. `write_index`'s output-file skip set (`_SKIP` in `server/tools.py`) also now excludes `.organizer`, so it never appears in the written `INDEX.md`; the quarantine folder deliberately stays *visible* there, since a human reviewing results should be able to see it — only agent-facing discovery hides it. `host/app.py`'s starter-pane `_directory_overview` applies the same two-name hide to its own local `os.walk`.
- **No migration:** there is no migration path for a pre-existing project-root `.organizer` folder from before this change — a fresh run against a new target simply starts that target's memory from scratch.

This does not change the security model — `target_dir` was already the confinement boundary enforced by `check_within_root` (M2); this only changes where `.organizer`/quarantine physically resolve to *within* that already-covered boundary. See [Security Model](security-model.md) for the confinement mechanism itself.

### Batch document-content tools (O1)

`read_file_batch`, `extract_text_batch`, and `compute_checksum_batch` in `server/tools.py` are batch counterparts of `read_file`/`extract_text`/`compute_checksum`: each takes a `paths: list[str]` instead of a single `path` and returns one dict keyed by the exact input path string, `{path: content_or_checksum | {"error": message}}`. A failure on one path (guard rejection, missing file, extraction error) never fails the whole batch — it just becomes that path's `{"error": ...}` entry, so the caller (host or agent) must discriminate a successful entry from a failed one by type (`str` vs `dict`). The `server/main.py` wrappers apply the same per-path guard sequence as their singular counterparts (allowlist + `check_within_root` for the two content tools, `check_within_root` alone for the checksum tool) before delegating to `server.tools`, and log egress per successful file the same way `read_file`/`extract_text` do.

These tools exist to cut MCP round trips: fetching N files one at a time costs N request/response cycles (and N LLM turns, if the agent reasons between each), while a batch call fetches them all in one. They are read-only and available in query mode (added to `QUERY_ALLOWED_TOOLS`). As of O3, the (now-removed) in-loop ANALYZE-phase prompt directed the agent to use these batch forms (plus `record_document_batch`, O2) itself, working through discovered documents in batches of 10 rather than one document per turn — as of P6, that batching still happens in exactly those group sizes, but is driven host-side by the stateless analyzer's `_fetch_batch_content` (`_ANALYZER_BATCH_SIZE = 10`) rather than by the model's own tool calls; `ORGANIZE_DENIED_TOOLS` excludes `read_file_batch`/`extract_text_batch`/`compute_checksum_batch` from the ORGANIZE-phase model's own toolset entirely (see "ORGANIZE-only agent loop + corpus digest (P6)" below).

### Batch document-registry tool (O2)

`record_document_batch` (`server/tools.py`) is the mutating counterpart to O1's read-only batch tools: it upserts many analyzed documents into the registry in one call instead of one `record_document` call per document. Validation is shared with the singular tool via a factored-out `_validate_and_build_record(doc, profile)` helper, so both enforce identical rules and identical error strings. A validation failure on one document (bad `type`, bad entity `role`, a missing entity `name`) is collected into an `errors` list keyed by the document's positional index rather than aborting the batch; valid documents are still upserted and returned in `recorded`. The registry is loaded once and saved once for the whole batch rather than once per document — an efficiency trade-off that means a mid-batch crash persists nothing.

Because it mutates the registry, it is *not* added to `QUERY_ALLOWED_TOOLS` (query mode stays strictly read-only). Its path-confinement behaviour also diverges from the O1 read-only batch tools: the `server/main.py` wrapper runs `_check_within_root` on every document's `path` before delegating to `server.tools`, and — unlike `read_file_batch`/`extract_text_batch`/`compute_checksum_batch`, which turn a disallowed path into that entry's `{"error": ...}` — a `PermissionError` here propagates and aborts the whole call, since registry validation errors and confinement errors are handled at different layers (`server.tools` vs. the `server.main` wrapper).

### Document-analysis progress tracking (O5, updated by P6, Q2)

`host/agent.py` tracks how many documents have been discovered versus analyzed over a run and emits a `"progress"` `AgentEvent` (text `"Analyzed {analyzed} / {total} documents"`, `data={"analyzed": int, "total": int}`) whenever those counts change. A `_ProgressTracker` dataclass accumulates two path sets, `discovered` and `analyzed` (`total` is their union, so a document recorded without ever surfacing via `walk_tree` still counts, and the total only grows monotonically).

As of P6, both sets are populated **before the ORGANIZE turn loop starts**, from the pre-pass + analyzer results, rather than incrementally from live tool calls during the loop: `discovered` from every file `run_prepass` found (skipping telcontar's own output artifacts, dotfiles, OS junk, `.organizer`, and the configured quarantine directory — mirroring the `_SKIP` precedent in `server/tools.py`'s `write_index`), `analyzed` from `PrepassResult.known` plus whichever new documents the analyzer successfully recorded. As of Q2, `_analyze_new_documents` takes the `_ProgressTracker` directly via a required keyword-only `tracker` parameter and updates `analyzed` — emitting a fresh `"progress"` event right there — **once per analysis batch**, immediately after that batch's `record_document_batch` call returns, rather than accumulating silently across the whole analyzer loop and computing/emitting one snapshot at the end (the old post-loop tracking/emission block in `run_agent_loop`, and the `progress_after_prepass` snapshot variable it compared against, are both gone). So progress now fires once from `run_prepass` (the pre-analysis snapshot of `known`/`total-so-far`), plus once per `_ANALYZER_BATCH_SIZE`-sized batch of newly-recorded documents — giving the TUI's progress bar incremental movement through analysis instead of jumping straight from the pre-pass snapshot to ~100% at the end. Since the ORGANIZE-phase model's toolset structurally excludes `record_document`/`record_document_batch` (`ORGANIZE_DENIED_TOOLS`, see "ORGANIZE-only agent loop + corpus digest (P6)" below), the ORGANIZE turn loop itself still never drives progress incrementally on its own.

This is purely additive to the event stream — no MCP tool signature or tool list changed. As of O6, `host/app.py`'s `OrganizerScreen` consumes the `"progress"` event: a `#progress-row` (a numeric `#progress-label` plus a Textual `ProgressBar`) sits between `#ops-journal` and the status bar, hidden until the first progress event carrying a known `total > 0` arrives (an unknown/`None` total is never shown, since that would trigger Textual's indeterminate spinning-bar mode), and hidden again — without snapping to 100% first — once the run reaches `"done"` or `"error"`.

### Adaptive turn budget (O4)

`run_agent_loop`'s turn ceiling scales with corpus size instead of a flat cap, so a large directory doesn't hit an artificial wall mid-analysis. `_analysis_turn_budget(total_discovered)` returns `max(_MAX_TURNS, min(_MAX_TURN_BUDGET, _TURN_BUDGET_BASE + _TURN_BUDGET_PER_DOCUMENT * total_discovered))` — floor `_MAX_TURNS = 50`, ceiling `_MAX_TURN_BUDGET = 2000`, `_TURN_BUDGET_BASE = 30` plus `_TURN_BUDGET_PER_DOCUMENT = 3` turns per document discovered so far (the O5 `_ProgressTracker`'s `total` count). The loop recomputes the budget every iteration, though as of P6 the tracker's `total` is populated upfront by the pre-pass + analyzer and no longer grows during the ORGANIZE loop itself (which no longer calls `walk_tree`/`record_document` — see O5 above), so in practice the same ceiling — computed once, from the pre-loop pre-pass/analysis totals — holds for the whole ORGANIZE loop of a fresh run. The "reached maximum turns" error event/return string reports the actual computed budget rather than a hard-coded number.

This is a backstop against a misbehaving or looping agent, not the primary cost control — that role belongs to the pre-analysis cost-approval gate (O8/P6), described next. `run_query_loop` (query/chat mode) is untouched and still uses the fixed `_MAX_TURNS = 50` ceiling — see the query data-flow section below.

### Pre-analysis cost-approval gate (O8/P6)

This is the **primary** cost control for an organize run — the adaptive turn budget above is a secondary runaway-loop backstop, not the primary lever. As of P6, the gate is no longer an interception of the first live batch-tool call inside the turn loop (`_COST_GATED_BATCH_TOOLS` was deleted); it fires once, host-side, **before the ORGANIZE turn loop even starts** — between the deterministic pre-pass (`run_prepass`, P4) and the stateless analyzer (`_analyze_new_documents`, P5). `run_prepass` itself always runs unconditionally and is never gated — it walks the whole tree and checksums every file via `compute_checksum_batch`, which is needed just to tell already-known documents from new ones before any cost decision can even be made. The gate decides only whether the analyzer is then allowed to fetch and record the resulting `new` set.

The estimate is scoped to **new documents only**: `_new_docs_cost_estimate(new_docs, sizes, max_snippet_chars) -> (doc_count, estimated_tokens)` (P5) mirrors `_ProgressTracker.cost_estimate`'s chars-per-token heuristic (`sum(min(size, max_snippet_chars) // 4 for size in sizes.values())`, 4 chars/token) but sums only over `PrepassResult.new`'s sizes — a re-run where most of the corpus is already known no longer estimates cost for the whole tree the way the pre-P6 gate did. `_handle_cost_approval` emits a `"cost_estimate"` `AgentEvent` with this estimate, then — unless `settings.approval_mode == "never"` or no `on_cost_approval_needed` callback is wired — awaits the host's `CostApprovalCallback` (`Callable[[str, dict], Awaitable[CostApprovalResult]]`). Rejection skips `_analyze_new_documents` entirely for this run — the new documents are neither fetched nor recorded, and surface in the corpus digest's error/unanalyzed count rather than as recorded documents; approval runs the analyzer normally. The gate fires **at most once per run**, and is skipped entirely — no event, no callback — when `run_prepass` finds no new documents at all.

As of P8, the `"cost_estimate"` event's `data` dict is `{"new": doc_count, "already_analyzed": already_analyzed_count, "estimated_tokens": estimated_tokens}` — `already_analyzed` (the `PrepassResult.known` count) sits alongside the new-doc estimate so the approval summary text reads "N new document(s) (M already analyzed, skipped), ~T input tokens estimated…" instead of leaving the skipped majority of a re-run corpus unmentioned. This completes the data-shape migration P6 deferred (the dict originally carried `{"documents": N, "estimated_tokens": T}`).

`host/app.py` wires the callback to `CostEstimateModal`, whose constructor is `(new_documents, already_analyzed, estimated_tokens, batch_size=10)` — matching the P8 data shape above — and narrates the estimate and the user's choice into the transcript. The status bar shows "Awaiting cost approval…" while the modal is open.

### Resumable chat after a stop (O7)

`run_agent`/`run_agent_loop` take trailing `history: list[dict[str, Any]] | None = None` and `message: str | None = None` parameters and now return `tuple[str, list[dict[str, Any]]]` (`(final_text, updated_history)`) at all three exit points — the no-tool-calls "done" path, the new exception-recovery path (below), and the turn-budget-exhausted path — mirroring the shape `run_query_loop` already used. When `history` is `None` (the default), a run seeds fresh exactly as before. When `history` is given (the list returned by a previous call), it is reused in place and `message` — a new free-text user turn — is appended before the loop resumes, so a run that finished, errored, or hit the turn ceiling can be continued with a new chat message using the same full mutating toolset, rather than only offering read-only query mode.

Each call gets its own fresh per-call turn budget — a continuation does not share the initiating run's allowance. This also means a continuation call starts with a fresh, empty `_ProgressTracker` (no new `walk_tree` survey happens on a continuation), so `_analysis_turn_budget` floors at `_MAX_TURNS = 50` on a continuation rather than reflecting the full corpus size the initial ANALYZE pass discovered — an accepted trade-off, since a follow-up chat message is typically a small, targeted ask rather than a fresh full-corpus ANALYZE.

The whole per-call turn loop is now wrapped in `try`/`except`: an unhandled exception no longer propagates out of `run_agent_loop`. It is caught, and any tool call belonging to the most recent assistant message that is still missing a matching tool-result message (e.g. an earlier call in the same batch already succeeded and appended its tool message before a later one raised) is answered with a synthesized `{"error": ...}` tool-response message, so `messages` stays a valid, resumable conversation for a follow-up call. An `"error"` `AgentEvent` fires and `(error_text, messages)` is returned instead of the exception propagating.

`host/app.py`'s `OrganizerScreen` was restructured off the one-shot `run_agent` convenience call onto `mcp_session(...)` + `run_agent_loop(...)` directly (mirroring `QueryScreen._query_worker`'s established pattern), so **one MCP session stays open across the initial automated run and all follow-up chat turns** instead of being torn down after the first run. `self._history` carries the conversation across calls; a bottom-docked `#organize-input` `Input` feeds an `asyncio.Queue[str]` (`self._messages`) that a submitted message is pushed onto. As of P7 (below), the input is enabled for the entire run rather than only after a terminal state, so this queue is now drained continuously by `run_agent_loop` itself rather than sitting inert until the worker's own resumption loop picks it up; that outer `while True` loop — `await self._messages.get()` then `run_agent_loop(..., history=self._history, message=message, message_queue=self._messages)` — still exists and still runs, but now only ever fires for a message that arrives strictly *after* a `run_agent_loop` call has already returned (i.e. the agent is fully idle and no live call is running to drain the queue itself). `_note_terminal_state()` fires the "press g / keep chatting" cue and the desktop notification only on the *first* terminal state (`self._done`), not on every chat-turn completion. The `g` keybinding (opens the read-only `QueryScreen`, on its own separate MCP session) is unchanged and still gated on `self._done` — it coexists alongside the in-place chat box: `g` for a clean read-only Q&A session, `#organize-input` for mutating continuations of the same conversation.

### Live mid-run chat (P7)

O7 above only let a chat message reach the agent once a `run_agent_loop` call had fully returned — a message typed while the agent was still actively working sat inert in `self._messages` until the whole run stopped. P7 makes the chat box live for the *entire* run instead: `run_agent_loop` gains a trailing `message_queue: asyncio.Queue[str] | None = None` parameter, and a new helper, `_drain_message_queue(message_queue) -> list[str]`, does a non-blocking drain (`queue.get_nowait()` in a loop until `asyncio.QueueEmpty`) — it never blocks the turn loop, and returns `[]` immediately when `message_queue` is `None` (the default, making this fully additive: existing callers that don't pass it get byte-for-byte the old behaviour).

The queue is drained at three points in `run_agent_loop`:

1. **Before the loop's first LLM call**, on a fresh run — catches anything typed during the pre-pass/analyzer phase, which can take a while on a large corpus.
2. **After every turn's tool-call batch completes**, before the next LLM call — catches a message typed mid-turn.
3. **When the LLM's response carries no tool calls** — the point that would normally end the run (see the "done" path in the data-flow section below). If the queue is empty here, the run ends exactly as before. If a message is waiting, it's appended as a new user turn and the loop `continue`s instead of returning — so a live chat message can redirect an in-progress run instead of only being picked up after it stops.

Drained messages are appended to `messages` as ordinary `{"role": "user", ...}` turns, the same shape as O7's `message` parameter — the LLM sees no difference between a message injected this way and one appended via a fresh `run_agent_loop(..., message=...)` call.

This mechanism is **independent of and additive to** O7's history/message resume contract: O7 handles a message that arrives after a call has already returned (no live queue exists to drain at that point — the agent is idle), while P7 handles a message that arrives while a call is still running. `host/app.py` wires `message_queue=self._messages` into *both* the initial `run_agent_loop` call and every O7 continuation call, so a continuation stays just as live as the original run — and enables `#organize-input` right at the start of `_agent_worker`, before the first call, rather than only after a terminal state.

### Knowledge graph

`server/graph.py` projects the registry and event journal into a node/edge graph persisted at `GRAPH_PATH` (`.organizer/graph.json`). The graph is a pure, reproducible derivation — no independent state. Node kinds: `document` (one per registry record), `entity` (deduplicated person/org by normalized name), `event` (one per recorded event). Edge types: doc→entity (role-typed), entity↔entity `co_occurrence` (weighted by shared documents), event→entity `mentions` (entity name found in event sentence). Exposed via `build_graph` (rebuild + persist + return), `get_graph` (return last persisted), and `get_actors` (entity nodes ranked by centrality, capped at `salient_cap`).

`rank_actors` scores entities by: document count (primary), total co-occurrence weight, then event-mention count, with a deterministic lowercased-name tie-break. The cap comes from the active profile's `[entities].salient_cap` field and is enforced in the tool itself.

### Three distinct journals

Telcontar maintains three append-only JSONL logs — each with a different purpose:

| Journal | Path | What it records | Drives |
|---|---|---|---|
| **Undo journal** | `JOURNAL_PATH` (`.organizer/journal.jsonl`) | Executed file operations (rename, move, quarantine, compress) | `undo_last` |
| **Event journal** | `EVENTS_PATH` (`.organizer/events.jsonl`) | Project narrative — verb-led, dated milestones | `list_events`, `build_graph` |
| **Archive log** | `ARCHIVE_PATH` (`.organizer/archive.jsonl`) | Documents withdrawn from active memory: why and where the file went | `list_archived` |

`archive_document` writes to both the undo journal (the file move, so it stays reversible) and the archive log (the reason a document left memory). These two writes serve different purposes and are never merged.

The paths above are relative defaults; as of P2, `Settings.for_target` anchors them at the run's target directory rather than telcontar's project root — see [Per-directory memory (P2)](#per-directory-memory-p2) above.

### ask_user chat checkpoint (P8)

At any point before or while building the plan, the agent may check in with the user — a genuine clarifying question, competing options to choose between, or a mix in the same call. P8 merges what used to be two separate, once-per-run modal checkpoints (K1's `ask_clarification`/`ClarificationResult`/`QuestionsCallback`/`_handle_clarification` and L7's `propose_options`/`OptionsResult`/`OptionsCallback`/`_handle_options`) into a single synthetic tool, still implemented entirely on the **host** side, not as an MCP server tool:

- `host/agent.py` defines one synthetic tool spec, `ask_user` (`_ASK_USER_TOOL_NAME`/`_ASK_USER_TOOL_SPEC`), appended to the OpenAI tool list only when the caller wires in an `on_ask_user_needed` callback (`AskUserCallback = Callable[[list[dict]], Awaitable[AskUserResult]]`) — never registered with, or forwarded to, the MCP server. Its schema takes 1-5 `questions`, each `{text, options?}` — `options` (2-5 mutually-exclusive strings) makes an item multiple-choice; omitting it makes it an open question.
- When the model calls `ask_user`, `_handle_ask_user` drops malformed/empty items, emits an `"ask_user"` `AgentEvent` (with the well-formed questions in `data`), and awaits the callback. `AskUserResult` carries the user's raw chat reply as free text (`reply: str`, `provided: bool`) rather than per-question structured answers — the host no longer distinguishes open answers from option picks once the reply comes back, since both now arrive as one chat message.
- **No once-per-run guard** — unlike the K1/L7 checkpoints it replaces, `ask_user` can be called any number of times in a run; live chat makes repeated check-ins natural instead of an interruption budget.
- `host/app.py` renders the question(s)/option(s) as a normal `telcontar` transcript turn and awaits the *same* live-chat message queue P7 wired up (`self._messages`) rather than `push_screen_wait`-ing a modal — there is no `ClarificationModal`/`OptionsModal` anymore, and the `RadioButton`/`RadioSet` imports they alone used are gone. The next chat message the user sends is returned as `AskUserResult(reply=message, provided=True)`.
- If the callback is unavailable or no well-formed questions were provided, the tool result is a note telling the agent to proceed with its own best judgement — the agent is instructed not to stall or offload every decision onto this checkpoint.

The system prompt's two former paragraphs ("Optional clarification checkpoint" / "Optional multiple-option checkpoint") are now one "Optional chat checkpoint" paragraph referencing `ask_user`, and the `"question"`/`"options"` `EventKind`s are merged into one `"ask_user"` kind.

### Settings from anywhere (P9)

`OrganizerApp` (the root Textual `App`) carries an app-level `ctrl+s` binding, `action_open_settings`, that pushes `ConfigScreen` from *any* screen — not just via `StartupScreen`'s pre-existing local `s` binding/button. It is a no-op if `ConfigScreen` is already the current screen (no double-push), and a no-op if `SetupScreen` is current, since `ConfigScreen` could persist a half-configured state that bypasses `SetupScreen`'s guided keyring/plaintext-fallback flow.

The binding must be declared as `Binding("ctrl+s", "open_settings", "Settings", priority=True)` (from `textual.binding`), not a plain tuple: Textual's non-priority key-binding resolution chain deliberately stops at the first `ModalScreen` it encounters, so that a modal fully captures input and a background shortcut (e.g. `q`/quit) can't fire accidentally while a confirmation dialog is open. Without `priority=True`, `ctrl+s` would silently not fire while `ApprovalModal` or `CostEstimateModal` is on screen. With it, the binding reaches `action_open_settings` regardless of what modal is stacked on top, and `ConfigScreen` stacks over it and pops back cleanly. Any future app-level binding meant to work while a modal is open needs the same `priority=True`.

### Output-sink abstraction

`server/sinks.py` defines a `Sink` protocol (`name`, `external`, `write_summary`, `write_folder_readme`) and a `resolve_sinks(names, allow_external)` factory. The MCP handlers for `write_summary` and `write_folder_readme` call `resolve_sinks` at request time, passing the profile's `[sinks] default` list and the `egress_allow_external_sinks` setting, then fan the call out to each resolved sink.

The only built-in sink is `local_markdown` (`external=False`) — it delegates directly to `tools.write_summary` / `tools.write_folder_readme` and writes Markdown files to the local filesystem. It is always allowed regardless of `egress_allow_external_sinks`.

Any sink name not in the built-in registry is treated as an external sink. If `egress_allow_external_sinks` is `False`, `resolve_sinks` raises `PermissionError` immediately (nothing leaves the machine without an explicit opt-in). If the flag is `True`, it raises `NotImplementedError` — external sinks (e.g. a MediaWiki wiki) are shipped as separate MCP integrations, not implemented in this codebase.

### Deterministic host pre-pass (P4)

`host/agent.py`'s `run_prepass(*, session, settings, target, on_event) -> PrepassResult` is a standalone, LLM-free corpus-discovery pass: given an already-open MCP session and a target directory, it walks the tree to exhaustion, checksums every file, and partitions the corpus into documents the registry already knows about versus genuinely new ones — laying the groundwork for the stateless analyzer (P5, below) that only needs to process the `new` set. As of P6, `run_agent_loop` calls this as the very first step of every fresh run (`history is None`), before the cost-approval gate, the analyzer, or the ORGANIZE turn loop.

Runs entirely through MCP tool calls, never local file I/O, so it behaves the same whether host and server share a filesystem or not:

1. **Discovery:** starting from `target`, calls `walk_tree(path, max_depth=3)` and collects every discovered file entry (skipping the same noise `_extract_discovered_entries`/O5 already skips). Any directory the result marks `truncated` (`_collect_truncated_dirs`) is queued and re-walked, repeating until no truncated directory remains anywhere in the tree.
2. **Checksumming:** calls `compute_checksum_batch` in chunks of `_PREPASS_CHUNK_SIZE = 300` paths per call, bounding round-trip size/latency on a large corpus. Per-path failures are collected into `PrepassResult.errors` rather than aborting the pass.
3. **Dedup by checksum:** identical-content files collapse to one representative path (`checksum_to_path`), since the registry — and any future analyzer — is keyed by checksum, not path.
4. **Known/new partition:** looks up every unique checksum against the registry in the same chunk size via `lookup_documents` (P3). A checksum with no registry record becomes a `new` entry (`{path, checksum}`); one with a record becomes a `known` entry (`{path, checksum, record}`).
5. **Re-homing:** for each `known` document whose registry-recorded path no longer matches where it was actually found on disk, batches a single `rehome_documents` call (`{checksum: new_path}`) to fix the drift. A checksum the server reports as `missing` (removed from the registry between the lookup and the rehome call) is recorded in `errors` rather than silently dropped.
6. **Progress:** emits exactly one `"progress"` `AgentEvent` (`data={"analyzed": len(known), "total": len(unique_checksums)}`) once discovery and partitioning are complete — a single snapshot, not the incremental per-tool-call updates `_ProgressTracker`/O5 produces during the LLM-driven loop.

Returns a `PrepassResult` dataclass: `new` (list of new-document dicts), `known` (list of known-document dicts, each carrying its current registry `record`), `rehomed` (checksums whose path was actually updated), `errors` (list of `{path, error}`), `total_files` (raw discovered-file count before dedup), and `sizes` (`{path: size_bytes}` for every discovered file, taken straight from `walk_tree`'s entries) — `sizes` is additive to P4's original shape, added so P5's new-docs-only cost estimate (below) has per-file sizes to work from without a second discovery pass.

### Stateless per-batch analyzer (P5)

`host/agent.py`'s `_analyze_new_documents(*, session, llm, settings, profile, new_docs, ledger, on_event) -> dict` is the piece that makes "each document's content is uploaded to the LLM at most once, ever" actually true: it analyzes only P4's `new_docs` (`PrepassResult.new`, host-authoritative `{path, checksum}` pairs) in isolated, per-batch LLM calls, rather than as part of the main ORGANIZE conversation where content could in principle be re-read or re-uploaded across turns. As of P6, `run_agent_loop` calls this right after `run_prepass`, gated by the cost-approval check above — and its structural half, `ORGANIZE_DENIED_TOOLS` (below), is what makes the guarantee actually hold end-to-end: without it, nothing would stop the ORGANIZE-phase model from calling `read_file`/`extract_text` on a document a second time.

`new_docs` is split into batches of at most `_ANALYZER_BATCH_SIZE = 10` (matching the batch size the old in-loop ANALYZE instructions used). For each batch:

1. **Fetch content** (`_fetch_batch_content`): dispatches each path to `extract_text_batch` (`.pdf`/`.docx`/`.xlsx`/`.pptx`/`.msg`, `_ANALYZER_EXTRACT_EXTENSIONS`) or `read_file_batch` (everything else) by file extension. This file-type split used to be left to the model's own judgement during the in-loop ANALYZE phase (see "Batch document-content tools (O1)" above) — the pre-pass/analyzer flow makes it a host-side decision instead, since there is no per-document agent turn left to reason it out.
2. **Wrap untrusted content**: each document's fetched text is wrapped with the existing `_wrap_untrusted` helper (S2) before being placed in the batch's user message — the same delimiter convention `_wrap_untrusted_content` applies at the main loop's tool-result-append sites, just invoked directly here since the analyzer builds its own throwaway message list rather than appending to the main conversation.
3. **One isolated, forced-tool LLM call per batch**: a synthetic `submit_document_records` tool (`_SUBMIT_RECORDS_TOOL_SPEC`) is forced via `tool_choice={"type": "function", "function": {"name": "submit_document_records"}}` — the first use of forced `tool_choice` in this codebase; every other LLM call here uses `tool_choice="auto"`. The tool's schema accepts only model-derived fields (title/type/summary/provenance/date/entities) — deliberately no `path`/`checksum`, which are host-authoritative. The messages list (`_ANALYZER_SYSTEM_PROMPT_TEMPLATE` + one user message with all the batch's delimited documents) is throwaway per batch, never threaded into the main conversation.
4. **Rejoin by position**: the model's returned `records` are matched to the batch's documents strictly by index (`records[i]` ↔ `batch[i]`) — never by any value the model might return as an identifier. If the model returns fewer records than documents in the batch, the unmatched tail becomes `errors` entries (`"No analysis record returned for this document"`) rather than being silently dropped or misaligned.
5. **Persist via the existing `record_document_batch` tool** — no new registry-write code. `checksum`/`path` come from the host-authoritative batch entry; the rest comes from the rejoined model record.

A batch whose LLM call raises is retried once (`_analyze_batch`'s two-attempt loop), then skipped — its documents are added to `errors` rather than aborting the whole run. `_analyze_new_documents` returns `{"recorded": [...], "errors": [...]}` across all batches combined, matching `record_document_batch`'s own return shape.

`_new_docs_cost_estimate(new_docs, sizes, max_snippet_chars) -> tuple[int, int]` is a small pure function — `(new_doc_count, estimated_input_tokens)` computed from only the `new_docs`' sizes (via `PrepassResult.sizes`), mirroring `_ProgressTracker.cost_estimate`'s chars-per-token heuristic but scoped to new documents only. As of P6, this feeds the O8 cost-approval gate directly — see "Pre-analysis cost-approval gate (O8/P6)" above.

### ORGANIZE-only agent loop + corpus digest (P6)

This is the item that wires P4 and P5 into `run_agent_loop` for real, completing the "content uploaded to the LLM at most once, ever" guarantee end-to-end rather than just building the pieces. On a fresh run (`history is None`), `run_agent_loop` now runs, in order: `run_prepass` (P4) → the cost-approval gate scoped to new documents (O8/P6, above) → `_analyze_new_documents` (P5) if approved → `_build_digest` (below) → the ORGANIZE-only turn loop. `history`-carrying continuation calls (O7) skip all of this and resume the existing conversation unchanged.

**Corpus digest (`_build_digest(prepass_result, analysis_result) -> str`):** a compact, host-composed summary seeded into the first ORGANIZE-phase user message in place of blank "please organize" instructions — one line per document (`title · type · path`, drawn from `PrepassResult.known`'s records and the analyzer's newly-recorded documents), plus totals (`N document(s) recorded (K already known, M newly analyzed this run)`) and an error/unanalyzed count when the pre-pass or analyzer reported any. Above `_DIGEST_MAX_LISTED_DOCS = 200` listed documents, the digest truncates the per-doc listing and points the agent at `list_documents`/`get_registry` for the rest — a fat digest would defeat its own purpose (avoiding a context blowup) on a large corpus. Deliberately NOT full per-document summaries, which would blow up context; the ORGANIZE agent reaches for the registry read tools (`list_documents`, `get_registry`, `get_document`, `find_duplicates`, `find_modified_documents`) for anything beyond title/type/path.

**`ORGANIZE_DENIED_TOOLS`** (`frozenset` in `host/agent.py`) is the structural half of the "content uploaded once" guarantee — not just a prompt instruction. `_discover_openai_tools` gained a `denied: frozenset[str] | None = None` parameter (alongside the existing `allowed`, used by query mode): when set, matching tool names are excluded from the OpenAI function-spec list regardless of what the prompt says. `run_agent_loop`'s fresh-run and continuation paths both call `_discover_openai_tools(session, denied=ORGANIZE_DENIED_TOOLS)`, so the ORGANIZE-phase model never even sees `read_file`, `extract_text`, `read_file_batch`, `extract_text_batch`, `compute_checksum`, `compute_checksum_batch`, `record_document`, `record_document_batch`, `compare_documents`, `lookup_documents`, or `rehome_documents` — the corpus was already analyzed by the pre-pass/analyzer, so there is no legitimate reason for this loop to fetch or record document content again, and `lookup_documents`/`rehome_documents` are pre-pass-only internals the ORGANIZE-phase model has no reason to call either. A denylist was chosen deliberately over an allowlist (like `QUERY_ALLOWED_TOOLS`): ORGANIZE needs almost every *other* tool (planning, execution, synthesis, registry/graph/event reads), so denying the few tools that don't belong is far less fragile than enumerating everything that does. As defense in depth, the turn-loop's tool-dispatch `if name in ORGANIZE_DENIED_TOOLS` branch rejects a hallucinated call to one of these with an explicit error even though it was never advertised — mirroring the same pattern `run_query_loop` already used for `QUERY_ALLOWED_TOOLS`.

**System prompt restructuring:** the old ANALYZE section ("survey the tree, batch-extract, record documents") is gone from `_SYSTEM_PROMPT_TEMPLATE` entirely — the corpus is already analyzed by the time the model sees this prompt. The prompt now opens by stating this plainly and pointing at the digest in the first message, instructs the model to use the registry read tools instead of raw file content, and its numbered steps run 1-10 across two sections (**A. ORGANIZE** the tree, **B. SYNTHESIZE**) instead of the old 1-14 across three (A. ANALYZE / B. ORGANIZE / C. SYNTHESIZE). The Safety rules section also gained a line telling the model to treat the digest as host-composed fact, not as instructions from the documents it summarizes.

### Plan-completion guard (T1)

Fixes an engine-level bug (Break 2) where the ORGANIZE-phase agent could finish
building a plan — `create_plan`, the `propose_*` ops, `review_plan`,
`set_plan_rationale`, `set_plan_folder_notes` — and then end its turn without
ever calling `execute_plan`, so the plan was never presented for approval and
the run went terminal silently. Lives entirely in `host/agent.py`, so it
applies identically to the Textual TUI and the NiceGUI web UI; no UI-layer
change was needed.

Three parts:

- **System prompt.** `_SYSTEM_PROMPT_TEMPLATE`'s step A.4 now frames
  `execute_plan(plan_id)` as the act that presents the plan to the user, not
  something that happens after approval — "the host pauses there, collects
  the user's approve / reject / refine decision, and returns it to you as the
  tool result." The Safety rules block gained two explicit lines: never end a
  turn with a plan built but not submitted via `execute_plan`, and never use
  `ask_user` to ask whether to proceed with a plan (`execute_plan` is the
  approval channel).
- **`next_step` hints.** `_dispatch` decorates the tool results of
  `review_plan` and `set_plan_folder_notes` — the two calls that immediately
  precede the seam where the stall was observed — with a `next_step` field
  pointing the model at `execute_plan`. Host-side only; the underlying MCP
  tool's own return shape is unchanged.
- **Loop-level guard.** In `run_agent_loop`, when a turn ends with no tool
  calls and no message queued (P7's usual reasons to keep going don't apply),
  the loop tracks `last_plan_id` (the most recent plan seen in this call's
  tool traffic, or seeded from `history` on a resumed/O7 conversation via
  `_seed_last_plan_id`) and, if `execute_plan` hasn't actually been dispatched
  yet this call, live-checks it with `_peek_pending_plan` (a `get_plan` call —
  never inferred from call-local state alone, since that can't distinguish
  "never submitted" from "already executed in an earlier call" on a resumed
  conversation). If the plan is still genuinely `pending` with at least one
  op, the loop re-prompts the model once (injected as a `"user"` role
  message) instead of stopping silently. The guard is permanently inert for
  the rest of the call once `execute_plan` has actually been dispatched
  (approved, rejected, or refined — doesn't matter) and fires at most once per
  `run_agent_loop` call; if the one re-prompt still doesn't get the model to
  call `execute_plan`, the run ends normally but the final text names the
  unexecuted plan id instead of losing it silently.

### NiceGUI web UI foundations (S4-S6, extended by T2/T3/T5/T6/T7/T8, U2/U3/U4)

`host/web/` is a package — the first piece of a planned Textual→NiceGUI web UI
migration (ROADMAP Phase 18). As of S6, `telcontar --web` (`host/main.py`) launches
it in place of the Textual TUI; the TUI remains the default when no flags are
passed, and `--web`'s `from host.web.main import run_web` import is lazy — scoped
to that branch only — mirroring the existing lazy `from host.app import
OrganizerApp` import on the no-flag path, so neither UI's dependency (`nicegui` vs.
`textual`) is paid for unless that UI is actually launched. A `--target PATH` flag,
meaningful only with `--web`, skips the landing page's directory picker and starts
a run for that directory immediately. It exists alongside `host/app.py`'s Textual
TUI, not in place of it — both `textual` and `nicegui` are main dependencies in
`pyproject.toml`. Feature parity with the TUI isn't fully there yet — no query mode,
no journal/undo UI (that's Phase 20, items U6/U7) — so the web UI is not (yet) the
primary way to use telcontar. As of U2, it does have its own first-run setup wizard
at `/setup`, at parity with the TUI's, so it no longer requires an
already-configured install to be usable. As of U3, it also has a settings view at
`/settings`, reachable from every screen via a persistent sidebar button — the
same parity goal applied to the TUI's `ConfigScreen`.

- `host/web/session.py` — `RunSession`, framework-agnostic per-run state. As of
  T5/T6, the transcript is turns-only: `RunSession.transcript: list[TranscriptItem]`
  (`seq`/`speaker`/`text`) holds genuine user↔telcontar exchanges (chat, ask_user,
  approval/cost outcomes, done/error) via `add_turn(speaker, text)`. Tool activity no
  longer interleaves there as a "steps"-kind item; instead `RunSession.steps:
  list[StepRecord]` (`seq`/`tool`/`summary`/`args`/`detail`/`status:
  "running"|"ok"|"error"`) holds it, and `RunSession.activity: str` holds a single
  mutable "what's happening right now" narration line. `open_step(tool, summary,
  args)` starts a step "running"; `close_step(result, ok=...)` pairs it with its
  result — `{"args": ..., "result": result}`, pretty-printed JSON, capped at
  `_MAX_STEP_DETAIL_CHARS = 20_000` with a "(truncated)" suffix — and marks it "ok"
  or "error". A step that never closes (the run errored out mid-call) stays
  "running" forever by design — it shows exactly where things stopped, not a bug.
  `transcript` and `steps` share the same `_seq` counter for a stable relative
  ordering. Also holds status, tokens, progress, a `pending` approval/cost request
  keyed to an `asyncio.Future`, a chat `messages` queue, conversation `history`,
  plus a module-level registry (`create`/`get`/`close`/`all_sessions`) keyed by a
  `secrets.token_urlsafe(16)` run id. Deliberately has no `nicegui` import, so it is
  unit-testable in plain pytest. `get_sidebar_width()`/`set_sidebar_width(width)`
  (T4) manage one in-memory sidebar-width preference (240-720px, default 380) for
  the process's lifetime, rather than a `RunSession` field, since it also applies on
  the picker route where no `RunSession` exists yet; `set_sidebar_width` clamps and
  returns the stored value. As of U4, `RunSession` also carries `fs_revision: int`,
  a counter `bump_fs_revision()` increments whenever the target directory's
  contents change on disk — consumed by the sidebar-tree refresh below. Also as of
  U4, `close_step(result, ok=...)` returns the closed `StepRecord` (`None` if none
  was open) instead of nothing, so a caller can inspect which tool just closed, and
  `resolve_pending(result, *, request_id=None)` takes an optional `request_id` that
  must match the *current* pending request's id or the call is silently ignored —
  stops a stale dialog (another tab, or one left over after a reload) from
  resolving a different, newer pending request than the one it was shown.
- `host/web/bridge.py` — `AgentBridge` wraps a `RunSession` and exposes
  `on_event`/`on_approval_needed`/`on_cost_approval_needed`/`on_ask_user_needed`, the
  same callback contract `host/agent.py`'s `run_agent_loop` already uses for the
  Textual TUI, plus `run()`/`start()`, which drive one full organize run (settings
  load → `mcp_session` → `run_agent_loop`, including the O7 continuation loop for
  follow-up chat messages) as a detached `asyncio.Task`. Also `nicegui`-free.
  Both `start()` and `run()` take an optional `instructions: str | None = None`,
  threaded into the *first* `run_agent_loop` call only — never into an O7
  continuation — mirroring the Textual TUI's `OrganizerScreen._agent_worker`.
  As of T5/T6, `on_event`'s `tool_call`/`tool_result` handling no longer appends a
  chat turn — that was the "telcontar talking to itself in bubbles" T5 fixes.
  `tool_call` narrates into `session.activity` (via `Narrator.narrate`) and calls
  `session.open_step(tool, event.text, args)`, reading `tool`/`args` off `event.data`
  (`host/agent.py`'s 5 `AgentEvent("tool_call", ...)` sites now carry `data={"tool":
  name, "args": args}`, previously just the tool name); `tool_result` calls
  `session.close_step(result, ok=...)`, inferring `ok` from whether `event.data`'s
  `result` dict contains an `"error"` key (the same 5 sites now carry
  `data={"result": result}`, previously no data at all — additive only, no
  `run_agent_loop`/`run_query_loop` signature change). As of U4, `on_event`'s
  `tool_result` case also calls `session.bump_fs_revision()` when the just-closed
  step's tool is one of the module-level `_TREE_MUTATING_TOOLS =
  {"execute_plan", "write_index", "write_summary", "write_folder_readme"}` and the
  result was ok — the only tools that change what's on disk under the target
  directory, and what drives the sidebar-tree refresh below.
- `host/web/dialogs.py` (U4) — one builder per `PendingRequest` kind, replacing the
  dialog-building code that used to live inline in `run_page`.
  `build_approval_dialog(session, pending)` is a faithful port of the TUI's
  `ApprovalModal`: rationale + disclaimer, target-layout preview + disclaimer,
  per-op checkboxes defaulting checked, the `ops_json_path` label, a free-text
  refine input, and Approve/Refine/Reject buttons — Refine only resolves on
  non-blank input and always takes priority over Approve, since they're mutually
  exclusive button clicks. `build_cost_dialog(session, pending)` is intentionally
  minimal for now (summary text, Proceed/Cancel) — its TUI-faithful content is
  U5's job. Both dialogs are `.props("persistent")` (no backdrop-click or Esc
  dismissal) and resolve via `session.resolve_pending(result,
  request_id=pending.request_id)` — this closes a live bug where the prior plain
  `ui.dialog()` could be dismissed without resolving its future, permanently
  deadlocking the run with no visible symptom: the same failure class as the
  reload-orphaning issue described below ("Reload-safe design"), just a different
  door into it.
- `host/web/steplog.py` (U4) — the internal-step log-strip rendering
  (`fmt_step_line`, `render_step_row`, `prune_log`, `sync_steps`, `StepLogState`)
  lifted out of `run_page`'s closure so later screens (U6's journal view, U7's
  query view) can reuse the same "one compact line per step, toggle opens full
  detail in the shell's drawer" idiom instead of re-deriving it per screen.
- `host/web/shell.py` (T2, extended by T3, T6, and U3) — `app_shell(*, target=None,
  on_select=None)`, a `@contextmanager` mounted by every `@ui.page` route, including
  the early-return branches (not-configured, run-not-found), so a left-sidebar frame
  is visible on every screen rather than being assembled per-page. As of U3 the
  drawer always renders a persistent "Settings" button navigating to `/settings`,
  reachable from every route — the web UI's counterpart to the TUI's app-level
  `ctrl+s` binding (`host/app.py`'s `action_open_settings`). It creates the
  `ui.left_drawer` as a direct child of the page body — NiceGUI's
  `require_top_level_layout` raises `RuntimeError` if a drawer is nested inside
  another container — mounts a `ui.tree` inside it from `host.web.tree.build_nodes`
  (`.props("dense no-connectors")` for a denser vertical rhythm). As of T6 it also
  creates a `ui.right_drawer` alongside the left one — both are top-level layout
  elements subject to the same `require_top_level_layout` constraint — and yields a
  `Shell` dataclass (`drawer`, `tree`, `content`, `detail_drawer`, `target`,
  `selected`) that the page body builds into via `with app_shell(...) as shell:`.
  `Shell.show_detail(title, detail)` populates and opens the right drawer with one
  internal step's full payload; it deliberately renders via
  `ui.codemirror(detail, language="JSON").disable()`, never `ui.code`/`ui.markdown`
  — both of those render through a markdown fenced-code path, and step detail can
  carry untrusted document content (e.g. a `read_file_batch`/`extract_text_batch`
  result) that must never be interpreted as markup. `ui.codemirror` takes the
  content as a plain value/prop instead, with `.disable()` making it read-only
  display, not an editor. `host/web/main.py`'s `run_page` is the only caller,
  reached via a per-step "code" icon button. The tree's `on_select`
  handler ignores clicks on lazy-load placeholder nodes and otherwise sets
  `shell.selected` and invokes the optional `on_select` callback; its `on_expand`
  handler is async and, the first time a real directory node is expanded, calls
  `host.web.tree.load_children` via `run.io_bound`, splices the result into
  `tree.props["nodes"]` in place, and calls `tree.update()` — S5's blocking-I/O
  rule means that listing must happen off the event loop. `Shell.refresh_tree(root)`
  (T3) re-roots the sidebar tree at `root`, used by the picker's "go up one level"
  button and (Windows-only) drive-root dropdown — both rendered only when
  `on_select` is wired in (i.e. only on the picker route, `/`; `/run/{run_id}`'s
  tree is for verification only, not re-rooting). `app_shell`'s signature is frozen:
  later Phase 20/21 work is expected to mount through it unchanged.
  `host/web/shell.py` now shares nicegui-importing duties with `host/web/main.py`.
  The drawer's width (T4) is set from `web_session.get_sidebar_width()` via the
  Quasar `width` prop (never raw CSS, since Quasar also offsets
  `.q-page-container` from that prop), and a 6px drag handle on the drawer's
  right edge — wired by a small injected JS snippet tracking
  mousedown/mousemove/mouseup on `document` — live-resizes the drawer in the DOM
  during the drag and, only on mouseup, emits a `tc_sidebar_resized` event that
  the Python side clamps, persists via `web_session.set_sidebar_width()`, and
  re-applies as the real `width` prop.
- `host/web/tree.py` (T2, fleshed out by T3) — NiceGUI-free, mirroring
  `session.py`/`bridge.py`'s invariant so it stays testable in plain pytest.
  `build_nodes(root: Path) -> list[dict]` builds the top-level node `ui.tree`
  expects (`{"id": <absolute path str>, "label": <basename>, "children": [...]}`),
  loading the root's immediate children eagerly (one directory listing) while
  deeper levels stay lazy behind a placeholder-child scheme (a node id ending in
  `PLACEHOLDER_SUFFIX`, a null-byte + ellipsis sentinel that's never a real path) —
  so a page load never walks the whole tree. `load_children(path)` lists one
  directory's immediate entries, both files and folders (the sidebar's job is
  letting the user verify files actually moved/renamed, not just browsing folders),
  sorted folders-then-files then alphabetically; it hides dotfiles but deliberately
  *not* `_quarantine` (the only removal path — the user must be able to see what
  landed there), never follows symlinks/junctions (Windows profile directories like
  "Application Data" can loop), and never raises — a permission error or a vanished
  directory yields an empty list rather than blanking the tree. `find_node` and
  `needs_loading` support `shell.py`'s expand handler. `rebuild_nodes(root,
  expanded_ids)` (U4) is a non-destructive alternative to `build_nodes`: it eagerly
  loads real children, recursively, for every directory id in `expanded_ids`
  instead of leaving the lazy-load placeholder, so refreshing the sidebar after a
  tree-mutating tool call doesn't collapse whatever the user had expanded; a
  directory that no longer exists (moved/renamed away by the very op that
  triggered the refresh) is silently dropped. `list_drive_roots()` wraps
  `os.listdrives()` (3.12+, Windows-only) so the picker can reach outside the home
  directory, degrading to an empty list on any other platform or on error.
- `host/web/theme.py` (T7, extended by T8) — product-identity helpers, `nicegui`-free
  (mirrors `session.py`/`bridge.py`/`tree.py`'s plain-pytest-testable invariant).
  `window_title(target: Path | None = None) -> str` returns `"telcontar"` with no
  target, or `f"telcontar — {target.name}"` once one is selected, falling back to
  the full path string when `.name` is empty (a Windows drive root has none, so the
  title never ends in a dangling "— "). T8 adds telcontar's visual identity — a
  Númenórean/human-king motif, gold and silver on a dark base — through two pieces
  consumed by `run_web()`: `PALETTE: dict[str, str]`, exactly the 9 keyword names
  `nicegui.app.colors()` accepts, with gold `primary`/mithril-silver `secondary` on
  a dark `dark_page`/`dark` base, while `positive`/`negative` deliberately stay in
  their own desaturated green/red families rather than being re-hued gold/silver —
  the approval dialog's Approve/Reject buttons are the highest-trust screen in the
  product and must stay unmistakable; and `css(font_dir=None) -> str`, one CSS layer
  binding the vendored Cinzel display face (via `font_face_css()`, emitted only when
  the woff2 actually exists on disk — otherwise a fallback serif stack, never a 404)
  onto Quasar's own `.text-h1`...`.text-h6` classes, plus a mandatory
  `.q-btn.bg-primary` text-colour fix (Quasar's default white button label is
  ~2.2:1 contrast on the gold primary — unreadable). `FAVICON_SVG` is an inline SVG
  (Elendil's seven-pointed star) passed to `ui.run(favicon=...)`, which NiceGUI
  inlines as a data URL with no file or network request.
- `host/configflow.py` (U2, extended by U3) — framework-agnostic (no `nicegui`, no
  `textual`) configuration-flow logic factored out of the TUI's
  `SetupScreen`/`ConfigScreen` so both the web UI's setup wizard and settings view
  read from the same source of truth: `profile_options()`
  (moved here from `host/app.py`'s old `_load_profile_options`/`_PROFILE_LABELS`),
  `SERVICE_HINTS` (per-service URL/model hint and placeholder text),
  `validate_credentials(url, key, model, *, key_required)` (url → key → model,
  first-error-wins; `key_required=False`, U3, is the settings view's blank-key-
  preserves-existing case), `build_wizard_updates(...)`, `build_settings_updates(url,
  key, model, profile, approval_mode)` (U3 — omits `llm_api_key` entirely when `key`
  is blank, instead of the wizard's always-include-the-key behaviour),
  `APPROVAL_OPTIONS` (U3, moved out of `host/app.py`'s `ConfigScreen`), and
  `plaintext_warning(button_label, recovery_action="go back")` — the shared
  warning-message builder that fixes U8's copy bug (the TUI wizard used to say
  "Press \"Finish\" again" while its button read "Save & continue →"). `host/app.py`
  now imports from here instead of owning this logic itself.
- `host/web/forms.py` (U2, extended by U3) — shared NiceGUI form fragments:
  `credential_inputs(...)` renders the URL/API-key/model input triple (each
  `.mark()`ed for NiceGUI's headless `user` test fixture); as of U3 it takes a
  `key_placeholder` parameter so the settings view can show "Paste a new key, or
  leave empty to keep the current one" in place of the wizard's generic
  placeholder. `save_with_plaintext_guard(build_updates, *,
  plaintext_confirmed, button_label, recovery_action="go back")` calls
  `config.settings.save_user_config` via `run.io_bound`, always from a *fresh* dict
  built by `build_updates()` — never a cached one, since `save_user_config` pops the
  API key out of its argument before raising `PlaintextKeyFallbackNeeded`, so reusing
  a dict across a retry would silently drop the key.
- `host/web/settings.py` (U3) — the settings view, a NiceGUI port of
  `host/app.py`'s `ConfigScreen` at parity with it, including the
  blank-key-preserves-existing rule. `build_settings_view(*, on_done)` fetches
  `configflow.profile_options()` and `config.settings.read_user_config()` via
  `run.io_bound`, then renders a single-page form (URL/API-key/model, document
  profile, approval mode, Save/Cancel) through one `@ui.refreshable` — unlike the
  wizard, there's no multi-step routing to do. Saves through the same
  `forms.save_with_plaintext_guard` as the wizard. Mounted at `@ui.page("/settings")`
  in `host/web/main.py`, reached from any screen via the sidebar's Settings button
  (`host/web/shell.py`, above).
- `host/web/wizard.py` (U2) — `build_setup_wizard(*, on_finish)`, a 1:1 port of
  `host/app.py`'s `SetupScreen`: the same 5 steps (welcome, service choice, API
  details, document profile, done), same validation order/strings (via
  `host/configflow.py`), same plaintext-keyring warn-then-confirm flow (via
  `forms.save_with_plaintext_guard`). Routes between steps with a `ui.refreshable`
  function keyed on a page-closure `_WizardState.step` — real per-step routing, the
  NiceGUI-native equivalent of the TUI's mount-all-five-and-toggle-`.display`
  approach. State lives only in that closure, never `app.storage` or a URL param —
  the API key must never touch either. Lives outside `host/web/main.py` on purpose:
  `main.py`'s `@ui.page` decorators stay thin shells: `@ui.page("/setup")` just
  calls `build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))` inside
  `app_shell()`.
- `host/web/main.py` now mounts `app_shell(...)` at the top of both page bodies
  instead of assembling its own layout. The landing page (`/`) first checks
  `config.settings.is_configured()`: if telcontar hasn't been set up yet, it
  navigates to `/setup` (the wizard above) instead of showing any picker. `/settings`
  (the settings view above, U3) is registered the same thin-shell way, reachable
  from the sidebar's Settings button on every route. Once configured, folder selection is the
  sidebar tree, which now doubles as the directory picker (T3, superseding the
  browse-view half of Phase 20's planned U1): clicking a node sets `shell.selected`
  (which may now be a file, since the tree shows files too), and a "Use selected
  directory" button starts the run only if `shell.selected.is_dir()`. The organizer view (`/run/{run_id}`) now opens on a
  **starter pane** shown before the run begins: a directory overview (reusing
  `host.paths.directory_overview`, also offloaded via `run.io_bound`) plus an
  optional free-text steering-instructions input (mirrors the Textual TUI's
  pre-analysis steering box) and a "Start organizing" button. Only clicking that
  button constructs the `AgentBridge` and calls `start(instructions=...)` — S4's
  version started the run immediately on directory selection. Once started
  (`session.started`), the starter pane hides and the main view (status/progress
  bar/chat input/approval-cost-dialogs, now via `host/web/dialogs.py`, U4) takes
  over. As of T5/T6, that main view is two independent zones instead of one
  interleaved stream: a
  `conversation_column` (turns only, `ui.chat_message`, rendering `session.transcript`)
  and, below a separator, a pinned-bottom `activity_label` (the current narration
  line, `session.activity`) plus a scrolling `log_column` (~25vh) rendering
  `session.steps` as one compact line each — a status glyph (▶ running / · ok /
  ✗ error) plus the step's summary — with a small "code" icon button per row that
  calls `shell.show_detail(step.summary, step.detail)` to open the full payload in
  the right-side detail drawer (T6). As of U4 this rendering is
  `host/web/steplog.py`'s `sync_steps`/`StepLogState` (above), not inline: `run_page`
  owns one `steplog.StepLogState()` and calls `steplog.sync_steps(log_column, shell,
  step_log_state, session.steps)` once per tick, which caps the DOM at
  `_MAX_LOG_ROWS = 500` (oldest row deleted first) and lets an already-rendered
  "running" step's line update in place once it closes — unlike `TranscriptItem`s,
  `StepRecord`s mutate after creation.
  `run_page`'s `with app_shell(...) as shell:` captures the `Shell` handle so
  `steplog.render_step_row` can reach `shell.show_detail()`.

  **Sidebar tree refresh (U4):** `_refresh()` is now `async`. On each tick, if
  `session.fs_revision` has changed since the render cursor last saw it, `run_page`
  reads the tree's currently-expanded node ids off its Quasar `expanded` prop (kept
  in sync by `shell.py`'s `on_expand` handler), rebuilds the node list via `await
  run.io_bound(web_tree.rebuild_nodes, session.target, expanded)`, and replaces
  `shell.tree.props["nodes"]` before calling `shell.tree.update()` — guarding
  against `run.io_bound` returning `None` on shutdown/cancel. This only fires when
  a tree-mutating tool actually closed since the last tick (see `bridge.py`'s
  `_TREE_MUTATING_TOOLS` above), never on every 0.5s poll, and never collapses
  whatever the user had expanded — closing the gap where the sidebar tree
  (Phase 19 T3) never updated as the agent moved/renamed/quarantined files.

  `run_web(target: Path | None = None)` still binds an ephemeral local port and
  calls `ui.run(host="127.0.0.1", ..., show=True, reload=False, title=..., dark=True,
  favicon=theme.FAVICON_SVG)` — never `0.0.0.0`, to avoid exposing the approval gate
  on the LAN. `reload=False` is load-bearing, not a style choice: with `reload=True`,
  uvicorn forces a `SelectorEventLoop` on Windows, where
  `asyncio.create_subprocess_exec` (used to launch the MCP server subprocess) raises
  `NotImplementedError`. `dark=True` is load-bearing too (T8): Quasar only honours
  the `dark`/`dark_page` `PALETTE` tokens in dark mode. Before `ui.run()`, `run_web`
  applies telcontar's visual identity globally and exactly once — `app.colors(
  **theme.PALETTE)` (never a per-page `ui.colors()`, which would silently override
  this and fragment the identity across routes), `app.add_static_files(
  theme.FONT_URL_PATH, theme.FONT_DIR)` to serve the vendored Cinzel woff2 when the
  fonts directory exists, and `ui.add_css(theme.css(), shared=True)`. The browser tab
  title (T7) comes from `host.web.theme.window_title`: `ui.run(...)`'s `title=`
  supplies the global default (no target yet), and `run_page` separately calls
  `ui.page_title(theme.window_title(session.target))` from inside the page body —
  `@ui.page(title=...)` is bound at decoration/import time and can't see the
  per-request session's target, so the call is made live instead, landing the
  target's name in the very first HTML response. `index_page` (the picker) never
  calls `ui.page_title()`, since no directory is "selected" until a run exists.

**Reload-safe design:** a page reload creates a new NiceGUI client, but `RunSession`
(looked up by run_id from the URL) persists independently of any one client, and a
pending approval/cost request is an `asyncio.Future` parked on the session rather
than an awaited NiceGUI dialog — so a reload re-attaches to an in-flight approval
instead of orphaning it. This was validated in a pre-implementation spike (see
ROADMAP.md's "Break 1" note ahead of Phase 18), which found that a bare reload does
**not** kill the background run — it silently orphans it, and any UI element the
run's task then tries to touch afterward targets a dead client, which can
permanently deadlock an approval gate with no visible symptom. As of U4, the
approval/cost dialogs themselves (`host/web/dialogs.py`) close the same failure
class's other door: they're `.props("persistent")` (no backdrop-click or Esc
dismissal) and resolve through `session.resolve_pending(result,
request_id=pending.request_id)`, so a dismissed-without-resolving dialog can no
longer deadlock a run, and a stale dialog from another tab or a pre-reload client
can't resolve a pending request it was never actually shown.

---

## Data flow (one organize session)

```
1. Host launches server subprocess (stdio)
2. On a fresh run (history=None): host runs run_prepass (P4) — walks the tree to
   exhaustion, checksums every file (compute_checksum_batch, never gated),
   partitions into known vs. new documents via lookup_documents, and re-homes any
   known document whose on-disk path drifted (rehome_documents); emits one
   "progress" AgentEvent (pre-analysis snapshot)
3. If run_prepass found any new documents: host computes a token estimate scoped
   to ONLY those new documents, emits a "cost_estimate" AgentEvent, shows
   CostEstimateModal, and — unless approval_mode == "never" — awaits approval
   before proceeding (O8/P6). No new documents → this step is skipped entirely
4. On approval (or auto-approval): host runs _analyze_new_documents (P5) — new
   documents in batches of ≤10, each analyzed by one isolated, forced-tool LLM
   call (submit_document_records) whose messages list is throwaway and never
   joins the conversation below; document content is wrapped in the
   untrusted-content delimiter (M10) before being sent. Each batch's results
   are persisted via record_document_batch immediately, and a "progress"
   AgentEvent fires right after (Q2) — one per successfully recorded batch,
   not just once at the end — so the bar advances incrementally through
   analysis instead of jumping at the end. On rejection, the new documents are
   neither fetched nor recorded this run
5. Host builds a compact corpus digest (_build_digest, P6) from the combined
   known + newly-analyzed documents — per-doc title/type/path (capped at 200
   listed) plus totals and any error count — and seeds it into the first
   ORGANIZE-phase user message in place of blank "please organize" instructions;
   the OrganizerScreen starter pane's optional steering instructions, if the user
   typed any, are appended to this same seed message
5b. Host drains any chat message queued via message_queue since the run started
    (P7) — catches anything typed during steps 2-4 above — and appends each as
    a user turn before the first LLM call. The #organize-input chat box is
    enabled from the very start of the run, not just after it stops
6. Host calls session.list_tools(denied=ORGANIZE_DENIED_TOOLS) → discovers the
   ORGANIZE-phase toolset, structurally excluding the content-fetching/recording
   tools already used in steps 2-4 (P6); if the caller wired in an
   on_ask_user_needed callback, the host also appends its own host-side ask_user
   tool spec (P8; never forwarded to the server)
7. Host sends the ORGANIZE-only system prompt (built from config + active
   profile: document types, naming conventions, and synthesis template — no
   ANALYZE section, since the corpus is already analyzed) + the digest-seeded
   user message from step 5
8. The model responds with tool calls
9. Host dispatches to server via MCP — a hallucinated call to one of
   ORGANIZE_DENIED_TOOLS is rejected with an explicit error instead of being
   forwarded, even though none of them were advertised in step 6 (defense in
   depth, P6)
10. Server executes tool, returns result
11. Host feeds result back to the model as a tool message — any document content a
    tool result still carries (e.g. compare_documents's diff field, in query
    mode only — ORGANIZE mode no longer exposes it) is wrapped in the
    untrusted-content delimiter first (M10)
12. Host drains message_queue again (P7) — catches anything typed during this
    turn's tool calls (step 9-11) — appending each queued message as a user
    turn; steps 8-11 then repeat (up to the adaptive turn budget — see above,
    effectively static for a fresh run since the O5 progress tracker no longer
    grows once the ORGANIZE loop starts)
13. At any point before or while building the plan, the agent MAY call ask_user
    (P8) with 1-5 items — plain questions, multiple-choice ones (2-5 options), or
    a mix; the host emits an "ask_user" AgentEvent, renders the item(s) as a
    transcript turn, and blocks on the live-chat message queue (P7) for the
    user's next chat reply (or a "proceed with best judgement" note if the
    callback is unavailable or nothing well-formed was asked) — unlimited per
    run, no once-per-run guard
14. Agent designs a target taxonomy from the types/themes already recorded in the
    digest, opens a plan (create_plan), and stages propose_create_dir for each
    folder (idempotent; no folder created for absent categories) alongside
    propose_rename / propose_move / propose_quarantine / propose_create_file /
    propose_update_file / propose_archive_document ops — every mutation is
    staged, never applied directly
15. On execute_plan call:
    a. Host fetches plan details (get_plan) and writes the full ops list to
       .organizer/plan_ops.json (path shown in the modal)
    b. Host shows ApprovalModal to user
    c. User approves (optionally deselecting ops), refines with free text, or rejects
    d. On approve: host calls approve_plan → execute_plan; server applies ops,
       journals each, reconciles registry
    e. On refine: the plan is NOT executed — the free-text request is returned to the
       agent as a tool result, which revises the plan (ops/rationale/folder notes) and
       calls execute_plan again to re-present it (back to step 15)
16. Agent calls build_graph → get_actors → list_events, then composes SUMMARY.md
    from registry + events + graph + actors per the profile's [synthesis] template;
    calls write_index + write_summary to persist INDEX.md, manifest.json, SUMMARY.md
17. Agent calls write_folder_readme(path=<folder>, content=<markdown>) once per
    meaningful folder of the organized tree; empty/trivial folders are skipped
18. Agent sends final text (no tool calls) → normally the loop would end here, UNLESS a chat message is waiting in message_queue at this exact instant (P7): if so, the queued message(s) are appended as a user turn and the loop continues from step 8 instead of ending — letting a live chat message redirect an in-progress run. Otherwise, the T1 plan-completion guard checks whether a plan it has seen (`last_plan_id`) is still genuinely `pending` (live-checked via `get_plan`) despite `execute_plan` never having been dispatched this call — this is the same check that can also fire right after step 14 if the model stops immediately after staging/reviewing a plan without ever reaching step 15; either way, it re-prompts the model once, and only ends the loop if that single re-prompt still doesn't produce an `execute_plan` call (in which case the final text names the unexecuted plan instead of losing it silently). Barring that, this is one of three ways the loop can reach a terminal state — the others being an unhandled exception (caught and returned as an error, O7) or the turn budget running out
19. Desktop notification fires and the "press g / keep chatting" cue is shown — but only on this first terminal state (O7)
20. The MCP session from step 1 stays open, and the #organize-input chat box (live since the start of the run, P7) stays enabled. The host's worker loop waits on `#organize-input` for any message that arrives strictly AFTER run_agent_loop has already returned (i.e. the agent is fully idle and no live call remains to drain the queue itself) — each such message resumes run_agent_loop on the SAME session with (history=<returned from the previous call>, message=<your text>, message_queue=<the same queue>) — back to step 8 directly (steps 2-7 do NOT repeat; no new pre-pass or analysis happens on a continuation), with the same ORGANIZE-only toolset, its own fresh turn budget, and the same live-chat draining (step 5b/12/18) as the initial run. An unhandled exception during any of these turns is caught rather than propagating: any tool call left without a matching result is answered with a synthesized {"error": ...} entry, an "error" AgentEvent fires, and the conversation history stays valid for the next chat message
```

---

## Data flow (one query session)

```
1. User opens QueryScreen (from StartupScreen "Query" button, or "g" in OrganizerScreen)
2. Host launches server subprocess (stdio) — same MCP server, same registry
3. Host calls session.list_tools() → filters to QUERY_ALLOWED_TOOLS (read-only subset)
4. Host sends query-mode system prompt (built from active profile) + user's first question
5. The model responds with tool calls against the read-only allowlist
6. Host dispatches to server via MCP (mutating tool names are blocked in the host even if
   the model hallucinates one — defense in depth)
7. Server executes tool, returns result
8. Host feeds result back to the model as a tool message — same untrusted-content
   delimiter wrapping as organize mode applies here too (M10)
9. Steps 5-8 repeat until the model produces a final text answer
10. Answer is displayed in the RichLog; conversation history is threaded across questions
    within the same session (the MCP session stays open for the whole chat)
11. User types another question (goto step 4) or presses Esc to return to the previous screen
```

The query loop uses the fixed `_MAX_TURNS = 50` ceiling; the organize loop (`run_agent_loop`) instead scales its ceiling with corpus size via `_analysis_turn_budget` (see "Adaptive turn budget (O4)" above) — `_MAX_TURNS` remains its floor. `QUERY_ALLOWED_TOOLS` is a
`frozenset` defined in `host/agent.py`; it covers inspection tools only — no plan, execution,
write, graph-build, event-creation, or archive tools are exposed to the model.

---

## Configuration flow

```
.env file
  │
  ▼
config/settings.py  (Pydantic Settings)
  │
  ├──► host/agent.py  (LLM endpoint, approval mode, profile)
  │
  └──► server/main.py  (plans_dir, journal_path, events_path, registry_path,
                         graph_path, archive_path, quarantine_dir, max_snippet_chars,
                         allowlist_dirs, egress_allow_external_sinks, profile,
                         target_dir)
```

Both host and server load `Settings` independently at startup — there is no shared singleton across the process boundary. The server's `_get_settings()` is lazy-initialized and cached per process. `target_dir` differs from the other server-side settings in one way: it isn't read from `.env` in practice, it's set per-run by `host/agent.py`'s `mcp_session`, which passes it to the server subprocess as a `TARGET_DIR` environment variable (see [Path confinement](#path-confinement-on-every-path-taking-tool-m2) above). As of P2, `config.settings.load()` also calls `Settings.for_target(target_dir)` whenever `target_dir` is set, rebasing `plans_dir`/`journal_path`/`events_path`/`registry_path`/`graph_path`/`archive_path`/`egress_path`/`quarantine_dir` onto it — see [Per-directory memory (P2)](#per-directory-memory-p2) above.

---

## Further reading

- [Module Reference](modules.md) — per-file breakdown with key classes and functions
- [Plan Lifecycle](internals/plan-lifecycle.md) — detailed design doc for the plan/journal system
- [MCP Tools Reference](../reference/mcp-tools.md) — complete tool signatures and semantics
