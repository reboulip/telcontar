# Roadmap Archive

Completed phases from `ROADMAP.md`, moved here to keep the active roadmap
focused on open work. See [ROADMAP.md](../ROADMAP.md) for current and future
phases.

---

## Phase 1 — Skeleton ✅

- [x] A1 · Project layout — create `host/`, `server/`, `config/` package stubs with `__init__.py`
- [x] A2 · Config layer — implement `config/settings.py` with pydantic-settings and `.env` loading; validate all required env vars at startup
- [x] A3 · MCP server skeleton — `server/main.py` entrypoint (stdio), empty tool stubs matching the CLAUDE.md tool list
- [x] A4 · MCP host skeleton — `host/main.py` agent loop, `host/llm.py` openai SDK wrapper (Azure/Mammouth via `base_url`)

---

## Phase 2 — Server: read-only tools

Core inspection capabilities the agent uses to understand a directory before proposing any changes.

- [x] B1 · `list_dir` — enumerate entries with size, type, and mtime
- [x] B2 · `read_file` — return text content up to `MAX_SNIPPET_CHARS`
- [x] B3 · `extract_text` — extract plain text from PDF/Office files via markitdown
- [x] B4 · `guards` module — no-overwrite check, safe quarantine path generation
- [x] B5 · `move_file` — move a file to a destination directory, respecting the no-overwrite guard
- [x] B6 · `rename_file` — rename a file in place, respecting the no-overwrite guard
- [x] B7 · `create_file` / `update_file` — write or overwrite index output files (`INDEX.md`, `manifest.json`, `SUMMARY.md`)

---

## Phase 3 — Server: plan, execution & journal

Stateful plan lifecycle and reversible execution. Supports multiple concurrent
plans persisted to disk so sessions survive crashes and restarts.

- [x] C1 · Plan data model — structured list of proposed ops with a stable UUID `plan_id`; multiple plans may be active concurrently; each plan serialized as a JSON file under `.organizer/plans/`; states: `pending | approved | executing | done | failed | stopped`
- [x] C2 · `propose_rename`, `propose_move`, `propose_quarantine` — append ops to a named plan (`plan_id` required); eager no-overwrite guard at proposal time; returns updated op list
- [x] C3 · `execute_plan` — apply approved ops with per-op retry (up to 2 retries before marking failed); if more than 3 ops fail in a single run, trigger hard stop and write a detailed failure summary to the journal; each successful op journaled immediately; plan status updated on disk throughout
- [x] C4 · `undo_last` — revert the most recent journaled op and remove it from the journal
- [x] C5 · Journal module — append-only JSONL at `JOURNAL_PATH`; `last` / `pop_last` helpers; hard-stop entries use `op_type: "hard_stop"` with full failure context
- [x] C6 · `review_plan` — deduplication pass before execution; flags ops sharing the same `(src, op_type)` pair; returns a highlighted report without modifying the plan

---

## Phase 4 — Host: agent loop ✅

End-to-end GPT-5 driving the MCP server over stdio.

- [x] D1 · MCP client connection — launch server as subprocess, connect over stdio
- [x] D2 · Tool-calling loop — feed tool results back into the GPT-5 context
- [x] D3 · Plan/approve/execute flow — present plan diff to user; gate `execute_plan` on approval
- [x] D4 · Rich CLI — formatted plan diffs, approval prompts, progress feedback

---

## Phase 5 — Outputs

Artifacts produced after a successful organize run.

- [x] E1 · `write_index` — emit `INDEX.md` (human-readable tree) and `manifest.json` (structured metadata)
- [x] E2 · `write_summary` — emit `SUMMARY.md` describing the directory's contents and changes made
- [x] E3 · File-naming heuristics — conventions for how the model should derive readable file names

---

## Phase 6 — Engine core + IS-IT profile #1

Turn the file-organizer into a profile-driven document-intelligence engine: persistent content-addressed memory + a per-document analysis pass, with all domain-specific vocabulary externalized into a declarative profile. The supplied IS/IT-project requirements ship as the first profile.

