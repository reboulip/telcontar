# Roadmap

Completed phases (1–15) have been moved to
[ROADMAP-ARCHIVE.md](ROADMAP-ARCHIVE.md) to keep this file focused on open
work.

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

## Phase 18 — Web UI foundations

- [x] S1 · Extract `host/format.py`, `host/paths.py`, `host/narration.py` from `host/app.py` — move the framework-independent formatters (`_fmt_journal_entry`, `_fmt_op`, `_render_target_layout`), path/discovery helpers (`_find_organizer_root`, `_resolve_journal_path`, `_resolve_plans_dir`, `_quarantine_basename`, `_directory_overview`, `_is_op_out_of_scope`, `_target_folders`/`_note_for`), and the `_TOOL_NARRATION` table + collapse rule into shared, UI-agnostic modules; `host/app.py` imports them from their new location. Zero behaviour change — first step of the Textual→NiceGUI migration, so both UIs can share one implementation.
- [x] S2 · Add direct unit tests for the extracted helpers — `tests/test_host_format.py` and `tests/test_host_paths.py`, including the five behaviours currently covered only indirectly through a Textual Pilot test (`_fmt_journal_entry`, `_directory_overview`, `_is_op_out_of_scope`, `_resolve_journal_path`, the narration collapse rule).
- [ ] S3 · Collapse the 8 fully-explicit `run_agent_loop` test doubles in `tests/test_app_ui.py` into one shared `**kwargs` factory — the duplicated 13-parameter header (currently at lines 386, 1111, 1158, 1201, 1247, 1292, 1340, 1412) breaks on any kwarg addition to `host/agent.py`'s `run_agent_loop`; safe `**kwargs`-based doubles already exist elsewhere in the same file as the pattern to follow.
- [ ] S4 · Add the NiceGUI web UI skeleton — `host/web/main.py`, `session.py`, `bridge.py`: `ui.run` on `127.0.0.1` with an ephemeral port, `show=True`, `reload=False`; a `RunSession` keyed by a run id (URL param or `app.storage`) so a page reload re-attaches to an existing run's pending dialog instead of silently orphaning it (validated via spike: an unreconnectable dialog can deadlock the approval gate with no visible symptom); the synchronous `AgentEvent` bridge and the three awaited callbacks (approval/cost/ask_user), driven against the registry's current client.
- [ ] S5 · A single working organizer view in NiceGUI — transcript, status, chat input, approval dialog, cost dialog; deliberately plain, no styling pass yet. Move blocking synchronous I/O off the event loop where it currently runs inline: `_directory_overview` (full `os.walk`), `_load_profile_options` (glob + TOML parse), journal reads, and `server.tools.undo_last` (moves files on disk).
- [ ] S6 · Wire `telcontar --web` into `host/main.py` (default stays the Textual TUI); add `nicegui` to `pyproject.toml`'s main dependencies.

---

## Phase 19 — Parity

*(Web UI gains everything the TUI does and becomes the default `telcontar` entrypoint; the TUI survives as `telcontar --tui` until Phase 21. Safety-critical paths — approval, cost, undo — are ported faithfully; everything else gets a cleaner surface but no redesign.)*

