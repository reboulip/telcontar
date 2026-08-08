# How It Works

Telcontar is built around two processes that communicate over the **Model Context Protocol (MCP)**:

```
User
  │
  ▼
MCP Host  (Textual TUI + agent loop)
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

- **First run** — the **setup wizard** (`SetupScreen`) appears automatically. It guides you through choosing an endpoint (Azure OpenAI or another OpenAI-compatible service), entering the service URL and API key, and selecting a document profile. The key is stored in the OS credential store (Windows Credential Manager / macOS Keychain); other settings go to `~/.telcontar/config.env`.
- **Returning user** — the **startup screen** (`StartupScreen`) appears directly. It offers three actions: **Organize**, **Query**, and **⚙ Settings**. Press `s` or click **⚙ Settings** at any time to open the settings panel (`ConfigScreen`), where you can change the URL, API key, profile, and approval mode.
- **From anywhere** — press `Ctrl+S` at any point in the app, on any screen, to open the same settings panel — not just from the startup screen. It even works while a modal (plan approval, cost estimate) is on screen; the settings panel stacks on top and pops back cleanly. It's a no-op if settings are already open, or during the first-run setup wizard (to avoid persisting a half-configured state that skips the wizard's guided keyring/plaintext-fallback flow).

---

## The starter pane

Pressing **Organize** does not launch the agent immediately. The `OrganizerScreen` opens on a **starter pane** first:

- A code-generated, deterministic **directory overview** (`_directory_overview`) — file count, subfolder count, and the most common file extensions, computed by scanning names and directory structure only. No file content is read and no LLM call is made.
- An optional **steering instructions** field for free text, e.g. "group by workstream", "keep the 2024 invoices together", or "don't quarantine drafts".
- A **Start organizing** button (pressing Enter in the instructions field works too).

Only once you proceed does the chat transcript appear and the agent loop start. Any instructions you typed are shown as a `you` turn in the transcript and passed to `run_agent_loop(..., instructions=...)`, which appends them to the agent's first user message so the run follows your intent instead of organizing blind.

---

## The agent loop

Once you proceed past the starter pane, the **host** launches the **server** as a subprocess. Before any chat turn happens, telcontar analyzes the corpus deterministically; only once that finishes does the LLM tool-calling loop begin, and it follows a fixed two-phase workflow.

### Analysis — before the chat loop starts

This step is **not** the model reasoning turn-by-turn — it is host-orchestrated code, in two stages, so that each document's content is sent to the model **at most once, ever**:

1. **Deterministic discovery (no LLM call).** The host recursively walks the **whole directory tree** (re-walking any subpath the server marks `truncated`, repeating until none remain anywhere — full coverage is mandatory), computes a sha256 checksum for every discovered file, and looks each checksum up against the registry to split the corpus into documents already known from a previous run and genuinely new ones. A known document whose on-disk location has drifted since it was last seen is silently corrected in the registry.
2. **Isolated, per-batch analysis of the NEW documents only.** If there are new documents, telcontar pauses once to show a rough cost estimate scoped to just those new documents — see [The cost-estimate approval gate](#the-cost-estimate-approval-gate) below. On approval, the new documents are processed in **batches of 10** (smaller only for unusually large individual files): each batch's content is fetched (`read_file_batch` or `extract_text_batch` for PDF/Office) and sent to the model in a single, isolated call that must return one structured record — title, type, summary, date, entities — per document in the batch, in order; that call never joins the chat conversation you see afterward. The results are upserted into the **registry** via `record_document_batch`.

The registry is **content-addressed**: if you rename a file, telcontar still recognises it by checksum on the next run, so a re-run only ever analyzes documents it hasn't seen before — previously-known documents are reused from the registry without being re-read or re-sent to the model at all.

Once analysis finishes, the host builds a compact **corpus digest** — every document's title, type, and path, plus totals — and hands it to the agent as the first message of the chat loop, in place of a blank "please organize" instruction. From here on, the chat-loop agent works from the digest and the read-only registry tools (`list_documents`, `get_registry`, `find_duplicates`, `find_modified_documents`, …) — it can no longer read raw file content, checksum a file, or record a document itself; those tools simply aren't offered to it.

### Phase A — Organize

1. The agent designs a **relevant target taxonomy** — a small, shallow, readable folder tree derived from the document types and themes already recorded (e.g. grouped by document type, workstream, or phase). It may redesign the **existing nested layout entirely** — documents already sitting in subfolders are reorganized too, not just those at the top level; it can call `walk_tree` to check the current on-disk layout first. Folders are only created for categories the corpus actually contains.
2. The agent calls `create_plan` to open a new plan
3. It stages operations with `propose_create_dir` for each new folder (idempotent and collision-safe), `propose_rename`, `propose_move` (filing each document into the taxonomy), `propose_quarantine` for duplicates or clutter — each carrying a concrete, stated reason shown at approval time; "unreadable" alone is never accepted as a sufficient reason on its own — `propose_create_file`/`propose_update_file` for any new or updated files, and `propose_archive_document` to withdraw a document from active memory when appropriate — **every filesystem mutation is staged this way; there is no tool that writes to disk directly**
4. It calls `review_plan` for a deduplication pre-flight check
5. It calls `set_plan_rationale` with a short plain-language paragraph explaining the plan's philosophy — how it grouped, renamed, and quarantined documents and why — and `set_plan_folder_notes` with a one-line purpose note for each target folder. The host shows the rationale above the op list in the approval modal, followed by a target-layout tree with the folder notes beside each folder
6. It calls `execute_plan` — at this point the **approval gate** fires. If the agent instead ends its turn right after staging/reviewing the plan without calling `execute_plan`, telcontar detects the built-but-unpresented plan and re-prompts it once automatically — you should never see a plan silently go missing

At any point before or while building the plan, the agent may pause to check in with the user — genuine clarifying questions, competing options to choose between, or a mix — see [The ask_user chat checkpoint](#the-ask_user-chat-checkpoint) below.

### Phase B — Synthesize

1. Throughout the run, the agent records key project milestones with `create_event` — one short, verb-led, dated sentence per decision or delivery
2. The agent calls `build_graph` to project the registry and events into the knowledge graph, then `get_actors` for the ranked main actors and `list_events` for the timeline
3. `write_index` walks the organized tree and emits `INDEX.md` + `manifest.json`
4. The agent composes the project narrative as Markdown — structured by the sections defined in the active profile's `[synthesis]` table — drawing on `list_documents`, `get_registry`, `list_events`, `get_graph`, and `get_actors`. It calls `write_summary` to persist the result as `SUMMARY.md`
5. The agent responds with a final text summary and the loop ends

---

## The cost-estimate approval gate

After deterministic discovery finds the corpus's **new** (previously-unanalyzed) documents but before any of their content is actually fetched, telcontar pauses once to show a rough cost estimate scoped to just those new documents, and lets you decide whether to proceed. This is the run's **primary cost control** — a single upfront checkpoint before the model can trigger real analysis spend, distinct from the adaptive turn budget that only acts as a backstop against a runaway or looping agent.

Because the estimate only ever covers new documents, re-running Organize on a folder that's mostly already analyzed shows a small estimate (or none at all — the gate is skipped entirely when there is nothing new to analyze), not a recalculation of the whole corpus. The estimate itself is a rough, local calculation from the file sizes discovery already found (no extraction and no LLM call needed to produce it), shown as e.g. "~42 new document(s) (10 already analyzed, skipped), ~18,500 input tokens estimated, batched in groups of 10 — proceed?".

This estimate covers **analysis only** — the ANALYZE phase that reads and records the new documents. The chat-driven ORGANIZE phase that follows (planning, staging, and any follow-up chat turns) adds further LLM calls of its own, so the session's actual running token total — shown on the status bar once the run is underway — will end up noticeably higher than this upfront estimate. That running total is cumulative for the whole screen's session (Organize or Query), not just the latest call, and also breaks out how many of the input tokens were served from cache.

```
Discovery finishes; N new (previously-unanalyzed) documents found
       │
       ▼
