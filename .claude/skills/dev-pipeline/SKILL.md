---
name: dev-pipeline
description: Orchestrate a full development sprint from ROADMAP.md. Reads unchecked items, batches independent ones into dependency-ordered waves, and implements them on a feat/ sub-branch of develop — using feature-forecast for background prefetch, one doc-keeper and one /test-select run per wave, and one commit per wave. Use when asked to run the sprint, work through the roadmap, or implement all pending items.
---

# /dev-pipeline — sprint orchestrator

## What this does

Reads `ROADMAP.md`, finds all unchecked items in the active milestone, then — for a non-trivial sprint — runs an up-front **strategic planning pass** (`sprint-planner` subagents, Opus at xhigh) that produces a persisted `sprint-brief.md` the rest of the sprint follows. That brief also partitions the items into **waves**: batches of items that write disjoint file sets and have no dependency between them. The sprint then advances **one wave at a time**:

1. **Forecast** (`feature-forecast` subagent, Haiku) — reads the codebase and produces a tactical Forecast Brief per item, persisted to a temp file.
2. **Implementation** — main session implements every item in the wave, in one pass, using those briefs.
3. **Documentation** (`doc-keeper` subagent, Sonnet) — syncs README/docs to the wave's changes. Runs **in the background, concurrently with tests**.
4. **Tests** (`/test-select` skill) — one run for the whole wave; gates the commit, red blocks advance.
5. **Commit** — one commit per wave, covering the code **and** doc changes, run directly in the main session.

Forecasts run **one wave ahead** of whichever wave is being implemented, so every brief is
ready with zero wait time. Each brief is written to
`.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md` as soon as its forecast completes —
implementation reads from that file rather than from conversation context, so briefs
survive context compaction and sprint interruption/resume. The sprint-wide
`sprint-brief.md` (Step 1.7) lives in the same directory and is likewise scratch state, so
a resumed sprint reuses it — including its wave plan — without re-planning.

> **Why waves and not parallel git worktrees.** Waves cut the *fixed per-item tail* — the
> cold-start doc-keeper round trip and the test run — without
> introducing any new failure mode: one editor, one branch, one gate. Fanning implementer
> agents out across worktrees was considered and rejected for this repo: the roadmap's
> items overwhelmingly pile onto the same few files (`host/web/main.py`, `host/agent.py`),
> foundation items define contracts that later items mount into (a *semantic* conflict that
> merges cleanly and still breaks), every item edits `ROADMAP.md`, each worktree needs its
> own `uv sync` (`.venv` is gitignored) on Windows, parallel branches destroy the `--ff-only`
> history and bisectability, and subagents can't call `AskUserQuestion` so they guess on
> ambiguity. Note also that the **full** suite is ~63s / 691 tests — pytest is not the
> bottleneck, agent round trips and token generation are. Don't re-propose worktrees
> without a milestone whose items are genuinely disjoint (separate views/modules, no shared
> contract); if one appears, parallelize the implementation body only and keep the merge,
> docs, roadmap edits, and the test gate serial in the main session.

> **Note on design clarification.** Non-obvious design decisions (output formats,
> tool signatures, LLM-vs-code prose, dependency ordering) are **not** resolved in a
> separate up-front step any more — the **Step 1.7 planning pass** surfaces them and
> asks the user **once**, consolidated. There is no standalone "Step 0 — Design
> clarification"; it has been folded into Step 1.7.

---

## Step 0 — Branch setup

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

**Run this step in the foreground** (synchronously — do not dispatch it as a
background `Agent` call). Step 1 cannot usefully proceed until the branch
exists, so there is no real parallelism to exploit here (unlike the
lookahead-forecast agents used later in the pipeline). Dispatching it in the
background invites an improvised busy-wait (e.g. a placeholder `Bash` no-op)
while waiting for something the harness will already notify about — just run
it and let the tool call return before moving to Step 1.

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
the user explicitly asks for it. The in-scope (non-deferred) items are what the
Step 1.7 planning pass partitions; the deferred ones are surfaced to the user
there (in the consolidated `AskUserQuestion` round) so they can opt them in.
Never silently implement a deferred item.

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

## Step 1.7 — Sprint planning & brief

Produce a persisted, sprint-wide `sprint-brief.md` that the rest of the sprint follows.
This replaces the old standalone design-clarification step: the planning agents surface
every ambiguity, and the user is asked **once**, consolidated.

The brief lives at:

```
.claude/tmp/dev-pipeline/<milestone-slug>/sprint-brief.md
```

