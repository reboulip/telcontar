# Installation

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | Tested on 3.12 and 3.13 |
| [uv](https://docs.astral.sh/uv/) | Package and environment manager |
| GPT-5 endpoint | Azure OpenAI (prod) or Mammouth (dev) — any OpenAI-compatible API works |

### Install uv

=== "Windows (PowerShell)"
    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

=== "macOS / Linux"
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

---

## Install telcontar

```bash
# Clone the repository
git clone https://github.com/rreboulleau/telcontar.git
cd telcontar

# Install all runtime dependencies
uv sync
```

This creates a `.venv/` in the project root and installs all packages declared in `pyproject.toml`.

---

## Configure

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```ini
LLM_BASE_URL=https://your-endpoint/openai/deployments/gpt-5   # or Mammouth URL
LLM_API_KEY=your-api-key
LLM_API_VERSION=2025-01-01-preview   # Azure only; leave blank for Mammouth
```

All other settings have sensible defaults. See [Configuration](configuration.md) for the full reference.

---

## Verify the install

```bash
# Confirm the entry point resolves
uv run organizer-host --help

# Run the test suite
uv run --group test pytest -q
```

---

## Entry points

| Command | What it does |
|---|---|
| `organizer-host` | Launches the Textual TUI (the normal way to use telcontar) |
| `organizer-server` | Starts the MCP server over stdio (usually invoked automatically by the host) |

Both are declared in `pyproject.toml` under `[project.scripts]` and are available after `uv sync`.
