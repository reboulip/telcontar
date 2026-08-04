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

- [ ] S1 · Extract `host/format.py`, `host/paths.py`, `host/narration.py` from `host/app.py` — move the framework-independent formatters (`_fmt_journal_entry`, `_fmt_op`, `_render_target_layout`), path/discovery helpers (`_find_organizer_root`, `_resolve_journal_path`, `_resolve_plans_dir`, `_quarantine_basename`, `_directory_overview`, `_is_op_out_of_scope`, `_target_folders`/`_note_for`), and the `_TOOL_NARRATION` table + collapse rule into shared, UI-agnostic modules; `host/app.py` imports them from their new location. Zero behaviour change — first step of the Textual→NiceGUI migration, so both UIs can share one implementation.
- [ ] S2 · Add direct unit tests for the extracted helpers — `tests/test_host_format.py` and `tests/test_host_paths.py`, including the five behaviours currently covered only indirectly through a Textual Pilot test (`_fmt_journal_entry`, `_directory_overview`, `_is_op_out_of_scope`, `_resolve_journal_path`, the narration collapse rule).
- [ ] S3 · Collapse the 8 fully-explicit `run_agent_loop` test doubles in `tests/test_app_ui.py` into one shared `**kwargs` factory — the duplicated 13-parameter header (currently at lines 386, 1111, 1158, 1201, 1247, 1292, 1340, 1412) breaks on any kwarg addition to `host/agent.py`'s `run_agent_loop`; safe `**kwargs`-based doubles already exist elsewhere in the same file as the pattern to follow.
- [ ] S4 · Add the NiceGUI web UI skeleton — `host/web/main.py`, `session.py`, `bridge.py`: `ui.run` on `127.0.0.1` with an ephemeral port, `show=True`, `reload=False`; a `RunSession` keyed by a run id (URL param or `app.storage`) so a page reload re-attaches to an existing run's pending dialog instead of silently orphaning it (validated via spike: an unreconnectable dialog can deadlock the approval gate with no visible symptom); the synchronous `AgentEvent` bridge and the three awaited callbacks (approval/cost/ask_user), driven against the registry's current client.
- [ ] S5 · A single working organizer view in NiceGUI — transcript, status, chat input, approval dialog, cost dialog; deliberately plain, no styling pass yet. Move blocking synchronous I/O off the event loop where it currently runs inline: `_directory_overview` (full `os.walk`), `_load_profile_options` (glob + TOML parse), journal reads, and `server.tools.undo_last` (moves files on disk).
- [ ] S6 · Wire `telcontar --web` into `host/main.py` (default stays the Textual TUI); add `nicegui` to `pyproject.toml`'s main dependencies.

---
