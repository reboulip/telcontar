# Configuration

For most users, first-run configuration is handled entirely by the **setup wizard** that appears the first time you launch `organizer-host`. The wizard stores the API key in the OS credential store (Windows Credential Manager / macOS Keychain) and saves non-sensitive settings to `~/.telcontar/config.env`. You can revisit any setting at any time via the **⚙ Settings** button on the startup screen.

The reference below is for **advanced or developer use**: env vars and a project-local `.env` file always take priority over `~/.telcontar/config.env` when both are present. No code changes are required to switch environments — config only.

---

## Full reference

### LLM endpoint

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | **yes** | `""` | Base URL of the OpenAI-compatible endpoint.<br>Azure: `https://<resource>.openai.azure.com/openai/deployments/<deployment>`<br>Mammouth: the standard Mammouth base URL.<br>Set by the wizard and stored in `~/.telcontar/config.env`. |
| `LLM_API_KEY` | **yes** | `""` | API key for the endpoint. Set by the wizard and stored in the OS credential store; falls back to `~/.telcontar/config.env` if the keyring is unavailable. |
| `LLM_MODEL` | no | `gpt-5` | Model name passed in chat completion requests |
| `LLM_API_VERSION` | no | `""` | Azure only — `api-version` query parameter (e.g. `2025-01-01-preview`). Leave blank for Mammouth. |

### Safety

| Variable | Required | Default | Description |
|---|---|---|---|
| `APPROVAL_MODE` | no | `always` | When to require user approval. See [Approval Modes](../user-guide/approval-modes.md). |
| `QUARANTINE_DIR` | no | `_quarantine` | Relative path (from the target directory) where clutter files are moved. Never deleted. |
| `JOURNAL_PATH` | no | `.organizer/journal.jsonl` | Append-only undo journal (file operations, drives `undo_last`). Relative to the project root. |
| `EVENTS_PATH` | no | `.organizer/events.jsonl` | Append-only project event journal (narrative log, drives `create_event` / `list_events`). Relative to the project root. |

### Domain profile

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROFILE` | no | `is_it_project` | Name of the active profile (without `.toml`). Must match a file in `PROFILES_DIR`. |
| `PROFILES_DIR` | no | `profiles` | Directory containing `.toml` profile files. Relative to the project root. |

### Document memory

| Variable | Required | Default | Description |
|---|---|---|---|
| `REGISTRY_PATH` | no | `.organizer/registry.json` | Path to the persistent document registry (content-addressed, sha256-keyed). |
| `GRAPH_PATH` | no | `.organizer/graph.json` | Path where the knowledge graph is persisted. Rebuilt on demand by `build_graph`; read without rebuilding by `get_graph`. |
| `ARCHIVE_PATH` | no | `.organizer/archive.jsonl` | Append-only archive log: records every document withdrawn from active memory via `archive_document` (what was archived, why, and where the file moved). |

### Egress / extraction

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAX_SNIPPET_CHARS` | no | `4000` | Maximum characters returned by `read_file` and `extract_text`. Defense-in-depth cap even when full content is allowed. |
| `ALLOWLIST_DIRS` | no | `""` | JSON array of absolute directory paths, e.g. `["C:/Users/me/docs"]`. When set, telcontar can only read content from these paths. Leave blank to allow any path. |
| `EGRESS_ALLOW_EXTERNAL_SINKS` | no | `false` | Allow non-local output sinks (e.g. a MediaWiki MCP integration). The built-in `local_markdown` sink is always allowed regardless of this flag. Set to `true` only when you have connected a separate MCP sink integration and want its name listed in the profile's `[sinks] default`. |

---

## Switching environments

Telcontar uses the same code path for Azure and Mammouth — only the `base_url` and `api_key` differ:

=== "Mammouth (dev)"
    ```ini
    LLM_BASE_URL=https://api.mammouth.ai/v1
    LLM_API_KEY=mam-...
    LLM_MODEL=gpt-5
    LLM_API_VERSION=
    ```

=== "Azure OpenAI (prod)"
    ```ini
    LLM_BASE_URL=https://my-resource.openai.azure.com/openai/deployments/gpt-5
    LLM_API_KEY=az-...
    LLM_MODEL=gpt-5
    LLM_API_VERSION=2025-01-01-preview
    ```

!!! tip
    Keep two `.env` files (`.env.dev`, `.env.prod`) and symlink or copy the active one to `.env`. No code changes are ever needed to switch.

---

## Persistent state locations

Telcontar writes its state under `.organizer/` in the **project root** (not the target directory):

```
.organizer/
├── plans/          # One JSON file per plan
├── journal.jsonl   # Append-only undo log (file operations)
├── events.jsonl    # Append-only project event journal (narrative log)
├── archive.jsonl   # Append-only archive log (why a document left active memory)
├── registry.json   # Document memory (sha256 → metadata)
└── graph.json      # Knowledge graph (derived from registry + events; rebuilt on demand)
```

These files are gitignored by default and survive between runs.
