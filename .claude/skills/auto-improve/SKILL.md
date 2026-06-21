---
name: auto-improve
description: After finishing a task or task series, analyse the current conversation for boilerplate user instructions, repeated corrections, and manual steps that could be automated. Propose concrete improvements to skills, agents, hooks, or config. Always ask the user before applying anything. Invoke at the end of a sprint (called automatically by /dev-pipeline), or on demand with /auto-improve.
tools:
  - Read
  - Glob
  - Grep
  - Edit
  - Write
---

# /auto-improve — end-of-sprint improvement loop

## Step 1 — Read prior improvements (avoid re-proposing)

Read all memory files for this project:

```
C:\Users\romai\.claude\projects\c--Users-romai-code-projects-telcontar\memory\*.md
```

Note the names and descriptions of any feedback memories already applied. Do not propose anything already captured there.

Also read `.claude/skills/` and `.claude/agents/` to see what already exists, so you don't propose creating a skill or agent that already does what you'd suggest.

## Step 2 — Scan the current conversation

Look for these signals in the conversation transcript (available in context):

| Signal type | Examples |
|-------------|---------|
| User correction | "don't do X", "always Y first", "you forgot Z", "stop doing X" |
| Repeated manual step | User does the same thing by hand every session that Claude could automate |
| Re-stated boilerplate | User re-explains the same constraint or rule in multiple prompts |
| Missing automation | A multi-step flow that was done manually but matches a hook/skill pattern |
| Config gap | A permission prompt that fired repeatedly for an allowed operation |

## Step 3 — Classify and draft proposals

For each finding, write a short proposal:

```
### Proposal N
**Finding:** [what the pattern is — be specific, quote the conversation]
**Type:** hook | skill | agent | config | claude.md
**Proposed change:** [one sentence describing the concrete change]
**Estimated value:** [what problem it solves / how many future prompts it saves]
```

Keep proposals small and concrete. One finding = one proposal.

## Step 4 — Present all proposals at once

Use AskUserQuestion to show all proposals simultaneously. Do NOT apply anything before the user responds. Frame the question so the user can approve individual proposals (multiSelect: true).

## Step 5 — Apply approved changes

For each approved proposal:

- **`hook`**: Use `Skill("update-config")` to add the hook to `.claude/settings.json`
- **`skill`**: Create or edit the relevant `SKILL.md` file directly
- **`agent`**: Create or edit the relevant agent `.md` file directly
- **`config`**: Use `Skill("update-config")` for settings.json changes
- **`claude.md`**: Edit `CLAUDE.md` directly with the standing instruction

## Step 6 — Save feedback memories

For each applied improvement, write a feedback memory at:
`C:\Users\romai\.claude\projects\c--Users-romai-code-projects-telcontar\memory\feedback_<slug>.md`

Use this frontmatter:
```markdown
---
name: <slug>
description: <one-line summary>
metadata:
  type: feedback
---

[Rule itself]

**Why:** [reason from the conversation]
**How to apply:** [when/where this kicks in]
```

Also add a pointer line to `MEMORY.md` in the same directory.

## Hard rule

**Never apply any change without explicit user approval from Step 4.** If the user approves zero proposals, exit cleanly with "No changes applied."
