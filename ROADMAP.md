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
- [ ] R3 · Update docs (README, docs/**) and the UI to describe telcontar as backend-agnostic (any OpenAI-compatible endpoint) rather than GPT-5/Mammouth/Azure-specific [#28]

---
