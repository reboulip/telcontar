# Quick Start

This walkthrough organizes a sample directory from scratch. It assumes you have completed [Installation](installation.md).

---

## 1. Launch telcontar

```bash
organizer-host
```

---

## 2. First-run setup wizard

If this is your first time running telcontar, the **setup wizard** appears automatically. It takes about two minutes.

```
┌────────────────────────────────────────────────────┐
│              Directory Organizer                   │
│                                                    │
│  Welcome! Let's get you set up in a couple of      │
│  steps.                                            │
│                                                    │
│  To read and analyze your documents, this app      │
│  needs to talk to an AI service. You'll need:      │
│                                                    │
│    • The web address of your AI service            │
│    • An API key (your provider gives you this)     │
│                                                    │
│                        [ Get started → ]           │
└────────────────────────────────────────────────────┘
```

**Step 1 — Choose your AI service:**

Select your provider (Mammouth, Azure OpenAI, or another compatible service).

**Step 2 — Enter your details:**

Paste the service URL and your API key. The key is stored securely in your OS credential store (Windows Credential Manager or macOS Keychain) — it never touches a plain text file.

**Step 3 — Choose your document type:**

Pick the vocabulary that best matches what you'll organize:

- **IS/IT project** — technical and business documents
- **Personal files** — invoices, contracts, administrative records
- **Research papers** — academic and scientific articles

The wizard saves your settings and moves straight to the main screen. You will not see it again on subsequent launches.

!!! tip
    You can update any of these settings at any time using the **⚙ Settings** button on the main screen.

---

## 3. Choose a directory to organize

The **startup screen** asks for a target directory:

```
┌─────────────────────────────────────────┐
│          Directory Organizer            │
│                                         │
│  Target directory:                      │
│  ┌─────────────────────────────────────┐│
│  │ C:\Users\me\Documents\messy        ││
│  └─────────────────────────────────────┘│
│  [ Organize ]  [ Query ]  [ ⚙ Settings ]│
└─────────────────────────────────────────┘
```

Enter the path to your messy directory and press **Organize** (or hit Enter) to start the full analyze-and-reorganize workflow.

!!! tip
    **Query** opens a read-only chat over an already-analyzed corpus (registry must exist). Use it after a previous Organize run to ask natural-language questions without touching the files.

---

## 4. Watch the agent work

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

## 5. Review and approve the plan

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

## 6. See the results

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

- Adjust the approval level in **⚙ Settings** once you trust the agent — see [Approval Modes](../user-guide/approval-modes.md).
- Understand the output files: [Outputs](../user-guide/outputs.md).
- Learn how to create a profile for your own document corpus: [Adding a Profile](../developer/adding-profiles.md).
