# Approval Modes

The `APPROVAL_MODE` setting controls when telcontar pauses and asks for user confirmation before executing file operations.

---

## Modes

### `always` (default)

`execute_plan` — the tool that applies every staged plan op (moves, renames, quarantines, file writes, folder creation, archiving, and quarantine compression) — requires explicit user approval before it runs. There is no tool that mutates the filesystem outside this gate. Read-only tools (`list_dir`, `read_file`, `extract_text`, registry/graph queries, …) and the output-writing tools (`write_index`, `write_summary`, `write_folder_readme`) are never routed through the approval gate; they always run freely, in every mode.

**Best for:** initial use, unfamiliar document corpora, any situation where you want full control.

```ini
APPROVAL_MODE=always
```

### `destructive_only`

`execute_plan` is the only tool ever subject to the approval gate, and it is inherently the destructive step (it performs the moves/renames/quarantines) — so in practice `destructive_only` gates it exactly like `always` does. Read-only tools and the output-writing tools (`write_index`, `write_summary`, `write_folder_readme`) already run freely without gating in every mode, so this setting does not change their behaviour.

**Best for:** once you trust the agent's analysis and want the same gate as `always`, without implying that anything else is held back.

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

- **A plan rationale**, if the agent attached one via `set_plan_rationale`, is shown as a short plain-language paragraph above the op checklist — explaining how the plan groups, renames, and quarantines documents and why. It is preceded by a subtle `[dim]`-styled disclaimer, "Model-generated rationale — not verified fact:", so you don't mistake the agent's prose for a verified account of what the plan does — the op checklist below it is the thing to actually check
- **How the target layout and op checklist are shown differs by UI:**
    - **Textual TUI** — a target-layout preview, when the plan has any move/quarantine destinations: a folder tree built from the plan's ops, with each folder's purpose note (set by the agent via `set_plan_folder_notes`) shown beside it — folders without a note appear as bare tree nodes. Rename-only plans show no tree. When any folder notes are present, a matching disclaimer ("Folder notes are model-generated — not verified fact.") appears above the tree — the tree structure itself is derived from the plan's own op destinations, but the prose notes beside each folder are the agent's commentary, not verified fact. Below it, every proposed operation is listed as a checked checkbox in one flat list: a `quarantine` op shows the agent's stated reason for it (e.g. "duplicate of report_v2.pdf") right beside the filename, or "no reason given" if none was supplied; an `update_file` op that will replace an existing file (`overwrite=True`) is labelled with a subtle `(overwrite)` marker so you aren't blindsided by a collision-safe write turning into a replace, and an op whose source resolves outside the directory you're organizing is labelled with an equally subtle `(outside target)` marker — a best-effort visual cue, not the security boundary itself (that's the server's own path-confinement guard)
    - **Web UI** — the layout preview and the op checklist are one and the same: an actual before/after file tree, a "Before" panel (current file locations, read-only) beside an "After" panel (destinations, folder notes shown beside folder lines the same way). A `rename`, `move`, a `quarantine` with a resolved destination, or a newly-created file gets its checkbox inline on its filename in the After tree — a `quarantine` node still carries its stated reason (or "no reason given") beside the filename, and any node whose source resolves outside the directory being organized still carries the `(outside target)` marker, exactly as the flat list did; only the `(overwrite)` marker is exclusive to "Other operations" below, since `update_file` (the only op type it applies to) always lands there rather than on a tree node. Everything without a clean tree slot — `create_dir`, `compress_quarantine`, `update_file`, and a `quarantine`/`archive_document` with no destination — is listed below the tree under "Other operations", each with its own checkbox, using the same labelling the TUI's flat list uses throughout. Every op still gets exactly one checkbox, in the tree or in "Other operations"
- You can **uncheck** individual ops to skip them (the rest still execute)
- **A full ops JSON path** is shown below the checklist: every time a plan is presented, its complete op list (plan id, rationale, folder notes, and every op) is written to `.organizer/plan_ops.json` (latest plan wins), so you can open the file to inspect the full detail while the modal itself only shows the summary
- **A free-text refine field**, for when the plan is close but not quite right — describe the change in plain language (e.g. "merge the drafts into one folder", "don't quarantine the specs")
- **Approve** confirms the checked ops and triggers `execute_plan`
- **Refine** (or pressing Enter in the field) sends your typed text back to the agent instead of executing anything — the agent revises the plan (adjusting ops, rationale, and folder notes) and calls `execute_plan` again to re-present it for another round of review. Leaving the field blank and pressing Refine is a no-op — the modal stays open
- **Reject** sends a rejection back to the agent, which will revise the plan and try again
- **Escape** is equivalent to Reject

---

## Hard stop

Regardless of `APPROVAL_MODE`, if more than 3 operations fail during a single `execute_plan` run, the server triggers a **hard stop**: execution is halted, a `hard_stop` entry is written to the journal, and the agent is notified of the failures. Each failed op's error message is clear and actionable (e.g. naming a locked file and hinting to close the program holding it), so the agent can explain precisely what went wrong. A transient lock is retried up to 3 times before being counted as a failure; a missing source file fails immediately. The agent will then explain what went wrong and offer to undo.
