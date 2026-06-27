# Approval Modes

The `APPROVAL_MODE` setting controls when telcontar pauses and asks for user confirmation before executing file operations.

---

## Modes

### `always` (default)

Every plan — whether it contains moves, renames, quarantines, or only index/summary writes — requires explicit user approval before execution.

**Best for:** initial use, unfamiliar document corpora, any situation where you want full control.

```ini
APPROVAL_MODE=always
```

### `destructive_only`

Only plans containing moves, renames, or quarantines require approval. Read-only operations (building indexes and summaries) run freely without gating.

**Best for:** once you trust the agent's analysis and just want to speed up the index/summary step.

```ini
APPROVAL_MODE=destructive_only
```

### `never`

All operations — including renames, moves, and quarantines — execute immediately without any approval gate.

**Best for:** fully automated batch runs after trust has been established over many sessions on a specific corpus.

!!! warning
    `never` mode skips the approval gate entirely. The undo journal and quarantine safety net still apply, but mistakes won't be caught before they happen. Only use this mode after extensive testing with `always` or `destructive_only`.

```ini
APPROVAL_MODE=never
```

---

## Recommended progression

```
First run on a new corpus      →  APPROVAL_MODE=always
After a few successful runs    →  APPROVAL_MODE=destructive_only
Fully trusted, batch use       →  APPROVAL_MODE=never
```

Start at `always`. Relax via config — no code changes required, no restart other than re-running the host.

---

## The approval modal

In `always` and `destructive_only` modes, when a plan is ready for execution the host presents an **ApprovalModal**:

- **Each proposed operation** is listed as a checked checkbox
- You can **uncheck** individual ops to skip them (the rest still execute)
- **Approve** confirms the checked ops and triggers `execute_plan`
- **Reject** sends a rejection back to the agent, which will revise the plan and try again
- **Escape** is equivalent to Reject

---

## Hard stop

Regardless of `APPROVAL_MODE`, if more than 3 operations fail during a single `execute_plan` run, the server triggers a **hard stop**: execution is halted, a `hard_stop` entry is written to the journal, and the agent is notified of the failures. The agent will then explain what went wrong and offer to undo.
