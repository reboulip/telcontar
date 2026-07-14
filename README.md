# telcontar

Local AI assistant that organizes a directory tree: renames files to readable names based on content, moves them to sensible locations, quarantines clutter, and produces an index and summary. Once a corpus is analyzed, an interactive **query mode** lets you ask natural-language questions over the registry, event journal, and knowledge graph — read-only, no reorganization needed. All file operations run locally; only content snippets are sent to the LLM endpoint.

**Architecture:** custom MCP server (file tools) + custom MCP host (GPT-5 agent loop) over stdio transport.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- Access to an OpenAI-compatible GPT-5 endpoint (Azure or Mammouth)

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

The Textual TUI opens. On first run the **setup wizard** appears; returning users land on the **startup screen**, which offers three actions:

- **Organize** — opens on a starter pane showing a code-generated directory overview (file/subfolder counts, common file types — no LLM call yet) plus an optional field for steering instructions (e.g. "group by workstream", "don't quarantine drafts"); press **Start organizing** to launch the run. Telcontar first recursively surveys nested subfolders (not just the top level) and analyzes any documents it hasn't seen before — a document already known from a previous run is never re-read or re-sent to the model; before fetching content for the new ones it pauses once to show a rough cost estimate scoped to just those new documents (document count, estimated input tokens, from file sizes alone) and waits for you to proceed or cancel. The agent then designs and stages the reorganization — free to redesign the existing layout entirely — and may pause once, before building the plan, to ask a few clarifying questions if something is genuinely ambiguous — answer them or skip to let it use its best judgement — and may pause once more, after re-examining its approach from a second angle, to let you pick between a few competing options (e.g. how to group a set of documents) — choose one per question or skip to let it decide.
- **Query** — open an interactive read-only chat over the already-analyzed corpus (requires the selected folder, or one of its parent folders, to contain a `.organizer/` from a previous Organize run — memory is per-directory, stored inside the organized tree itself).
- **⚙ Settings** — edit URL, API key, profile, and approval mode at any time (also accessible by pressing `s`).

Once the run reaches a stopping point (done, error, or max-turns), a chat box appears at the bottom of the Organizer screen — type a follow-up message (e.g. "quarantine the drafts too") to resume the same conversation, on the same MCP session, with the same organize toolset (document content stays unavailable, since the corpus was already analyzed). Press **g** instead to jump into a separate read-only query mode over the just-analyzed corpus.

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
