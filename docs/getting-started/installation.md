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

=== "From PyPI (recommended)"
    ```bash
    uv tool install telcontar
    ```

=== "From GitHub"
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
    You can re-open the settings at any time from any page using the nav bar's **Settings** tab or the **⚙ Settings** entry in the sidebar.

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
| `telcontar` | Launches the NiceGUI web UI, by default in its own native window (via `pywebview`, Windows only — falls back to a browser tab automatically if unavailable) — the normal way to use telcontar |
| `telcontar-server` | Starts the MCP server over stdio (usually invoked automatically by the host) |

Both accept `--help` and `--version`, which print and exit immediately without launching either UI or the server.

`telcontar` also accepts `--browser` (launches the web UI in the system browser instead of a native window) and `--target PATH` (skips the landing page's directory picker and jumps straight to a run for `PATH`).

`telcontar --auto-class --target PATH [--dry-run]` runs headless: it never opens the web UI. For a directory telcontar has already organized, it analyzes the **new** documents sitting directly at the root, records them in the registry, and moves each into one of the folders that already exist. Nothing else changes: no renames, no new folders, no quarantine, and no approval prompt (the flag is your consent for that run, whatever `APPROVAL_MODE` says). The directory must already contain `.organizer/`, `INDEX.md` and `.organizer/registry.json`; otherwise the command exits with code 2 and leaves the directory untouched. New documents in sub-folders are reported but not touched, duplicates stay where they are, and a document with no fitting folder (or whose move would collide with an existing name) is left in place. After moving, it refreshes `INDEX.md` and `manifest.json`, then re-composes `SUMMARY.md` with one LLM call. `--dry-run` still analyzes and places (so it calls the LLM) but records nothing, moves nothing, writes no index or summary, and prints "Would file N of M". `--dry-run` is only valid with `--auto-class`. Exit codes: `0` done (including nothing to do or documents left in place), `1` done with errors (analysis failures, failed moves, or a failed index/summary refresh), `2` precondition or configuration error.

---

## Advanced: developer setup

If you want to contribute or run the full test suite, clone and sync the dev dependencies instead:

```bash
git clone https://github.com/reboulip/telcontar.git
cd telcontar
uv sync --all-groups
```

For dev, point `LLM_BASE_URL`/`LLM_API_KEY` at your endpoint via a `.env` file in the project root.  See [Configuration](configuration.md) for the full reference.

For the full toolchain (ruff, mypy, ty, pre-commit hooks) and contribution workflow, see [Contributing](../developer/contributing.md).
