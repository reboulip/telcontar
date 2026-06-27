# Roadmap

## v0.1.0 — Skeleton ✅

- [x] A1 · Project layout — create `host/`, `server/`, `config/` package stubs with `__init__.py`
- [x] A2 · Config layer — implement `config/settings.py` with pydantic-settings and `.env` loading; validate all required env vars at startup
- [x] A3 · MCP server skeleton — `server/main.py` entrypoint (stdio), empty tool stubs matching the CLAUDE.md tool list
- [x] A4 · MCP host skeleton — `host/main.py` agent loop, `host/llm.py` openai SDK wrapper (Azure/Mammouth via `base_url`)

---

## v0.2.0 — Server: read-only tools

Core inspection capabilities the agent uses to understand a directory before proposing any changes.

- [x] B1 · `list_dir` — enumerate entries with size, type, and mtime
- [x] B2 · `read_file` — return text content up to `MAX_SNIPPET_CHARS`
- [x] B3 · `extract_text` — extract plain text from PDF/Office files via markitdown
- [x] B4 · `guards` module — no-overwrite check, safe quarantine path generation
- [x] B5 · `move_file` — move a file to a destination directory, respecting the no-overwrite guard
- [x] B6 · `rename_file` — rename a file in place, respecting the no-overwrite guard
- [x] B7 · `create_file` / `update_file` — write or overwrite index output files (`INDEX.md`, `manifest.json`, `SUMMARY.md`)

---

## v0.3.0 — Server: plan, execution & journal

Stateful plan lifecycle and reversible execution. Supports multiple concurrent
plans persisted to disk so sessions survive crashes and restarts.

- [x] C1 · Plan data model — structured list of proposed ops with a stable UUID `plan_id`; multiple plans may be active concurrently; each plan serialized as a JSON file under `.organizer/plans/`; states: `pending | approved | executing | done | failed | stopped`
- [x] C2 · `propose_rename`, `propose_move`, `propose_quarantine` — append ops to a named plan (`plan_id` required); eager no-overwrite guard at proposal time; returns updated op list
- [x] C3 · `execute_plan` — apply approved ops with per-op retry (up to 2 retries before marking failed); if more than 3 ops fail in a single run, trigger hard stop and write a detailed failure summary to the journal; each successful op journaled immediately; plan status updated on disk throughout
- [x] C4 · `undo_last` — revert the most recent journaled op and remove it from the journal
- [x] C5 · Journal module — append-only JSONL at `JOURNAL_PATH`; `last` / `pop_last` helpers; hard-stop entries use `op_type: "hard_stop"` with full failure context
- [x] C6 · `review_plan` — deduplication pass before execution; flags ops sharing the same `(src, op_type)` pair; returns a highlighted report without modifying the plan

---

## v0.4.0 — Host: agent loop ✅

End-to-end GPT-5 driving the MCP server over stdio.

- [x] D1 · MCP client connection — launch server as subprocess, connect over stdio
- [x] D2 · Tool-calling loop — feed tool results back into the GPT-5 context
- [x] D3 · Plan/approve/execute flow — present plan diff to user; gate `execute_plan` on approval
- [x] D4 · Rich CLI — formatted plan diffs, approval prompts, progress feedback

---

## v0.5.0 — Outputs

Artifacts produced after a successful organize run.

- [x] E1 · `write_index` — emit `INDEX.md` (human-readable tree) and `manifest.json` (structured metadata)
- [x] E2 · `write_summary` — emit `SUMMARY.md` describing the directory's contents and changes made
- [x] E3 · File-naming heuristics — conventions for how the model should derive readable file names

---

## v0.6.0 — Engine core + IS-IT profile #1

Turn the file-organizer into a profile-driven document-intelligence engine: persistent content-addressed memory + a per-document analysis pass, with all domain-specific vocabulary externalized into a declarative profile. The supplied IS/IT-project requirements ship as the first profile.

