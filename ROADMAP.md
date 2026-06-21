# Roadmap

## v0.1.0 — Skeleton ✅

- [x] A1 · Project layout — create `host/`, `server/`, `config/` package stubs with `__init__.py`
- [x] A2 · Config layer — implement `config/settings.py` with pydantic-settings and `.env` loading; validate all required env vars at startup
- [x] A3 · MCP server skeleton — `server/main.py` entrypoint (stdio), empty tool stubs matching the CLAUDE.md tool list
- [x] A4 · MCP host skeleton — `host/main.py` agent loop, `host/llm.py` openai SDK wrapper (Azure/Mammouth via `base_url`)

---

## v0.2.0 — Server: read-only tools

Core inspection capabilities the agent uses to understand a directory before proposing any changes.

- `list_dir` — enumerate entries with size, type, and mtime
- `read_file` — return text content up to `MAX_SNIPPET_CHARS`
- `extract_text` — extract plain text from PDF/Office files via markitdown
- `guards` module — no-overwrite check, safe quarantine path generation

---

## v0.3.0 — Server: plan, execution & journal

Stateful plan lifecycle and reversible execution.

- Plan data model — structured list of proposed ops with a stable `plan_id`
- `propose_rename`, `propose_move`, `propose_quarantine` — append ops to the active plan
- `execute_plan` — apply approved ops atomically; write each to the undo journal
- `undo_last` — revert the most recent journaled op
- Journal module — append-only JSONL; `last` / `pop_last` helpers

---

## v0.4.0 — Host: agent loop

End-to-end GPT-5 driving the MCP server over stdio.

- MCP client connection — launch server as subprocess, connect over stdio
- Tool-calling loop — feed tool results back into the GPT-5 context
- Plan/approve/execute flow — present plan diff to user; gate `execute_plan` on approval
- Rich CLI — formatted plan diffs, approval prompts, progress feedback

---

## v0.5.0 — Outputs

Artifacts produced after a successful organize run.

- `write_index` — emit `INDEX.md` (human-readable tree) and `manifest.json` (structured metadata)
- `write_summary` — emit `SUMMARY.md` describing the directory's contents and changes made
- File-naming heuristics — conventions for how the model should derive readable file names

---

## v1.0.0 — Hardening

Production-readiness and operator ergonomics.

- End-to-end integration tests against a fixture directory
- Robust error handling — bad paths, permission errors, partial plan failures
- `APPROVAL_MODE=destructive_only` — let read-only ops run without approval
- Packaging — verify `uv run organizer-host` and `uv run organizer-server` entry points work end-to-end