- [x] G1 · Domain profile loader — `server/profile.py`: load + validate a TOML profile via stdlib `tomllib`; resolve the active profile from a new `profile` setting; typed accessors (`document_type_ids()`, `entity_roles()`, `salient_cap`, `extraction_fields()`, `naming()`). Ship `profiles/is_it_project.toml` carrying the French document-type vocabulary (communication_formelle, releve_de_decision, document_de_travail, support_copil, support_reunion, draft_officiel, notes, echanges, autre), `salient_cap = 5`, and extraction required/optional fields. Config: add `profile` (default `is_it_project`) and `profiles_dir` (default `profiles/`)
- [x] G2 · Document registry — `server/registry.py`: JSON store at `.organizer/registry.json` keyed by sha256 checksum; dataclass + `to_dict`/`from_dict` + load/save mirroring `server/plan.py`; record fields = checksum, path, title, date|null, type, summary, provenance, entities (list of {name, role, kind}), attributes, status (active|archived|quarantined), first_seen, last_analyzed. Config: add `registry_path` (default `.organizer/registry.json`)
- [x] G3 · `compute_checksum` tool — sha256, chunk-streamed via `hashlib`; pure/deterministic
- [x] G4 · `record_document` + registry read/query tools — `record_document` validates `type` against the active profile (not a hardcoded enum) and enforces the author guardrail (author optional, null unless explicit), upserts by checksum; `get_registry` / `list_documents` read-only dump; `find_duplicates` (exact-checksum collisions + candidate groups for the host to LLM-judge); `find_modified_documents` (same title, differing checksum) (requires: G1, G2, G3)
- [x] G5 · Registry path reconcile — extend `execute_plan` (or add `sync_registry_paths`) to update each record's `path` and set `status="quarantined"` from the undo journal's `src → dst` after execution, so checksum stays the identity while paths track moves (requires: G2)
- [x] G6 · Profile-driven host analysis pass — `host/agent.py`: compose the system prompt from the active profile (document types, extraction fields + guardrails, entity roles, naming), replacing the hardcoded `_SYSTEM_PROMPT_TEMPLATE` + `_DEFAULT_NAMING_CONVENTIONS` (keep `.organizer/NAMING.md` override); add the analysis pass (per doc: extract_text/read_file → derive metadata → compute_checksum → record_document); run order analyse → plan → approve/execute → reconcile (requires: G1, G4)

---

## Phase 7 — Entity graph + project narrative

- [x] H0 · chore: repo-wide `ruff format` — run `ruff format .` so CI's `ruff format --check .` gate passes (currently ~27/32 files are non-compliant, so the next develop→main PR would fail). Do this FIRST, as its own standalone commit, before any Phase 7 feature work.
- [x] H1 · Event journal — `events.jsonl` + `create_event(sentence, date)` (verb-led, dated); distinct from the undo journal
- [x] H2 · Entity / knowledge graph — `server/graph.py`: project registry + events into nodes/edges at `.organizer/graph.json` (derived, reproducible from the registry)
- [x] H3 · Actors — top entities ranked from the graph, capped at the profile's `salient_cap`
- [x] H4 · Project synthesis — enrich `write_summary` to compose the project markdown from registry + events + graph, per the profile's `[synthesis]` template
- [x] H5 · Archived-documents journal — archive log + registry `status` ("retirer de la mémoire")

---

## Phase 8 — Organization, tree & output sinks

- [x] I1 · `create_dir` — collision-safe directory creation
- [x] I2 · Folder README writer — per-folder README of the arborescence
- [x] I3 · Taxonomy classification — relevant-tree reasoning in the host prompt (reuses `propose_move` + `write_index`)
- [x] I4 · `compare_documents(a, b)` — extract both + diff (e.g. successive COPIL slides)
- [x] I5 · Output-sink abstraction — `Sink` protocol; `local_markdown` default built-in; MediaWiki sink plugin (re-admits the gandalf wiki) behind an explicit egress allow-flag

---

## Phase 9 — Interactive query + generality

- [x] J1 · Interactive query mode — NL questions over the registry/graph in the Textual TUI ("charger un doc pour l'interroger", generalized to the whole corpus)
- [x] J2 · Second profile — author a second domain profile (e.g. research-papers or personal-files) purely as data, proving the engine is profile-driven, not IS-IT-shaped
- [ ] J3 · [deferred/hard] Read content of links inside attachments — revisit egress policy first
- [x] J4 · Lossless compression of quarantined archives

