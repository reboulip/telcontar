# Contributing

## Development setup

```bash
git clone https://github.com/reboulip/telcontar.git
cd telcontar

# Install runtime + dev + test dependencies in one call — separate `uv sync`
# calls each resync to exactly the groups named on that call, so running
# `--group dev` then `--group test` separately uninstalls the dev-only tools.
uv sync --group dev --group test

# Activate the pre-commit hook (one-time per clone)
uv run pre-commit install
```

For dev, configure your LLM endpoint either by running `telcontar` once (setup wizard) or by placing a project-local `.env` file with `LLM_BASE_URL` and `LLM_API_KEY`. See [Configuration](../getting-started/configuration.md) for the full reference.

---

## Toolchain

| Tool | Purpose | Command |
|---|---|---|
| **ruff** | Lint + format | `uv run ruff check .` / `uv run ruff format .` |
| **mypy** | Type checking (CI gate) | `uv run mypy host server config` |
| **ty** | Type checking (fast local check) | `uv run ty check host server config` |
| **pytest** | Tests | `uv run --group test pytest` |
| **pre-commit** | Runs the above on `git commit` | `uv run pre-commit run --all-files` |
| **mkdocs** | Docs (local preview) | `uv run --group docs mkdocs serve` |

Run all checks before opening a PR:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy host server config && uv run ty check host server config && uv run --group test pytest -q
```

### Pre-commit hooks

`.pre-commit-config.yaml` wires the table above (minus pytest — the full
suite is too slow for a per-commit hook) into `git commit`, plus routine
hygiene checks (trailing whitespace, end-of-file newline, merge-conflict
markers, large files, TOML/YAML syntax). `ruff format` and `ruff check --fix`
auto-fix in place; a hook that modifies files reports as failed on that run
so you can review the diff, re-stage, and commit again. mypy and ty run
project-wide (`pass_filenames: false`) rather than per-changed-file, since
type checking needs whole-project context. All local (Python) hooks run
through `uv run`, so they always use the exact tool versions pinned in
`uv.lock` — never a separately-managed version. Vendored assets
(`host/web/assets/`) are excluded from the hygiene hooks so they stay
byte-identical to upstream.

The full test suite is too slow for a per-commit hook, so it isn't wired
into pre-commit at all. It still gates every commit through the `/test-select`
workflow step (see Workflow automation below) and every PR through the CI
gate.

---

## Branch model

| Branch | Role | Merge strategy |
|---|---|---|
| `main` | Protected stable releases | PR only, **no squash** |
| `develop` | Integration branch | Direct push allowed |
| `feat/<name>` | One feature / one ROADMAP item | Sub-branch of `develop`; squash-merge into `develop` |
| `fix/<name>` | Bug fix on develop | Same as `feat/` |
| `hotfix/<name>` | Urgent fix on `main` | Branch from `main`; PR back; then merge `main` → `develop` |

### Merge rules

- **Feature/fix → `develop`:** local squash-merge (`git merge --squash`), one commit per feature. No PR required.
- **`develop` → `main`:** PR only, **no squash** (full `develop` history preserved on `main`).
- **Hotfix → `main`:** PR only, no squash. Immediately after: merge `main` into `develop`.
- **Never push directly to `main`.**

### Commit message convention

```
<type>: <summary in imperative mood, ≤72 chars>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`.

Example: `feat: add find_modified_documents to registry`

---

## Workflow automation

The project ships several Claude Code skills and agents for development workflow:

| Tool | Purpose |
|---|---|
| `/dev-pipeline` | Full sprint orchestrator — reads ROADMAP.md, implements items on `feat/` branches |
| `/test-select` | Runs minimal pytest scope for the current branch's changes — call before every commit |
| `doc-keeper` agent | Updates docs at the end of each feature step — runs before the commit |
| `feature-forecast` agent | Background prefetch — pre-reads codebase for the next ROADMAP item |

---

## Writing tests

Tests live in `tests/`. The suite uses `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`).

```bash
uv run --group test pytest -q                  # full suite
uv run --group test pytest tests/test_plan.py  # single module
```

**Patterns in use:**

- Server tool tests (`test_tools_*.py`) use `tmp_path` (pytest fixture) for isolation — no real `.env` needed
- Host/agent tests (`test_host.py`) mock the MCP `ClientSession` and `AsyncOpenAI` client
- Registry/journal tests exercise the in-memory classes and file persistence directly
- `test_e2e_toolchain.py` is a cross-module end-to-end integration suite: no LLM, it drives the real server tool implementations against a seeded fixture directory through the full lifecycle (`list_dir` → `read_file`/`extract_text` → `compute_checksum` → `record_document` → `propose_*` → `review_plan` → `approve_plan` → `execute_plan` with registry reconcile → `undo_last` → `write_index`/`write_summary`), asserting real file-I/O effects. This is an intentional exception to the one-module-per-test convention below.
- `test_web_ui.py` drives the NiceGUI web UI through a real headless page render, using NiceGUI's `user` test fixture (`nicegui.testing.user_plugin`, enabled via `addopts` in `pyproject.toml`'s `[tool.pytest.ini_options]`, plus `main_file = "host/web/main.py"` marking that module as NiceGUI's testing entry point). This is a narrower, non-Selenium fixture — no extra dependency, since `nicegui.testing` ships inside `nicegui`, already a runtime dependency. Everything the web UI's underlying logic needs to prove (approval/cost future resolution, ledger threading, step open/close, etc.) stays covered NiceGUI-free elsewhere (`test_web_session.py`, `test_host_format.py`, `test_host_paths.py`); this file only asserts that a page renders what the session data says and that a click/interaction wires through to the right session call. The module docstring documents five hard-won gotchas (runpy double-module patching — test seams must live in `host/web/session.py`, never `host/web/main.py`, since the `user` fixture runpy-executes the latter as a second module object; `sys.modules['__main__']` eviction between tests; dialog visibility-after-close; the render-poll-interval-vs-assertion-retry-budget race, addressed by `host.web.session.REFRESH_INTERVAL`; and a `.mark("...")` naming convention) — read it before adding more tests here.

**Convention:** Each test module maps to one source module. When adding a new module, add `tests/test_<module>.py` — `/test-select` picks it up automatically (full-suite-by-default below ~120s, falling back to same-named-file matching above that; no hand-maintained table to update).

---

## ROADMAP conventions

Items in `ROADMAP.md` must use the checkbox + label format so `/dev-pipeline` detects them:

```markdown
- [ ] X1 · Short description of the item
```

If an item depends on a later-listed item, annotate it:

```markdown
- [ ] C3 · execute_plan — ... (requires: C5)
```

`/dev-pipeline` handles prerequisite inversion automatically.

---

## Documentation

Docs are built with MkDocs Material and deployed to GitHub Pages on every push to `main`.

```bash
# Local preview
uv run --group docs mkdocs serve

# Build (strict — fails on warnings)
uv run --group docs mkdocs build --strict
```

The `doc-keeper` agent updates `docs/**` and `README.md` at the end of each feature implementation step. Do not manually update docs for feature changes — run `/dev-pipeline` and let `doc-keeper` handle it.

---

## Safety constraints for contributors

These are non-negotiable and enforced in code:

1. **No delete tool.** The only removal path is quarantine. Do not add a `delete_file` tool.
2. **No overwrite.** `check_no_overwrite` must be called before any rename or move. Do not bypass it.
3. **Journal every destructive op.** `execute_plan` appends to the undo journal before returning. Maintain this invariant if you extend `execute_plan`.
4. **Plan state machine.** Only transition plans via `Plan.transition()`. Do not set `plan.state` directly.
