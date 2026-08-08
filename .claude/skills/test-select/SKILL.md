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

Choose the diff base by where the work lives (the branch model is `feat/*` and
`fix/*` sub-branch off `develop`, and `develop` integrates toward `main`):

```bash
# On a feat/* or fix/* branch (sub-branch of develop): base on develop
git diff --name-only $(git merge-base HEAD develop)..HEAD

# On develop directly: base on main (use origin/main if available)
git diff --name-only $(git merge-base HEAD origin/main)..HEAD
```

Also fold in uncommitted work so a pre-commit run sees staged/unstaged changes:

```bash
git status --porcelain
```

> **Why the base matters:** `main` is usually far behind `develop`, so basing a
> feat-branch diff on `main` returns the *entire* `develop` history and defeats
> scoped selection. Always base a feat/fix branch on `develop`.

## Step 2 — Cheap-suite escape hatch

The full suite is currently **~91s for 868 tests** (measured 2026-08-08; was 63s/691 tests
two days earlier — it grows as the roadmap adds tests, so re-measure occasionally rather
than trusting this number indefinitely). **If the full suite would run in well under
~120s, skip scope selection entirely and just run it:**

```bash
uv run --group test pytest -q
```

This replaces hand-maintained per-file scope selection — the token/time cost of picking a
scoped subset stopped being worth it once the full run was already this cheap, and a
hand-maintained table is a recurring source of missed updates. Only fall back to a scoped
run (below) if the suite has grown past the ~120s mark since it was last measured here —
re-time it (`uv run --group test pytest -q`, note the total) and update this section's
number if so.

### Scoped fallback (only if the full suite has grown past ~120s)

Base the diff on `develop` (feat/fix branches) or `main` (from `develop`), per Step 1, and
run only the test files touching the changed modules — check `tests/` for a same-named or
clearly-related test file per changed `host/`/`server/` module. When no test file obviously
corresponds to a changed module, or the change is cross-cutting (spans `server/` + `host/`,
touches `config/settings.py`, or is a broad refactor), run the full suite instead of
guessing.

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
