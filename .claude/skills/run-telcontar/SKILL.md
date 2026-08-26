---
name: run-telcontar
description: Build, run, and drive telcontar's NiceGUI web UI (and the MCP server it launches). Use when asked to start telcontar, run the app, take a screenshot of its UI, or confirm a change works in the real app.
---

telcontar is a NiceGUI web app with no browser-installed `chromium-cli` in
this environment, so it's driven directly with Playwright via the Python
REPL driver at `.claude/skills/run-telcontar/driver.py`. The driver
launches `host.web.main.run_web()` as a subprocess (capturing the
per-launch auth token + port that `host/web/security.py` generates
internally — never printed to stdout) and feeds a headless Chromium page
through stdin commands. It also exercises the real MCP server subprocess
(`server.main`) once a run reaches Organize/Query, since that's the
project's other deployable surface (`telcontar-server` in
`pyproject.toml`'s `[project.scripts]`) and isn't normally launched
standalone.

All paths below are relative to the repo root (`/workspace/telcontar`).

## Prerequisites

Chromium is already installed system-wide here, but the driver launches
its own Playwright-managed Chromium (already cached in this container at
`~/.cache/ms-playwright/`). On a fresh machine:

```bash
uv run --with playwright playwright install chromium
```

## Setup

```bash
uv sync --group dev --group test
```

**Known bad install:** `uv sync` can leave `magika` (a `markitdown[pdf,...]`
transitive dep, imported by `server/extract.py`) partially installed — the
`.dist-info` lands but the actual `magika/` package directory doesn't, so
`import magika` fails with `ModuleNotFoundError` the moment the MCP server
subprocess starts (see Troubleshooting). Check for it up front:

```bash
uv run python -c "import magika" || uv sync --reinstall-package magika
```

No LLM endpoint is required to drive the flows below — the driver stubs
`LLM_BASE_URL`/`LLM_API_KEY` with fake values just to satisfy
`config.settings.is_configured()` (so the setup wizard doesn't block the
landing page). Nothing in these flows actually calls that endpoint: the
starter pane's directory overview is code-generated (no LLM call), and
Query mode only opens an MCP session and waits for input.

## Run (agent path)

```bash
uv run --with playwright python .claude/skills/run-telcontar/driver.py
```

Wrap in tmux for interactive use — poll for the `driver>` prompt/output
markers between `send-keys` and `capture-pane` rather than sleeping a
fixed amount:

```bash
tmux new-session -d -s app -x 200 -y 50
tmux send-keys -t app 'uv run --with playwright python .claude/skills/run-telcontar/driver.py' Enter
timeout 30 bash -c 'until tmux capture-pane -t app -p | grep -q "driver>"; do sleep 0.3; done'

tmux send-keys -t app 'launch /tmp/telcontar-sample' Enter
timeout 30 bash -c 'until tmux capture-pane -t app -p | grep -qE "launched\.|TIMEOUT|exited"; do sleep 0.3; done'

tmux send-keys -t app 'ss 01-run-starter' Enter
timeout 10 bash -c 'until tmux capture-pane -t app -p | tail -3 | grep -q "screenshot:"; do sleep 0.3; done'
tmux capture-pane -t app -p
```

`launch [target-dir]` accepts an optional directory to pre-select (skips
the landing page's directory picker and jumps straight to the run's
starter pane, same as the real `--target` CLI flag). Without an argument
it opens on the landing page for interactive directory picking instead.

Screenshots land in `/tmp/shots/` (override: `SCREENSHOT_DIR`). The app
subprocess's own stdout/stderr (uvicorn/NiceGUI logs, and any traceback
from a crashed MCP server subprocess) go to `/tmp/telcontar_app.log`
(override: `TELCONTAR_APP_LOG`) — check it first whenever `launch` times
out or an in-app error message appears.

### Commands

| command | what it does |
|---|---|
| `launch [target-dir]` | boot the app (subprocess) + open headless Chromium |
| `nav <path>` | navigate to an app-relative path, e.g. `nav /settings` |
| `ss [name]` | screenshot → `/tmp/shots/<name>.png` |
| `click <css-selector>` | click first matching element |
| `click-text <exact text>` | click element with this exact visible text |
| `fill <css-selector> <text>` | fill an input |
| `text [css-selector]` | print innerText (default: whole body) |
| `eval <js-expression>` | evaluate in the page, print JSON |
| `wait <css-selector>` | wait up to 10s for an element |
| `quit` | close the browser and stop the app subprocess |

