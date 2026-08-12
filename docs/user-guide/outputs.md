# Outputs

After organizing a directory, telcontar writes three output files **into the target directory** and maintains its own state under `.organizer/`, also inside the target directory (memory is per-directory — see [Configuration](../getting-started/configuration.md#persistent-state-locations)).

---

## Files written to the target directory

### `INDEX.md`

A human-readable Markdown file containing:

- **Generated timestamp** (UTC ISO-8601)
- **Directory tree** — an ASCII tree of all files and subdirectories (excluding the index files themselves), with file sizes
- **Changes made** — a summary of how many renames, moves, and quarantines were applied in the current session (pulled from the undo journal)

Example:

```markdown
# Directory Index

Generated: 2024-01-15T14:32:00+00:00

## Tree

\`\`\`
messy/
├── 2024-01-15_rapport_q1.docx (142.3 KB)
├── 2024-01-10_support_copil_jan.pptx (2.1 MB)
├── notes_reunion.txt (3.2 KB)
├── draft_contrat.docx (88.1 KB)
└── _quarantine/
    └── copy_of_rapport_final_v3.docx (142.3 KB)
\`\`\`

## Changes Made

- 4 renames
- 1 quarantines
- **Total operations:** 5
```

### `manifest.json`

A structured JSON file containing:

- `generated` — UTC ISO timestamp
- `target` — absolute path of the organized directory
- `files` — array of `{path, abs_path, size, mtime}` for every file in the tree
- `dirs` — array of subdirectory paths
- `journal_summary` — `{total_ops, by_type}` counts from the undo journal

Use `manifest.json` to pipe telcontar's output into other tools (search indexes, dashboards, compliance reports).

### `SUMMARY.md`

An **LLM-authored narrative** structured according to the active profile's `[synthesis]` table. For the bundled `is_it_project` profile the output is a French-language project synthesis titled "Synthèse du projet" with six `##` sections:

- **Vue d'ensemble** — what the corpus covers and its overall status
- **Acteurs principaux** — who appears and in what capacity (from ranked actors)
- **Chronologie** — key dated milestones in chronological order (from the event journal)
- **Documents clés** — major deliverables and their knowledge contribution
- **Doublons et versions** — duplicates and modified versions detected
- **Points d'attention** — risks, gaps, or open decisions — only when evidenced in the data

The agent draws on `list_documents` / `get_registry`, `list_events`, `get_graph`, and `get_actors` to compose the prose. It never invents facts absent from the data. `write_summary` persists the resulting Markdown unchanged — it is a pure sink with no transformation logic.

### Per-folder `README.md`

After writing `SUMMARY.md`, the agent writes a short `README.md` **inside each meaningful subfolder** of the organized tree. Each README is one or two paragraphs naming what that folder holds and its role in the arborescence, drawn from the documents the agent recorded there. Trivial or empty folders are skipped.

These files are produced by `write_folder_readme` during the SYNTHESIZE phase. They are overwritten on each run, so re-running telcontar on an already-organized tree produces up-to-date READMEs without leaving stale files behind.

---

## State files (in `.organizer/`)

These live **inside the target directory** you organized (`<target>/.organizer/`), one memory per directory — not the telcontar project root. They persist across runs, and Query mode finds them by walking up from the folder you select until it finds a `.organizer`.

### `.organizer/registry.json`

The **document memory** — a content-addressed store of every document the agent has ever analysed, keyed by sha256 checksum. Because identity is the checksum (not the path), a record survives renames and moves. On subsequent runs, telcontar can detect that a file was already analysed even if it was renamed.

Each record contains: `checksum`, `path`, `title`, `type`, `summary`, `provenance`, `date`, `entities`, `attributes`, `status` (`active` | `archived` | `quarantined`), `first_seen`, `last_analyzed`.

### `.organizer/journal.jsonl`

An **append-only undo log**. Every operation executed by `execute_plan` is written here as a JSONL entry with `op_type`, `src`, `dst`, and `timestamp`. `undo_last` (a web-UI-only action, not an MCP tool — see [Undo](#undo) below) reads the last entry and inverts it.

Hard-stop events are also recorded here (with `op_type: "hard_stop"`) so that `undo_last` can safely skip them.

### `.organizer/plans/<uuid>.json`

One JSON file per plan. Plans progress through a state machine:

```
pending → approved → executing → done
                               → failed
                   → stopped
```

Completed plans are kept on disk for audit purposes but are not cleaned up automatically.

---

## Undo

`undo_last` is **not** an MCP tool — the agent cannot call it. To revert the most recent operation, open the journal dialog from the "Journal" button on the organize view and click "Undo last operation". This calls `server.tools.undo_last` directly from the web UI, bypassing the agent entirely — undo is a deliberate, user-only action.

`undo_last` reads the last journal entry, inverts the operation (rename → rename back, move → move back, quarantine → move back to original path, create_file/update_file → delete the written file, create_dir → left in place as a no-op), removes the entry, and returns the reverted op. It does not touch the registry — path reconciliation only runs forward during `execute_plan`.
