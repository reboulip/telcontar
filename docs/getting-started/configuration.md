# Configuration

For most users, first-run configuration is handled entirely by the **setup wizard** that appears the first time you launch `telcontar`. The wizard stores the API key in the OS credential store (Windows Credential Manager / macOS Keychain) and saves non-sensitive settings to `~/.telcontar/config.env`. You can revisit any setting at any time via the **⚙ Settings** button on the startup screen.

The reference below is for **advanced or developer use**: env vars and a project-local `.env` file always take priority over `~/.telcontar/config.env` when both are present. No code changes are required to switch environments — config only.

---

## Full reference

### LLM endpoint

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | **yes** | `""` | Base URL of the OpenAI-compatible endpoint.<br>Azure: any of a bare resource root (`https://<resource>.openai.azure.com`), `.../openai`, `.../openai/deployments/<deployment>`, or `.../openai/v1` — all four shapes are recognized (Y4, GH #61).<br>Mammouth: the standard Mammouth base URL.<br>Set by the wizard and stored in `~/.telcontar/config.env`. |
| `LLM_API_KEY` | **yes** | `""` | API key for the endpoint. Set by the wizard and stored in the OS credential store. If the keyring is unavailable, the wizard/settings screen warns loudly and requires pressing the save/finish button a second time to explicitly confirm storing it in plaintext at `~/.telcontar/config.env` instead — it is never written there silently. |
| `LLM_MODEL` | no | `gpt-5` | Model name passed in chat completion requests |
| `LLM_API_VERSION` | no | `""` | Azure only — `api-version` query parameter (e.g. `2025-01-01-preview`). Leave blank for every other provider. When left blank on an Azure host detected by hostname (`*.azure.com`) rather than an explicit version, telcontar now falls back to a built-in default API version automatically (Y4). Ignored entirely when `LLM_BASE_URL` ends in `/openai/v1` (Azure's own OpenAI-compatible surface, which must not receive an injected `api-version`). |

### Safety

| Variable | Required | Default | Description |
|---|---|---|---|
| `APPROVAL_MODE` | no | `always` | When to require user approval. See [Approval Modes](../user-guide/approval-modes.md). |
| `TARGET_DIR` | no | *(unset)* | The directory being organized this run. Not meant to be set by hand — the host sets it automatically (as a subprocess env var) whenever it launches an organize or query session, so the MCP server can confine every path-taking tool to it. Every path-taking tool call is checked against `TARGET_DIR` plus the server's own working directory via `check_within_root`; a path outside both is rejected regardless of `ALLOWLIST_DIRS`. As of per-directory memory, `TARGET_DIR` is also where `.organizer/` and the quarantine dir physically live for the run (see [Persistent state locations](#persistent-state-locations) below) — `Settings.for_target(target)` rebases all the memory paths below onto it whenever `TARGET_DIR` is set, which is every real organize/query session. |
| `QUARANTINE_DIR` | no | `_quarantine` | Relative path (from the target directory) where clutter files are moved. Never deleted. |
| `EMPTY_FOLDER_POLICY` | no | `quarantine` | How `execute_plan` disposes of a folder its own `move`/`quarantine`/`archive_document` ops left empty, once all ops have run (Y6, GH #57). `quarantine` (default) moves the folder into `QUARANTINE_DIR`, falling back to an in-place `_empty_`-prefixed rename on any error; `rename` always renames in place; `off` disables the sweep. Only activates when `TARGET_DIR` is set (every real organize/query session). Treated as an automatic, fully journaled/undoable consequence of the already-approved moves, not a separate approval-requiring op. |
| `JOURNAL_PATH` | no | `.organizer/journal.jsonl` | Append-only undo journal (file operations, drives `undo_last`). Relative to the target directory being organized (rebased there per run — an explicit absolute override passes through unchanged). |
| `EVENTS_PATH` | no | `.organizer/events.jsonl` | Append-only project event journal (narrative log, drives `create_event` / `list_events`). Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |

### Domain profile

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROFILE` | no | `is_it_project` | Name of the active profile (without `.toml`). Must match a file in `PROFILES_DIR`. |
| `PROFILES_DIR` | no | `profiles` | Directory containing `.toml` profile files. Relative to the project root. |

### Document memory

| Variable | Required | Default | Description |
|---|---|---|---|
| `REGISTRY_PATH` | no | `.organizer/registry.json` | Path to the persistent document registry (content-addressed, sha256-keyed). Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |
| `GRAPH_PATH` | no | `.organizer/graph.json` | Path where the knowledge graph is persisted. Rebuilt on demand by `build_graph`; read without rebuilding by `get_graph`. Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |
| `ARCHIVE_PATH` | no | `.organizer/archive.jsonl` | Append-only archive log: records every document withdrawn from active memory via `archive_document` (what was archived, why, and where the file moved). Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |
| `SESSIONS_DIR` | no | `.organizer/sessions` | Directory holding one JSON snapshot per session (transcript, activity log, full LLM message history), for cross-restart resume (Y2, GH #53). Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`), since a snapshot carries corpus-derived text and must stay inside the same allowlist/egress boundary the rest of this table does. Not exposed as an MCP tool. See also `~/.telcontar/sessions.json` under [Persistent state locations](#persistent-state-locations) — a separate, metadata-only home-directory index of every session, not rebased by this variable. |

### Egress / extraction

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAX_SNIPPET_CHARS` | no | `4000` | Maximum characters returned by `read_file` and `extract_text` (and, per file, by their batch forms `read_file_batch`/`extract_text_batch`). Defense-in-depth cap even when full content is allowed. |
| `ALLOWLIST_DIRS` | no | `""` | JSON array of absolute directory paths, e.g. `["C:/Users/me/docs"]`. When set, telcontar can only read content from these paths for `read_file`/`extract_text`/`compare_documents` and the batch forms `read_file_batch`/`extract_text_batch` — a stricter, explicit bound that replaces (not merges with) the default. Leave blank and it now defaults to `[TARGET_DIR]` rather than "no restriction" — narrower than the always-on `TARGET_DIR` + server-cwd confinement described above, which still applies independently to every other path-taking tool. |
| `MAX_EXTRACT_FILE_BYTES` | no | `200000000` | Input-size cap (bytes) for `extract_text`/`compare_documents` (and `extract_text_batch`, per file). Files larger than this are rejected before `markitdown` ever runs, with a `ValueError`. S5 hardening — see [Security Model](../developer/security-model.md). |
| `MAX_EXTRACT_TIMEOUT_SECS` | no | `30` | Wall-clock timeout (seconds) for the `markitdown` parse itself, run in a worker thread so it works cross-platform (including Windows). A parse that exceeds this raises `TimeoutError`. |
| `EGRESS_PATH` | no | `.organizer/egress.jsonl` | Append-only audit log: one entry (path, size in bytes, tool, timestamp) per `read_file`/`extract_text`/`compare_documents` call — and, per successful file, per `read_file_batch`/`extract_text_batch` call — recording what content was sent to the LLM endpoint. Not exposed as an MCP tool — open the file directly to audit a run. Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). S8 hardening — see [Security Model](../developer/security-model.md). |
| `EGRESS_ALLOW_EXTERNAL_SINKS` | no | `false` | Allow non-local output sinks (e.g. a MediaWiki MCP integration). The built-in `local_markdown` sink is always allowed regardless of this flag. Set to `true` only when you have connected a separate MCP sink integration and want its name listed in the profile's `[sinks] default`. |

### Token usage

| Variable | Required | Default | Description |
|---|---|---|---|
| `TOKEN_LOG_PATH` | no | `.organizer/tokens.jsonl` | Append-only profiling log: one entry per LLM call (analyze/organize/query/estimate phases) recording input/output/cached token counts and running totals, for optimization analysis (R2). Always on — no flag to disable it. Not exposed as an MCP tool — open the file directly. Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |
| `LLM_DEBUG_LOG_PATH` | no | `.organizer/llm-debug.jsonl` | Append-only debug log: one entry per outbound LLM HTTP call (client construction, request, response, transport error), recording redacted URLs, status codes, durations, and request ids — no message content, no API key (Y5, GH #60). Always on — no flag to disable it. Not exposed as an MCP tool — open the file directly. Relative to the target directory being organized (same rebasing as `JOURNAL_PATH`). |

---

## Backend compatibility

Telcontar works with **any OpenAI-compatible chat-completions endpoint that
supports tool calling** — the code path never branches on which provider
you point it at; swapping `LLM_BASE_URL`/`LLM_API_KEY` (and, for Azure only,
`LLM_API_VERSION`) is the only thing that changes. Concretely, the endpoint
must support:

- `POST /chat/completions` with `model`, `messages`, `tools`, and
  `tool_choice`.
- **Forced tool choice** (`tool_choice={"type": "function", "function":
  {"name": ...}}`), used by the document analyzer to guarantee a structured
  response for every batch.
- Multiple `tool_calls` per assistant message.
- *(optional)* `usage.prompt_tokens` / `usage.completion_tokens` (and,
  ideally, `usage.prompt_tokens_details.cached_tokens`) — telcontar's token
  counters and profiling log (see [Token usage](#token-usage) above) degrade
  to a silent no-op when a response omits `usage`.
- *(optional)* an `api-version` query parameter — Azure OpenAI only; every
  other provider ignores it (leave `LLM_API_VERSION` blank).

!!! warning
    An endpoint that silently ignores forced `tool_choice` will degrade
    document analysis rather than error loudly — if analysis results look
    wrong on an unfamiliar endpoint, verify it actually honors forced tool
    choice before assuming a telcontar bug.

Verified against Azure OpenAI and Mammouth. Any other endpoint meeting the
contract above should work.

---

## Switching environments

Telcontar uses the same code path for every OpenAI-compatible endpoint — only the `base_url` and `api_key` (and, for Azure, `api_version`) differ:

=== "Generic OpenAI-compatible endpoint"
    ```ini
    LLM_BASE_URL=https://api.mammouth.ai/v1
    LLM_API_KEY=mam-...
    LLM_MODEL=gpt-5
    LLM_API_VERSION=
    ```

=== "Azure OpenAI (needs api-version)"
    ```ini
    LLM_BASE_URL=https://my-resource.openai.azure.com/openai/deployments/gpt-5
    LLM_API_KEY=az-...
    LLM_MODEL=gpt-5
    LLM_API_VERSION=2025-01-01-preview
    ```

!!! tip
    Keep two `.env` files (e.g. `.env.dev`, `.env.prod`) and symlink or copy the active one to `.env`. No code changes are ever needed to switch.

---

## Persistent state locations

Telcontar's memory is **per-directory**: each run's `.organizer/` state lives *inside the directory being organized* (`TARGET_DIR`), not the project root — so organizing several unrelated folders keeps each one's registry, journal, and plans separate. This is handled by `Settings.for_target(target)`: whenever `TARGET_DIR` is set (every real organize/query session), it rebases each of the paths below onto `target.resolve()`; an explicitly absolute path (a manual override) passes through unchanged instead of being rebased.

```
<target directory>/
├── .organizer/
│   ├── plans/          # One JSON file per plan
│   ├── sessions/        # One JSON snapshot per session (transcript/history, Y2)
│   ├── journal.jsonl   # Append-only undo log (file operations)
│   ├── events.jsonl    # Append-only project event journal (narrative log)
│   ├── archive.jsonl   # Append-only archive log (why a document left active memory)
│   ├── egress.jsonl    # Append-only audit log of content sent to the LLM endpoint
│   ├── tokens.jsonl    # Append-only per-LLM-call token-usage profiling log
│   ├── llm-debug.jsonl # Append-only debug log of outbound LLM HTTP calls (metadata only)
│   ├── registry.json   # Document memory (sha256 → metadata)
│   └── graph.json      # Knowledge graph (derived from registry + events; rebuilt on demand)
└── _quarantine/         # Quarantined files (QUARANTINE_DIR)
```

A separate, home-directory file — `~/.telcontar/sessions.json` — indexes every
session ever started, across every target: metadata only (`run_id`/`target`/
`mode`/timestamps/`status`), never the transcript/history above. It is
deliberately **not** rebased by `SESSIONS_DIR` or anything else in this
section — it lives outside every target directory (and its allowlist/egress
boundary) on purpose, the same way `~/.telcontar/config.env` does.

Both `.organizer/` and the quarantine folder are hidden from the agent's own directory discovery (`walk_tree`) so it never proposes moving or quarantining its own memory — the quarantine folder is deliberately still shown in the written `INDEX.md`, since a human reviewing results should be able to see it.

`PROFILES_DIR` and `.organizer/NAMING.md` are the exception: they are cross-corpus, project-level conventions rather than per-run memory, and are deliberately **not** rebased — they always resolve relative to telcontar's own project root regardless of which directory you're organizing.

These files survive between runs. If the target directory is itself a git repository, add your own `.organizer/` and `_quarantine/` entries to its `.gitignore` if you don't want them tracked — telcontar's own `.gitignore` only covers its project root, not directories you organize. There is no migration path for `.organizer` folders created at the project root by a pre-per-directory-memory version of telcontar — a fresh run against a new target simply starts that target's memory from scratch.

Query mode resolves which memory to use by walking up from the folder you select until it finds a `.organizer` — so picking a subfolder of a previously-organized tree still resolves to that tree's memory.
