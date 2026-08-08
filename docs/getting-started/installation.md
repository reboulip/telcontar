# Installation

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | Tested on 3.12 and 3.13 |
| [uv](https://docs.astral.sh/uv/) | Package and environment manager |
| OpenAI-compatible endpoint | Azure OpenAI, Mammouth, or any other provider with an OpenAI-compatible chat-completions API |

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

=== "From GitHub (recommended)"
    ```bash
    uv tool install git+https://github.com/reboulip/telcontar.git
    ```

=== "From a local clone"
    ```bash
    git clone https://github.com/reboulip/telcontar.git
    cd telcontar
    uv tool install .
    ```

`uv tool install` places `telcontar` in your PATH so you can run it from any directory.

---

## First run

Launch the app once to complete the setup wizard:

```bash
telcontar
```

The wizard asks for your AI service URL and API key, then saves them securely (OS credential store on Windows and macOS, or `~/.telcontar/config.env` as fallback). No manual editing of config files required.

!!! tip
    You can re-open the settings at any time from the main screen using the **⚙ Settings** button or pressing `s`.

---

## Verify the install

```bash
# Confirm the entry point resolves
telcontar --help

# Check the installed version
telcontar --version

# Run the test suite (if you cloned the repo)
uv run --group test pytest -q
```

---

## Entry points

| Command | What it does |
|---|---|
| `telcontar` | Launches the NiceGUI web UI in a local browser tab (the normal way to use telcontar) |
| `telcontar-server` | Starts the MCP server over stdio (usually invoked automatically by the host) |

Both accept `--help` and `--version`, which print and exit immediately without launching either UI or the server.

`telcontar` also accepts `--tui` (launches the Textual TUI instead of the web UI — has its own setup wizard, settings view, journal/undo view, and read-only query mode at parity with the web UI's, including a startup screen with direct Organize/Query/Settings entry points) and `--target PATH` (skips the web UI's landing page directory picker and jumps straight to a run for `PATH`; ignored together with `--tui`).

---

## Advanced: developer setup

If you want to contribute or run the full test suite, clone and sync the dev dependencies instead:

```bash
git clone https://github.com/reboulip/telcontar.git
cd telcontar
uv sync --all-groups
```

For dev, point `LLM_BASE_URL`/`LLM_API_KEY` at your endpoint via a `.env` file in the project root.  See [Configuration](configuration.md) for the full reference.
