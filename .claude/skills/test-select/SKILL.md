---
name: test-select
description: Select and run the minimal pytest scope for the current branch's changes, then report a pass/fail verdict. Call before any commit. For narrow changes (single module, single MCP tool) avoids the full suite while still gating on correctness. Also use when asked to run tests, check test coverage, or verify a change before committing.
tools:
  - Bash
  - PowerShell
  - Read
  - Glob
  - Grep
---

# /test-select — scoped test runner for telcontar

## Step 1 — Identify changed files

```bash
git diff --name-only $(git merge-base HEAD main)..HEAD
```

This captures all changes on the current branch (staged or committed) relative to `main`. If on `develop` directly, use `git diff --name-only origin/main..HEAD`.

## Step 2 — Select test scope

Apply these rules in order (first match wins):

| Changed file(s) | Run |
|-----------------|-----|
| `config/settings.py` only | Full suite — settings affect everything |
| `server/tools.py` only | `tests/test_tools_readonly.py tests/test_tools_write.py tests/test_tools_propose.py tests/test_execute_plan.py tests/test_undo_last.py tests/test_review_plan.py` |
| `server/guards.py` only | `tests/test_guards.py` |
| `server/extract.py` only | `tests/test_tools_readonly.py` (extract_text delegates here) |
| `server/plan.py` only | `tests/test_plan.py` |
| `server/journal.py` only | `tests/test_journal.py` |
| `server/tools.py` propose_* only | `tests/test_tools_propose.py` |
| `server/tools.py` execute_plan only | `tests/test_execute_plan.py` |
| `server/tools.py` undo_last only | `tests/test_undo_last.py` |
| `server/tools.py` review_plan only | `tests/test_review_plan.py` |
| Multiple files in `server/` | Full `tests/` suite |
| `host/agent.py` only | `tests/test_host.py` |
| Any file in `host/` | `tests/test_host.py` |
| Changes span `server/` + `host/` | Full suite |
| Cross-cutting refactor or interface change | Full suite |
| When in doubt | Full suite |

> **Note:** Update this table whenever new test files are added. When a test file doesn't exist yet for a changed module, run the full suite.

State explicitly which files you selected and why before running.

## Step 3 — Run tests

```bash
uv run --group test pytest <selected files or dirs> -v
```

For the full suite:
```bash
uv run --group test pytest -v
```

If test dependencies aren't installed yet:
```bash
uv sync --group test
```

## Step 4 — Report verdict

After the run:
- List which test files were run and why
- Pass count, fail count, skip count
- For any failure: module, test name, and full traceback
- **Verdict:** GREEN (all passed) or RED (any failed)

## Rules

- **Green → proceed to commit.** Red → block commit, surface all failures. Do not commit on red.
- A coverage-gate failure on a partial run does not escalate to the full suite automatically.
- New tests can be added freely. Modifying or deleting existing tests requires presenting the change and waiting for user approval.
- If `tests/` doesn't exist yet, report "No tests to run — test suite not yet created" and proceed to commit.
