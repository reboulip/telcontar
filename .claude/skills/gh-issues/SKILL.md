---
name: gh-issues
description: Triage open GH issues (implement / defer / drop with labels), then plan implement issues into ROADMAP.md and label/comment each one in GH. Use when asked to triage the issue backlog, or fold GH issues into the roadmap.
---

# /gh-issues — plan open GH issues into the roadmap

## What this does

1. Fetches all open GH issues not yet carrying a disposition label (`roadmap`, `deferred`, or `dropped`).
2. Reads each one and classifies its nature.
3. **Triages each issue** with the user — implement, defer, or drop — and immediately applies the decided disposition (label + comment, closing if dropped) before planning starts.
4. For issues marked *implement*: proposes a mapping into `ROADMAP.md` following this project's `- [ ] <Label> · <description>` convention, asks disambiguation questions as needed, writes the items directly and commits (see Step 7), labels each issue `roadmap`, and posts a roadmap-link comment.

---

## Step 1 — Fetch open issues

```powershell
gh issue list --state open --json number,title,labels,body --limit 100
```

For a readable at-a-glance listing (number + labels + title), don't improvise a display
command — piping `ForEach-Object` string interpolation directly to output garbles when
labels are multi-value (fields run together). Use this known-good pattern instead:

```powershell
gh issue list --state open --json number,title,labels --limit 100 |
  ConvertFrom-Json |
  ForEach-Object {
    [PSCustomObject]@{
      Number = $_.number
      Labels = ($_.labels.name -join ', ')
      Title  = $_.title
    }
  } | Format-Table -AutoSize | Out-String -Width 200
```

Filter out any issue that already carries one of the following labels — those are already triaged:
- `roadmap`
- `deferred`
- `dropped`

