# telcontar

Local AI assistant that organizes a directory tree: renames files to readable names based on content, moves them to sensible locations, quarantines clutter, and produces an index and summary. Once a corpus is analyzed, an interactive **query mode** lets you ask natural-language questions over the registry, event journal, and knowledge graph — read-only, no reorganization needed. All file operations run locally; only content snippets are sent to the LLM endpoint.

**Architecture:** custom MCP server (file tools) + custom MCP host (GPT-5 agent loop) over stdio transport.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- Access to an OpenAI-compatible GPT-5 endpoint (Azure or Mammouth)

## Setup

```bash
uv tool install git+https://github.com/rreboulleau/telcontar.git
```

Then launch `organizer-host` once. On first run the **setup wizard** appears automatically — it collects your AI service URL and API key, stores the key in the OS credential store (Windows Credential Manager / macOS Keychain), and saves non-sensitive settings to `~/.telcontar/config.env`. No manual editing of config files required.

For developer / contributor setup (clone + `uv sync`), see [docs/getting-started/installation.md](docs/getting-started/installation.md).

## Usage

```bash
organizer-host
```

The Textual TUI opens. On first run the **setup wizard** appears; returning users land on the **startup screen**, which offers three actions:

- **Organize** — analyze and reorganize the target directory (full agent loop).
- **Query** — open an interactive read-only chat over the already-analyzed corpus (requires an existing registry at `REGISTRY_PATH`).
- **⚙ Settings** — edit URL, API key, profile, and approval mode at any time (also accessible by pressing `s`).

After organizing, press **g** in the Organizer screen to jump straight into query mode over the just-analyzed corpus.

## Development

```bash
uv run pytest          # run tests
uv run ruff check .    # lint
uv run mypy .          # type check
```

## Safety model

- `APPROVAL_MODE=always` (default): every plan requires explicit user approval before execution.
- Nothing is ever deleted — clutter goes to `QUARANTINE_DIR` (`_quarantine/` by default).
- Every destructive operation is journaled; `undo_last` reverts the most recent one.
- `compress_quarantine` bundles loose quarantine files into a verified ZIP archive and reclaims space; it is the only tool that removes bytes from disk, and it remains fully reversible via `undo_last`.
