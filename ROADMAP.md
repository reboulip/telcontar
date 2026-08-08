# Roadmap

Completed phases (1–19) have been moved to
[docs/ROADMAP-ARCHIVE.md](docs/ROADMAP-ARCHIVE.md) to keep this file focused
on open work.

---

## Phase 20 — Parity

*(Web UI gains everything the TUI does and becomes the default `telcontar` entrypoint; the TUI survives as `telcontar --tui` until Phase 23. Safety-critical paths — approval, cost, undo — are ported faithfully; everything else gets a cleaner surface but no redesign. Everything here is built inside the Phase 19 shell.)*

- [x] U1 · Startup view — Organize / Query / Settings entry points. The folder-picker half is superseded by Phase 19's T3 sidebar tree (built once, in the shell) — check before rebuilding. A browser can't return a real filesystem path from a native picker, so selection stays a server-side browse over the existing `list_dir` tool.
- [x] U2 · Setup wizard — 5 steps with real routing (the TUI mounts all five and toggles `.display`). Preserve `PlaintextKeyFallbackNeeded` → warn → explicit second confirmation.
- [x] U3 · Settings view — same behaviour as the wizard, including the blank-key-preserves-existing rule.
- [x] U4 · Approval dialog — faithful: per-op toggles returning `removed_op_ids`, refinement text, reject, both disclaimers; refinement takes priority over approval. While here: refresh the Phase 19 T3 sidebar tree on `execute_plan` result (it currently never updates as the agent moves files).
- [x] U5 · Cost estimate dialog — faithful. While here: fix `batch_size` never being passed (it always claims "groups of 10").
- [x] U6 · Journal view + undo, with a visible affordance (currently keyboard-only: `j` then `u`) — user-only, the agent still has no undo tool. While here: refresh the ops-journal strip after an undo (it currently goes stale). This is also where Phase 18's deferred `run.io_bound` offloads for journal reads and `server.tools.undo_last` finally get a real call site.
- [x] U7 · Query view — answer pane + tool timeline. While here: fix it calling `load()` without `.for_target()`, unlike the organizer.
- [x] U8 · Fix the remaining warts found during planning: `error` AgentEvents are wrongly treated as terminal (an analysis-batch failure emits `error` but the run continues — only `done`, max-turns, and the loop's own exception path should end a run); the token counter resets to 0 per continuation (`token_totals` is per-call) — accumulate across calls in the UI, coordinating with Phase 17 R1's fix rather than re-deriving it; the wizard's "Press Finish again" message doesn't match its "Save & continue →" button label.
- [x] U9 · Replace the Textual Pilot test suite (`tests/test_app_ui.py`, 67 tests) with NiceGUI's headless `user` fixture, reserving the real-browser `screen` fixture for a handful of genuine end-to-end paths; delete the 5 tests that are thin wrappers over `_fmt_op`/`_render_target_layout` now that S2 covers that logic directly.
- [x] U10 · Flip the default: `telcontar` opens the web UI, `telcontar --tui` becomes the Textual escape hatch. Update README + docs.

---

## Phase 21 — Experience & delivery

*(Grouped into clusters by theme; several clusters share files with each other — `host/web/main.py` especially — so the actual per-wave batching is computed at sprint start, not assumed from the grouping below. See `sprint-brief.md`'s Waves section. Resolved: the former Break 3 asked whether V4/V5/V6 deserved their own phase — V4 merged into V5 and stays here; V6 moved out to Phase 22.)*

#### Delivery & packaging

- [x] V1 · Native window — `ui.run(native=True)` via pywebview, with `telcontar --browser` as the escape hatch. Restores "one command, one window".
- [x] V2 · Security hardening pass — confirm `127.0.0.1` binding, a per-launch token in the opened URL, Origin check, `storage_secret`; have doc-keeper record the new local-server trust boundary in `docs/developer/security-model.md` (still unrecorded there as of Phase 18 — `telcontar` binds a loopback TCP socket and serves the approval gate over HTTP, which the audit's trust-boundary and capability-surface sections don't yet reflect). (requires: V1)

#### Plan-approval trust surface — `host/web/dialogs.py::build_approval_dialog`

- [ ] V3 · Plan review as a before/after tree — the approval gate is the highest-trust screen; a checkbox list is a terminal-era compromise. Backed by real usage (#32): state a rationale for the target layout, render it as an actual before/after tree instead of `render_target_layout`'s flat text block, and size the modal bigger/denser. [#32] (requires: V10)
- [x] V10 · Quarantine op rationale — every `propose_quarantine` op shows a stated reason (unreadable, duplicate, superseded, ...) in the approval dialog, not just the basename; "telcontar couldn't read it" stops being sufficient justification on its own. The fuller ask — don't auto-quarantine unreadable files, ask the user interactively instead — needs V12's `ask_user` dialog and can follow once that lands. [#29]
- [ ] V17 · Interactive quarantine confirmation for unreadable files — the agent must `ask_user` before proposing quarantine for a document it could not extract text from, instead of quarantining unilaterally. (requires: V12) [#29]

#### Interaction dialogs & robustness — `host/web/dialogs.py` (new builder), `bridge.py`, `session.py`

- [x] V12 · `ask_user` structured dialog — a real modal (radio-button options + free-text "additional comment"), replacing today's plain-text question rendered into the transcript; while here, audit the dialog↔session resolution path for the reported double-post-on-answer bug — `ask_user` never got the persistent-dialog / request-scoped-resolution treatment U4/U5 already gave the approval and cost dialogs (see `dialogs.py`'s header docstring). [#33]

#### Prompts & settings — `host/web/settings.py`, `host/agent.py`

- [x] V11 · Prompt inspection — a read-only Settings view of the composed system prompt(s) `host/agent.py` sends, so the user can see what telcontar is actually being told. Editing is deliberately out of scope here: an editable prompt sits next to Phase 12 M10's injection-resistance guardrails and needs its own security pass before it's safe to expose as a write path. [#30]

#### Sidebar tree & live state — `host/web/tree.py`, `shell.py`

- [ ] V7 · Live-updating file tree — builds on Phase 19's T3 sidebar tree and Phase 20 U4's one-shot post-`execute_plan` refresh (`tree.py::rebuild_nodes`). Backed by real usage (#38): add a manual refresh button, plus a periodic poll that doesn't disturb the running agent. [#38]
- [x] V15 · Fix the sidebar resize handle — the drag handle has never actually been wired in any browser (not an Edge-specific regression): `_RESIZE_JS` (`shell.py`) is a bare arrow-function *literal*, and NiceGUI's `eval`-based `run_javascript` evaluates but never invokes it, so `mousedown`/`mousemove`/`mouseup` are never bound. Make it a self-invoking IIFE with document-level delegation (the handle may not exist in the DOM yet when the script first runs). [#34]

#### Theme & layout polish — theme tokens, chat rendering (`bridge.py`/`session.py`), `steplog.py`'s detail drawer

- [x] V13a · Theme & layout polish — chat bubbles — right-align user chat bubbles, left-align telcontar's [#39]; silver (user) / gold (telcontar) bubble backgrounds, checked for accessible contrast in both light and dark themes [#36].
- [ ] V13b · Theme & layout polish — step-detail drawer — move the step-detail drawer (`steplog.py`) from the shell's right side into the left sidebar next to the file tree, widening the sidebar to fit; fix it following a fixed white background instead of the browser's theme along the way (root cause: `ui.codemirror`'s light theme default). [#35] (requires: V7, V15)
- [x] V13c · Theme & layout polish — display face — more use of the elvish display face where relevant. [#36]

#### Progress & status legibility — `host/web/main.py` (progress bar, activity label)

- [x] V8a · Per-document progress — agent-side — emit the in-progress document name(s) on the `"progress"` event (`host/agent.py`) so a batch's current file(s) are visible to any UI, not just the analyzed/total counts.
- [ ] V8b · Per-document progress — web UI — label the progress bar with "which document, right now", using V8a's data. (requires: V8a, V14)
- [ ] V14 · Progress bar as integer percent — `ui.linear_progress` in `host/web/main.py` shows its raw float value by default ("0.58765304"); format as an integer percentage instead. [#40]
- [ ] V16 · Status/activity messages as reviewable stages — `activity_label` (`host/web/main.py`) shows the current macro-phase ("analyzing directory structure", ...) as a single line overwritten on every phase change and lost once done; render these as small, discrete entries in the conversation/log area instead, so the sequence can be reviewed afterward — distinct from `steplog.py`'s per-tool-call step log, which already covers finer-grained tool activity. [#37]

#### Corpus browser — `.organizer/registry.json`-backed view

- [ ] V5 · Corpus browser — a sortable, filterable table over `.organizer/registry.json` (title, type, date, summary, entities) with a document detail pane (click a row to see its full summary, provenance, and entities) — merges the former V4 (document preview) into one screen; today only reachable by asking the agent.

---

## Phase 22 — Corpus intelligence surfaces

*(Split out from Phase 21's former Break 3: unlike V4/V5 — which surface data the agent already narrates in the transcript — this is new product surface with no settled design yet.)*

- [ ] V6 · Knowledge-graph view — `server/graph.py` already builds the graph; currently invisible. Needs a design pass before implementation: rendering choice (force-directed graph vs. a ranked-actors table, or both — `server/graph.py::rank_actors` already produces a ranked, scored actor list), node/edge caps for large corpora, kind filters (document/entity/event), and what a node click surfaces.

---

> #### ◆ Break 4 — the retirement decision
>
> Fold this into Phase 23's `/dev-pipeline` Step 1.7 planning round -- Phase 23 only
> proceeds if this break says so:
>
> - Has the web UI earned enough trust to delete the TUI? There is no rush; carrying it
>   costs almost nothing now that the shared helpers are extracted (Phase 18 S1).
> - Is there a real headless/SSH use case worth keeping the TUI permanently for? (If
>   yes, Phase 23 is cancelled, and the answer is "both, forever" -- a legitimate
>   outcome, not a failure.)
> - Any parity gap that only showed up in daily use?

---

## Phase 23 — Retire the TUI

*(Only proceeds if a later decision judges the web UI has earned enough trust to retire the TUI — "keep both forever" is an acceptable outcome, not a failure. All items deferred until that decision is made.)*

- [ ] W1 · [deferred] Delete `host/app.py` and `tests/test_app_ui.py`; remove the `--tui` flag. Only if a later design break decides the web UI has earned enough trust to retire the TUI.
- [ ] W2 · [deferred] Drop `textual` and `textual-dev` from `pyproject.toml` (note: `textual-dev` transitively provides `textual-serve`, the terminal-in-a-browser fallback — removing it drops that too).
- [ ] W3 · [deferred] Docs sweep — 11 files currently mention Textual/TUI (42 occurrences), heaviest in `docs/user-guide/how-it-works.md`, `docs/getting-started/quickstart.md`, `docs/index.md`; prune the `test-select` scope-table rows for the deleted files.

---