Also scan `ROADMAP.md` for `[#N]` back-references (this project's item format is `- [ ] <Label> · <description> [#N]`, e.g. `- [ ] L2 · ... [#42]`). If an issue number appears there but lacks the `roadmap` label, it's effectively planned already — add the label silently (Step 8) and skip it from the triage flow.

If no unresolved issues remain after filtering, report that and stop.

---

## Step 2 — Read issue details

For each unresolved issue:

```powershell
gh issue view <number> --json number,title,body,labels,comments
```

Classify each issue as one of:
- **Bug** — defect in existing functionality.
- **Enhancement** — improvement to existing functionality.
- **Feature** — new capability.

If a comment links a `github.com/user-attachments/files/...` attachment (an error
traceback, a `.organizer/journal.jsonl` excerpt, a config dump, etc.), fetch its
raw content with `curl -L -o <scratchpad-path> <url>` (or `gh api`), not
`WebFetch` — WebFetch summarizes plain-text attachments through a small model
and can drop the exact exception type/stack-trace lines needed to root-cause a
bug.

Also form a tentative triage suggestion for each issue:
- Bugs → lean toward **implement**, especially anything touching the safety
  model (guards, undo journal, approval gating, quarantine-not-delete) — these
  are high priority per CLAUDE.md's "Safety first" principle.
- Enhancements/Features → weigh scope, complexity, and fit with the current
  profile-driven architecture (does it belong in the engine, or is it really a
  domain-profile change under `profiles/`?). Large or speculative items lean
  toward **defer**.

---

## Step 3 — Triage review

Present the full list of unresolved issues as a text summary with your suggested disposition:

```
#N — <title> [Bug / Enhancement / Feature]
   Suggested: implement / defer / drop
   Reason: <one sentence>

#M — <title> [Bug / Enhancement / Feature]
   Suggested: implement / defer / drop
   Reason: <one sentence>
```

Then use `AskUserQuestion` to collect the user's disposition for each issue. Process in batches of up to 4 issues per call (one question per issue), where each question has options:
- **Implement** — plan into roadmap
- **Defer** — label and set aside for a future cycle
- **Drop** — label and close; will not implement

After each batch, immediately apply the decided dispositions (Step 3a) before asking the next batch — this keeps progress visible and is resilient to the user stopping mid-review.

### Step 3a — Apply triage dispositions

For each issue just triaged, apply its disposition immediately via direct PowerShell (GH API mutations, not local git state):

**Dropped:**
```powershell
# Create label if needed (ignore non-zero exit — label may already exist)
gh label create dropped --color b60205 --description "Will not be implemented" 2>$null

gh issue edit <number> --add-label dropped
gh issue comment <number> --body "Closing as out of scope — will not implement in the current product direction."
gh issue close <number>
```

**Deferred:**
```powershell
# Create label if needed (ignore non-zero exit — label may already exist)
gh label create deferred --color e4e669 --description "Deferred to a future cycle" 2>$null

gh issue edit <number> --add-label deferred
gh issue comment <number> --body "Deferring for now — this may be revisited in a future cycle."
```

**Implement:** no action yet — these proceed to roadmap planning in Steps 4–9.

> Note: GH-level "Deferred" (this step) is a different mechanism from the
> `[deferred]` / `[deferred/hard]` tag `/dev-pipeline` recognizes inside
> `ROADMAP.md` items — that tag marks an item that *is* in the roadmap but out
> of default sprint scope. A GH-deferred issue does not get a `ROADMAP.md`
> entry at all; it just stays open in GH, untouched, for a future triage pass.

---

## Step 4 — Read ROADMAP.md

Read `ROADMAP.md` in full. Identify:
- The **active milestone section** — the first `## Phase N — <theme>` with at least one unchecked item (same rule `/dev-pipeline` uses to pick the active sprint).
- Any other open milestone sections (further down, with unchecked items) that could reasonably absorb new items.
- The **next available label** for each candidate section. Labels are a single letter per milestone (`A`, `B`, `C`, …) with items numbered within it (`A1`, `A2`, …). **Letter assignment does not follow file order** — e.g. in the current `ROADMAP.md`, `F` (Phase 10 — Hardening) sits after the `G`–`K` sections in file position. So:
  - Appending to an **existing** section: take `max(number)` under that section's own letter, `+1`.
  - Opening a **brand-new** section: scan the *entire* file for the highest letter used anywhere, and take the next unused one — don't just look at the tail.

Skip this step if no issues were triaged as *implement*.

---

## Step 5 — Propose a mapping

For each *implement* issue (or group of related issues), propose:
- **Target section**: an existing open milestone when it fits naturally; a new milestone when a different theme/version makes more sense.
- **Item label**: next unused label in that section (per Step 4's rule).
- **Item text**: one concise imperative sentence, matching the terse, tool/module-referencing style already used in `ROADMAP.md`.
- **Dependency**: if the item can't land before another not-yet-done item, note it inline as `(requires: <label>)` — this is the project's existing convention (see CLAUDE.md's "ROADMAP conventions"); `/dev-pipeline` uses it to detect and sequence prerequisites.
- **Grouping rationale**: if two issues share a root cause or subsystem, propose merging them into one item; if one issue covers distinct sub-problems, propose splitting. Always explain the rationale.

When target section is uncertain, use `AskUserQuestion` before proceeding. Keep each question focused: present the proposed placement and ask the user to confirm or redirect. Do not batch all disambiguation into one giant question — ask per uncertain group.

---

## Step 6 — Confirm the full plan

Before writing anything to `ROADMAP.md`, present the complete proposed mapping as a text summary:

```
Issue #N — <title>
→ <Phase N> <Label>: <proposed item text>

Issue #M — <title>
→ <Phase N> <Label>: <proposed item text>
```

Then use `AskUserQuestion`:
- **"Confirm — proceed"** — write the plan as proposed.
- **"Redirect — I'll provide corrections"** — wait for the user to describe what to change, then revise and ask again.

Only proceed to Step 7 after receiving explicit confirmation.

Skip this step if no issues are being implemented.

---

## Step 7 — Update ROADMAP.md

Verify the current branch is `develop` (this is a planning-doc change, not a feature
implementation, so it does not need a `feat/` branch) — if it is not, stop and report
back rather than switching branches.

Insert the item(s) into `ROADMAP.md` directly: for each item, the target section header
(or the exact new `## Phase N — <theme>` header to insert, and where — immediately after
the active milestone's closing `---`, matching existing style), and the exact line to add:
`- [ ] <Label> · <item text> [#N]` (or `[#N] [#M]` if it covers multiple issues).

Then stage and commit ONLY `ROADMAP.md`:
```bash
git add ROADMAP.md
git commit -m "docs: roadmap items from GH issue triage (#N, #M, ...)"
```

Spot-check that the section hierarchy is intact (no orphaned headers, checklist still well-formed) by re-reading `ROADMAP.md`.

---

## Step 8 — Label implement issues in GH

```powershell
# Create the label if it does not exist (ignore non-zero exit)
gh label create roadmap --color 0075ca --description "Planned in the roadmap" 2>$null

# Label each planned issue
gh issue edit <number> --add-label roadmap
```

---

## Step 9 — Post a planning comment on each implement issue

```powershell
gh issue comment <number> --body "This issue has been planned for development.

**Roadmap:** <Phase N> → <Label> — <item text>"
```

---

## Rules

- Never write to `ROADMAP.md` before Step 6 confirmation.
- `ROADMAP.md` edits and their commit run directly in the main session (see Step 7).
- Always include a `[#N]` back-reference on every roadmap item sourced from a GH issue.
- Apply triage dispositions (Step 3a) per batch as soon as the user confirms — do not wait until the end of the full triage.
- `dropped` issues are closed in GH; `deferred` issues remain open. Neither gets a `ROADMAP.md` entry — that's reserved for `implement` issues (see the note in Step 3a distinguishing GH-deferred from the roadmap's own `[deferred]` tag).
- If a proposed new milestone section could conflict with existing version numbering, ask the user before creating it.
- Unrelated issues can live in the same milestone section. A hotfix-flavored section before a themed minor section is valid when urgency warrants it — explain the reasoning when proposing it.
