---
name: dev-pipeline
description: Orchestrate a full development sprint from ROADMAP.md. Reads unchecked items, implements them in order on a feat/ sub-branch of develop, using feature-forecast for background prefetch, /test-select before each commit, and repo-manager for all git work. Runs /auto-improve at the end. Use when asked to run the sprint, work through the roadmap, or implement all pending items.
---

# /dev-pipeline — sprint orchestrator

## What this does

Reads `ROADMAP.md`, finds all unchecked items in the active milestone, and drives each one to completion in order. Every item goes through:

1. **Forecast** (`feature-forecast` subagent, Haiku) — reads the codebase and produces a Forecast Brief for the item.
2. **Implementation** — main session implements the item using the brief.
3. **Documentation** (`doc-keeper` subagent, Sonnet) — syncs README/docs to the change before it is committed.
4. **Tests** (`/test-select` skill) — gates the commit; red run blocks advance.
5. **Commit** (`repo-manager` subagent, Haiku) — stages and commits the code **and** doc changes on the feature branch.

Forecast for item N+1 runs **in the background** while item N is being implemented, so the brief is ready with zero wait time.

---

## Step 0 — Design clarification

Before any implementation, scan the unchecked items in the active milestone and identify non-obvious design decisions:
- Output formats (file contents, JSON shape, Markdown structure)
- Tool signatures (parameters, return types, who composes content — LLM or code)
- LLM integration patterns (which side generates prose, when to call which tool)
- Dependency ordering (does item N require item M to exist first)

If **any** such decisions are ambiguous, call `AskUserQuestion` with focused, concrete options before proceeding to Step 1. Do not start forecasting or implementing until design is settled.

---

## Step 0.5 — Branch setup

1. Ensure `develop` is up to date:
   ```bash
   git checkout develop && git pull origin develop
   ```
2. Create a feature branch for this sprint:
   ```bash
   git checkout -b feat/<milestone-slug>
   ```
   Example: `feat/v0.1.0-skeleton` for milestone `v0.1.0 — Skeleton`.
3. All implementation commits go on this branch. The branch squash-merges into `develop` when the sprint is complete.

---

## Step 1 — Find the active milestone

Read `ROADMAP.md`. The active milestone is the **first** `## vX.X.X` section containing at least one unchecked item (`- [ ]`). Extract:
- The milestone label (e.g. `v0.1.0`).
- The ordered list of unchecked items: label (A1, A2, …), title, full item text.

**Deferred items are out of scope by default.** Any unchecked item whose text is
tagged `[deferred]` or `[deferred/hard]` is NOT part of the sprint scope unless
the user explicitly asks for it. In Step 0, list the in-scope (non-deferred) items
and surface the deferred ones separately via `AskUserQuestion`, letting the user
opt them in. Never silently implement a deferred item.

If no unchecked items remain in any section, report sprint complete and skip to Step 6.

---

## Step 2 — Prepare item[0] (foreground)

Spawn `feature-forecast` and wait for the result:

```
Agent({
  subagent_type: "feature-forecast",
  description: "Forecast brief for [milestone] [label]",
  prompt: "Milestone: [milestone label]\nItem: [label] — [title]\n\n[full item text verbatim from ROADMAP.md]"
})
```

---

## Step 3 — Pre-fetch item[1] in the background

Immediately after receiving item[0]'s brief, if item[1] exists, start its forecast without waiting:

```
Agent({
  subagent_type: "feature-forecast",
  run_in_background: true,
  description: "Forecast brief for [milestone] [next label]",
  prompt: "Milestone: [milestone label]\nItem: [next label] — [next title]\n\n[full next item text verbatim from ROADMAP.md]"
})
```

---

## Step 4 — Implement item[0]

The Forecast Brief for item[0] is in context. Implement the item now:
- Follow the "Suggested implementation order" from the brief.
- Edit only files under `host/`, `server/`, `config/`, `tests/`. Use direct Edit/Write tools.
- Check off the item in `ROADMAP.md` (`- [ ]` → `- [x]`).

---

## Step 4.5 — Update documentation

Once the item is implemented (before testing/commit), spawn `doc-keeper` so the docs land in the **same** commit as the code. Wait for its report.

```
Agent({
  subagent_type: "doc-keeper",
  description: "Update docs for [milestone] [label]",
  prompt: "Item: [label] — [title]\n\nChanged files:\n[list of files edited/created in Step 4]\n\nSummary of change:\n[1-2 sentences: what the implementation did — new/changed MCP tools, signatures, config keys, behaviour]"
})
```

