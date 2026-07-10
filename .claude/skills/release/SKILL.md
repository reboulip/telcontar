---
name: release
description: Cut a release — bump the version in pyproject.toml, open a develop -> main PR, wait for CI, merge (no squash), tag main, and verify the GitHub Release publishes. Use when asked to cut/ship a release, bump and release, or tag and release.
---

# /release — release pipeline

## What this does

Drives a full release from `develop` to a published GitHub Release, per this
project's `## Branch Model` / `## Releases` rules in `CLAUDE.md`: `main` only
moves via a no-squash PR from `develop`, and tags/releases are always cut from
`main` **after** that PR merges — never directly from `develop`.

All git/gh work is delegated to `repo-manager`. The main session never runs
`git commit`, `git push`, `git merge`, or `gh pr merge` directly.

---

## Step 1 — Determine the version bump

1. Read the current `version` from `pyproject.toml`.
2. If the user didn't specify the bump type, ask via `AskUserQuestion`
   (e.g. next beta `0.1.0b2` → `0.1.0b3`, promote current beta to stable
   `0.1.0b3` → `0.1.0`, next patch, next minor). Use PEP 440 formatting.
3. Confirm working tree is clean and the current branch is `develop`,
   up to date with `origin/develop` (`git fetch`, compare).

---

## Step 2 — Bump, push, open the PR

Delegate to `repo-manager` in one call:

1. Edit `pyproject.toml`'s `version` field.
2. Commit: `chore: bump version to <X.Y.Z> (beta)` (drop "(beta)" for a
   stable release) — matching the precedent style of prior bump commits.
3. Push `develop` to `origin`.
4. Open a PR `develop` → `main` via `gh pr create --base main --head develop`.
   Title: `develop → main: sync for v<X.Y.Z> release`. Body: a concise
   summary of `git log --oneline main..develop` grouped by theme, not a raw
   commit dump.
5. Report the PR number/URL.

Do not merge yet.

---

## Step 3 — Wait for CI

Poll the PR's checks (`gh pr checks <N> --json name,bucket`) via `Monitor`
until every check has left `pending`. Do not manually `sleep`-poll in the
main session — arm a `Monitor` and continue only once notified.

**If a check fails, investigate the root cause — do not just retry or widen
a timeout.** Pull the failing job's log (`gh run view <run-id> --log-failed`),
reproduce locally if possible, and fix it for real. A test that's flaky
under CI load specifically (passes locally, fails intermittently in CI) is a
real bug in the test — chasing it with a bigger `sleep()` treats the symptom;
find what invariant the fixed delay was actually standing in for and wait on
that instead (see `tests/test_app_ui.py`'s own "gotchas" docstring for a
worked example — a Textual `Button` click-debounce that silently swallows a
too-fast re-click). Push the fix (delegate commit+push to `repo-manager`,
same PR/branch — do not open a new PR) and re-arm the CI wait.

Do not proceed to Step 4 until every check is green.

---

## Step 4 — Merge, tag, verify

Delegate to `repo-manager` in one call:

1. Merge the PR **without squashing**: `gh pr merge <N> --merge --delete-branch=false`
   (keep `develop` — it's the permanent integration branch, not a throwaway
   feature branch).
2. `git checkout main && git pull origin main`.
3. Confirm `pyproject.toml` on `main` shows the new version.
4. `git tag -a v<X.Y.Z> -m "v<X.Y.Z>"`.
5. `git push origin v<X.Y.Z>`.
6. `git checkout develop` (return to the branch the session started on).

Then, in the main session (read-only, no delegation needed):

1. Confirm the tag commit matches `origin/main` HEAD
   (`git rev-list -n1 v<X.Y.Z>` == `git rev-list -n1 origin/main`).
2. Watch `.github/workflows/release.yml` run to completion
   (`gh run list --workflow=release.yml --limit 1`).
3. Confirm the release published: `gh release view v<X.Y.Z>` — check
   `published:` is set and the wheel/sdist assets are attached.

---

## Report

- Version released and the GitHub Release URL.
- Whether any CI failure was hit mid-pipeline and what the actual fix was
  (not just "retried").
- Confirm `develop` still exists and the session is back on it.
