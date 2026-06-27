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
│  │ Organizer/   │   │  - MCP client (stdio)       │  │
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
│  │  server/guards.py    no-overwrite / allowlist   ││
│  │  server/journal.py   append-only undo log       ││
│  │  server/events.py    project event journal      ││
│  │  server/graph.py     knowledge graph projection ││
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

The MCP server has no delete tool. The `propose_quarantine` / `quarantine` path is the only way to remove files from the working tree. Quarantined files are moved to `QUARANTINE_DIR` and journaled — they can be recovered manually or via `undo_last`.

### Knowledge graph

`server/graph.py` projects the registry and event journal into a node/edge graph persisted at `GRAPH_PATH` (`.organizer/graph.json`). The graph is a pure, reproducible derivation — no independent state. Node kinds: `document` (one per registry record), `entity` (deduplicated person/org by normalized name), `event` (one per recorded event). Edge types: doc→entity (role-typed), entity↔entity `co_occurrence` (weighted by shared documents), event→entity `mentions` (entity name found in event sentence). Exposed via `build_graph` (rebuild + persist + return), `get_graph` (return last persisted), and `get_actors` (entity nodes ranked by centrality, capped at `salient_cap`).

`rank_actors` scores entities by: document count (primary), total co-occurrence weight, then event-mention count, with a deterministic lowercased-name tie-break. The cap comes from the active profile's `[entities].salient_cap` field and is enforced in the tool itself.

---

## Data flow (one organize session)

```
1. Host launches server subprocess (stdio)
2. Host calls session.list_tools() → discovers all MCP tools
3. Host sends system prompt (built from config + active profile) + user message
4. GPT-5 responds with tool calls
5. Host dispatches to server via MCP
6. Server executes tool, returns result
7. Host feeds result back to GPT-5 as tool message
8. Steps 4-7 repeat (up to MAX_TURNS = 50)
9. On execute_plan call:
   a. Host fetches plan details (get_plan)
   b. Host shows ApprovalModal to user
   c. User approves (optionally deselecting ops)
   d. Host calls approve_plan → execute_plan
   e. Server applies ops, journals each, reconciles registry
10. Agent composes summary, calls write_index + write_summary
11. Agent sends final text (no tool calls) → loop ends
12. Desktop notification fires
```

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
                         graph_path, quarantine_dir, max_snippet_chars,
                         allowlist_dirs, profile)
```

Both host and server load `Settings` independently at startup — there is no shared singleton across the process boundary. The server's `_get_settings()` is lazy-initialized and cached per process.

---

## Further reading

- [Module Reference](modules.md) — per-file breakdown with key classes and functions
- [Plan Lifecycle](internals/plan-lifecycle.md) — detailed design doc for the plan/journal system
- [MCP Tools Reference](../reference/mcp-tools.md) — complete tool signatures and semantics
