# Architecture

Telcontar is a locally-run AI directory organizer built on the **Model Context Protocol (MCP)**. Two Python processes communicate over stdio: a **host** that runs the GPT-5 agent loop and a **server** that owns all file operations.

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
│  OpenAI-compatible endpoint (Azure / Mammouth)      │
│  GPT-5 — chat completions with tool use             │
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
every `propose_*` tool, `write_index`, `write_summary`, `write_folder_readme`,
`record_document`). `_confinement_roots(cfg)` builds the allowed roots as
`[settings.target_dir, Path.cwd()]` (target_dir omitted when unset). `target_dir`
is populated from a `TARGET_DIR` env var that `host/agent.py`'s `mcp_session` sets
on the server subprocess whenever a `target` is passed in — i.e. on every real
organize (`run_agent`) or query (`run_query`) session — and `Path.cwd()` is
included because the server always runs with its cwd set to the project root,
where `.organizer/*` and the quarantine dir live regardless of which directory is
being organized. Both `.resolve()`-normalize the candidate path first, so an
absolute escape and a `..` traversal are rejected identically. For the three
tools that already ran `check_allowlist` (`read_file`, `extract_text`,
`compare_documents`), `check_within_root` runs *after* it — the allowlist remains
a stricter, opt-in bound; target-dir confinement is the always-on floor
underneath it.

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

### Recursive tree exploration

`walk_tree(path, max_depth=3)` complements `list_dir` (a single level): it returns a bounded recursive directory listing, where each directory entry carries a nested `children` list until `max_depth` is reached — deeper directories come back with `children: null` and `truncated: true`, signalling the agent to call `walk_tree` again on that subpath to descend further. Files carry `size`/`mtime` like `list_dir`; unreadable entries are marked `type: "unknown"`.

The ANALYZE phase's system prompt instructs the agent to survey the whole tree with `walk_tree` first, rather than stopping at the top level, so documents nested in subfolders are discovered in one pass. The ORGANIZE phase's prompt correspondingly permits the agent to redesign the *existing* nested layout entirely, not just reorganize what already sits at the root.

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

### Post-analysis clarification checkpoint

Between the ANALYZE and ORGANIZE phases, the agent may surface a batch of clarifying questions when it hits genuine ambiguity (unclear document type, competing taxonomy groupings, ambiguous naming). This is implemented entirely on the **host** side, not as an MCP server tool:

- `host/agent.py` defines a synthetic tool spec, `ask_clarification`, that is appended to the OpenAI tool list only when the caller wires in an `on_questions_needed` callback (`QuestionsCallback = Callable[[list[str]], Awaitable[ClarificationResult]]`) — it is never registered with, or forwarded to, the MCP server.
- When the model calls `ask_clarification`, `_handle_clarification` enforces the at-most-once-per-run rule and the "nothing to add → proceed" path, emits a `"question"` `AgentEvent` (with the questions in `data`), and awaits the callback.
- `host/app.py` wires `on_questions_needed` to show `ClarificationModal` (mirrors `ApprovalModal`): one free-text `Input` per question, with **Submit answers** and **Skip — best judgement** buttons, returning a `ClarificationResult`.
- If the user skips, submits no answers, or the checkpoint was already used this run, the tool result is a note telling the agent to proceed with its own best judgement — the agent is instructed never to stall waiting for answers.

### Multiple-option checkpoint

Also between the ANALYZE and ORGANIZE phases, after re-examining its intended approach from a second angle, the agent may surface a few questions carrying competing, mutually-exclusive options for the user to pick from — implemented the same way, entirely on the **host** side:

- `host/agent.py` defines a synthetic tool spec, `propose_options`, that is appended to the OpenAI tool list only when the caller wires in an `on_options_needed` callback (`OptionsCallback = Callable[[list[dict]], Awaitable[OptionsResult]]`) — it is never registered with, or forwarded to, the MCP server.
- When the model calls `propose_options`, `_handle_options` enforces the at-most-once-per-run rule, drops any question that lacks a non-empty prompt or has fewer than two options, emits an `"options"` `AgentEvent` (with the questions in `data`), and awaits the callback.
- `host/app.py` wires `on_options_needed` to show `OptionsModal` (mirrors `ClarificationModal`): one `RadioSet` per question (2-5 `RadioButton` options, first pre-selected), with **Submit choices** and **Skip — best judgement** buttons, returning an `OptionsResult`.
- If the user skips, makes no selection, or the checkpoint was already used this run, the tool result is a note telling the agent to proceed with its own best judgement — the agent is instructed not to stall or offload every decision onto this checkpoint.

### Output-sink abstraction

`server/sinks.py` defines a `Sink` protocol (`name`, `external`, `write_summary`, `write_folder_readme`) and a `resolve_sinks(names, allow_external)` factory. The MCP handlers for `write_summary` and `write_folder_readme` call `resolve_sinks` at request time, passing the profile's `[sinks] default` list and the `egress_allow_external_sinks` setting, then fan the call out to each resolved sink.

The only built-in sink is `local_markdown` (`external=False`) — it delegates directly to `tools.write_summary` / `tools.write_folder_readme` and writes Markdown files to the local filesystem. It is always allowed regardless of `egress_allow_external_sinks`.