Host computes a size-based estimate scoped to just those N documents,
shows CostEstimateModal
       │
   User reviews
   ├── Proceed
   │       │
   │       ▼
   │   The new documents are analyzed, then the chat loop begins
   │
   └── Cancel
           │
           ▼
       Analysis is skipped for this run — the new documents stay
       unrecorded; the chat loop begins with only the already-known
       documents in the digest
```

In `APPROVAL_MODE=never`, this gate is skipped automatically (the estimate is still emitted for observability, it just never blocks). In `always` and `destructive_only`, it shows once per run whenever there are new documents to analyze.

---

## The ask_user chat checkpoint

At any point before or while building the plan, the agent **may** call `ask_user` with a short batch of items — plain clarifying questions, multiple-choice options for the user to pick from, or a mix in the same call — when it hits genuine ambiguity (unclear document type, competing taxonomy groupings, ambiguous naming) or there are genuinely several valid ways to classify or handle the corpus (e.g. group COPIL decks by date vs. by workstream vs. one flat folder). If there is no real ambiguity, the agent skips this and proceeds with its own best judgement.

This is a **host-side** capability, not an MCP server tool: `ask_user` is a synthetic tool the host injects into the model's tool list, and it is never forwarded to the MCP server. There is no once-per-run cap: the agent can check in as many times as it genuinely needs to. How you're asked differs by UI:

- **Textual TUI** — no dialog of its own: the question(s) render as a normal `telcontar` turn in the chat transcript, and the agent's tool call blocks on the exact same live-chat message queue described in [Chatting during and after a run](#chatting-during-and-after-a-run-live-chat-resumable-chat) below, so your next chat message is read as the reply.

  ```
  Agent hits genuine ambiguity, at any point before/while building the plan
         │
         ▼
  Agent calls ask_user(questions)   (1-5 items; each may carry 2-5 options)
         │
         ▼
  Question(s) rendered as a "telcontar" turn in the transcript
         │
         ▼
  Agent's tool call blocks on the live-chat message queue
         │
     You type a reply in the chat box
         │
         ▼
  Reply echoed as a "you" turn, returned to the agent as free text;
  the agent continues — and may call ask_user again later if a new
  ambiguity comes up
  ```

- **Web UI** — a modal dialog, matching the plan-approval and cost-estimate dialogs: each question gets its own radio-button choice (when the agent supplied options) plus one free-text "Additional comment" field, and **Submit** / **Skip — you decide** buttons. Submitting composes the reply from your selections — `"<question> → <selected option>"` per answered question, plus your comment if you added one — and sends it back to the agent; **Skip** tells the agent to proceed with its own best judgement instead.

The agent is instructed not to stall or use this to offload every decision — only for real, close judgement calls; otherwise it proceeds with its own best judgement.

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

The plan is only ever shown when the agent calls `execute_plan` — if it finishes staging and reviewing a plan but ends its turn without calling `execute_plan`, telcontar recognises the still-pending plan and re-prompts the agent once to submit it, instead of silently ending the run with an unpresented plan. If that single re-prompt still doesn't get the agent to call `execute_plan`, the run ends normally but its final message names the unexecuted plan rather than losing it without a trace.

---

## Persistence

All state lives under `.organizer/` **inside the target directory** you organized — memory is per-directory, so each organized folder keeps its own registry, plans, and journals. (`PROFILES_DIR` and `.organizer/NAMING.md` are the exception — they stay project-level, since they're cross-corpus conventions rather than per-run memory.)

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

Undo is a **manual, user-only action** — the agent has no tool to trigger it. In the Textual TUI's Organizer screen, press **j** to open the operations-journal viewer, then **u** to revert the most recent journaled operation. In the web UI, click the **Journal** button in the toolbar (visible from the moment a run's page loads, before you even start organizing) to open the same viewer, then **Undo last operation** followed by a confirmation step. Both call `server.tools.undo_last` directly, bypassing the agent entirely. In the web UI, the Undo button is disabled — replaced with an explanatory label — while a tool call is actively in progress, since undo rewrites the whole journal file while the agent's own tool call may still be appending to it.

---

## Chatting during and after a run (live chat + resumable chat)

The chat box (`#organize-input`) at the bottom of the Organizer screen is enabled from the moment you press **Start organizing** — you don't have to wait for the agent to stop before you can type. The same MCP server subprocess stays open for as long as the screen does, whether the agent is actively working or fully idle.

