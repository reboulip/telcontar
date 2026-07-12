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

When you run `telcontar`, the app checks whether a minimum configuration (AI service URL + API key) is present:

- **First run** — the **setup wizard** (`SetupScreen`) appears automatically. It guides you through choosing an AI provider, entering the service URL and API key, and selecting a document profile. The key is stored in the OS credential store (Windows Credential Manager / macOS Keychain); other settings go to `~/.telcontar/config.env`.
- **Returning user** — the **startup screen** (`StartupScreen`) appears directly. It offers three actions: **Organize**, **Query**, and **⚙ Settings**. Press `s` or click **⚙ Settings** at any time to open the settings panel (`ConfigScreen`), where you can change the URL, API key, profile, and approval mode.

---

## The starter pane

Pressing **Organize** does not launch the agent immediately. The `OrganizerScreen` opens on a **starter pane** first:

- A code-generated, deterministic **directory overview** (`_directory_overview`) — file count, subfolder count, and the most common file extensions, computed by scanning names and directory structure only. No file content is read and no LLM call is made.
- An optional **steering instructions** field for free text, e.g. "group by workstream", "keep the 2024 invoices together", or "don't quarantine drafts".
- A **Start organizing** button (pressing Enter in the instructions field works too).

Only once you proceed does the chat transcript appear and the agent loop start. Any instructions you typed are shown as a `you` turn in the transcript and passed to `run_agent(..., instructions=...)`, which appends them to the agent's first user message so the run follows your intent instead of organizing blind.

---

## The agent loop

Once you proceed past the starter pane, the **host** launches the **server** as a subprocess and begins a GPT-5 tool-calling loop. The agent follows a fixed three-phase workflow:

### Phase A — Analyse

The agent first surveys the **whole directory tree** with `walk_tree` (recursive, up to `max_depth=3` levels; if a subpath comes back marked `truncated`, it **must** call `walk_tree` again on it, repeating until no truncated directory remains anywhere) so it discovers documents nested in subfolders, not just those sitting at the top level. Full coverage is mandatory — the agent is directed to never sample a subset of the discovered documents.

Then the agent works through the discovered documents in **batches of 10** (smaller only for unusually large individual files). For each batch:

1. Calls `read_file_batch` or `extract_text_batch` (for PDF/Office) to get the content of the batch's paths, and `compute_checksum_batch` for their sha256 content IDs — a bad path in a batch surfaces as that entry's error without failing the rest
2. Calls `record_document_batch` once to upsert title, type, summary, date, and entities for the whole batch into the **registry** — a validation failure on one document doesn't block the rest
3. Once every discovered document is recorded, calls `find_duplicates` and `find_modified_documents` to identify candidates for quarantine