Any sink name not in the built-in registry is treated as an external sink. If `egress_allow_external_sinks` is `False`, `resolve_sinks` raises `PermissionError` immediately (nothing leaves the machine without an explicit opt-in). If the flag is `True`, it raises `NotImplementedError` — external sinks (e.g. a MediaWiki wiki) are shipped as separate MCP integrations, not implemented in this codebase.

---

## Data flow (one organize session)

```
1. Host launches server subprocess (stdio)
2. Host calls session.list_tools() → discovers all MCP tools; if the caller wired in
   an on_questions_needed callback, the host also appends its own host-side
   ask_clarification tool spec, and if an on_options_needed callback is wired in, the
   host also appends its own host-side propose_options tool spec (neither forwarded
   to the server)
3. Host sends system prompt (built from config + active profile: document types,
   naming conventions, and synthesis template) + user message (the OrganizerScreen
   starter pane's optional steering instructions, if the user typed any, are
   appended to this seed user message before the loop starts)
4. GPT-5 responds with tool calls
5. Host dispatches to server via MCP
6. Server executes tool, returns result
7. Host feeds result back to GPT-5 as tool message — document content from
   read_file/extract_text/compare_documents's diff field is wrapped in the
   untrusted-content delimiter first (M10)
8. Steps 4-7 repeat (up to MAX_TURNS = 50)
9. Once analysis is far enough along, the agent MAY call ask_clarification once with a
   short batch of questions; the host emits a "question" AgentEvent, shows
   ClarificationModal, and feeds the user's answers (or a "proceed with best judgement"
   note if skipped or already used) back as the tool result
10. After re-examining its approach from a second angle, the agent MAY also call
    propose_options once with a few questions, each carrying 2-5 mutually-exclusive
    options; the host emits an "options" AgentEvent, shows OptionsModal, and feeds the
    user's selections (or a "proceed with best judgement" note if skipped or already
    used) back as the tool result
11. Agent designs a target taxonomy from the types/themes found, opens a plan
    (create_plan), and stages propose_create_dir for each folder (idempotent; no
    folder created for absent categories) alongside propose_rename / propose_move /
    propose_quarantine / propose_create_file / propose_update_file /
    propose_archive_document ops — every mutation is staged, never applied directly
12. On execute_plan call:
    a. Host fetches plan details (get_plan) and writes the full ops list to
       .organizer/plan_ops.json (path shown in the modal)
    b. Host shows ApprovalModal to user
    c. User approves (optionally deselecting ops), refines with free text, or rejects
    d. On approve: host calls approve_plan → execute_plan; server applies ops,
       journals each, reconciles registry
    e. On refine: the plan is NOT executed — the free-text request is returned to the
       agent as a tool result, which revises the plan (ops/rationale/folder notes) and
       calls execute_plan again to re-present it (back to step 12)
13. Agent calls build_graph → get_actors → list_events, then composes SUMMARY.md
    from registry + events + graph + actors per the profile's [synthesis] template;
    calls write_index + write_summary to persist INDEX.md, manifest.json, SUMMARY.md
14. Agent calls write_folder_readme(path=<folder>, content=<markdown>) once per
    meaningful folder of the organized tree; empty/trivial folders are skipped
15. Agent sends final text (no tool calls) → loop ends
16. Desktop notification fires
```

---

## Data flow (one query session)

```
1. User opens QueryScreen (from StartupScreen "Query" button, or "g" in OrganizerScreen)
2. Host launches server subprocess (stdio) — same MCP server, same registry
3. Host calls session.list_tools() → filters to QUERY_ALLOWED_TOOLS (read-only subset)
4. Host sends query-mode system prompt (built from active profile) + user's first question
5. GPT-5 responds with tool calls against the read-only allowlist
6. Host dispatches to server via MCP (mutating tool names are blocked in the host even if
   the model hallucinates one — defense in depth)
7. Server executes tool, returns result
8. Host feeds result back to GPT-5 as tool message — same untrusted-content
   delimiter wrapping as organize mode applies here too (M10)
9. Steps 5-8 repeat until the model produces a final text answer
10. Answer is displayed in the RichLog; conversation history is threaded across questions
    within the same session (the MCP session stays open for the whole chat)
11. User types another question (goto step 4) or presses Esc to return to the previous screen
```

The query loop shares `_MAX_TURNS = 50` with the organize loop. `QUERY_ALLOWED_TOOLS` is a
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

Both host and server load `Settings` independently at startup — there is no shared singleton across the process boundary. The server's `_get_settings()` is lazy-initialized and cached per process. `target_dir` differs from the other server-side settings in one way: it isn't read from `.env` in practice, it's set per-run by `host/agent.py`'s `mcp_session`, which passes it to the server subprocess as a `TARGET_DIR` environment variable (see [Path confinement](#path-confinement-on-every-path-taking-tool-m2) above).

---

## Further reading

- [Module Reference](modules.md) — per-file breakdown with key classes and functions
- [Plan Lifecycle](internals/plan-lifecycle.md) — detailed design doc for the plan/journal system
- [MCP Tools Reference](../reference/mcp-tools.md) — complete tool signatures and semantics
