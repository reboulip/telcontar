# Quick Start

This walkthrough organizes a sample directory from scratch. It assumes you have completed [Installation](installation.md).

---

## 1. Launch telcontar

```bash
telcontar
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

Pick **Any OpenAI-compatible service** (Mammouth, OpenAI, a local inference server, etc.) or **Azure OpenAI**, if that's what you use.

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

The **startup screen** shows a browsable folder tree, rooted at your home directory:

```
┌─────────────────────────────────────────┐
│          Directory Organizer            │
│                                         │
│  Choose the folder to organize:         │
│  ┌─────────────────────────────────────┐│
│  │ ▾ Documents                        ││
│  │   ▾ messy                          ││
│  │       invoice.pdf                  ││
│  │       report.docx                  ││
│  └─────────────────────────────────────┘│
│  Selected: C:\Users\me\Documents\messy  │
│  [ Organize ]  [ Query ]  [ ⚙ Settings ]│
└─────────────────────────────────────────┘
```

Browse the tree and click the folder you want to organize — the "Selected:" label updates to show your choice (it points at your home directory by default). Then press **Organize** to open the organizer screen.

!!! tip
    **Query** opens a read-only chat over an already-analyzed corpus — the selected folder, or a parent of it, must contain a `.organizer/` from a previous Organize run. Use it after a previous Organize run to ask natural-language questions without touching the files.

---

## 4. Review the overview and add instructions (optional)

The organizer screen opens on a **starter pane** instead of jumping straight into the agent loop. It shows a code-generated, deterministic overview of the target directory — no file content is read and no LLM call is made yet:

```
┌─────────────────────────────────────────────────────────┐
│ Here's what I found                                     │
│ ┌───────────────────────────────────────────────────┐   │
│ │ Target directory: C:\Users\me\Documents\messy      │   │
│ │ 5 file(s) across 0 subfolder(s).                   │   │
│ │ Most common types: 2× .docx, 1× .pdf, 1× .pptx,    │   │
│ │ 1× .txt.                                           │   │
│ └───────────────────────────────────────────────────┘   │
│ Tell me how you'd like it organized (optional) — e.g.    │
│ "group by workstream", "keep the 2024 invoices          │
│ together", "don't quarantine drafts":                   │
│ [ Steering instructions — leave blank to use my   ]      │
│ [ best judgement                                  ]      │
│           [ Start organizing ]                          │
└─────────────────────────────────────────────────────────┘
```

Type any steering instructions you want the agent to follow, or leave the field blank to let it use its own best judgement. Press **Start organizing** (or Enter in the input field) to leave the starter pane — the chat transcript appears and the agent loop begins. If you typed instructions, they appear as your first turn in the transcript and are passed along to the agent.

---

## 5. Watch the agent work

The main screen shows a sidebar file tree on the left and a single chat transcript on the right, with a compact **operations journal** strip docked along the bottom (above the status bar) — a horizontally-scrollable, one-line-per-entry view of the file operations recorded so far.

Before any chat turn appears, telcontar deterministically discovers and checksums the whole directory tree, silently — no LLM call, and no transcript turn for this step, just a **progress bar** (e.g. "12 / 47 documents analyzed") above the status bar. If it finds documents it hasn't seen before, it pauses once to show a cost estimate scoped to just those new documents (see [How It Works](../user-guide/how-it-works.md#the-cost-estimate-approval-gate)) before reading and analyzing them. `telcontar` narrates the steps that follow in plain language, one turn per macro-task; the raw tool calls behind each turn collect into a click-to-expand **internal steps** group:

```
telcontar  Reading documents…
▸ internal steps

telcontar  Recording documents in memory…
▸ internal steps

telcontar  Checking for duplicates…
▸ internal steps
```

Expand an **internal steps** group to see the raw calls and results behind it, e.g.:

```
▶ extract_text_batch(paths=['.../rapport final v3.docx', ...])
  {".../rapport final v3.docx": "Rapport trimestriel Q1 2024...", ...}
▶ record_document_batch(documents=[{"checksum": "a3f9...", "title": "Rapport Q1 2024", ...}, ...])
  {"recorded": [{"checksum": "a3f9...", "status": "active"}, ...], "errors": []}
```

New documents are analyzed in isolated batches of 10 — read/extract, then one forced-tool LLM call returning a structured record per document, then recorded into the registry in one `record_document_batch` call per batch. Only once every new document is recorded does the chat loop begin; the agent (now working from a digest of the whole corpus, not raw file content) calls `find_duplicates` while planning to spot the copy.

---

## 6. Review and approve the plan

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
    Each operation is journaled. If something goes wrong after approval, open the operations journal (press **j** in the Organizer screen) and press **u** to undo the most recent step — undo is a manual TUI action, not something the agent can trigger itself.

---

## 7. See the results

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

A desktop notification fires when the agent is done. The chat box at the bottom of the screen has actually been live the whole time — you can type a message at any point during the run (e.g. "actually, group by year instead") and it gets woven in as soon as the agent is between turns, instead of waiting for the run to finish. Now that the run is done, it works the same way to continue the conversation — type a follow-up message (e.g. "quarantine the drafts too") to keep going with the same mutating toolset, without restarting the run. Press **g** instead to open a separate read-only query mode and ask questions about the corpus, **j** to view the operations journal (and **u** there to undo the most recent operation), or **q** to quit the TUI.

---

## Next steps

- Adjust the approval level in **⚙ Settings** once you trust the agent — see [Approval Modes](../user-guide/approval-modes.md).
- Understand the output files: [Outputs](../user-guide/outputs.md).
- Learn how to create a profile for your own document corpus: [Adding a Profile](../developer/adding-profiles.md).

!!! note "Also available: the web UI"
    `telcontar --web` opens the same organize experience in a local browser tab instead of the Textual TUI, including its own first-run setup wizard at `/setup`. It's a foundational, still-in-progress alternative (ROADMAP Phase 18) — it doesn't have query mode or a journal/undo view yet, so those workflows still need the TUI described above. `telcontar --web --target PATH` skips its landing page's directory picker and jumps straight to a run for `PATH`.
