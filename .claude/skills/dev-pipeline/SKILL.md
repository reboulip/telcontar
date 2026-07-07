---
name: dev-pipeline
description: Orchestrate a full development sprint from ROADMAP.md. Reads unchecked items, implements them in order on a feat/ sub-branch of develop, using feature-forecast for background prefetch, /test-select before each commit, and repo-manager for all git work. Runs /auto-improve at the end. Use when asked to run the sprint, work through the roadmap, or implement all pending items.
---

# /dev-pipeline — sprint orchestrator

## What this does

Reads `ROADMAP.md`, finds all unchecked items in the active milestone, and drives each one to completion in order. Every item goes through:

1. **Forecast** (`feature-forecast` subagent, Haiku) — reads the codebase and produces a Forecast Brief for the item, persisted to a temp file.
2. **Implementation** — main session implements the item using the brief read from that file.
3. **Documentation** (`doc-keeper` subagent, Sonnet) — syncs README/docs to the change before it is committed.
4. **Tests** (`/test-select` skill) — gates the commit; red run blocks advance.
5. **Commit** (`repo-manager` subagent, Haiku) — stages and commits the code **and** doc changes on the feature branch.

Forecasts run **up to 2 items ahead** of whichever item is currently being implemented, so
the brief for the next item (and the one after it) is ready with zero wait time. Each
brief is written to `.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md` as soon as its
forecast completes — implementation reads from that file rather than from conversation
context, so briefs survive context compaction and sprint interruption/resume.

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
   Example: `feat/phase-1-skeleton` for milestone `Phase 1 — Skeleton`.
3. All implementation commits go on this branch. The branch merges into `develop` when the sprint is complete (fast-forward by default for a clean sprint-only branch; see Step 7).

---

## Step 1 — Find the active milestone

**(Re-)read `ROADMAP.md` now — *after* branch setup (Step 0.5) — as the source of
truth. Do NOT rely on any earlier read of the roadmap (e.g. one taken during Step 0):
a separate process may commit roadmap changes to `develop` between an initial read and
the branch being cut, so a stale read can silently drop whole milestone items.** The
active milestone is the **first** `## Phase N` section containing at least one unchecked
item (`- [ ]`). Extract:
- The milestone label (e.g. `Phase 1`).
- The ordered list of unchecked items: label (A1, A2, …), title, full item text.

**Deferred items are out of scope by default.** Any unchecked item whose text is
tagged `[deferred]` or `[deferred/hard]` is NOT part of the sprint scope unless
the user explicitly asks for it. In Step 0, list the in-scope (non-deferred) items
and surface the deferred ones separately via `AskUserQuestion`, letting the user
opt them in. Never silently implement a deferred item.

If no unchecked items remain in any section, report sprint complete and skip to Step 6.

---

## Step 1.5 — Forecast persistence setup

Forecast briefs are written to disk instead of being kept only in conversation context,
so they survive compaction and sprint interruption/resume:

```
.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md
```

1. Ensure this directory exists (create it if missing).
2. If any `<label>.md` files already exist there (a resumed sprint), treat those items as
   already forecast — don't re-fire `feature-forecast` for them in Step 2/6 unless the
   staleness check in Step 6 says otherwise.
3. **Standing rule for the rest of the sprint:** whenever a `feature-forecast` agent
   (foreground or background) completes, immediately write its full Forecast Brief output
   verbatim to the matching `<label>.md`, before doing anything else, then resume whatever
   step you were on. This applies at any point in the pipeline, not just right after the
   call that triggered it — background completions can land while you're mid-way through
   an unrelated step.

`.claude/tmp/` is gitignored — these files are sprint-scratch state, never committed.

---

## Step 2 — Prepare item[0] and prefetch the lookahead window

1. If item[0]'s brief file doesn't already exist, spawn `feature-forecast` and wait for
   the result:

   ```
   Agent({
     subagent_type: "feature-forecast",
     description: "Forecast brief for [milestone] [label]",
     prompt: "Milestone: [milestone label]\nItem: [label] — [title]\n\n[full item text verbatim from ROADMAP.md]"
   })
   ```

   Persist it per the Step 1.5 standing rule. If the file already existed, just read it.

