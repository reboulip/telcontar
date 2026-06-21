# CLAUDE.md — Local Directory Organizer (MCP + GPT-5)

## Project Overview

A locally-run AI assistant that cleans up and organizes a directory tree: renaming files, moving them, quarantining clutter, building a searchable index, and producing an overall summary. The runner executes locally (all file operations stay on the machine); intelligence comes from GPT-5 via an OpenAI-compatible API endpoint.

**Architecture:** MCP-based (Stack B1).
- A custom Python MCP server exposes guarded file-system tools.
- A thin custom Python MCP host runs the GPT-5 agent loop and routes to an OpenAI-compatible endpoint (Azure in prod, Mammouth in dev).

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

### Components

1. **MCP Server** (`server/`) — exposes file tools, owns all guardrails, the quarantine logic, and the undo journal. Never deletes.
2. **MCP Host** (`host/`) — runs the GPT-5 loop, connects to the MCP server over stdio, manages the plan/approval flow and config.
3. **Config** (`config/`) — env-based, swaps dev/prod endpoints.

### Data flow

```
User → Host (GPT-5 loop) → MCP Server (tools) → Local filesystem
              ↑                      ↓
       OpenAI-compatible       Undo journal +
       endpoint (GPT-5)        quarantine folder
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

# --- Egress / extraction ---
MAX_SNIPPET_CHARS=4000   # defense-in-depth even though full content is allowed
ALLOWLIST_DIRS=          # optional: restrict content upload to these dirs
```

> **Note:** Azure OpenAI and Mammouth are both OpenAI-compatible. Use the openai SDK with `base_url`/`api_key` overrides. For Azure, set `api_version` and the deployment-style endpoint; for Mammouth, the standard base URL. **No code change to switch — config only.**

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

**Gated execution** (respect `APPROVAL_MODE`):
- `execute_plan(plan_id)` — apply approved ops; journal each one.
- `write_index(path)` — emit Markdown index + JSON manifest.
- `write_summary(path)` — emit overall summary.

**Recovery:**
- `undo_last()` — revert the most recent journaled op.

**No delete tool exists. Quarantine only.**

## Outputs

- `INDEX.md` — human-readable index of the organized tree.
- `manifest.json` — structured file metadata + final locations.
- `SUMMARY.md` — overall summary of the directory's contents.
- `.organizer/journal.jsonl` — append-only undo journal.

## Project Structure

```
project/
├── CLAUDE.md
├── pyproject.toml          # uv-managed
├── .env                    # config (gitignored)
├── host/
│   ├── __init__.py
│   ├── main.py             # GPT-5 agent loop, MCP client, approval flow
│   └── llm.py              # openai SDK wrapper (Azure/Mammouth via base_url)
├── server/
│   ├── __init__.py
│   ├── main.py             # MCP server entrypoint (stdio)
│   ├── tools.py            # tool implementations
│   ├── guards.py           # collision/overwrite/quarantine rules
│   ├── journal.py          # undo journal
│   └── extract.py          # markitdown/pypdf text extraction
└── config/
    └── settings.py         # env loading + validation
```

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

## Workflow Agents

All git work and task orchestration is delegated to specialized agents and skills. The main session focuses on domain implementation only.

- **`repo-manager`** (Haiku subagent): Handles all git operations and edits to generic project files (`pyproject.toml`, `.gitignore`, `README.md`, `ROADMAP.md`, `CLAUDE.md`). **Always delegate git commits and branch operations here — never run `git commit` in the main session.**

- **`feature-forecast`** (Haiku subagent, background): Pre-reads the codebase for the next ROADMAP item while the current item is being implemented. Invoked automatically by `/dev-pipeline` with `run_in_background: true`.

- **`/test-select`**: Select and run the minimal pytest scope for the current branch's changes. Call before every commit. Blocks commit if any test fails.

- **`/auto-improve`**: At the end of a task series, scans the conversation for boilerplate instructions, repeated corrections, and automation opportunities. Proposes improvements to skills, hooks, or config. **Always asks before applying anything.** Run this at the end of each sprint.

- **`/dev-pipeline`**: Full sprint orchestrator. Reads `ROADMAP.md`, implements all unchecked items in order on a `feat/` branch using the agents above, then squash-merges into `develop`. Start here when working through the roadmap.

## ROADMAP conventions

- All items must use `- [ ] X1 · description` format (checkbox + label). Plain `- ` bullets are invisible to dev-pipeline.
- If an item depends on a later-listed item, note it inline: `(requires: C5)`. dev-pipeline uses this to detect and handle prerequisite inversion — implementing the dependency first and noting the reordering in the commit body.
- Example: `- [ ] C3 · execute_plan — ... (requires: C5)`

## Roadmap / Future

- Relax `APPROVAL_MODE` to `destructive_only` once trusted.
- Persistent embedding index for semantic search (optional Stack C add-on).
- Reusable MCP server consumable by other MCP hosts (e.g., Claude Desktop).
- Batch/parallel extraction for large trees.