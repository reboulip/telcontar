# Quick Start

This walkthrough organizes a sample directory from scratch. It assumes you have completed [Installation](installation.md).

---

## 1. Launch telcontar

```bash
telcontar
```

Telcontar opens in its own native window (via `pywebview`, Windows only) rather than a browser tab — pass `--browser` to always use the system browser instead, or telcontar falls back to it automatically (with a warning) if `pywebview` isn't installed or the platform isn't Windows.

---

## 2. First-run setup wizard

If this is your first time running telcontar, the **setup wizard** appears automatically (`/setup`). It takes about two minutes.

**Welcome** — a short explanation of what telcontar needs: the web address of your AI service, and an API key.

**Step 1 — Choose your AI service:**

Pick **Any OpenAI-compatible service** (Mammouth, OpenAI, a local inference server, etc.) or **Azure OpenAI**, if that's what you use.

**Step 2 — Enter your details:**

Paste the service URL and your API key. The key is stored securely in your OS credential store (Windows Credential Manager or macOS Keychain) — it never touches a plain text file.

**Step 3 — Choose your document type:**

Pick the vocabulary that best matches what you'll organize:

- **IS/IT project** — technical and business documents
- **Personal files** — invoices, contracts, administrative records
- **Research papers** — academic and scientific articles

The wizard saves your settings and takes you straight to the startup page. You will not see it again on subsequent launches.

!!! tip
    You can update any of these settings at any time from **⚙ Settings** — reachable from the nav bar's **Settings** tab or the sidebar, on every page.

---

## 3. Choose a directory to organize

The startup page shows a browsable directory tree in the left sidebar, rooted at the directory you launched `telcontar` from (falling back to your home directory if that directory can't be read). Use the sidebar's "up one level" button or drive-root dropdown to browse elsewhere, then click the folder you want to organize — the sidebar highlights your selection. Then press **Use selected directory** to start an Organize run, or **Query** to open a read-only chat over an already-analyzed corpus.

!!! tip
    **Query** requires the selected folder, or a parent of it, to contain a `.organizer/` from a previous Organize run. Use it after a previous Organize run to ask natural-language questions without touching the files.

---

## 4. Review the overview and add instructions (optional)

The Organize run page opens on a **starter pane** instead of jumping straight into the agent loop. It shows a code-generated, deterministic overview of the target directory — no file content is read and no LLM call is made yet: target directory, file/subfolder counts, and the most common file types present.

Below that, an optional field invites steering instructions — e.g. "group by workstream", "keep the 2024 invoices together", "don't quarantine drafts". Type any instructions you want the agent to follow, or leave the field blank to let it use its own best judgement. Press **Start organizing** to leave the starter pane — the chat transcript appears and the agent loop begins. If you typed instructions, they appear as your first turn in the transcript and are passed along to the agent.

---

## 5. Watch the agent work

The run page shows the directory tree in the left sidebar (which live-updates as files are renamed, moved, or quarantined) and a chat transcript on the right, with a progress bar and status line above the chat box.

Before any chat turn appears, telcontar deterministically discovers and checksums the whole directory tree, silently — no LLM call, and no transcript turn for this step, just the **progress bar** (e.g. "12 / 47 documents analyzed", with the document currently being read). If it finds documents it hasn't seen before, it pauses once to show a cost estimate scoped to just those new documents (see [How It Works](../user-guide/how-it-works.md#the-cost-estimate-approval-gate)) before reading and analyzing them. `telcontar` narrates the steps that follow in plain language, one entry per macro-task (e.g. "Reading documents…", "Recording documents in memory…", "Checking for duplicates…") — interleaved right into the chat transcript, in the order things actually happened. Below that, a separate compact strip lists every individual tool call telcontar makes, one line each — each entry can be expanded to see the raw call and result behind it.

New documents are analyzed in isolated batches of 10 — read/extract, then one forced-tool LLM call returning a structured record per document, then recorded into the registry in one `record_document_batch` call per batch. Only once every new document is recorded does the chat loop begin; the agent (now working from a digest of the whole corpus, not raw file content) calls `find_duplicates` while planning to spot the copy.

---

## 6. Review and approve the plan

Once analysis is complete, the agent proposes a plan. A dialog opens showing the target directory tree **before** and **after** the plan, side by side — each proposed operation appears as a checked-by-default checkbox on the "after" side. Below the tree:

- **Uncheck** any operation you want to skip before approving.
- Type a description in the **refine** field and press **Refine** to send feedback and have the agent revise the plan (e.g. "merge the drafts into one folder", "don't quarantine the specs").
- **Reject** discards the plan; the agent starts over.
- **Approve** executes the checked operations immediately.

!!! tip
    Each operation is journaled. If something goes wrong after approval, open the **Journal** button (top of the run page, showing a live count of recorded operations) and press **Undo last operation** — undo is a manual, confirmed action, not something the agent can trigger itself.

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

A desktop notification fires when the agent is done. The chat box at the bottom of the run page has actually been live the whole time — you can type a message at any point during the run (e.g. "actually, group by year instead") and it gets woven in as soon as the agent is between turns, instead of waiting for the run to finish. Now that the run is done, it works the same way to continue the conversation — type a follow-up message (e.g. "quarantine the drafts too") to keep going with the same mutating toolset, without restarting the run. Two buttons appear once the run is done: **Query this corpus** opens a separate read-only query mode to ask questions about the corpus, and **Browse corpus** opens a table/detail view of every analyzed document.

---

## Next steps

- Adjust the approval level in **⚙ Settings** once you trust the agent — see [Approval Modes](../user-guide/approval-modes.md).
- Understand the output files: [Outputs](../user-guide/outputs.md).
- Learn how to create a profile for your own document corpus: [Adding a Profile](../developer/adding-profiles.md).
