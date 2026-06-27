# Contributing

## Development setup

```bash
git clone https://github.com/rreboulleau/telcontar.git
cd telcontar

# Install runtime + dev dependencies
uv sync --group dev

# Install test dependencies separately
uv sync --group test

# Copy and fill in the config
cp .env.example .env
```

---

## Toolchain

| Tool | Purpose | Command |
|---|---|---|
| **ruff** | Lint + format | `uv run ruff check .` / `uv run ruff format .` |
| **mypy** | Type checking | `uv run mypy host server config` |
| **pytest** | Tests | `uv run --group test pytest` |
| **mkdocs** | Docs (local preview) | `uv run --group docs mkdocs serve` |

Run all checks before opening a PR:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy host server config && uv run --group test pytest -q
```

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
| `repo-manager` agent | Handles all git operations — delegate commits and branch ops here |
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

**Convention:** Each test module maps to one source module. When adding a new module, add `tests/test_<module>.py` and update the `test-select` scope table in `pyproject.toml`.

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