2. Top up the lookahead window to 2 items ahead: for each of item[1] and item[2]
   (whichever exist) that doesn't already have a brief file on disk, fire
   `feature-forecast` in the background without waiting:

   ```
   Agent({
     subagent_type: "feature-forecast",
     run_in_background: true,
     description: "Forecast brief for [milestone] [label]",
     prompt: "Milestone: [milestone label]\nItem: [label] — [title]\n\n[full item text verbatim from ROADMAP.md]"
   })
   ```

At this point item[0]'s brief is in hand, and item[1] and item[2] (whichever exist) are
already being forecast in the background.

---

## Step 4 — Implement the current item

Read the Forecast Brief for the current item from
`.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md`. If it isn't there yet (forecast
still in flight), wait for the completion notification — the Step 1.5 standing rule
writes it to that path as soon as it arrives. Implement the item now:
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

After item[K] is committed:

1. **Staleness check (judgment call, not automatic).** Consider what item[K]'s
   implementation just changed. If it invalidates something the already-fetched brief for
   item[K+1] or item[K+2] relied on — e.g. it created a helper the brief listed as a
   "missing prerequisite," renamed/moved a file the brief references, or changed a
   function signature the brief quotes — re-fire `feature-forecast` for that specific item
   now (foreground if it's item[K+1] and it's needed right away, background otherwise) to
   overwrite its `<label>.md`. Skip this if nothing item[K] did touches later briefs — this
   is the common case and costs nothing.
2. Top up the lookahead window: if item[K+3] exists and doesn't have a brief file yet,
   fire its forecast in the background now (same pattern as Step 2.2), keeping the window
   at 2 items ahead of whatever comes next.
3. Read item[K+1]'s brief from `.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md`. In
   the common case it's already there — that prefetch started two commits ago. If it
   isn't there yet, wait for the completion notification.
4. Return to Step 4 for item[K+1].
5. Repeat until all items are committed.

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
commits while preserving the foreign commit, vs. squash everything). When the
branch is exactly the sprint's own commits **and** is a clean fast-forward
(`git log --oneline feat/[milestone-slug]..develop` is empty), proceed straight
to a **fast-forward** merge (`git merge --ff-only`) — this preserves the
per-feature commits, matching CLAUDE.md's "one commit per feature" rule and
keeping a readable history on `develop` (which flows verbatim to `main`, since
`develop`→`main` is a no-squash PR). Only squash if the user explicitly prefers
to collapse the whole sprint into one commit.

**7c — Merge.** Per the chosen strategy (**fast-forward, preserving the
per-feature commits, is the default** for a clean fast-forwardable sprint-only
branch; squash only on request):

```
Agent({
  subagent_type: "repo-manager",
  description: "Merge feat/[milestone-slug] into develop",
  prompt: "Merge branch feat/[milestone-slug] into develop using <chosen strategy>:\n  git checkout develop\n  # ff-only (DEFAULT for a clean fast-forwardable branch):  git merge --ff-only feat/[milestone-slug]\n  # squash (only on request):  git merge --squash feat/[milestone-slug] && git commit -m '[milestone]: complete sprint'\nThen delete the local feature branch."
})
```

---

## Step 8 — Auto-improve

```
Skill("auto-improve")
```

---

## Step 9 — Sprint complete

Delete the sprint's forecast directory, `.claude/tmp/dev-pipeline/<milestone-slug>/` — its
briefs are scratch state, no longer needed once every item is committed and merged.

Report:
- Milestone completed.
- All items done (label + title for each).
- Next steps: push `develop` when ready, then open a PR `develop` → `main` (no squash).

---

## Rules

- **Never start item N+1 until item N is committed and green.** Forecasting runs ahead of
  schedule; implementation never does.
- **Never commit on a red test run** — fix first.
- If implementation fails after 3 fix attempts, **pause the sprint**, surface the error to the user, and wait for guidance.
- The `ROADMAP.md` checkbox update is part of each implementation step (not a separate commit).
- If the user interrupts the sprint, resume by re-reading `ROADMAP.md` from Step 1 to
  discover remaining unchecked items. Step 1.5 automatically picks up any brief files
  already on disk from before the interruption, so resuming doesn't re-forecast items that
  are already covered.
- The lookahead window is fixed at 2 items ahead — don't prefetch further out than that
  even if the phase has many remaining items; it keeps concurrent forecast agents bounded
  and keeps briefs reasonably close to current codebase state.
- All git work (staging, committing, branching, merging) is delegated to `repo-manager`. Never run git commands directly in the main session.
