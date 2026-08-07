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

## Phase 20 — Parity

*(Web UI gains everything the TUI does and becomes the default `telcontar` entrypoint; the TUI survives as `telcontar --tui` until Phase 22. Safety-critical paths — approval, cost, undo — are ported faithfully; everything else gets a cleaner surface but no redesign. Everything here is built inside the Phase 19 shell.)*

- [ ] U1 · Startup view — Organize / Query / Settings entry points. The folder-picker half is superseded by Phase 19's T3 sidebar tree (built once, in the shell) — check before rebuilding. A browser can't return a real filesystem path from a native picker, so selection stays a server-side browse over the existing `list_dir` tool.
- [x] U2 · Setup wizard — 5 steps with real routing (the TUI mounts all five and toggles `.display`). Preserve `PlaintextKeyFallbackNeeded` → warn → explicit second confirmation.
- [x] U3 · Settings view — same behaviour as the wizard, including the blank-key-preserves-existing rule.
- [ ] U4 · Approval dialog — faithful: per-op toggles returning `removed_op_ids`, refinement text, reject, both disclaimers; refinement takes priority over approval. While here: refresh the Phase 19 T3 sidebar tree on `execute_plan` result (it currently never updates as the agent moves files).
- [ ] U5 · Cost estimate dialog — faithful. While here: fix `batch_size` never being passed (it always claims "groups of 10").
- [ ] U6 · Journal view + undo, with a visible affordance (currently keyboard-only: `j` then `u`) — user-only, the agent still has no undo tool. While here: refresh the ops-journal strip after an undo (it currently goes stale). This is also where Phase 18's deferred `run.io_bound` offloads for journal reads and `server.tools.undo_last` finally get a real call site.
- [ ] U7 · Query view — answer pane + tool timeline. While here: fix it calling `load()` without `.for_target()`, unlike the organizer.
- [x] U8 · Fix the remaining warts found during planning: `error` AgentEvents are wrongly treated as terminal (an analysis-batch failure emits `error` but the run continues — only `done`, max-turns, and the loop's own exception path should end a run); the token counter resets to 0 per continuation (`token_totals` is per-call) — accumulate across calls in the UI, coordinating with Phase 17 R1's fix rather than re-deriving it; the wizard's "Press Finish again" message doesn't match its "Save & continue →" button label.
- [ ] U9 · Replace the Textual Pilot test suite (`tests/test_app_ui.py`, 67 tests) with NiceGUI's headless `user` fixture, reserving the real-browser `screen` fixture for a handful of genuine end-to-end paths; delete the 5 tests that are thin wrappers over `_fmt_op`/`_render_target_layout` now that S2 covers that logic directly.
- [ ] U10 · Flip the default: `telcontar` opens the web UI, `telcontar --tui` becomes the Textual escape hatch. Update README + docs.

> #### ◆ Break 3 — choose where the UX budget goes
>
> The app is now fully usable in a browser. This is the moment to decide what's actually
> worth building, with real usage behind the opinion -- fold this into Phase 21's
> `/dev-pipeline` Step 1.7 planning round. The candidate table already lives below as
> V3-V9 (`[deferred]` until chosen); pick 3-4 of them here. Also decide whether the
> corpus browser (V5) and knowledge-graph view (V6) deserve their own phase entirely --
> they're arguably new features rather than UI work, not just a redesign of an existing
> screen.

---

## Phase 21 — Experience & delivery

*(Scope beyond the two fixed items below is chosen once Phase 20 has seen daily use — pick 3–4 of the candidates below at that point; they're marked `[deferred]` until chosen.)*

- [ ] V1 · Native window — `ui.run(native=True)` via pywebview, with `telcontar --browser` as the escape hatch. Restores "one command, one window".
- [ ] V2 · Security hardening pass — confirm `127.0.0.1` binding, a per-launch token in the opened URL, Origin check, `storage_secret`; have doc-keeper record the new local-server trust boundary in `docs/developer/security-model.md` (still unrecorded there as of Phase 18 — `telcontar --web` binds a loopback TCP socket and serves the approval gate over HTTP, which the audit's trust-boundary and capability-surface sections don't yet reflect).
- [ ] V3 · [deferred] Plan review as a before/after tree — the approval gate is the highest-trust screen; a checkbox list is a terminal-era compromise.
- [ ] V4 · [deferred] Document preview pane — click a file, see the PDF/text inline.
- [ ] V5 · [deferred] Corpus browser — a sortable, filterable table over `.organizer/registry.json` (title, type, date, summary, entities), today only reachable by asking the agent.
- [ ] V6 · [deferred] Knowledge-graph view — `server/graph.py` already builds the graph; currently invisible.
- [ ] V7 · [deferred] Live-updating file tree — builds on Phase 19's T3 sidebar tree; watch the reorganization happen as ops execute.
- [ ] V8 · [deferred] Per-document progress — replace the single progress bar with "which document, right now".
- [ ] V9 · [deferred] Query answers with citations — link answers back to the documents they came from.

> #### ◆ Break 4 — the retirement decision
>
> Fold this into Phase 22's `/dev-pipeline` Step 1.7 planning round -- Phase 22 only
> proceeds if this break says so:
>
> - Has the web UI earned enough trust to delete the TUI? There is no rush; carrying it
>   costs almost nothing now that the shared helpers are extracted (Phase 18 S1).
> - Is there a real headless/SSH use case worth keeping the TUI permanently for? (If
>   yes, Phase 22 is cancelled, and the answer is "both, forever" -- a legitimate
>   outcome, not a failure.)
> - Any parity gap that only showed up in daily use?

---

## Phase 22 — Retire the TUI

*(Only proceeds if a later decision judges the web UI has earned enough trust to retire the TUI — "keep both forever" is an acceptable outcome, not a failure. All items deferred until that decision is made.)*

- [ ] W1 · [deferred] Delete `host/app.py` and `tests/test_app_ui.py`; remove the `--tui` flag. Only if a later design break decides the web UI has earned enough trust to retire the TUI.
- [ ] W2 · [deferred] Drop `textual` and `textual-dev` from `pyproject.toml` (note: `textual-dev` transitively provides `textual-serve`, the terminal-in-a-browser fallback — removing it drops that too).
- [ ] W3 · [deferred] Docs sweep — 11 files currently mention Textual/TUI (42 occurrences), heaviest in `docs/user-guide/how-it-works.md`, `docs/getting-started/quickstart.md`, `docs/index.md`; prune the `test-select` scope-table rows for the deleted files.

---