- [ ] T1 · Startup view — folder picker (may already be partially done via Phase 18's S5 directory browser — check before rebuilding), Organize / Query / Settings. A browser can't return a real filesystem path from a native picker, so build this as a server-side browse view on the existing `list_dir` tool.
- [ ] T2 · Setup wizard — 5 steps with real routing (the TUI mounts all five and toggles `.display`). Preserve `PlaintextKeyFallbackNeeded` → warn → explicit second confirmation.
- [ ] T3 · Settings view — same behaviour as the wizard, including the blank-key-preserves-existing rule.
- [ ] T4 · Approval dialog — faithful: per-op toggles returning `removed_op_ids`, refinement text, reject, both disclaimers; refinement takes priority over approval. While here: refresh the file tree on `execute_plan` result (it currently never updates as the agent moves files).
- [ ] T5 · Cost estimate dialog — faithful. While here: fix `batch_size` never being passed (it always claims "groups of 10").
- [ ] T6 · Journal view + undo, with a visible affordance (currently keyboard-only: `j` then `u`) — user-only, the agent still has no undo tool. While here: refresh the ops-journal strip after an undo (it currently goes stale).
- [ ] T7 · Query view — answer pane + tool timeline. While here: fix it calling `load()` without `.for_target()`, unlike the organizer.
- [ ] T8 · Fix the remaining warts found during planning: `error` AgentEvents are wrongly treated as terminal (an analysis-batch failure emits `error` but the run continues — only `done`, max-turns, and the loop's own exception path should end a run); the token counter resets to 0 per continuation (`token_totals` is per-call) — accumulate across calls in the UI, coordinating with Phase 17 R1's fix rather than re-deriving it; the wizard's "Press Finish again" message doesn't match its "Save & continue →" button label.
- [ ] T9 · Replace the Textual Pilot test suite (`tests/test_app_ui.py`, 67 tests) with NiceGUI's headless `user` fixture, reserving the real-browser `screen` fixture for a handful of genuine end-to-end paths; delete the 5 tests that are thin wrappers over `_fmt_op`/`_render_target_layout` now that S2 covers that logic directly.
- [ ] T10 · Flip the default: `telcontar` opens the web UI, `telcontar --tui` becomes the Textual escape hatch. Update README + docs.

---

## Phase 20 — Experience & delivery

*(Scope beyond the two fixed items below is chosen once Phase 19 has seen daily use — pick 3–4 of the candidates below at that point; they're marked `[deferred]` until chosen.)*

- [ ] U1 · Native window — `ui.run(native=True)` via pywebview, with `telcontar --browser` as the escape hatch. Restores "one command, one window".
- [ ] U2 · Security hardening pass — confirm `127.0.0.1` binding, a per-launch token in the opened URL, Origin check, `storage_secret`; have doc-keeper record the new local-server trust boundary in `docs/developer/security-model.md`.
- [ ] U3 · [deferred] Plan review as a before/after tree — the approval gate is the highest-trust screen; a checkbox list is a terminal-era compromise.
- [ ] U4 · [deferred] Document preview pane — click a file, see the PDF/text inline.
- [ ] U5 · [deferred] Corpus browser — a sortable, filterable table over `.organizer/registry.json` (title, type, date, summary, entities), today only reachable by asking the agent.
- [ ] U6 · [deferred] Knowledge-graph view — `server/graph.py` already builds the graph; currently invisible.
- [ ] U7 · [deferred] Live-updating file tree — watch the reorganization happen as ops execute.
- [ ] U8 · [deferred] Per-document progress — replace the single progress bar with "which document, right now".
- [ ] U9 · [deferred] Query answers with citations — link answers back to the documents they came from.

---

## Phase 21 — Retire the TUI

*(Only proceeds if a later decision judges the web UI has earned enough trust to retire the TUI — "keep both forever" is an acceptable outcome, not a failure. All items deferred until that decision is made.)*

- [ ] V1 · [deferred] Delete `host/app.py` and `tests/test_app_ui.py`; remove the `--tui` flag. Only if a later design break decides the web UI has earned enough trust to retire the TUI.
- [ ] V2 · [deferred] Drop `textual` and `textual-dev` from `pyproject.toml` (note: `textual-dev` transitively provides `textual-serve`, the terminal-in-a-browser fallback — removing it drops that too).
- [ ] V3 · [deferred] Docs sweep — 11 files currently mention Textual/TUI (42 occurrences), heaviest in `docs/user-guide/how-it-works.md`, `docs/getting-started/quickstart.md`, `docs/index.md`; prune the `test-select` scope-table rows for the deleted files.

---