A representative flow (verified end-to-end this session):

```
launch /tmp/telcontar-sample
ss 01-run-starter              # starter pane: code-generated dir overview, no LLM call
click-text Settings
ss 02-settings                 # /settings — LLM endpoint / profile / approval-mode form
nav /corpus/<run-id>           # run-id is printed by `launch`
text                           # "No analyzed documents yet — run Organize first"
```

Query mode (exercises the real `server.main` MCP subprocess) needs a
target with an existing `.organizer/` dir — an empty one is enough, since
`server/registry.py`'s `load()` treats a missing/empty registry as empty:

```bash
mkdir -p /tmp/telcontar-sample/.organizer
```

then, once launched on that target: `click-text Query` → the page shows
"Initialising…" while the MCP server subprocess spawns and the client
completes its handshake, then "Ready — ask a question." Typing into the
box and clicking `ASK` would trigger a real LLM call against the stub
endpoint and fail — not exercised here, since the stub URL isn't real.

## Run (human path)

```bash
uv run telcontar --browser --target /tmp/telcontar-sample
```

Opens in the system browser (native `pywebview` mode is Windows-only and
falls back automatically elsewhere). Useless in this headless container;
`Ctrl-C` to stop. `telcontar-server` (the MCP server) is never launched by
hand in normal use — the host spawns it as a subprocess per run.

## Test

```bash
uv run --group test pytest -q --tb=short
```

## Gotchas

- **The auth token + port are never printed.** `host/web/main.py`'s
  `run_web()` generates a random per-launch token (`security.new_token()`)
  and picks an ephemeral port, then only passes them into
  `security.configure(token, port)` and pywebview's native window args —
  neither reaches stdout. The driver monkeypatches
  `host.web.security.configure` (and the `security` reference `main.py`
  already imported) to snapshot both to a JSON file before calling through,
  which is the only way to get a URL an external Playwright process can
  hit. Every request needs `?token=<token>` on first navigation (it then
  sets an auth cookie); a request without it gets a 403 from
  `_AuthMiddleware`.
- **NiceGUI's uppercase nav labels are CSS-only.** `click-text Settings`
  works; `click-text SETTINGS` (matching what a screenshot visually shows)
  times out — the actual DOM text is title-case, `text-transform:
  uppercase` just renders it that way.
- **`.mark("...")` markers aren't DOM attributes.** They're an internal
  NiceGUI `ElementFilter` construct for that project's own Python-side
  tests, not something a `[data-*]` CSS selector can reach from outside.
  Target elements by visible text (`click-text`) or Quasar's own rendered
  classes instead.
- **The Settings page shows blank fields even with a "configured" app.**
  It reads from `~/.telcontar/config.env` via `read_user_config()`, not
  from whatever satisfied `is_configured()` — the driver's env-var stub
  (`LLM_BASE_URL`/`LLM_API_KEY`) unblocks the setup-wizard redirect but
  never appears in the form. Expected, not a bug.
- **Each `launch` needs a fresh port/token.** The app binds an ephemeral
  port (`_pick_port()`) per process, so nothing needs killing between runs
  the way a fixed-port dev server would — but also don't reuse a stale
  `/tmp/telcontar_conn.json` from a previous `launch`; the driver deletes
  it at the start of every `launch` for this reason.

## Troubleshooting

- **`launch` times out, and `/tmp/telcontar_app.log` shows
  `ModuleNotFoundError: No module named 'magika'`**: `uv sync` left
  `magika`'s package directory missing (only its `.dist-info` landed) —
  `server/extract.py` imports it transitively via `markitdown`, so the
  MCP server subprocess (`server.main`) dies the moment Organize/Query
  starts it, even though the NiceGUI process itself came up fine. Fix:
  `uv sync --reinstall-package magika`.
- **`launch` times out with no `/tmp/telcontar_app.log` output at all**:
  something else is holding the ephemeral port `_pick_port()` picked (rare
  — it's a fresh `bind(("127.0.0.1", 0))` each time) or the subprocess
  never started; check `_proc.poll()`'s exit code the driver printed.
- **An in-app "Query error: McpError: Connection closed"**: the MCP server
  subprocess crashed on import — same as the magika case above; check
  `/tmp/telcontar_app.log` for the underlying traceback rather than
  trusting the generic UI error text.