Add any docs the agent reports as updated/created to the file list passed to `repo-manager` in Step 5. If it reports "None — internal/test-only," proceed with no doc changes.

---

## Step 5 — Test and commit

**Scope table first:**
If any new `tests/test_*.py` files were created for this item, update the scope table in `.claude/skills/test-select/SKILL.md` **before** calling test-select. Add the new file to the correct row(s) and update any catch-all rows (e.g. `server/tools.py only`). Do not defer this to auto-improve.

**Test:**
```
Skill("test-select")
```
If the verdict is RED, fix the failures before continuing. Do not advance until green.

**Commit via repo-manager:**
```
Agent({
  subagent_type: "repo-manager",
  description: "Commit [milestone] [label]",
  prompt: "Stage and commit the following files on branch feat/[milestone-slug]:\n[list of changed files]\n\nCommit message: [type]: [item title]\n\nBody (optional): [1-2 sentence summary of what changed and why]"
})
```

---

## Step 6 — Advance to the next item

After item[0] is committed:
1. Wait for the background notification confirming item[1]'s brief is ready (if not already received).
2. If item[2] exists and hasn't been pre-fetched yet, start it in the background now (same pattern as Step 3).
3. Brief for item[1] is in context. Return to Step 4 for item[1].
4. Repeat until all items are committed.

---

## Step 7 — Merge into develop

When the last item is committed:

**7a — Format gate (do this BEFORE merging).** The `develop`→`main` PR has a
`ruff format --check .` CI gate, so any format drift anywhere in the repo (even in
files this sprint did not touch) will block that PR. Catch it now while on the
feature branch:

```bash
uv run ruff format --check .
```

If it reports files that "would be reformatted," run `uv run ruff format .` to fix
them, then re-run `--check` to confirm clean. Commit the formatting fix as its own
commit via `repo-manager` (message: `chore: ruff format`) so it merges with the
sprint. Then proceed to the divergence check.

**7b — Divergence check (do this BEFORE merging).** A squash-merge assumes the
branch contains only this sprint's own commits. Verify that first:

```
Agent({
  subagent_type: "repo-manager",
  description: "Report branch divergence before merge",
  prompt: "Read-only: report `git log --oneline develop..feat/[milestone-slug]` and `git log --oneline -1 develop`. Do not merge or change anything."
})
```

Compare the listed commits against the sprint's own implementation commits
(one per ROADMAP item). If the branch contains **any commit you did not author
this sprint** — e.g. a commit from a separate process landed on the branch base,
or develop advanced underneath you — **do not blind-squash.** Surface the
divergence and use `AskUserQuestion` to let the user choose the merge strategy
(preserve all commits via fast-forward / non-squash, vs. squash the sprint
commits while preserving the foreign commit, vs. squash everything). Only when
the branch is exactly the sprint's own commits should you proceed straight to a
squash.

**7c — Merge.** Per the chosen strategy (squash is the default for a clean
sprint-only branch):

```
Agent({
  subagent_type: "repo-manager",
  description: "Merge feat/[milestone-slug] into develop",
  prompt: "Merge branch feat/[milestone-slug] into develop using <chosen strategy>:\n  git checkout develop\n  # squash:   git merge --squash feat/[milestone-slug] && git commit -m '[milestone]: complete sprint'\n  # ff-only:  git merge --ff-only feat/[milestone-slug]\nThen delete the local feature branch."
})
```

---

## Step 8 — Auto-improve

```
Skill("auto-improve")
```

---

## Step 9 — Sprint complete

Report:
- Milestone completed.
- All items done (label + title for each).
- Next steps: push `develop` when ready, then open a PR `develop` → `main` (no squash).

---

## Rules

- **Never start item N+1 until item N is committed and green.**
- **Never commit on a red test run** — fix first.
- If implementation fails after 3 fix attempts, **pause the sprint**, surface the error to the user, and wait for guidance.
- The `ROADMAP.md` checkbox update is part of each implementation step (not a separate commit).
- If the user interrupts the sprint, resume by re-reading `ROADMAP.md` from Step 1 to discover remaining unchecked items.
- All git work (staging, committing, branching, merging) is delegated to `repo-manager`. Never run git commands directly in the main session.