The very first call to any of these four batch tools triggers a one-time **cost-approval gate** — see [The cost-estimate approval gate](#the-cost-estimate-approval-gate) below.

The registry is **content-addressed**: if you rename a file, telcontar still recognises it by checksum on the next run. Analysis results accumulate across sessions.

Between Phase A and Phase B, the agent has two optional, at-most-once checkpoints: it may pause to ask the user a short batch of clarifying questions if it hit genuine ambiguity — see [The clarification checkpoint](#the-clarification-checkpoint) below — and, after re-examining its intended approach from a second angle, it may also surface a few competing options for the user to choose between — see [The multiple-option checkpoint](#the-multiple-option-checkpoint) below.

### Phase B — Organize

1. The agent designs a **relevant target taxonomy** — a small, shallow, readable folder tree derived from the document types and themes actually found in the corpus (e.g. grouped by document type, workstream, or phase). It may redesign the **existing nested layout entirely** — documents already sitting in subfolders are reorganized too, not just those at the top level. Folders are only created for categories the corpus actually contains.
2. The agent calls `create_plan` to open a new plan
3. It stages operations with `propose_create_dir` for each new folder (idempotent and collision-safe), `propose_rename`, `propose_move` (filing each document into the taxonomy), `propose_quarantine` for duplicates or clutter, `propose_create_file`/`propose_update_file` for any new or updated files, and `propose_archive_document` to withdraw a document from active memory when appropriate — **every filesystem mutation is staged this way; there is no tool that writes to disk directly**
4. It calls `review_plan` for a deduplication pre-flight check
5. It calls `set_plan_rationale` with a short plain-language paragraph explaining the plan's philosophy — how it grouped, renamed, and quarantined documents and why — and `set_plan_folder_notes` with a one-line purpose note for each target folder. The host shows the rationale above the op list in the approval modal, followed by a target-layout tree with the folder notes beside each folder
6. It calls `execute_plan` — at this point the **approval gate** fires

### Phase C — Synthesize

1. Throughout the run, the agent records key project milestones with `create_event` — one short, verb-led, dated sentence per decision or delivery
2. The agent calls `build_graph` to project the registry and events into the knowledge graph, then `get_actors` for the ranked main actors and `list_events` for the timeline
3. `write_index` walks the organized tree and emits `INDEX.md` + `manifest.json`
4. The agent composes the project narrative as Markdown — structured by the sections defined in the active profile's `[synthesis]` table — drawing on `list_documents`, `get_registry`, `list_events`, `get_graph`, and `get_actors`. It calls `write_summary` to persist the result as `SUMMARY.md`
5. The agent responds with a final text summary and the loop ends

---

## The cost-estimate approval gate

Before any document content is actually fetched, telcontar pauses once to show a rough cost estimate and let you decide whether to proceed. This is the run's **primary cost control** — a single upfront checkpoint before the model can trigger real analysis spend, distinct from the adaptive turn budget that only acts as a backstop against a runaway or looping agent.

The gate fires on the **first call** to any of the batch tools (`extract_text_batch`, `read_file_batch`, `compute_checksum_batch`, `record_document_batch`) — i.e. right at the start of Phase A's per-batch work — and never again for the rest of the run. The estimate itself is a rough, local calculation from the file sizes already discovered via `walk_tree` (no extraction and no LLM call needed to produce it), shown as e.g. "~42 documents, ~18,500 input tokens estimated, batched in groups of 10 — proceed?".

```
Agent is about to call the first batch tool (e.g. extract_text_batch)
       │
       ▼
Host computes a size-based estimate, shows CostEstimateModal
       │
   User reviews
   ├── Proceed
   │       │
   │       ▼
   │   The call is forwarded and Phase A continues normally
   │
   └── Cancel
           │
           ▼
       The agent is told the user did not approve; it stops and reports back
```

In `APPROVAL_MODE=never`, this gate is skipped automatically (the estimate is still emitted for observability, it just never blocks). In `always` and `destructive_only`, it always shows once per run.

---

## The clarification checkpoint

After Phase A (Analyse) and before Phase B (Organize) begins, the agent **may** pause once to ask the user a short batch of clarifying questions — but only when it hits genuine ambiguity (unclear document type, competing taxonomy groupings, ambiguous naming). If there is no real ambiguity, the agent skips this and moves straight into Phase B with its own best judgement.

This is a **host-side** capability, not an MCP server tool: `ask_clarification` is a synthetic tool the host injects into the model's tool list, and it is never forwarded to the MCP server.

```
Agent finishes Phase A (Analyse)
       │
       ▼
Agent calls ask_clarification(questions)   (at most once per run)
       │
       ▼
Host shows ClarificationModal — one free-text input per question
       │
   User reviews
   ├── Submit answers (any subset, blanks are skipped)
   │       │
   │       ▼
   │   Answers fed back to the agent to refine its decisions before create_plan
   │
   └── Skip — best judgement
           │
           ▼
       Agent proceeds using its own judgement
```

A second call to `ask_clarification` in the same run is refused — the host tells the agent it already asked and to proceed with its own best judgement. The agent is instructed not to stall waiting for answers.

---

## The multiple-option checkpoint

Also between Phase A and Phase B, the agent **may** pause once more — after re-examining its intended approach from a second angle — to let the user choose between competing courses of action, when there are genuinely several valid ways to classify or handle the corpus (e.g. group COPIL decks by date vs. by workstream vs. one flat folder). If one approach is clearly best, the agent skips this and moves straight into Phase B.

Like the clarification checkpoint, this is a **host-side** capability, not an MCP server tool: `propose_options` is a synthetic tool the host injects into the model's tool list, and it is never forwarded to the MCP server.

```
Agent finishes Phase A, re-examines its approach from a second angle
       │
       ▼
Agent calls propose_options(questions)   (at most once per run)
       │
       ▼
Host shows OptionsModal — one RadioSet (2-5 options) per question
       │
   User reviews
   ├── Submit choices (one option per question; first option pre-selected)
   │       │
   │       ▼
   │   Selections fed back to the agent, which follows them before create_plan
   │
   └── Skip — best judgement
           │
           ▼
       Agent proceeds using its own judgement
```

A second call to `propose_options` in the same run is refused — the host tells the agent it already proposed options and to proceed with its own best judgement. Like `ask_clarification`, the agent is instructed to use this only for real, close judgement calls, not to offload every decision, and never to stall.

---

## The approval gate

Before any file is moved or renamed, telcontar shows the full plan to the user and waits for explicit approval. This is the heart of the safety model:

```
Agent proposes plan
       │
       ▼
Host writes the full ops list to .organizer/plan_ops.json,
fetches plan details  →  shows ApprovalModal
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
   ├── Refine (free-text change request, e.g. "merge the drafts into one folder")
   │       │
   │       ▼
   │   Plan is NOT executed — the request is fed back to the agent, which
   │   revises the plan (ops, rationale, folder notes) and calls execute_plan
   │   again to re-present it
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
| `.organizer/plan_ops.json` | Inspectable snapshot of the most recently presented plan's full op list (plan id, rationale, folder notes, ops) — overwritten each time a plan is shown for approval |
| `.organizer/journal.jsonl` | Append-only undo log — every executed file op recorded |
| `.organizer/events.jsonl` | Append-only project event journal — verb-led narrative entries |
| `.organizer/graph.json` | Knowledge graph — derived from registry + events; rebuilt on demand |
| `.organizer/archive.jsonl` | Append-only archive log — documents withdrawn from active memory |

Because the registry is keyed by checksum, moving or renaming a file does **not** lose its analysis. The `execute_plan` function reconciles paths automatically as files move.

### Undoing an operation

Undo is a **manual, user-only action** — the agent has no tool to trigger it. In the Organizer screen, press **j** to open the operations-journal viewer, then **u** to revert the most recent journaled operation. This calls `server.tools.undo_last` directly from the TUI, bypassing the agent entirely.

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
   - `read_file_batch` / `extract_text_batch` / `compute_checksum_batch` — batch forms of the above, for inspecting several files in one round trip
3. The model produces an answer citing specifics (titles, dates, actor names, event sentences) drawn only from the tool results.
4. The answer appears in the log; the next question can be typed immediately.

### Safety guarantees in query mode

The host exposes only the tools in `QUERY_ALLOWED_TOOLS` to the model — no plan, execution, write, `build_graph`, `create_event`, or archive tools are available. Even if the model were to name a mutating tool, the host blocks it before forwarding to the server (defense in depth). Query mode **cannot modify the corpus**.

Press **Esc** to return to the previous screen.

---

## The server's safety invariants

The MCP server enforces five non-negotiable rules in code:

1. **No delete tool exists.** The only removal path is `propose_quarantine`, which moves files to `QUARANTINE_DIR`.
2. **No overwrite.** `check_no_overwrite` raises `FileExistsError` before any move or rename touches an existing destination.
3. **Every destructive op is journaled.** `execute_plan` appends to the undo journal before returning success.
4. **Hard-stop on repeated failures.** More than 3 failures in one `execute_plan` run triggers a hard stop and surfaces the failed ops to the user.
5. **Every mutation goes through the plan flow.** There is no tool that writes, moves, renames, quarantines, or archives a file directly — the agent must always stage a `propose_*` op and apply it via `execute_plan`. Undo, correspondingly, is not something the agent can trigger at all: it's a manual action in the TUI (see [Persistence](#persistence) below).