- [x] G1 · Domain profile loader — `server/profile.py`: load + validate a TOML profile via stdlib `tomllib`; resolve the active profile from a new `profile` setting; typed accessors (`document_type_ids()`, `entity_roles()`, `salient_cap`, `extraction_fields()`, `naming()`). Ship `profiles/is_it_project.toml` carrying the French document-type vocabulary (communication_formelle, releve_de_decision, document_de_travail, support_copil, support_reunion, draft_officiel, notes, echanges, autre), `salient_cap = 5`, and extraction required/optional fields. Config: add `profile` (default `is_it_project`) and `profiles_dir` (default `profiles/`)
- [x] G2 · Document registry — `server/registry.py`: JSON store at `.organizer/registry.json` keyed by sha256 checksum; dataclass + `to_dict`/`from_dict` + load/save mirroring `server/plan.py`; record fields = checksum, path, title, date|null, type, summary, provenance, entities (list of {name, role, kind}), attributes, status (active|archived|quarantined), first_seen, last_analyzed. Config: add `registry_path` (default `.organizer/registry.json`)
- [x] G3 · `compute_checksum` tool — sha256, chunk-streamed via `hashlib`; pure/deterministic
- [x] G4 · `record_document` + registry read/query tools — `record_document` validates `type` against the active profile (not a hardcoded enum) and enforces the author guardrail (author optional, null unless explicit), upserts by checksum; `get_registry` / `list_documents` read-only dump; `find_duplicates` (exact-checksum collisions + candidate groups for the host to LLM-judge); `find_modified_documents` (same title, differing checksum) (requires: G1, G2, G3)
- [x] G5 · Registry path reconcile — extend `execute_plan` (or add `sync_registry_paths`) to update each record's `path` and set `status="quarantined"` from the undo journal's `src → dst` after execution, so checksum stays the identity while paths track moves (requires: G2)
- [x] G6 · Profile-driven host analysis pass — `host/agent.py`: compose the system prompt from the active profile (document types, extraction fields + guardrails, entity roles, naming), replacing the hardcoded `_SYSTEM_PROMPT_TEMPLATE` + `_DEFAULT_NAMING_CONVENTIONS` (keep `.organizer/NAMING.md` override); add the analysis pass (per doc: extract_text/read_file → derive metadata → compute_checksum → record_document); run order analyse → plan → approve/execute → reconcile (requires: G1, G4)

---

## v0.7.0 — Entity graph + project narrative

- [x] H0 · chore: repo-wide `ruff format` — run `ruff format .` so CI's `ruff format --check .` gate passes (currently ~27/32 files are non-compliant, so the next develop→main PR would fail). Do this FIRST, as its own standalone commit, before any v0.7.0 feature work.
- [x] H1 · Event journal — `events.jsonl` + `create_event(sentence, date)` (verb-led, dated); distinct from the undo journal
- [x] H2 · Entity / knowledge graph — `server/graph.py`: project registry + events into nodes/edges at `.organizer/graph.json` (derived, reproducible from the registry)
- [x] H3 · Actors — top entities ranked from the graph, capped at the profile's `salient_cap`
- [x] H4 · Project synthesis — enrich `write_summary` to compose the project markdown from registry + events + graph, per the profile's `[synthesis]` template
- [ ] H5 · Archived-documents journal — archive log + registry `status` ("retirer de la mémoire")

---

## v0.8.0 — Organization, tree & output sinks

- [ ] I1 · `create_dir` — collision-safe directory creation
- [ ] I2 · Folder README writer — per-folder README of the arborescence
- [ ] I3 · Taxonomy classification — relevant-tree reasoning in the host prompt (reuses `propose_move` + `write_index`)
- [ ] I4 · `compare_documents(a, b)` — extract both + diff (e.g. successive COPIL slides)
- [ ] I5 · Output-sink abstraction — `Sink` protocol; `local_markdown` default built-in; MediaWiki sink plugin (re-admits the gandalf wiki) behind an explicit egress allow-flag

---

## v0.9.0 — Interactive query + generality

- [ ] J1 · Interactive query mode — NL questions over the registry/graph in the Textual TUI ("charger un doc pour l'interroger", generalized to the whole corpus)
- [ ] J2 · Second profile — author a second domain profile (e.g. research-papers or personal-files) purely as data, proving the engine is profile-driven, not IS-IT-shaped
- [ ] J3 · [deferred/hard] Read content of links inside attachments — revisit egress policy first
- [ ] J4 · [deferred] Lossless compression of quarantined archives

---

## v1.0.0 — Hardening

Production-readiness and operator ergonomics.

- [ ] F1 · End-to-end integration tests against a fixture directory
- [ ] F2 · Robust error handling — bad paths, permission errors, partial plan failures
- [ ] F3 · `APPROVAL_MODE=destructive_only` — let read-only ops run without approval
- [ ] F4 · Packaging — verify `uv run organizer-host` and `uv run organizer-server` entry points work end-to-end