(co-located with the forecast briefs — already gitignored, already the resume-state
location, and cleaned up by Step 8's directory delete).

**Resume check first.** If `sprint-brief.md` already exists there, read its
`Covers:` fingerprint line (the ordered list of in-scope item labels). If it matches
the current in-scope item set exactly, **reuse it and skip the rest of this step** — a
resumed sprint does not re-plan. If it's missing or the fingerprint no longer matches
the roadmap (items added/removed/retitled since it was written), regenerate it below.

**Skip gate.** Skip planning entirely — no `sprint-planner` agents, no brief — when the
sprint is trivial or homogeneous:
- exactly one in-scope item, **or**
- all in-scope items touch a single module and raise no obvious cross-item decision.

In that case note "planning skipped (trivial sprint)" and go straight to Step 2; per-item
`feature-forecast` covers it. **With no planning pass there is no collision data, so there
is no wave batching either** — each in-scope item is its own wave, which is exactly the
old per-item rhythm. Never guess a wave plan without a `Files touched & dependencies`
table; batching two items that turn out to collide costs more than it saves.

**Otherwise, plan:**

1. **Partition** the in-scope items into **coherent feature clusters** — group items that
   share a module, a data shape, or an approach; keep unrelated items in separate clusters.
   Cap the number of clusters (and therefore `sprint-planner` agents) at **4**. If there
   are more than 4 natural clusters, merge the smallest/most-related ones to stay ≤4.

2. **Fan out** one `sprint-planner` per cluster, in parallel (foreground; wait for all):

   ```
   Agent({
     subagent_type: "sprint-planner",
     description: "Plan cluster: [cluster theme]",
     prompt: "Milestone: [milestone label]\n\nCluster: [theme]\n\nItems in this cluster (verbatim from ROADMAP.md):\n[label — title + full item text, one per item]\n\nOther clusters in this sprint (for cross-cluster awareness, do not plan them):\n[one line per other cluster: theme + item labels]"
   })
   ```

   Each returns a Planning Report (scope, approach & sequencing, cross-cutting decisions,
   open questions, proposed adjustments, risks, notes).

3. **Aggregate.** Collect all reports. Merge and dedupe their **Open questions** and
   **Proposed adjustments** (including any deferred-item opt-in from Step 1 and any
   prerequisite-inversion). Resolve anything the reports already answer decisively; keep
   only what genuinely needs the user.

4. **Ask the user once.** If any open question / proposed adjustment / deferred-item
   choice remains, call `AskUserQuestion` **a single consolidated time** (batch related
   decisions into that one call's questions), using each planner's recommendation as the
   first/recommended option. Do not fan out multiple question rounds. If nothing needs the
   user, skip the prompt.

5. **Compute the waves.** Union the `Files touched & dependencies` tables from every
   planning report into one sprint-wide table (items from different clusters absolutely
   can collide — that's why each planner is told about the other clusters). Then greedily
   partition the items into ordered **waves**, walking the implementation order settled in
   step 3/4:

   - Open a new wave with the first unplaced item.
   - Add a later item to that wave **only if both hold**: its `Writes` set is disjoint from
     every item already in the wave, **and** it lists no `Depends on` pointing at an item in
     this wave or any *later* one.
   - Otherwise it opens the next wave.
   - **Cap a wave at 4 items.** Beyond that, a red test run is too hard to attribute and
     the implementation pass gets too big to hold coherently.

   Sanity rules, all of which override the greedy result:
   - Any item whose row carried an "unsure" note in `Notes` is **serialized** — its own wave.
   - An item that establishes shared scaffolding (a shell, a base class, a new module other
     items import) is **always alone in its wave**, even if its `Writes` set looks disjoint.
     Later items need it committed and green before they build on it.
   - Waves preserve the settled implementation order; batching never reorders items.

   Expect many sprints to come out **all-singleton** — a UI-shell milestone where every
   item edits the same page module has no legitimate waves, and forcing them is how you
   get a merge-shaped mess in a single worktree. Report the wave plan to the user in one
   line per wave when you write the brief; don't ask for approval of it.

6. **Write the brief** to `sprint-brief.md` with these sections:
   - `Covers:` — ordered list of in-scope item labels (the resume fingerprint) + the
     milestone label.
   - **Waves** — the wave plan from step 5: one line per wave (`Wave 1: T1, T7`), each
     followed by a one-clause reason the items are safe to batch (or, for a singleton, why
     it stands alone). This is what Steps 2/4/5/6 execute against.
   - **Plan** — the agreed approach & item ordering across clusters (fold in any
     user-approved reordering/splitting; if the implementation order now differs from
     ROADMAP's listed order, state it explicitly here).
   - **Cross-cutting decisions** — the settled shared shapes/signatures/formats.
   - **Resolved questions** — each user decision and the chosen answer.
   - **Risks & watch-items** — carried from the reports, most-likely-to-bite first.

   This file is **never committed** — it's in gitignored `.claude/tmp/`.

**Standing rule for the rest of the sprint:** Step 4 implementation and the
`feature-forecast` briefs are subordinate to `sprint-brief.md` — when a forecast or the
literal ROADMAP text conflicts with an agreed decision in the brief, the brief wins. Only
the user can change the brief.

---

## Step 2 — Prepare wave[0] and prefetch the lookahead window

Read the **Waves** section of `sprint-brief.md` (or, if planning was skipped, treat each
item as its own wave). The lookahead unit is a wave, not an item.

1. For **every item in wave[0]** whose brief file doesn't already exist, spawn
   `feature-forecast` — all of them in parallel, in one message — and wait for the results:

   ```
   Agent({
     subagent_type: "feature-forecast",
     description: "Forecast brief for [milestone] [label]",
     prompt: "Milestone: [milestone label]\nItem: [label] — [title]\n\n[full item text verbatim from ROADMAP.md]"
   })
   ```

   Persist each per the Step 1.5 standing rule. If a file already existed, just read it.

2. Top up the lookahead window to one wave ahead: for every item in **wave[1]** (if it
   exists) that doesn't already have a brief file on disk, fire `feature-forecast` in the
   background without waiting:

   ```
   Agent({
     subagent_type: "feature-forecast",
     run_in_background: true,
     description: "Forecast brief for [milestone] [label]",
     prompt: "Milestone: [milestone label]\nItem: [label] — [title]\n\n[full item text verbatim from ROADMAP.md]"
   })
   ```

At this point every wave[0] brief is in hand, and wave[1] (if it exists) is already being
forecast in the background.

---

## Step 4 — Implement the current wave

Read the Forecast Brief for **each item in the wave** from
`.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md`. If one isn't there yet (forecast
still in flight), wait for the completion notification — the Step 1.5 standing rule
writes it to that path as soon as it arrives. Then implement **every item in the wave in
this one pass**, in the wave's listed order:
- **Consult `sprint-brief.md` first** — its Plan, cross-cutting decisions, and resolved
  questions govern every item. Where a forecast brief or the literal ROADMAP text
  conflicts with it, the sprint brief wins.
- Follow the "Suggested implementation order" from each forecast brief.
- Edit only files under `host/`, `server/`, `config/`, `tests/`. Use direct Edit/Write tools.
- Check off **every item in the wave** in `ROADMAP.md` (`- [ ]` → `- [x]`).
- Run `uv run ruff format .` on the way out, so format drift is fixed per wave instead of
  piling up for Step 7a to discover.

**If two items in the wave turn out to collide** — the planner's `Writes` set was wrong,
or one genuinely needs the other's code — don't force it. Implement the first, drop the
rest back into the *next* wave, note the correction in `sprint-brief.md`'s Waves section
so a resumed sprint doesn't repeat the mistake, and carry on. A mis-batched wave costs one
extra cycle; forcing it costs a tangled commit.

---

## Step 4.5 — Update documentation (in the background)

Once the whole wave is implemented, hand the wave to **one** `doc-keeper`, so the docs land
in the **same** commit as the code. It always runs in the background — doc-keeper writes only
`README.md` and `docs/**`, which pytest never imports, so it runs safely alongside the test
run instead of adding a serial leg. Go straight on to Step 5 after firing it.

**One doc-keeper per sprint, not per wave.** `docs/developer/modules.md` and
`docs/developer/architecture.md` are ~880 lines each (~54k tokens together) and are touched
by nearly every wave. A fresh agent re-reads them from cold every time, which is why doc
updates lag far behind the ~63s test run. So:

- **Wave 1** — spawn it with `Agent` and remember the returned agent id/name.
- **Waves 2..N** — continue that **same** agent with `SendMessage`, passing the same prompt
  body. Its context still holds the big docs, so later waves skip the cold read entirely.
- Only spawn a fresh `doc-keeper` if the sprint was interrupted and the earlier agent is
  gone, or if it reports that its in-context copy of a doc no longer matches disk.

**Give it the diff and the targets — do not make it search.** The main session already read
the source and already knows which docs the change lands in. Passing that removes a whole
exploration pass:

- `Diff:` the actual unified diff of the wave's source changes
  (`git diff -- host server config profiles`, plus `git diff --cached`/untracked file bodies
  for new files). With this, doc-keeper needs **zero** source Reads.
- `Target docs:` the specific pages **and sections** you expect to change, for example
  `docs/developer/modules.md § host/web/session.py`, `docs/developer/architecture.md § Data flow`.
  Write "unknown" only when you genuinely cannot tell; doc-keeper stays free to correct you
  and to touch a page you did not list.

```
# Wave 1
Agent({
  subagent_type: "doc-keeper",
  run_in_background: true,
  description: "Update docs for [milestone] wave [N]",
  prompt: "Items in this wave:\n[label — title, one per item]\n\nChanged files:\n[list of files edited/created in Step 4]\n\nTarget docs:\n[page § section, one per line, or \"unknown\"]\n\nSummary of change:\n[1-2 sentences per item: what the implementation did — new/changed MCP tools, signatures, config keys, behaviour]\n\nDiff:\n```diff\n[unified diff of the wave's source changes]\n```"
})

# Waves 2..N — same agent, context still warm
SendMessage({ agent: "[doc-keeper id from wave 1]", message: "[same prompt body, this wave's values]" })
```

If the diff is very large (a wide refactor), pass the diff for the behaviour-bearing files
and fall back to the plain file list for the rest — say which is which in the prompt.

---

## Step 5 — Test and commit the wave

**Scope table first:**
If any new `tests/test_*.py` files were created anywhere in this wave, update the scope table in `.claude/skills/test-select/SKILL.md` **before** calling test-select. Add the new files to the correct row(s) and update any catch-all rows (e.g. `server/tools.py only`). Do not defer this to auto-improve.

**Test:**
```
Skill("test-select")
```
One run for the whole wave, scoped to the union of the wave's changes. If the verdict is
RED, fix the failures before continuing. Do not advance until green. (The full suite is
~63s, so when a wave's changes are broad, running everything is a perfectly good answer.)

**Join doc-keeper:** before committing, wait for the Step 4.5 agent to report — whether it
was spawned with `Agent` (wave 1) or continued with `SendMessage` (later waves). Add any
docs it updated/created to the commit's file list; if it reports "None — internal/test-only,"
proceed with no doc changes. Never commit while it is still in flight — that's how doc
changes get orphaned into the next wave's commit.

**Commit — one commit per wave**, covering every item in it plus the doc changes, run
directly in the main session:
```
git add [list of changed files]
git commit -m "[type]: [wave summary — the shared theme, or the items joined]" -m "[body: one bullet per item in the wave ([label] — what changed and why)]"
```

The body's one-bullet-per-item is what keeps a wave commit auditable against the roadmap
now that history is no longer 1:1 with items — don't skip it. For a singleton wave this is
exactly the old per-item commit. Stage only the listed files — never `git add -A`/`.` — and
follow CLAUDE.md's Branch Model hard rules (no `--force`, no `--no-verify`, no amending a
published commit).

---

## Step 6 — Advance to the next wave

After wave[K] is committed:

1. **Staleness check (judgment call, not automatic).** Consider what wave[K]'s
   implementation just changed. If it invalidates something an already-fetched brief for a
   wave[K+1] or wave[K+2] item relied on — e.g. it created a helper the brief listed as a
   "missing prerequisite," renamed/moved a file the brief references, or changed a
   function signature the brief quotes — re-fire `feature-forecast` for that specific item
   now (foreground if it's in wave[K+1] and needed right away, background otherwise) to
   overwrite its `<label>.md`. Skip this if nothing wave[K] did touches later briefs — this
   is the common case and costs nothing. Waves make this check **more** important, not
   less: a wave commits several items at once, so it perturbs more of the codebase per
   cycle than a single item did.
2. Top up the lookahead window: for every item in wave[K+2] (if it exists) without a brief
   file yet, fire its forecast in the background now (same pattern as Step 2.2), keeping
   the window one wave ahead of whatever comes next.
3. Read the briefs for wave[K+1]'s items from
   `.claude/tmp/dev-pipeline/<milestone-slug>/<label>.md`. In the common case they're
   already there — that prefetch started a wave ago. If any isn't, wait for its completion
   notification.
4. Return to Step 4 for wave[K+1].
5. Repeat until all waves are committed.

---

## Step 7 — Merge into develop

When the last wave is committed:

**7a — Format gate (do this BEFORE merging).** The `develop`→`main` PR has a
`ruff format --check .` CI gate, so any format drift anywhere in the repo (even in
files this sprint did not touch) will block that PR. Catch it now while on the
feature branch:

```bash
uv run ruff format --check .
```

If it reports files that "would be reformatted," run `uv run ruff format .` to fix
them, then re-run `--check` to confirm clean. Commit the formatting fix as its own
commit directly in the main session (`git commit -m "chore: ruff format"`) so it merges
with the sprint. Then proceed to the divergence check.

Step 4 now runs `ruff format` at the end of every wave, so this gate should normally come
back clean — it stays here to catch drift in files no wave touched. A clean result is the
expected outcome, not a reason to skip the check.

**7b — Divergence check (do this BEFORE merging).** A squash-merge assumes the
branch contains only this sprint's own commits. Verify that first, directly (read-only):

```bash
git log --oneline develop..feat/[milestone-slug]
git log --oneline -1 develop
```

Compare the listed commits against the sprint's own implementation commits
(**one per wave**, not one per ROADMAP item — a wave commit legitimately covers
several items, so a lower commit count than item count is expected here, and the
wave plan in `sprint-brief.md` is what you check against). If the branch contains
**any commit you did not author
this sprint** — e.g. a commit from a separate process landed on the branch base,
or develop advanced underneath you — **do not blind-squash.** Surface the
divergence and use `AskUserQuestion` to let the user choose the merge strategy
(preserve all commits via fast-forward / non-squash, vs. squash the sprint
commits while preserving the foreign commit, vs. squash everything). When the
branch is exactly the sprint's own commits **and** is a clean fast-forward
(`git log --oneline feat/[milestone-slug]..develop` is empty), proceed straight
to a **fast-forward** merge (`git merge --ff-only`) — this preserves the
per-wave commits, matching CLAUDE.md's "one commit per feature" rule and
keeping a readable history on `develop` (which flows verbatim to `main`, since
`develop`→`main` is a no-squash PR). Only squash if the user explicitly prefers
to collapse the whole sprint into one commit.

**7c — Merge.** Per the chosen strategy (**fast-forward, preserving the
per-wave commits, is the default** for a clean fast-forwardable sprint-only
branch; squash only on request), run directly in the main session:

```bash
git checkout develop
# ff-only (DEFAULT for a clean fast-forwardable branch):
git merge --ff-only feat/[milestone-slug]
# squash (only on request):
git merge --squash feat/[milestone-slug] && git commit -m "[milestone]: complete sprint"
```

Then delete the local feature branch (`git branch -d feat/[milestone-slug]`).

---

## Step 8 — Sprint complete

Delete the sprint's forecast directory, `.claude/tmp/dev-pipeline/<milestone-slug>/` — its
forecast briefs **and `sprint-brief.md`** are scratch state, no longer needed once every
item is committed and merged.

(End-of-session improvement reflection is handled automatically by the global
Stop hook — no explicit auto-improve step needed here.)

Report:
- Milestone completed.
- All items done (label + title for each).
- Next steps: push `develop` when ready, then open a PR `develop` → `main` (no squash).

---

## Rules

- **Never start wave N+1 until wave N is committed and green.** Forecasting runs ahead of
  schedule; implementation never does. Batching happens *within* a wave, never across the
  test gate.
- **Waves batch, they never parallelize.** One editor, one branch, one gate — a wave's
  items are implemented sequentially in a single main-session pass. Never fan implementer
  subagents out over the items of a wave, with or without worktrees (see the rationale
  under "What this does"). The only agents that run concurrently are the read-only
  `feature-forecast` prefetch and the docs-only `doc-keeper`.
- **Never commit on a red test run** — fix first.
- If implementation fails after 3 fix attempts, **pause the sprint**, surface the error to the user, and wait for guidance.
- The `ROADMAP.md` checkbox update is part of each implementation step (not a separate commit).
- If the user interrupts the sprint, resume by re-reading `ROADMAP.md` from Step 1 to
  discover remaining unchecked items. Step 1.5 automatically picks up any forecast brief
  files already on disk, and Step 1.7 reuses an existing `sprint-brief.md` when its
  `Covers:` fingerprint still matches the in-scope items — so resuming re-plans and
  re-forecasts nothing that's already covered.
- The lookahead window is fixed at **one wave ahead** — don't prefetch further out than
  that even if the phase has many remaining waves; it keeps concurrent forecast agents
  bounded and keeps briefs reasonably close to current codebase state. (An all-singleton
  sprint therefore prefetches one item ahead rather than the old two — the wave gate
  already runs less often per item, so the deeper window bought nothing.)
- **Resuming mid-sprint resumes at a wave boundary.** Waves commit atomically, so a
  resumed sprint re-reads the Waves section of `sprint-brief.md` and restarts at the first
  wave whose items are still unchecked in `ROADMAP.md` — never mid-wave.
- All git work (staging, committing, branching, merging) runs directly in the main
  session, per CLAUDE.md's Branch Model hard rules (no `--force`, no `--no-verify`,
  no amending a published commit, no direct push to `main`).
