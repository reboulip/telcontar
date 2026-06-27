# Quick Start

This walkthrough organizes a sample directory from scratch. It assumes you have completed [Installation](installation.md) and [Configuration](configuration.md).

---

## 1. Prepare a target directory

Create or choose a directory with a few documents to organize:

```
C:\Users\me\Documents\messy\
├── rapport final v3.docx
├── copil jan 2024.pptx
├── notes réunion.txt
├── draft_contrat_v2_FINAL.docx
└── copy of rapport final v3.docx
```

---

## 2. Launch telcontar

```bash
organizer-host
```

The Textual TUI starts with a **startup screen**:

```
┌─────────────────────────────────────────┐
│          Directory Organizer            │
│                                         │
│  Target directory:                      │
│  ┌─────────────────────────────────────┐│
│  │ C:\Users\me\Documents\messy        ││
│  └─────────────────────────────────────┘│
│  [ Organize ]  [ Query ]                │
└─────────────────────────────────────────┘
```

Enter the path to your messy directory and press **Organize** (or hit Enter) to start the full analyze-and-reorganize workflow.

!!! tip
    **Query** opens a read-only chat over an already-analyzed corpus (registry must exist). Use it after a previous Organize run to ask natural-language questions without touching the files.

---

## 3. Watch the agent work

The main screen shows a sidebar file tree on the left and a scrolling agent log on the right:

```
▶ list_dir(path='C:/Users/me/Documents/messy')
  {"path": "...", "entries": [...]}
▶ extract_text(path='.../rapport final v3.docx', max_chars=4000)
  "Rapport trimestriel Q1 2024..."
▶ compute_checksum(path='.../rapport final v3.docx')
  {"checksum": "a3f9..."}
▶ record_document(checksum='a3f9...', title='Rapport Q1 2024', ...)
  {"checksum": "a3f9...", "status": "active"}
```

The agent reads, checksums, and records each document in the registry, then uses `find_duplicates` to spot the copy.

---

## 4. Review and approve the plan

Once analysis is complete, the agent proposes a plan. A modal appears:

```
╔══════════════════════════════════════════════════════╗
║  Plan Review  ·  a1b2c3d4  ·  5 op(s)               ║
╠══════════════════════════════════════════════════════╣
║  ☑  RENAME   rapport final v3.docx  →  2024-01-15_rapport_q1.docx  ║
║  ☑  RENAME   copil jan 2024.pptx  →  2024-01-10_support_copil_jan.pptx ║
║  ☑  RENAME   notes réunion.txt  →  notes_reunion.txt                ║
║  ☑  RENAME   draft_contrat_v2_FINAL.docx  →  draft_contrat.docx    ║
║  ☑  QUARANTINE  copy of rapport final v3.docx                       ║
╠══════════════════════════════════════════════════════╣
║           [ Approve ]        [ Reject ]              ║
╚══════════════════════════════════════════════════════╝
```

- **Uncheck** any operation you want to skip before approving.
- **Reject** sends feedback to the agent, which will revise the plan.
- **Approve** executes the checked operations immediately.

!!! tip
    Each operation is journaled. If something goes wrong after approval, `undo_last` (available via the MCP server) reverts the most recent step.

---

## 5. See the results

After execution the agent synthesizes:

```
messy/
├── 2024-01-15_rapport_q1.docx
├── 2024-01-10_support_copil_jan.pptx
├── notes_reunion.txt
├── draft_contrat.docx
├── INDEX.md          ← human-readable tree + changelog
├── manifest.json     ← structured metadata
├── SUMMARY.md        ← narrative summary
└── _quarantine/
    └── copy_of_rapport_final_v3.docx
```

A desktop notification fires when the agent is done. Press **g** to open query mode and ask questions about the corpus, or **q** to quit the TUI.

---

## Next steps

- Adjust `APPROVAL_MODE` in `.env` once you trust the agent — see [Approval Modes](../user-guide/approval-modes.md).
- Understand the output files: [Outputs](../user-guide/outputs.md).
- Learn how to create a profile for your own document corpus: [Adding a Profile](../developer/adding-profiles.md).
