---
name: dev-pipeline
description: Orchestrate a full development sprint from ROADMAP.md. Reads unchecked items, implements them in order on a feat/ sub-branch of develop, using feature-forecast for background prefetch, /test-select before each commit, and repo-manager for all git work. Runs /auto-improve at the end. Use when asked to run the sprint, work through the roadmap, or implement all pending items.
---

# /dev-pipeline — sprint orchestrator

## What this does

Reads `ROADMAP.md`, finds all unchecked items in the active milestone, and drives each one to completion in order. Every item goes through:

1. **Forecast** (`feature-forecast` subagent, Haiku) — reads the codebase and produces a Forecast Brief for the item.
2. **Implementation** — main session implements the item using the brief.
3. **Tests** (`/test-select` skill) — gates the commit; red run blocks advance.
4. **Commit** (`repo-manager` subagent, Haiku) — stages and commits on the feature branch.

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

## Step 7 — Squash-merge into develop

When the last item is committed:

```
Agent({
  subagent_type: "repo-manager",
  description: "Squash-merge feat/[milestone-slug] into develop",
  prompt: "Squash-merge branch feat/[milestone-slug] into develop:\n  git checkout develop\n  git merge --squash feat/[milestone-slug]\n  git commit -m '[milestone]: complete sprint'\nThen delete the local feature branch."
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
