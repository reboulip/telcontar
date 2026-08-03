# Roadmap

Completed phases (1–15) have been moved to
[ROADMAP-ARCHIVE.md](ROADMAP-ARCHIVE.md) to keep this file focused on open
work.

---

## Phase 16 — Follow-up fixes

- [x] Q1 · Fix `review_plan` flagging `create_dir` ops as missing_sources — a `create_dir` op's `src` is the not-yet-created destination directory path, so `review_plan`'s existence check (`server/tools.py`) produces a permanent false-positive that blocks plan approval for any plan containing directory creation [#26]
- [ ] Q2 · Emit `progress` AgentEvent per analysis batch — move the progress computation/emission (`host/agent.py`, currently only after the full `_analyze_new_documents` loop completes) inside its per-batch loop, so the TUI progress bar advances incrementally instead of jumping from 0 to ~100% at the end [#25]

---