---

## Phase 10 — Hardening

Production-readiness and operator ergonomics.

- [x] F1 · End-to-end integration tests against a fixture directory
- [x] F2 · Robust error handling — bad paths, permission errors, partial plan failures
- [x] F3 · `APPROVAL_MODE=destructive_only` — let read-only ops run without approval
- [x] F4 · Packaging — verify `uv run telcontar` and `uv run telcontar-server` entry points work end-to-end
- [x] K1 · Post-analysis clarification checkpoint — after the ANALYZE pass (before building the plan), the agent may surface a batch of clarifying questions when it hits real ambiguity (unclear document type, competing taxonomy groupings, ambiguous naming). `host/agent.py` gains a new `AgentEvent` kind (`"question"`) and an `on_questions_needed` callback (mirrors the existing `on_approval_needed` shape); `host/app.py` gets a `ClarificationModal` (mirrors `ApprovalModal`) shown at most once per run. The agent uses the answers to refine before calling `create_plan`; if the user has nothing to add, the agent proceeds with its own best judgement. System-prompt change: explicitly tell the agent it MAY ask questions at this checkpoint but must not stall waiting for answers indefinitely.
- [x] F5 · Fix TUI quit — bottom-left quit button and 'q' shortcut don't terminate the app [#10]
- [x] F6 · Fix query screen NoMatches error — `#query-log` widget lookup fails on QueryScreen [#9]
- [x] F7 · Fix rename+move sequencing — track file identity (checksum) across chained ops in a plan so a move following a rename in the same run succeeds [#6]
- [x] F8 · Plan-approval philosophy summary — present the plan's rationale in plain language alongside the detailed op list, rather than only the raw op list [#8]
- [x] F9 · Token-consumption estimate — track and display input/output token counts per macro-step in readable format (12K, 3.5M) [#7]
- [x] F10 · Status messages via conversation pane — surface macro-task narration ("Reading files...", "Computing checksums...") while ops run in the side panel [#5]
- [x] F11 · Directory picker — replace the raw address field with a folder-browsing UI for selecting the target directory [#4]

---

## Phase 11 — Interactive UX & deeper exploration

- [x] L1 · Recursive tree exploration — walk nested subfolders during ANALYZE (recursive walk affordance in `server/tools.py` + prompt the agent to descend) and allow redesigning the existing layout, not just the top level [#17]
- [x] L2 · Conversation main pane — restyle the `host/app.py` main pane as a chat transcript with speaker-differentiated turns (telcontar / user / internal steps) and click-to-expand thinking steps [#16]
- [x] L3 · Prior-instructions conversation starter — before ANALYZE, summarize the directory from names/structure and invite steering instructions instead of auto-organizing; feed them into the agent's first turn (requires: L2) [#15]
- [x] L4 · Operations journal at the bottom — move the ops journal to a bottom panel with one-line entries and horizontal scrolling; `host/app.py` [#12]
- [x] L5 · Plan target-layout preview — render the proposed folder tree with per-folder purpose notes in the plan/approval view [#14]
- [x] L6 · Natural-language plan editing — accept free-text plan refinements ("merge X with Y", "don't quarantine Z") and regenerate a revised plan; show the plan summary in the UI with the detailed ops as an inspectable JSON file (requires: L2) [#13]
- [x] L7 · Multiple-option proposals — let the agent self-review from a second angle and surface competing classification/handling options as user-facing questions; builds on the K1 clarification checkpoint [#18]

---

## Phase 12 — Security hardening (remediation plan)

Closes findings from `docs/developer/security-model.md` (S1–S8), in the priority order
(P0→P3) that document lays out. `docs/developer/security-model.md` must be kept current
as each item lands: cross off the remediation item there, and update any affected
trust-boundary / capability-surface / findings-register prose so the doc describes
current behaviour, not just the original audit. Item #9 from that document (profiles/
`NAMING.md` as trusted config, S6) is intentionally excluded from this sprint by
explicit decision — already marked skipped in the security doc, not tracked here.

- [x] M1 · Gate every mutating tool by routing it through the plan flow (S1) — remove
      `move_file`, `rename_file`, `create_file`, `update_file`, `create_dir`,
      `archive_document`, `compress_quarantine` from the toolset advertised to the agent
      in organize mode. Add matching plan-op types and `propose_*` tools
      (`propose_create_file`, `propose_update_file`, `propose_create_dir`,
      `propose_archive_document`, `propose_compress_quarantine`) so every mutation becomes
      a proposed op that goes through `create_plan` → `review_plan` → `approve_plan` →
      `execute_plan`, the same lifecycle renames/moves/quarantine already use. Keep
      `undo_last` as an explicit user action only, never an agent-callable tool.
- [x] M2 · Path-confinement guard on every path-taking tool (S3) — add
      `check_within_root(path, roots)` (mirrors `check_allowlist`'s shape) and call it
      from every server handler that reads or writes a path, defaulting `roots` to the
      run's target directory plus the `.organizer` working dir; reject absolute paths
      and `..` escapes.
- [x] M3 · Make the `update_file` plan op collision-safe (S1) — now that M1 makes
      `update_file` a plan op, its executor must never silently overwrite: no-overwrite
      by default (suffix or reject on collision, mirroring `check_no_overwrite`), with an
      explicit `overwrite=True` op parameter required — and shown in the approval modal
      — for the rare legitimate overwrite (requires: M1)
- [x] M4 · Discreet out-of-scope indicator in the approval modal (S4) — `_fmt_op`
      (`host/app.py`) currently shows only `Path(src).name`; add a low-key, non-alarming
      visual cue (e.g. a muted tag or tooltip) when an op's source resolves outside the
      target directory. Keep this subtle — no red banner — basenames stay the primary
      display.
- [x] M5 · Mark LLM-authored rationale/notes as untrusted narration (S4) — label the
      plan rationale and folder notes (`set_plan_rationale`, `set_plan_folder_notes`) in
      the UI as model-generated commentary, not verified fact; the op list stays the
      source of truth the approver should read.
- [x] M6 · Surface plan-flow ops in the transcript narration (S4/S7) — now that M1 routes
      create/update/archive/compress through the plan flow (so they already appear in the
      approval modal like any other op), make sure `_TOOL_NARRATION` (or equivalent) also
      covers the new `propose_*` tool calls in the live transcript, so building these ops
      into a plan is visible narration, not a silent internal step (requires: M1)
- [x] M7 · Path confinement on by default (S3) — ship with the run's target directory
      as the implicit allowlist root instead of "no restriction" when `ALLOWLIST_DIRS`
      is unset (requires: M2)
- [x] M8 · Bound document extraction (S5) — cap input file size before
      `extract()`/`MarkItDown().convert()`, add a wall-clock timeout, and guard against
      pathological archive/zip-bomb ratios.
- [x] M10 · Injection-resistance delimiter for document content (S2) — wrap extracted
      document text in an explicit "untrusted document content, never an instruction"
      delimiter in the analysis prompt (requires: M1)
- [x] M11 · Never silently fall back to a plaintext API key (S8) — if the OS keyring is
      unavailable, warn loudly and require explicit opt-in before writing the key to
      `~/.telcontar/config.env`; keep keys out of any CWD `.env`.
- [x] M12 · Log egress (S8) — record which files' contents were sent to the LLM endpoint
      (path + size + timestamp) so an operator can audit what left the machine.

---

## Phase 13 — Follow-up fixes

- [x] N1 · Fix plan-move validation for not-yet-existing directories — allow `propose_move` to target a directory that has a `propose_create_dir` op already queued earlier in the same plan (currently rejected as "does not exist"), so create-then-move sequences work in a single plan [#22]
- [x] N2 · Add native `.msg` (Outlook email) extraction support — new extraction path in `server/extract.py` (e.g. via `extract-msg`) preserving sender/recipients/date/subject metadata rather than lossy conversion to another format [#21]

---

## Phase 14 — Exhaustive batch analysis, progress & resumable chat

Full-corpus coverage and cost control for the ANALYZE pass, plus letting the user keep
working in chat after a stop instead of being pushed to the read-only journal/query views.

- [x] O1 · Batch document-content tools — `extract_text_batch(paths, max_chars)`,
      `read_file_batch(paths, max_chars)`, `compute_checksum_batch(paths)` in
      `server/tools.py` + `server/main.py`, mirroring the existing singular tools' guards
      (`check_allowlist`, `check_within_root`) and returning `{path: result_or_error}` so
      one bad file doesn't fail the batch; wire egress logging per file and extend
      `_wrap_untrusted_content` in `host/agent.py` to delimit each file's content
      individually.
- [x] O2 · `record_document_batch` tool — accepts a list of document dicts (same shape as
      `record_document`'s params) and upserts each into the registry in one call,
      collecting per-document validation errors instead of failing the whole batch
      (requires: O1).
- [x] O3 · Rewrite the ANALYZE prompt for batching + full coverage — update step A of
      `_SYSTEM_PROMPT_TEMPLATE` in `host/agent.py` to have the agent work through
      documents in batches via the new tools (factoring the prompt instead of one document
      per LLM turn), and explicitly require every document discovered by `walk_tree` to be
      analyzed before moving to ORGANIZE — never sample a subset (requires: O1, O2).
- [x] O4 · Adaptive turn budget — replace the fixed `_MAX_TURNS = 50` in `host/agent.py`
      with a budget that scales with the number of documents discovered, so a large corpus
      doesn't hit an artificial ceiling mid-analysis; keep a sane hard ceiling as a safety
      valve against runaway loops.
- [x] O5 · Analysis progress tracking — track documents discovered (accumulated from
      `walk_tree` results) vs. documents analyzed (from `record_document`/
      `record_document_batch` calls) inside `run_agent_loop`, and emit a new `"progress"`
      `AgentEvent` on each change.
- [x] O6 · Progress bar in the TUI — add a Textual `ProgressBar` to `OrganizerScreen`,
      wired to the `"progress"` event, showing analyzed/total document counts during the
      run (requires: O5).
- [x] O7 · Resumable chat after a stop — refactor `run_agent_loop` to take/return
      conversation history (mirroring `run_query_loop`'s `history` in/out shape) so a run
      that finished, errored, or hit the turn ceiling can be continued with a new free-text
      user message using the same mutating toolset, instead of only offering the journal
      viewer or read-only query mode. Add a chat `Input` to `OrganizerScreen` (mirroring
      `QueryScreen`'s pattern) enabled once the run reaches a terminal state, keeping the
      MCP session open across turns.
- [x] O8 · Pre-ANALYZE token-estimate approval gate — before the first
      `extract_text_batch`/`read_file_batch`/`compute_checksum_batch`/`record_document_batch`
      call in a run, the host computes a rough total input-token estimate for the whole
      ANALYZE pass from the documents discovered so far via `walk_tree` (e.g.
      `min(file_size, max_snippet_chars) / 4` per file, summed) and gates those batch
      tool calls behind a one-time user approval — mirroring `execute_plan`'s
      `on_approval_needed`/`APPROVAL_MODE` gating pattern in `host/agent.py`'s
      `_dispatch` — showing something like "~N documents, ~M input tokens estimated,
      batched in groups of 10 — proceed?". This is a single approval for the whole
      ANALYZE pass, not one per batch. Add a matching `CostEstimateModal` in
      `host/app.py` (mirrors `ApprovalModal`) (requires: O1, O2, O5).

---

## Phase 15 — Stateless analysis, per-directory memory & live chat

Kill the quadratic ANALYZE token cost (content sent to the API at most once, ever),
make `.organizer` live inside the organized directory and skip already-analyzed
files on re-runs, keep the chat input live for the whole run (clarifications and
option picks become normal chat turns), and stop create-dir/move ordering from
hard-stopping plan execution.

- [x] P1 · Two-sub-phase plan execution — `execute_plan` runs all `create_dir` ops
      first, then file ops, preserving relative order within each group; the `move`
      executor creates missing destination parents so a deselected or failed
      `create_dir` can no longer cascade into a hard stop.
- [x] P2 · Per-directory `.organizer` memory — `Settings.for_target(target)` rebases
      every relative memory path (journal, events, plans, registry, graph, archive,
      egress, `_quarantine`) onto the target dir; applied in `config.settings.load()`
      when `TARGET_DIR` is set (server) and explicitly in the host worker/screens;
      server CWD stays at project root. Hide `.organizer` from `walk_tree`,
      `write_index` and the starter-pane overview. No migration (beta).
- [x] P3 · `lookup_documents(checksums)` read-only tool — batch registry lookup
      `{checksum: record | null}`; add to `QUERY_ALLOWED_TOOLS`. (requires: P2)
- [x] P4 · Deterministic host pre-pass — host code (no LLM) walks the tree to
      exhaustion (re-walking `truncated` dirs), checksums via
      `compute_checksum_batch`, partitions known/new via `lookup_documents`,
      re-homes known records whose path changed, emits `progress` events.
      (requires: P2, P3)
- [x] P5 · Stateless analyzer with accurate cost gate — per batch of ≤10 NEW docs,
      fetch via `extract_text_batch`/`read_file_batch` (egress/confinement/bounds
      unchanged), one isolated LLM call with profile extraction rules + untrusted
      delimiters, forced `submit_document_records` tool call, records rejoined to
      host-authoritative path/checksum by index, persisted via
      `record_document_batch`. Cost gate fires once, counts only new docs, skipped
      when nothing is new. (requires: P4)
- [x] P6 · ORGANIZE-only agent loop + digest — `run_agent_loop` runs pre-pass +
      analyzer internally on a fresh run, seeds the conversation with the digest,
      rewrites system-prompt section A ("corpus already analyzed"), removes the
      in-loop `_COST_GATED_BATCH_TOOLS` gate, feeds pre-pass corpus size into the
      turn budget. (requires: P5)
- [x] P7 · Live mid-run chat — `run_agent_loop` gains `message_queue`; queued user
      messages injected as user turns between agent turns; `#organize-input`
      enabled for the whole run. (requires: P6)
- [x] P8 · `ask_user` chat checkpoint — merge `ask_clarification`/`propose_options`
      into one synthetic `ask_user` tool that renders in the transcript and awaits
      the next chat message; delete `ClarificationModal`/`OptionsModal`.
      `CostEstimateModal` reworded to "N new documents (M already analyzed,
      skipped)". (requires: P7)
- [x] P9 · Settings from anywhere — app-level `ctrl+s` binding opening
      `ConfigScreen` from any screen, guarded against double-push.

---

## Phase 16 — Follow-up fixes

- [x] Q1 · Fix `review_plan` flagging `create_dir` ops as missing_sources — a `create_dir` op's `src` is the not-yet-created destination directory path, so `review_plan`'s existence check (`server/tools.py`) produces a permanent false-positive that blocks plan approval for any plan containing directory creation [#26]
- [x] Q2 · Emit `progress` AgentEvent per analysis batch — move the progress computation/emission (`host/agent.py`, currently only after the full `_analyze_new_documents` loop completes) inside its per-batch loop, so the TUI progress bar advances incrementally instead of jumping from 0 to ~100% at the end [#25]

---

## Phase 17 — Follow-up fixes (round 2)

- [x] R1 · Fix token-count discrepancy between displayed running totals (`_accumulate_tokens` in host/agent.py) and actual API-reported usage — investigate whether totals are double-counted or a wrong field/estimate is being accumulated. One probable reason is that the total token count is added to the previous total of the session, rather than updating the total value. [#27]
- [x] R2 · Add a per-step token profiling log (input/output tokens per analysis batch/LLM call) to a local log file, to enable optimization analysis [#27]
- [x] R3 · Update docs (README, docs/**) and the UI to describe telcontar as backend-agnostic (any OpenAI-compatible endpoint) rather than GPT-5/Mammouth/Azure-specific [#28]

---

> #### ◆ Break 1 — resolved (Stage 0 spike, predates Phase 18)
>
> A throwaway spike (not in the repo) validated four assumptions behind the Phase 18-22
> plan against a real corpus and a real LLM endpoint, before any of these phases were
> implemented:
>
> 1. **Event delivery** — a synchronous `on_event` mutating elements directly, held
>    inside `with client:` for the run's lifetime, kept pace with a real run without
>    dropping events.
> 2. **Awaited dialogs** — `await ui.dialog()` held open 20-30s+ without stalling the
>    page or the engine's MCP session.
> 3. **The one-queue idiom** — a chat message sent mid-run reached `message_queue` and
>    was correctly labelled in the transcript.
> 4. **Tab lifecycle — the one that changed the plan.** A bare reload does NOT kill or
>    cleanly detach the background run; it silently orphans it instead (the run keeps
>    calling tools/the LLM, but every UI element it touches -- including any future
>    dialog -- targets the now-dead client, so an approval/cost gate hit after reload
>    deadlocks forever with zero visible symptom).
>
> **Resolution:** direct element mutation from a background task, held inside `with
> client:`, is the winning event-delivery pattern. The reload finding means a run
> registry with real reconnect (keyed by run id, re-attaching a fresh page load to an
> existing run's pending dialog) is REQUIRED from Phase 18's S4, not deferred -- this is
> already folded into S4's item text below. Nothing else the spike touched contradicted
> the plan.

---

## Phase 18 — Web UI foundations

- [x] S1 · Extract `host/format.py`, `host/paths.py`, `host/narration.py` from `host/app.py` — move the framework-independent formatters (`_fmt_journal_entry`, `_fmt_op`, `_render_target_layout`), path/discovery helpers (`_find_organizer_root`, `_resolve_journal_path`, `_resolve_plans_dir`, `_quarantine_basename`, `_directory_overview`, `_is_op_out_of_scope`, `_target_folders`/`_note_for`), and the `_TOOL_NARRATION` table + collapse rule into shared, UI-agnostic modules; `host/app.py` imports them from their new location. Zero behaviour change — first step of the Textual→NiceGUI migration, so both UIs can share one implementation.
- [x] S2 · Add direct unit tests for the extracted helpers — `tests/test_host_format.py` and `tests/test_host_paths.py`, including the five behaviours currently covered only indirectly through a Textual Pilot test (`_fmt_journal_entry`, `_directory_overview`, `_is_op_out_of_scope`, `_resolve_journal_path`, the narration collapse rule).
- [x] S3 · Collapse the 8 fully-explicit `run_agent_loop` test doubles in `tests/test_app_ui.py` into one shared `**kwargs` factory — the duplicated 13-parameter header (currently at lines 386, 1111, 1158, 1201, 1247, 1292, 1340, 1412) breaks on any kwarg addition to `host/agent.py`'s `run_agent_loop`; safe `**kwargs`-based doubles already exist elsewhere in the same file as the pattern to follow.
- [x] S4 · Add the NiceGUI web UI skeleton — `host/web/main.py`, `session.py`, `bridge.py`: `ui.run` on `127.0.0.1` with an ephemeral port, `show=True`, `reload=False`; a `RunSession` keyed by a run id (URL param or `app.storage`) so a page reload re-attaches to an existing run's pending dialog instead of silently orphaning it (validated via spike: an unreconnectable dialog can deadlock the approval gate with no visible symptom); the synchronous `AgentEvent` bridge and the three awaited callbacks (approval/cost/ask_user), driven against the registry's current client.
- [x] S5 · A single working organizer view in NiceGUI — transcript, status, chat input, approval dialog, cost dialog; deliberately plain, no styling pass yet. Move blocking synchronous I/O off the event loop where it currently runs inline: `_directory_overview` (full `os.walk`), `_load_profile_options` (glob + TOML parse), journal reads, and `server.tools.undo_last` (moves files on disk).
- [x] S6 · Wire `telcontar --web` into `host/main.py` (default stays the Textual TUI); add `nicegui` to `pyproject.toml`'s main dependencies.

> #### ◆ Break 2 — resolved (first real browser session, after Phase 18)
>
> Phase 18 shipped and the web UI was driven against a real corpus for the first time.
> The reactions below became **Phase 19**, deliberately inserted *before* the parity
> port so the eight remaining screens land in their final frame instead of being
> rebuilt after it.
>
> - **Layout of the organizer screen** → the sidebar shape is right in principle, but
>   the sidebar must be **permanent on every screen** (it's where the user verifies that
>   things actually happened), much **denser**, a **real tree** rather than a flat list,
>   **wider**, and **live-resizable**. (Phase 19 T2/T3/T4.)
> - **Transcript form** → split it. Only user↔telcontar exchanges belong in the
>   conversation; telcontar's own steps should read as **logs**, not chat bubbles. The
>   internal-steps idea is worth keeping but is useful only rarely, so it should take
>   very little space — a small per-line toggle opening a **separate zone**, with
>   **pretty-printed JSON** instead of raw dumps. (Phase 19 T5/T6.)
> - **Visual identity** → don't settle for Quasar's defaults. Establish telcontar's own:
>   its namesake is a human/Númenórean king with elven ties — gold-and-silver accents,
>   an elvish-flavoured but genuinely readable display face. (Phase 19 T8.)
> - **Does anything suggest reordering the parity work?** → yes, the whole shell and
>   interaction layer moves ahead of it. Real use also surfaced one defect that is *not*
>   a UI problem at all: the plan is never presented for approval unless the user
>   explicitly asks for it (Phase 19 T1) — an engine-level flow bug that hits the TUI
>   just as hard.

---

## Phase 19 — Shell, interaction model & identity

*(Break 2's feedback, folded in ahead of the parity port: the frame every screen lives in — permanent navigation, a real tree, the conversation/log split, telcontar's own visual identity — plus one engine-level approval-flow bug found in real use. Ordered defect-first, then shell, then the surfaces that hang off it.)*

- [x] T1 · Fix: a finished plan is never presented for approval unless the user asks — the approval gate fires *only* from `_handle_execute_plan` (`host/agent.py`), i.e. only when the model actually calls the `execute_plan` tool. Observed at Break 2: the model completed ANALYZE, built and saved the plan, then ended its turn without calling `execute_plan`, so the run went terminal with no dialog and only a follow-up "I approve the plan" chat message drove it to call the tool. This is engine-level and affects the Textual TUI identically — fix the ORGANIZE-phase prompt/loop so a completed plan always leads into `execute_plan`, and add a loop-level guard so a run that is about to end with a saved-but-never-executed plan re-prompts the model once instead of stopping silently.
- [x] T2 · Persistent app shell — a left navigation/inspection sidebar rendered on **every** route, not just the run view, so the tree stays visible while the user checks that things happen correctly. Build it once as a shared layout (a `ui.left_drawer`-based shell function that each `@ui.page` mounts) rather than per-page markup; `host/web/main.py`'s pages currently each compose their own bare column.
- [x] T3 · A real file-tree view in the sidebar — replace the flat one-button-per-folder listing built in S5 with a genuine collapsible tree (`ui.tree`) over the target directory, at a far denser vertical rhythm (the current spacing wastes most of the column). This doubles as the folder picker, so it supersedes the browse-view half of Phase 20's U1 — build it once, here. Live refresh as ops execute stays deferred (Phase 21 V7).
- [x] T4 · Sidebar width — wider by default, and **live-resizable** by the user (drag handle, remembered for the session), so deep trees aren't cramped and the main content doesn't have to shrink to compensate.
- [x] T5 · Split conversation from logs — only genuine user↔telcontar exchanges (chat messages, `ask_user` checkpoints, approval/cost outcomes) render as conversation; telcontar's own tool activity renders as a compact log stream in its own zone, never as chat bubbles. `host/web/session.py`'s `TranscriptItem` already carries the `kind` discriminator ("turn" vs "steps"); this is a rendering change plus a per-`AgentEvent`-kind routing decision in `host/web/bridge.py`.
- [x] T6 · Internal steps — minimal footprint, readable payloads: one compact line per step with a small toggle that opens the raw detail in a **separate zone** (side panel or drawer) instead of today's inline `ui.expansion` shoving the transcript around; pretty-print JSON payloads (indented, syntax-highlighted) rather than the current single-line `_fmt_result` dump.
- [x] T7 · Browser tab title — "telcontar", plus the target directory once one is selected (e.g. `telcontar — invoices-2024`); every tab currently reads "NiceGUI" (`ui.run(title=…)` plus per-page `ui.page(title=…)`).
- [x] T8 · Visual identity — a palette and type treatment of telcontar's own, expressing its namesake: a Númenórean/human king (Aragorn's Quenya name), his elven connections, and kingly metal — gold and silver accents on a dark base, an elvish-flavoured but genuinely readable display face for headings against a plain, legible body face. Apply it through Quasar/NiceGUI theme tokens (`ui.colors` plus one small CSS layer) so the identity lives in one place rather than being sprinkled per-component.

---
