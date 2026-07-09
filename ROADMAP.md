# Roadmap

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
- [ ] M12 · Log egress (S8) — record which files' contents were sent to the LLM endpoint
      (path + size + timestamp) so an operator can audit what left the machine.
