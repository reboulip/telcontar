# Telcontar

Telcontar is a locally-run, profile-driven document-intelligence engine. Point it at a messy directory, and it will:

1. **Analyse** each file — extract title, date, document type, summary, and key people via GPT-5
2. **Organize** the tree — rename files to readable names, move them to sensible locations, quarantine clutter
3. **Synthesize** — produce `INDEX.md`, `manifest.json`, and a narrative `SUMMARY.md`

All file I/O stays on your machine. Only the text content needed for reasoning is sent to the LLM endpoint (Azure OpenAI GPT-5 or any OpenAI-compatible API).

---

## Core concepts

| Concept | What it is |
|---|---|
| **MCP server** | A Python process exposing guarded file tools over the Model Context Protocol |
| **MCP host** | A Textual TUI that drives the GPT-5 agent loop and handles user approval |
| **Domain profile** | A TOML file externalizing everything corpus-specific: types, entities, naming convention |
| **Plan** | A list of proposed file operations that the user reviews and approves before execution |
| **Registry** | A content-addressed document memory (sha256 → metadata), keyed so records survive renames |

---

## Navigation

<div class="grid cards" markdown>

- **[Getting Started](getting-started/installation.md)**

    Install, configure, and run telcontar on your first directory in under 10 minutes.

- **[User Guide](user-guide/how-it-works.md)**

    Understand the agent loop, approval flow, domain profiles, and output files.

- **[MCP Tools Reference](reference/mcp-tools.md)**

    Complete reference for all tools exposed by the MCP server, grouped by safety category.

- **[Developer Guide](developer/architecture.md)**

    Internals, module-by-module breakdown, and how to extend or contribute.

</div>

---

## Quick example

```bash
# Install
uv tool install git+https://github.com/rreboulleau/telcontar.git

# Run (first launch opens the setup wizard)
organizer-host
```

The setup wizard guides you through entering your API key (stored securely in the OS credential store). The TUI then asks for a target directory, analyses it, and shows a plan of file operations for your approval before anything is moved.

---

## Design principles

- **Safety first.** Nothing is ever deleted — clutter goes to a quarantine folder. Every destructive operation is journaled and reversible via `undo_last`.
- **Local execution.** File I/O never leaves the machine; only content snippets go to the model.
- **Profile-driven.** Swap corpus types (IS/IT project, legal, personal archive…) by pointing at a different TOML file — no code changes.
- **One language, one toolchain.** Python + uv end to end.
