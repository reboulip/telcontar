# How It Works

Telcontar is built around two processes that communicate over the **Model Context Protocol (MCP)**:

```
User
  │
  ▼
MCP Host  (Textual TUI + GPT-5 agent loop)
  │  stdio transport
  ▼
MCP Server  (guarded file tools, plan engine, registry)
  │
  ▼
Local filesystem  +  .organizer/ state
```

---

## Startup flow

When you run `organizer-host`, the app checks whether a minimum configuration (AI service URL + API key) is present:

- **First run** — the **setup wizard** (`SetupScreen`) appears automatically. It guides you through choosing an AI provider, entering the service URL and API key, and selecting a document profile. The key is stored in the OS credential store (Windows Credential Manager / macOS Keychain); other settings go to `~/.telcontar/config.env`.
- **Returning user** — the **startup screen** (`StartupScreen`) appears directly. It offers three actions: **Organize**, **Query**, and **⚙ Settings**. Press `s` or click **⚙ Settings** at any time to open the settings panel (`ConfigScreen`), where you can change the URL, API key, profile, and approval mode.

---

## The agent loop

When you point telcontar at a directory, the **host** launches the **server** as a subprocess and begins a GPT-5 tool-calling loop. The agent follows a fixed three-phase workflow:

### Phase A — Analyse

For each document the agent:

1. Calls `read_file` or `extract_text` (for PDF/Office) to get the content
2. Calls `compute_checksum` to obtain the file's sha256 content ID
3. Calls `record_document` to upsert title, type, summary, date, and entities into the **registry**
4. Calls `find_duplicates` and `find_modified_documents` to identify candidates for quarantine

The registry is **content-addressed**: if you rename a file, telcontar still recognises it by checksum on the next run. Analysis results accumulate across sessions.

### Phase B — Organize

1. The agent designs a **relevant target taxonomy** — a small, shallow, readable folder tree derived from the document types and themes actually found in the corpus (e.g. grouped by document type, workstream, or phase). It creates each folder with `create_dir` (idempotent and collision-safe). Folders are only created for categories the corpus actually contains.
2. The agent calls `create_plan` to open a new plan
3. It stages operations with `propose_rename`, `propose_move` (filing each document into the taxonomy), and `propose_quarantine` for duplicates or clutter
4. It calls `review_plan` for a deduplication pre-flight check
5. It calls `execute_plan` — at this point the **approval gate** fires

### Phase C — Synthesize

1. Throughout the run, the agent records key project milestones with `create_event` — one short, verb-led, dated sentence per decision or delivery
2. The agent calls `build_graph` to project the registry and events into the knowledge graph, then `get_actors` for the ranked main actors and `list_events` for the timeline
3. `write_index` walks the organized tree and emits `INDEX.md` + `manifest.json`
4. The agent composes the project narrative as Markdown — structured by the sections defined in the active profile's `[synthesis]` table — drawing on `list_documents`, `get_registry`, `list_events`, `get_graph`, and `get_actors`. It calls `write_summary` to persist the result as `SUMMARY.md`
5. The agent responds with a final text summary and the loop ends

---

## The approval gate

Before any file is moved or renamed, telcontar shows the full plan to the user and waits for explicit approval. This is the heart of the safety model:

```
Agent proposes plan
       │
       ▼
Host fetches plan details  →  shows ApprovalModal
       │
   User reviews
   ├── Approve (with optional per-op deselection)
   │       │
   │       ▼
   │   approve_plan → execute_plan
   │       │
   │       ▼
   │   Each op executed + journaled + registry reconciled
   │
   └── Reject
           │
           ▼
       Agent receives "Plan rejected" and revises
```

The gate is controlled by `APPROVAL_MODE`. See [Approval Modes](approval-modes.md).

---

## Persistence

All state lives under `.organizer/` in the **project root** (not the target directory):

| File | What it stores |
|---|---|
| `.organizer/registry.json` | Document records keyed by sha256 — the engine's memory |
| `.organizer/plans/<uuid>.json` | One JSON file per plan, with ops and state machine |
| `.organizer/journal.jsonl` | Append-only undo log — every executed file op recorded |
| `.organizer/events.jsonl` | Append-only project event journal — verb-led narrative entries |
| `.organizer/graph.json` | Knowledge graph — derived from registry + events; rebuilt on demand |
| `.organizer/archive.jsonl` | Append-only archive log — documents withdrawn from active memory |

Because the registry is keyed by checksum, moving or renaming a file does **not** lose its analysis. The `execute_plan` function reconciles paths automatically as files move.

---

## Interactive query mode

After a corpus has been analyzed (registry exists), telcontar offers a **read-only query mode** where you can ask natural-language questions about it without reorganizing anything.

### How to start query mode

- From the **startup screen**, press **Query** (the registry must already exist at `REGISTRY_PATH`).
- From the **Organizer screen**, press **g** once organizing completes.

### What happens

The host opens a `QueryScreen` — a chat-style TUI with a `RichLog` output area and an `Input` bar. A single MCP server subprocess stays open for the whole session, and conversation history is threaded across questions so the model retains context.

For each question:

1. The host sends the query-mode system prompt (built from the active profile) plus the user's question to GPT-5.
2. GPT-5 calls read-only tools to gather facts:
   - `list_documents` / `get_registry` / `get_document` — recorded documents and their metadata
   - `list_events` — the dated project timeline
   - `get_graph` / `get_actors` — the knowledge graph and ranked main actors
   - `find_duplicates` / `find_modified_documents` — duplicate clusters and modified versions
   - `list_archived` — documents withdrawn from active memory
   - `list_dir` / `read_file` / `extract_text` / `compare_documents` / `compute_checksum` — for ad-hoc file inspection
3. The model produces an answer citing specifics (titles, dates, actor names, event sentences) drawn only from the tool results.
4. The answer appears in the log; the next question can be typed immediately.

### Safety guarantees in query mode

The host exposes only the tools in `QUERY_ALLOWED_TOOLS` to the model — no plan, execution, write, `build_graph`, `create_event`, or archive tools are available. Even if the model were to name a mutating tool, the host blocks it before forwarding to the server (defense in depth). Query mode **cannot modify the corpus**.

Press **Esc** to return to the previous screen.

---

## The server's safety invariants

The MCP server enforces four non-negotiable rules in code:

1. **No delete tool exists.** The only removal path is `propose_quarantine`, which moves files to `QUARANTINE_DIR`.
2. **No overwrite.** `check_no_overwrite` raises `FileExistsError` before any move or rename touches an existing destination.
3. **Every destructive op is journaled.** `execute_plan` appends to the undo journal before returning success.
4. **Hard-stop on repeated failures.** More than 3 failures in one `execute_plan` run triggers a hard stop and surfaces the failed ops to the user.
