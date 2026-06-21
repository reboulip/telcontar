# Architecture

## Overview

Telcontar is a locally-run AI directory organizer that automatically cleans up and reorganizes file hierarchies. It combines intelligent analysis (via GPT-5 on an OpenAI-compatible endpoint) with guarded local file operations to rename files, move them to appropriate locations, quarantine clutter, build searchable indexes, and produce summaries. All file I/O stays on the local machine; only content and metadata needed for reasoning are sent to the model endpoint.

## Components

**MCP Server** (`server/`)
Exposes guarded file-system tools via the Model Context Protocol. Owns all collision detection, quarantine logic, and the undo journal. Never deletes—only quarantines. Tools are grouped into read-only (safe, no approval needed), plan-building (propose operations to a pending plan), and gated execution (respect `APPROVAL_MODE`).

**MCP Host** (`host/`)
Runs the GPT-5 agent loop, connecting to the server over stdio. Manages the plan/approval/execute flow and coordinates with the OpenAI-compatible endpoint (Azure in production, Mammouth in development). Handles user interaction, presenting plan diffs for approval before execution.

**Config** (`config/`)
Centralized environment-based configuration. Swaps between dev and prod by changing only `LLM_BASE_URL` and `LLM_API_KEY`; all other config remains unchanged.

## Data Flow

```
User
  ↓
Host (GPT-5 loop, approval gate)
  ↓
MCP Server (tool execution, guards, journal)
  ↓
Filesystem + Undo journal + Quarantine folder
  ↑
OpenAI-compatible endpoint (GPT-5)
```

The host inspects the directory tree via read-only MCP tools, feeds observations to the model, collects proposed operations, presents them to the user for approval, and then executes them via the server. Each destructive operation (rename, move, quarantine) is immediately journaled so that `undo_last` can reverse it.

## Configuration

Single `.env` file (not committed) controls all behavior. Swap dev ↔ prod by updating only `LLM_BASE_URL` and `LLM_API_KEY`.

| Variable | Type | Default | Notes |
|----------|------|---------|-------|
| `LLM_BASE_URL` | string | (required) | Azure OpenAI endpoint (prod) or Mammouth base URL (dev) |
| `LLM_API_KEY` | string | (required) | API key for the endpoint |
| `LLM_MODEL` | string | `gpt-5` | Model name to request |
| `LLM_API_VERSION` | string | (optional) | Azure only; ignored for Mammouth |
| `APPROVAL_MODE` | enum | `always` | `always` (every plan requires approval), `destructive_only` (read-only ops run freely), `never` (full autonomy) |
| `QUARANTINE_DIR` | string | `_quarantine` | Relative path where unwanted files are moved |
| `JOURNAL_PATH` | string | `.organizer/journal.jsonl` | Relative path to the append-only undo journal |
| `MAX_SNIPPET_CHARS` | int | `4000` | Maximum characters returned by `read_file` and `extract_text` (defense-in-depth) |
| `ALLOWLIST_DIRS` | string | (optional) | Comma-separated list of directories; if set, restricts content upload to these paths only |

Both Azure OpenAI and Mammouth APIs are OpenAI-compatible. The `openai` Python SDK uses the same code path; only the `base_url` and optional `api_version` differ.

## Safety Model

### Approval Modes

| Mode | Description |
|------|-------------|
| `always` (default) | Every plan, destructive or not, requires explicit user approval before execution |
| `destructive_only` | Only moves, renames, and quarantines require approval; read-only ops (index, summary) run without gating |
| `never` | Full autonomy; all operations execute immediately (only after trust is established) |

Start in `always` mode during development. Relax via config—no code changes required.

### Non-Negotiable Rules

- **Never delete.** Unwanted files go to `QUARANTINE_DIR`; they can be recovered or permanently deleted manually later.
- **Never overwrite.** On name collision, the operation fails and suggests a manual suffix or skip. The guard is applied eagerly at proposal time.
- **Every destructive op is journaled and reversible.** Each rename, move, or quarantine is appended to the undo journal with enough information to reverse it. The `undo_last` tool pops the most recent entry and inverts it.

### Plan → Approve → Execute Flow

1. Agent inspects the directory tree using read-only tools.
2. Agent proposes a set of operations (rename, move, quarantine) via `propose_*` tools, which accumulate in a pending plan.
3. Host presents a human-readable diff of the plan to the user.
4. User approves or rejects the plan.
5. If approved, host calls `execute_plan`, which applies each operation in order and journals each success.
6. Agent may propose further plans and repeat.

Approval is gated by `APPROVAL_MODE`. In `destructive_only` mode, read-only operations (building indexes and summaries) skip the approval gate.
