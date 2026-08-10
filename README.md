# telcontar

Local AI assistant that organizes a directory tree: renames files to readable names based on content, moves them to sensible locations, quarantines clutter, and produces an index and summary. Once a corpus is analyzed, an interactive **query mode** lets you ask natural-language questions over the registry, event journal, and knowledge graph — read-only, no reorganization needed. All file operations run locally; only content snippets are sent to the LLM endpoint.

**Architecture:** custom MCP server (file tools) + custom MCP host (agent loop) over stdio transport.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- Access to any OpenAI-compatible chat-completions endpoint (Azure OpenAI, Mammouth, or another compatible provider)

## Setup

```bash
uv tool install git+https://github.com/reboulip/telcontar.git
```

Then launch `telcontar` once. On first run the **setup wizard** appears automatically — it collects your AI service URL and API key, stores the key in the OS credential store (Windows Credential Manager / macOS Keychain), and saves non-sensitive settings to `~/.telcontar/config.env`. No manual editing of config files required.

For developer / contributor setup (clone + `uv sync`), see [docs/getting-started/installation.md](docs/getting-started/installation.md).

## Usage

```bash
telcontar
```

Telcontar opens in its own native window (via `pywebview`, Windows only) rather than a browser tab — pass `--browser` to use the system browser instead, or telcontar falls back to it automatically (with a warning) if `pywebview` isn't installed or the platform isn't Windows.

On first run the **setup wizard** appears automatically (`/setup`) — it collects your AI service URL and API key. Once configured, telcontar opens on a **startup page** with a directory tree in the left sidebar; pick a folder there, then choose:

- **Use selected directory** — starts an Organize run. It opens on a starter pane showing a code-generated directory overview (file/subfolder counts, common file types — no LLM call yet) plus an optional field for steering instructions (e.g. "group by workstream", "don't quarantine drafts"); press **Start organizing** to launch the run. Telcontar first recursively surveys nested subfolders (not just the top level) and analyzes any documents it hasn't seen before — a document already known from a previous run is never re-read or re-sent to the model; before fetching content for the new ones it pauses once to show a rough cost estimate scoped to just those new documents (new document count, already-analyzed count, estimated input tokens, from file sizes alone) and waits for you to proceed or cancel. The agent then designs and stages the reorganization — free to redesign the existing layout entirely — and may pause at any point before or while building the plan to check in with you in chat: genuine clarifying questions, a few competing options to pick between (e.g. how to group a set of documents), or a mix — reply in the chat box and it continues; not capped at once, since it's a normal chat exchange, so it can check in again later if a new ambiguity comes up.
- **Query** — opens an interactive read-only chat over an already-analyzed corpus (requires the selected folder, or one of its parent folders, to contain a `.organizer/` from a previous Organize run — memory is per-directory, stored inside the organized tree itself).

A chat box at the bottom of the Organize run page is live for the whole run, not just once it stops — type a message at any point (e.g. "actually, group by year instead") and it's woven into the agent's in-progress work as soon as it's between turns, without waiting for the run to finish first. After the run reaches a stopping point (done, error, or max-turns), the same box keeps working — a follow-up message (e.g. "quarantine the drafts too") resumes the same conversation, on the same MCP session, with the same organize toolset (document content stays unavailable, since the corpus was already analyzed). Once the run is done, a **Query this corpus** button jumps into the separate read-only query mode, and a **Browse corpus** button opens a table/detail view of every analyzed document. A **Journal** button (with a live undo-able-operations count) is available throughout the run — it opens a dialog listing every filesystem operation telcontar has made, with an **Undo last operation** action (confirmed before it runs).

Every page keeps the same left sidebar: the directory tree (which live-updates as files are renamed/moved/quarantined), and a **⚙ Settings** entry (`/settings`, reachable from anywhere) — edit URL, API key, profile, and approval mode at any time, including mid-run. Settings also has a read-only "What telcontar tells the model" panel showing the exact organize/query/analyze system prompts telcontar composes and which domain profile actually resolved.

**CLI flags:**

| Flag | Description |
|---|---|
| `--version` | Print the installed version and exit. |
| `--target PATH` | Skip the landing page's directory picker and start a run for `PATH` immediately. |
| `--browser` | Launch the web UI in the system browser instead of a native window. |

## Development

```bash
uv run pytest          # run tests
uv run ruff check .    # lint
uv run mypy .          # type check
```

## Safety model

- `APPROVAL_MODE=always` (default): every plan requires explicit user approval before execution.
- Every path-taking tool is confined to the directory you're organizing (plus telcontar's own working files) — an agent can't be steered into reading or writing outside it.
- Nothing is ever deleted — clutter goes to `QUARANTINE_DIR` (`_quarantine/` by default).
- Every filesystem mutation — renames, moves, quarantines, file writes, folder creation, archiving, and quarantine compression — is staged as a plan op and only takes effect through `execute_plan`; there is no tool that touches the filesystem directly.
- Every destructive operation is journaled. Undo is a manual, user-only action: press **j** in the Organizer screen to open the operations journal, then **u** to revert the most recent operation — the agent itself has no undo tool.
- Compressing loose quarantine files into a verified ZIP archive (reclaiming space) is staged the same way and remains fully reversible via undo.

## Credits

- The web UI's display typeface is [Cinzel](https://github.com/NDISCOVER/Cinzel), copyright The Cinzel Project Authors, licensed under the [SIL Open Font License 1.1](host/web/assets/fonts/OFL.txt) (vendored at `host/web/assets/fonts/`).
