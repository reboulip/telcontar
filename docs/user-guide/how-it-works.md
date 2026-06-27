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

1. The agent calls `create_plan` to open a new plan
2. It stages operations with `propose_rename`, `propose_move`, and `propose_quarantine`
3. It calls `review_plan` for a deduplication pre-flight check
4. It calls `execute_plan` — at this point the **approval gate** fires

### Phase C — Synthesize

1. `write_index` walks the organized tree and emits `INDEX.md` + `manifest.json`
2. The agent composes a narrative summary and calls `write_summary` to persist `SUMMARY.md`
3. The agent responds with a final text summary and the loop ends

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
| `.organizer/journal.jsonl` | Append-only undo log — every executed op recorded |

Because the registry is keyed by checksum, moving or renaming a file does **not** lose its analysis. The `execute_plan` function reconciles paths automatically as files move.

---

## The server's safety invariants

The MCP server enforces four non-negotiable rules in code:

1. **No delete tool exists.** The only removal path is `propose_quarantine`, which moves files to `QUARANTINE_DIR`.
2. **No overwrite.** `check_no_overwrite` raises `FileExistsError` before any move or rename touches an existing destination.
3. **Every destructive op is journaled.** `execute_plan` appends to the undo journal before returning success.
4. **Hard-stop on repeated failures.** More than 3 failures in one `execute_plan` run triggers a hard stop and surfaces the failed ops to the user.
