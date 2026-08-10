# Roadmap

Completed phases (1–21) have been moved to
[docs/ROADMAP-ARCHIVE.md](docs/ROADMAP-ARCHIVE.md) to keep this file focused
on open work.

---

> #### ◆ Break 4 — resolved (full user test, 2026-08-09)
>
> - Has the web UI earned enough trust to delete the TUI? There is no rush; carrying it
>   costs almost nothing now that the shared helpers are extracted (Phase 18 S1).
> - Is there a real headless/SSH use case worth keeping the TUI permanently for? (If
>   yes, this phase is cancelled, and the answer is "both, forever" -- a legitimate
>   outcome, not a failure.)
> - Any parity gap that only showed up in daily use?
>
> **Outcome:** retire it. The full user test found the web UI trustworthy enough, raised
> no headless/SSH use case, and the gaps it did find were UX polish (French-language
> feedback, becoming Phase 23 below), not missing parity.

---

## Phase 22 — Retire the TUI

*(Break 4 above resolved: the web UI has earned the TUI's retirement.)*

- [x] W1 · Delete `host/app.py` and `tests/test_app_ui.py`; remove the `--tui` flag.
- [x] W2 · Drop `textual` and `textual-dev` from `pyproject.toml` (note: `textual-dev` transitively provides `textual-serve`, the terminal-in-a-browser fallback — removing it drops that too).
- [x] W3 · Docs sweep — 11 files currently mention Textual/TUI (42 occurrences), heaviest in `docs/user-guide/how-it-works.md`, `docs/getting-started/quickstart.md`, `docs/index.md`; prune the `test-select` scope-table rows for the deleted files.

---

## Phase 23 — Web UI polish from live user testing

*(French-language UX feedback from the full user test that resolved Break 4 above: organize-screen clarity, plan-review polish, corpus browser navigation, and related polish. Session switching (mentioned in that feedback) is deferred to Phase 24 as V7 — it needs its own design pass.)*

- [ ] X1 · Keep the target directory visible in the main content area for the whole run (not just before it starts, `host/web/main.py`'s `directory_overview()`), and highlight the selected folder in the sidebar treeview. [#41]
- [ ] X2 · Investigate and fix chat text not being selectable/copyable — likely lead: the sidebar-resize drag handler's `document.body.style.userSelect = 'none'` not resetting when a drag ends outside the browser window. [#42]
- [ ] X3 · Interleave V16 activity/stage messages chronologically into the conversation thread itself (merge `session.transcript` and `session.activity_log` in `host/web/main.py`), instead of a separate column; `steplog.py`'s per-tool-call step log stays in its own drawer. [#43]
- [ ] X4 · Teach `plan_tree_diff` (`host/format.py`) to chain same-file rename+move ops the way `execute_plan` (`server/tools.py`) already does, so each file appears once in the before/after view. [#44]
- [ ] X5 · Add a "Reveal in file explorer" action to the plan approval dialog that opens the OS file manager with `plan_ops.json` selected. [#45]
- [ ] X6 · Add a label/tooltip to the plan "after" view checkboxes explaining that unchecking excludes that op from execution. [#46]
- [ ] X7 · Add vertical connector lines and denser row spacing to all tree views — the sidebar file tree and the plan before/after tree. [#47]
- [ ] X8 · Guard the quarantine dir name against agent-proposed taxonomy folder names (case/locale-insensitive) in `server/guards.py`, and steer the taxonomy prompt away from quarantine-adjacent naming. [#48]
- [ ] X9 · Add a document preview pane to the organize/run screen, reusing the corpus browser's (V5) detail-pane approach. [#49]
- [ ] X10 · Add periodic polling (or a manual refresh) to the corpus browser so it reflects `execute_plan` renames/moves, matching the run page and sidebar tree's existing poll pattern. [#50]
- [ ] X11 · Add persistent shell navigation (tabs: Conversation | Corpus | Query | Settings) so users can move freely between views. [#51]
- [ ] X12 · In the corpus browser, trigger the document detail pane from a title/row click instead of the selection checkbox; remove the checkbox. [#52]
- [ ] X13 · Design and wire in an SVG telcontar logo (White Tree of Gondor + branching roots) matching the gold/silver theme and elvish display face (Phase 19 T8), in the browser tab/header. [#54]
- [ ] X14 · Split `docs/developer/modules.md` and `docs/developer/architecture.md` (~880 lines each) into smaller per-area pages and rebuild the `mkdocs.yml` nav — doc-keeper cost scales with page size and both files are touched by nearly every wave. [deferred]

*Once Phase 22 and Phase 23 are both done, telcontar is ready to cut its first official release.*

---

## Phase 24 — Corpus intelligence surfaces

*(Split out from Phase 21's former Break 3: unlike V4/V5 — which surface data the agent already narrates in the transcript — this is new product surface with no settled design yet.)*

- [ ] V6 · Knowledge-graph view — `server/graph.py` already builds the graph; currently invisible. Needs a design pass before implementation: rendering choice (force-directed graph vs. a ranked-actors table, or both — `server/graph.py::rank_actors` already produces a ranked, scored actor list), node/edge caps for large corpora, kind filters (document/entity/event), and what a node click surfaces.
- [ ] V7 · Session list/switch/resume view — persist the session list to disk (target directory, timestamp, status per session); add a view to list and switch between sessions; "Resume" live-reattaches the conversation when the session's agent state survives a restart, falling back to a read-only transcript view when it doesn't. Needs its own design pass first: storage format/location, what agent state must survive a restart, retention/cleanup of old sessions. [#53]

---