### Live mid-run chat

While the agent is still working — even during the pre-pass/analysis stage, before the first chat turn — a message you type is queued and woven into the run at the next opportunity, without waiting for it to finish: right before the run's first LLM call, after every turn's batch of tool calls completes, and — most importantly — at the moment the agent would otherwise stop (its response carries no more tool calls). At that last point, if a message is waiting, the agent takes it as a new instruction and keeps going instead of ending the run. This lets you course-correct an in-progress run, e.g. "actually, group by year instead", without waiting for it to finish first.

In the **Textual TUI**, this is the same queue [the `ask_user` chat checkpoint](#the-ask_user-chat-checkpoint) blocks on when the agent has a question for you — there, an `ask_user` call is really just a special case of this mechanism, one where the agent is the one waiting on your next message. The **web UI**'s `ask_user` checkpoint uses its own modal dialog instead and never touches this queue.

```
Run in progress (pre-pass / analysis / a turn's tool calls)
       │
   You type a message  →  echoed as a "you" turn, queued
       │
       ▼
Host drains the queue at the next opportunity:
  • before the first LLM call
  • after each turn's tool-call batch
  • when the agent's response has no tool calls (would otherwise stop)
       │
       ▼
Queued message(s) injected as a new user turn; the agent continues
instead of ending the run
```

### Continuing after a run (resumable chat)

A run doesn't have to end the conversation, either. Once it reaches a **terminal state** — it finishes normally, hits an error, or exhausts its turn budget — with nothing left waiting in the queue at that instant, typing a message still works exactly as before:

1. Echoes it into the transcript as a `you` turn
2. Resumes the agent loop with the conversation history returned by the previous call, plus your new message appended as a fresh user turn
3. Runs with the **same organize-mode toolset** as the initial run — plan, execute, write, and every registry/graph/event read tool (document-content tools such as reading or re-extracting a file stay unavailable, since the corpus was already analyzed) — so a follow-up like "quarantine the drafts too" or "actually group these by workstream" continues the *same* conversation instead of starting a fresh one; it also stays just as "live" as the initial run, since the chat queue is wired into every continuation call too

```
Run reaches a terminal state (done / error / max turns),
nothing waiting in the queue
       │
       ▼
"press g or keep chatting" cue shown once
       │
   You type a message
       │
       ▼
Message echoed as a "you" turn; the agent loop resumes on the SAME
session with (history=<previous>, message=<your text>)
       │
       ▼
Agent responds — may call any tool, including execute_plan (a new
plan gets a new approval) — the chat box stays enabled throughout,
live for this continuation just like the initial run
```

This is distinct from **query mode** (`g`): query mode opens a *separate* screen on a *separate* MCP session with a strictly read-only toolset — safe for "just asking" without risking a mutation. The chat box instead continues the mutating conversation in place. Query mode becomes available once a run reaches its first terminal state; the chat box is available for the whole run, live or stopped.

A couple of things carry over differently on a continuation:

- Each chat message gets its **own fresh turn budget**, not a share of the original run's. Since a continuation doesn't re-run the discovery/analysis stage, the adaptive turn-budget calculation (which scales with corpus size) resets to its floor of 50 turns — in practice not a limitation, since a follow-up message is normally a small, targeted ask rather than a fresh full-corpus analysis.
- The desktop notification and the "press g / keep chatting" cue fire only once, on the *first* terminal state — not again after every subsequent chat turn.
- If a turn raises an unhandled error partway through a batch of tool calls, telcontar no longer crashes the conversation: any tool call left without a result is answered with a synthetic error so the history stays valid, and you can keep typing to try again.

---

## Interactive query mode

After a corpus has been analyzed (its `.organizer/` memory exists), telcontar offers a **read-only query mode** where you can ask natural-language questions about it without reorganizing anything.

### How to start query mode

- From the **startup screen**, press **Query**. The selected folder — or one of its parent folders — must contain a `.organizer/` from a previous Organize run; telcontar walks up from the folder you picked until it finds one, so choosing a subfolder of a previously-organized tree still resolves to that tree's memory. If none is found, an error asks you to run Organize first.
- From the **Organizer screen**, press **g** once organizing completes.

### What happens

The host opens a `QueryScreen` — a chat-style TUI with a `RichLog` output area and an `Input` bar. A single MCP server subprocess stays open for the whole session, and conversation history is threaded across questions so the model retains context.

For each question:

1. The host sends the query-mode system prompt (built from the active profile) plus the user's question to the model.
2. The model calls read-only tools to gather facts:
   - `list_documents` / `get_registry` / `get_document` / `lookup_documents` — recorded documents and their metadata (`lookup_documents` is the batch form of `get_document`, one round trip for many checksums)
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
