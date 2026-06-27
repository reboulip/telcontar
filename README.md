# telcontar

Local AI assistant that organizes a directory tree: renames files to readable names based on content, moves them to sensible locations, quarantines clutter, and produces an index and summary. All file operations run locally; only content snippets are sent to the LLM endpoint.

**Architecture:** custom MCP server (file tools) + custom MCP host (GPT-5 agent loop) over stdio transport.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- Access to an OpenAI-compatible GPT-5 endpoint (Azure or Mammouth)

## Setup

```bash
# Install dependencies
uv sync

# Copy and fill in the config
cp .env.example .env
# Edit .env: set LLM_BASE_URL, LLM_API_KEY, and (for Azure) LLM_API_VERSION
```

## Usage

```bash
# Organize a directory (runs in APPROVAL_MODE=always by default)
uv run python -m host.main --target "C:\path\to\messy\dir"

# Run the MCP server standalone (usually launched automatically by the host)
uv run python -m server.main
```

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
