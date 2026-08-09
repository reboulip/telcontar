# CLAUDE.md — Local Directory Organizer (MCP-based)

## Project Overview

A locally-run, **profile-driven document-intelligence engine**. Given a directory of documents dumped in bulk, it analyzes each one (title, date, type, summary, author, mentioned people, why it's here), records them in a persistent content-addressed memory registry, detects duplicates and modified versions, organizes the tree (rename / move / quarantine), and synthesizes an overall summary — all locally. Everything domain-specific (the document-type vocabulary, entity/role model, extraction guardrails, naming, synthesis template, output sinks) is externalized into a declarative **domain profile**, so the same engine serves different kinds of corpora. The **IS/IT-project profile** (`profiles/is_it_project.toml`) ships as profile #1. Intelligence comes from an LLM via any OpenAI-compatible endpoint; the server stays deterministic (it persists what the model reasons out).

**Architecture:** MCP-based (Stack B1).
- A custom Python MCP server exposes guarded file-system tools.
- A thin custom Python MCP host runs the agent loop and routes to any OpenAI-compatible endpoint (Azure OpenAI, Mammouth, or another compatible provider).

## Core Principles

1. **Safety first.** No destructive op runs without an approved plan (initially). Never delete — only quarantine.
2. **Local execution.** All file I/O happens locally; only content/metadata needed for reasoning is sent to the model endpoint.
3. **One language, one toolchain.** Python + uv end to end.
4. **Portable config.** Swap dev ↔ prod by changing base URL + key only.
5. **Auditability.** Every action logged to an undo journal.

## Tech Stack

- **Language:** Python (latest stable, 3.12+)
- **Package/env manager:** uv (`uv tool install`, `uv run`)
- **MCP:** MCP Python SDK (server + host)
- **LLM SDK:** openai Python SDK, pointed at an OpenAI-compatible base URL
  - **Prod:** Azure OpenAI GPT-5 (private cloud endpoint)
  - **Dev/test:** GPT-5 via Mammouth API
- **Text extraction:** markitdown, pypdf (and Office formats as needed)
- **OS:** Windows first; keep paths cross-platform (pathlib)

## Architecture

> **For any architecture-level, effort-estimate, or "how does X work" question, read
> `docs/developer/architecture.md` and `docs/developer/modules.md` first** — they are the
> maintained, current source of truth (kept up to date by the `doc-keeper` subagent) for
> component boundaries, data flow, and per-file responsibilities. Prefer those two reads
> over Glob/Grep-exploring `host/`/`server/` from scratch.

### Components

1. **MCP Server** (`server/`) — exposes file tools, owns all guardrails, the quarantine logic, and the undo journal. Never deletes.
2. **MCP Host** (`host/`) — runs the agent loop, connects to the MCP server over stdio, manages the plan/approval flow and config.
3. **Config** (`config/`) — env-based, swaps dev/prod endpoints.

### Data flow

```
User → Host (agent loop) → MCP Server (tools) → Local filesystem
              ↑                      ↓
       OpenAI-compatible       Undo journal +
       endpoint (LLM)          quarantine folder
```

## Configuration

Single env-based config. Swap dev ↔ prod by changing base URL + key only.

```env
# --- LLM endpoint ---
LLM_BASE_URL=        # Azure (prod) or Mammouth (dev) base URL
LLM_API_KEY=
LLM_MODEL=gpt-5
LLM_API_VERSION=     # Azure only; ignored for Mammouth

# --- Safety ---
APPROVAL_MODE=always # always | destructive_only | never
QUARANTINE_DIR=_quarantine
JOURNAL_PATH=.organizer/journal.jsonl

# --- Domain profile ---
PROFILE=is_it_project        # which profiles/<name>.toml to load
PROFILES_DIR=profiles
REGISTRY_PATH=.organizer/registry.json

# --- Egress / extraction ---
MAX_SNIPPET_CHARS=4000   # defense-in-depth even though full content is allowed
ALLOWLIST_DIRS=          # optional: restrict content upload to these dirs
```

> **Note:** Azure OpenAI and Mammouth are both OpenAI-compatible. Use the openai SDK with `base_url`/`api_key` overrides. For Azure, set `api_version` and the deployment-style endpoint; for Mammouth, the standard base URL. **No code change to switch — config only.**

## Domain Profiles

Everything domain-specific lives in a declarative TOML profile under `profiles/` (e.g. `profiles/is_it_project.toml`). A profile defines the document-type vocabulary, the entity/role taxonomy and salient-actor cap, the extraction fields (required vs optional "IF POSSIBLE" guardrails), the naming convention, the synthesis template, and the output sinks. The active profile is chosen by the `profile` setting (default `is_it_project`); the host composes its system prompt from it and `record_document` validates against it. Add a sibling `.toml` to adapt telcontar to a different corpus — no code change.

## Safety Model

### Approval modes (`APPROVAL_MODE`)

| Mode | Description |
|---|---|
| `always` (default, start here) | Every plan, destructive or not, requires explicit user approval before execution. |
| `destructive_only` | Only moves/renames/quarantine need approval; read-only ops (index, summary) run freely. |
| `never` | Full autonomy (only after trust is established). |

Start at `always`; relax over time via config — no code changes.

### Plan → Approve → Execute flow

1. Agent inspects the tree (read-only tools).
2. Agent emits a structured plan (list of proposed ops).
3. Plan is shown to the user (human-readable diff).
4. On approval, Host calls `execute_plan`.
5. Every executed op is appended to the undo journal.

### Non-negotiable rules

- **Never delete.** Clutter goes to `QUARANTINE_DIR`.
- **Never overwrite.** On name collision, suffix or skip — never clobber.
- **Every destructive op is journaled and reversible** via `undo_last`.

## MCP Tools (Server)

**Read-only** (safe, may run without approval depending on mode):
- `list_dir(path)` — enumerate entries with metadata (size, type, mtime).
- `read_file(path, max_chars)` — content up to `MAX_SNIPPET_CHARS`.
- `extract_text(path, max_chars)` — text from PDF/Office via markitdown/pypdf.

**Plan-building** (write to plan, do not execute):
- `propose_rename(path, new_name)`
- `propose_move(path, dest_dir)`
- `propose_quarantine(path)`

**Document memory (registry):**
- `compute_checksum(path)` — sha256 of a file, its unique content id.
- `record_document(checksum, path, title, type, summary, provenance, date, entities)` — upsert an analyzed document into the registry; `type` is validated against the active profile, `entities` is a list of `{name, role, kind}` (author = an entity with role "author", only when explicit).
- `get_registry()` / `list_documents()` / `get_document(checksum)` — read-only views of the registry.
- `find_duplicates()` — fuzzy candidate-duplicate clusters (title-token similarity within a type) for the host to judge.
- `find_modified_documents()` — documents sharing a title but differing in content (modified versions).

**Gated execution** (respect `APPROVAL_MODE`):
- `execute_plan(plan_id)` — apply approved ops; journal each one.
- `write_index(path)` — emit Markdown index + JSON manifest.
- `write_summary(path)` — emit overall summary.

**Recovery:**
- `undo_last()` — revert the most recent journaled op.

**No delete tool exists. Quarantine only.**

> **Two journals:** the **undo journal** (`.organizer/journal.jsonl`, file operations, drives `undo_last`) is distinct from the future project **event journal**. `execute_plan` reconciles registry paths automatically as files move (the checksum stays the identity).

## Outputs

- `INDEX.md` — human-readable index of the organized tree.
- `manifest.json` — structured file metadata + final locations.
- `SUMMARY.md` — overall summary of the directory's contents.
- `.organizer/journal.jsonl` — append-only undo journal.

## Project Structure

```
project/
├── CLAUDE.md
├── ROADMAP.md
├── pyproject.toml          # uv-managed
├── .env                    # config (gitignored)
├── host/                   # MCP host: agent.py (agent loop), app.py (Textual TUI),
│                           # web/ (web UI), main.py (thin CLI entry point)
├── server/                 # MCP server: tools.py (tool implementations) + guards.py,
│                           # journal.py, extract.py, profile.py, registry.py, archive.py,
│                           # events.py, graph.py, sinks.py, egress.py, plan.py, main.py
├── config/
│   └── settings.py         # env loading + validation
├── profiles/
│   └── is_it_project.toml  # domain profile #1
├── docs/developer/         # architecture.md, modules.md — maintained source of truth
└── tests/
```

> **Note:** this tree is a rough map, not authoritative — it has drifted before (e.g. it once
> omitted `host/app.py` and `host/web/` entirely). `docs/developer/architecture.md` and
> `docs/developer/modules.md` are the maintained, current source of truth; see the pointer
> under "## Architecture" above.

## Development Setup

```bash
# Install Python + deps via uv
uv sync

# Run the MCP server (stdio) — usually launched by the host
uv run python -m server.main

# Run the host (agent loop) against a target directory
uv run python -m host.main --target "C:\path\to\messy\dir"
```

For dev, point `LLM_BASE_URL`/`LLM_API_KEY` at Mammouth. For prod, point them at the Azure OpenAI private endpoint. Nothing else changes.

## Conventions for Claude / the Agent

- Always run in `APPROVAL_MODE=always` during development.
- Propose before you execute. Build the full plan, then await approval.
- Never invent a delete capability. Quarantine is the only removal path.
- Respect `MAX_SNIPPET_CHARS` and `ALLOWLIST_DIRS` for content egress.
- Use pathlib everywhere; keep Windows path handling correct.
- Journal every destructive op so `undo_last` always works.
- Prefer idempotent operations; re-running a plan must not double-apply.

## Branch Model

| Branch | Role | How to merge in |
|--------|------|-----------------|
| `main` | Protected — stable releases only | PR from `develop` or `hotfix/*` only. Never push directly. No squash. |
| `develop` | Integration branch | Direct push allowed. Receives squash-merges from `feat/*` / `fix/*`. |
| `feat/<name>` | One feature / one ROADMAP item | Sub-branch of `develop`. Squash-merge into `develop` when green. |
| `fix/<name>` | Bug fix on develop | Sub-branch of `develop`. Same squash-merge rule. |
| `hotfix/<name>` | Urgent fix on top of `main` | Branch from `main`. PR back to `main` (no squash). Then merge `main` → `develop`. |

### Merge rules
- **Feature/fix branches → `develop`:** local squash-merge (`git merge --squash`), one commit per feature. No PR required.
- **`develop` → `main`:** PR only, **no squash** (full `develop` history preserved on `main`).
- **Hotfix → `main`:** PR only, no squash. Immediately after: merge `main` into `develop`.
- Never push directly to `main`.
- Squash commit message: `<type>: <summary>` (imperative, ≤72 chars).

### Hard rules (all git work, run directly in the main session)
- Never force-push (`--force`, `-f`).
- Never skip hooks (`--no-verify`).
- Never amend a published commit.
- Never commit without being asked to (outside an approved `/dev-pipeline` or `/release` run).

## Releases

Releases are **automated** by `.github/workflows/release.yml`, which triggers on pushing any `v*` tag. To cut a release:

Releases are always tagged on `main`: first merge the `develop` → `main` PR (per the Branch Model), then bump the version and tag.

1. Bump `version` in `pyproject.toml` (PEP 440 — e.g. `1.0.0`, or `0.1.0b2` for a beta), commit.
2. Push the branch, then push an annotated tag `vX.Y.Z[abrc]N` (e.g. `v0.1.0b2`).

CI then runs `uv build` and creates a GitHub Release with the wheel + sdist attached and auto-generated notes — marked **prerelease** when the tag contains `a`, `b`, or `rc`. It also publishes to PyPI when the `PYPI_ENABLED` repo variable is `true` (otherwise that job is skipped).

- **Do NOT** manually `uv build` + `gh release create` — CI does it, and a manual `gh release create` conflicts with the auto-created release ("release with the same tag name already exists").
- To improve the auto-generated notes, run `gh release edit <tag> --notes-file <file>` after the workflow has published.

## Issue Tracking

ROADMAP items that resolve a GitHub issue must tag it inline: `[#N]` at the end of the line (see Phases 10–11 for examples). On every push to `main` (i.e. after a develop→main PR or hotfix merges), `.github/workflows/close-resolved-issues.yml` scans ROADMAP.md for `[x]` items carrying an `[#N]` tag and auto-closes the matching issue if still open, with a comment citing the roadmap line and commit SHA. No manual issue-closing should be needed once this convention is followed.

## Workflow Agents

Task orchestration for non-trivial work is delegated to specialized agents and skills; git operations run directly in the main session (see Branch Model's Hard rules).

- **`feature-forecast`** (Haiku subagent, background): Pre-reads the codebase for the next ROADMAP item while the current item is being implemented. Invoked automatically by `/dev-pipeline` with `run_in_background: true`.

- **`sprint-planner`** (Opus subagent, xhigh reasoning): Strategic, up-front sprint planner. At the start of a non-trivial sprint, `/dev-pipeline` partitions the in-scope ROADMAP items into feature clusters and spawns one `sprint-planner` per cluster (up to 4, in parallel) to deep-read the code and return a Planning Report — recommended approach/sequencing, cross-cutting decisions, open questions, proposed roadmap adjustments, risks, and a required `Files touched & dependencies` table (`Writes` / `Depends on` / `Notes`) per item. The root unions those tables across clusters to compute the sprint's wave plan, aggregates open questions, asks the user once (consolidated), and writes an uncommitted `sprint-brief.md` (in gitignored `.claude/tmp/dev-pipeline/<slug>/`) that the rest of the sprint follows and that a resumed sprint reuses. Read-only; never edits, never asks the user directly. This replaces the old standalone design-clarification step.

- **`doc-keeper`** (Sonnet subagent): Documentation maintainer. Runs once per wave in `/dev-pipeline` — Step 4.5 spawns it in the background on wave 1 and continues that **same** agent with `SendMessage` on later waves, so the two big developer docs are read from cold once per sprint rather than once per wave. It runs concurrently with the wave's test run and is joined before the commit, so docs land in the same commit as the code. It is given the wave's diff and the target doc pages, reads narrowly (grep + offset reads on large pages), and makes surgical updates to `README.md` and `docs/**`. Edits docs only — never source, `ROADMAP.md`, or `CLAUDE.md`.

- **`/test-select`**: Select and run the minimal pytest scope for the current branch's changes. Call before every commit. Blocks commit if any test fails.

- **`/dev-pipeline`**: Full sprint orchestrator. Reads `ROADMAP.md`, batches independent unchecked items into dependency-ordered waves, and implements them on a `feat/` branch using the agents above — one commit per wave. Fast-forward-merges into `develop` by default, preserving per-wave commits (squash only on request). Start here when working through the roadmap. (Parallel git worktrees were evaluated and rejected for this repo — see `dev-pipeline/SKILL.md`'s "Why waves and not parallel git worktrees" if this comes up again.) End-of-session improvement reflection is not part of this skill — see `/learn` below.

- **`/learn`**: End-of-session reflection, triggered by the user only. Reads the session transcript in a subagent and writes improvement proposals to `~/.claude/pending-improvements/` and learning notes to `~/.claude/pending-learnings/` — notes only, no review. Review happens later via `/experience-feedback` and `/teach-me-things`. It replaced a global Stop hook that fired too early in a session; never re-wire it as a hook and never run it on your own initiative.

## ROADMAP conventions

- All items must use `- [ ] X1 · description` format (checkbox + label). Plain `- ` bullets are invisible to dev-pipeline.
- If an item depends on a later-listed item, note it inline: `(requires: C5)`. dev-pipeline uses this to detect and handle prerequisite inversion — implementing the dependency first and noting the reordering in the commit body.
- Example: `- [ ] C3 · execute_plan — ... (requires: C5)`

## Concurrent sessions

More than one Claude Code session may work in this repo at once (e.g. one session
mid-sprint while another edits the roadmap or a skill file). This applies to
`ROADMAP.md`, `CLAUDE.md`, and everything under `.claude/skills/**` and
`.claude/agents/**` — files an agent reads once into context and then edits by anchor.

- If an `Edit` result reports the file was modified on disk since it was last read,
  **re-read the file before making any further edit to it** — do not chain more
  anchor-based edits off a stale in-context view.
- Before starting a multi-edit pass on `ROADMAP.md`, a skill/agent file, or `CLAUDE.md`
  itself, re-read it once at the start of the pass rather than trusting an earlier read
  from the same session (see `/dev-pipeline` Step 1's roadmap re-read rule for the
  canonical example).

## Roadmap / Future

- Relax `APPROVAL_MODE` to `destructive_only` once trusted.
- Persistent embedding index for semantic search (optional Stack C add-on).
- Reusable MCP server consumable by other MCP hosts (e.g., Claude Desktop).
- Batch/parallel extraction for large trees.