# MCP Tools Reference

Complete reference for all tools exposed by the telcontar MCP server. Tools are grouped by safety category and call order in the agent workflow.

The server registers tools via FastMCP (`server/main.py`); the implementations live in `server/tools.py`.

!!! note "Error messages"
    Tools that touch disk (`read_file`, `compute_checksum`, `execute_plan`, `write_index`, `write_summary`, `write_folder_readme`) re-raise I/O failures as a clear `Could not <action> <path>: <detail>` message instead of a raw traceback, with a plain-language hint for the two operator-actionable cases: a locked file (`... the file is open in another program — close it and retry`) and a plain permission denial (`... permission denied`). The original exception *type* is preserved, so `execute_plan`'s retry/fail-fast classification (below) is unaffected. File writes (`create_file`, `update_file`, `create_dir`) are staged via `propose_*` calls and only touch disk inside `execute_plan` — there is no standalone tool for them.

!!! note "Path confinement"
    Every tool that takes a `path` (or `path_a`/`path_b`/`dest_dir`) argument is checked with `check_within_root` before it runs, and raises `PermissionError` if the resolved path falls outside both the run's `TARGET_DIR` and the server's own working directory. This applies whether the escape attempt is an absolute path or a `..` traversal. As of per-directory memory (P2), `.organizer/` and the quarantine dir themselves live *inside* `TARGET_DIR` for every real run, so that boundary is also where the run's own memory resides. For `read_file` / `extract_text` / `compare_documents` and their batch forms `read_file_batch` / `extract_text_batch`, `ALLOWLIST_DIRS` is also checked first via `Settings.effective_allowlist_dirs()` — an explicit, non-empty `ALLOWLIST_DIRS` is used as-is; otherwise it defaults to `[TARGET_DIR]` rather than no restriction — and `check_within_root` then applies as the always-on floor underneath it. In the batch forms, both checks run per path *before* that file is read/extracted, so one disallowed path in a batch surfaces as `{"error": ...}` for that entry rather than raising and failing the whole call. See [Security Model](../developer/security-model.md).

!!! note "Pre-analysis cost-approval gate (O8/P6, host-side)"
    Gated entirely by the **host**, not the server. As of P6, a fresh organize run first runs a deterministic pre-pass (`run_prepass`) that walks the whole corpus and checksums every file via `compute_checksum_batch` — this call is unconditional and never gated, since it's needed just to tell already-known documents from new ones. If that partition finds any new documents, `host/agent.py` computes a token estimate scoped to ONLY those new documents' sizes and — unless `APPROVAL_MODE=never` — awaits a one-time user approval (`CostEstimateModal`) before running the stateless analyzer, which is what actually calls `extract_text_batch`/`read_file_batch` (to fetch content) and `record_document_batch` (to persist results) for the new documents. A rejection skips the analyzer for this run — the new documents are neither fetched nor recorded. The gate fires at most once per run and is skipped entirely (no event, no callback) when there are no new documents. The MCP server itself has no awareness of this gate — see [Architecture § Pre-analysis cost-approval gate (O8/P6)](../developer/architecture.md#pre-analysis-cost-approval-gate-o8p6).

!!! note "ORGANIZE-mode tool denylist (P6)"
    The corpus is fully analyzed by the pre-pass + analyzer described above BEFORE the ORGANIZE turn loop starts, so the ORGANIZE-phase model's own toolset structurally excludes `read_file`, `extract_text`, `read_file_batch`, `extract_text_batch`, `compute_checksum`, `compute_checksum_batch`, `record_document`, `record_document_batch`, `compare_documents`, `lookup_documents`, and `rehome_documents` (`ORGANIZE_DENIED_TOOLS` in `host/agent.py`) — content-fetching/recording tools it has no legitimate reason to call again. This is a denylist, not just a prompt instruction: none of these tools are advertised to the model in ORGANIZE mode, and a hallucinated call to one of them is rejected with an explicit error regardless. Query mode is unaffected by this denylist — `read_file`, `extract_text`, `compare_documents`, `compute_checksum`, and their batch forms remain available there via `QUERY_ALLOWED_TOOLS`.

---

## Read-only tools

Safe — inspect the filesystem without modification. May run without approval in any `APPROVAL_MODE`.

### `list_dir`

```python
list_dir(path: str) -> dict
```

Enumerate directory entries with metadata.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `path` | str | Absolute path to the directory |

**Returns:** `{path, entries}` where each entry is `{name, path, type, size, mtime}`.

- `type`: `"dir"` | `"file"` | `"unknown"`
- `size`: bytes (int, or `null` on permission error)
- `mtime`: Unix timestamp (float, or `null`)

---

### `walk_tree`

```python
walk_tree(path: str, max_depth: int = 3) -> dict
```

Recursively enumerate a directory tree up to `max_depth` levels deep. Complements `list_dir` (a single level): during ANALYZE, the agent surveys nested subfolders in one call and may redesign the whole existing layout, not just the top level. Depth is counted from the root — the root's immediate entries are depth 1. Raises `ValueError` if `max_depth < 1`.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `path` | str | Absolute path to the root directory |
| `max_depth` | int | Maximum depth to descend (default 3) |

**Returns:** `{path, max_depth, entries}` where each entry is `{name, path, type, size, mtime}`, and directory entries additionally carry:

- `children`: nested list of entries in this same shape, until `max_depth` is reached
- `truncated`: `false` while `children` is populated; `true` once `max_depth` is reached, in which case `children` is `null` — call `walk_tree` again on that subpath to descend further

Files carry `size`/`mtime` like `list_dir`; unreadable entries are marked `type: "unknown"` with `size`/`mtime` set to `null`.

The MCP server wrapper always excludes `.organizer` and the configured quarantine folder from the results, at every depth (P2) — so the agent never sees, and can never propose moving or quarantining, its own memory. This is discovery-hiding only, not a security guard: `list_dir` (the single-level tool) is not filtered the same way, and the exclusion doesn't change what `check_within_root` allows.

---

### `read_file`

```python
read_file(path: str, max_chars: int = 4000) -> str
```

Return the UTF-8 text content of a file, capped at `max_chars`. Binary replacement characters are used for non-decodable bytes. Truncation is marked with `[... content truncated ...]`.

!!! note
    The effective cap is `min(max_chars, MAX_SNIPPET_CHARS)` — the server enforces `MAX_SNIPPET_CHARS` as a hard ceiling regardless of what the agent requests.

---

### `extract_text`

```python
extract_text(path: str, max_chars: int = 4000) -> str
```

Extract plain text from a PDF or Office file (docx, xlsx, pptx…) via **markitdown**, or from an Outlook `.msg` file via **extract-msg**. Same truncation semantics as `read_file`.

For a `.msg` file, the returned text is the message headers (`From`/`To`/`Cc`/`Bcc` when present/`Date`/`Subject`) followed by a blank line and the plain-text body, rather than markitdown's lossy conversion.

!!! note "Bounded extraction (S5)"
    Before parsing, the file is rejected with `ValueError` if it exceeds `MAX_EXTRACT_FILE_BYTES` (default 200,000,000 bytes), and for zip-based formats (`.docx`/`.xlsx`/`.pptx`/`.zip`) any archive entry with a compressed:uncompressed ratio over 100x (and an uncompressed size ≥ 10MB) is rejected as a possible zip bomb (`.msg` is an OLE compound file, not a zip container, so this check does not apply to it). The actual parse (`markitdown`, or `extract-msg` for `.msg`) then runs under a `MAX_EXTRACT_TIMEOUT_SECS` wall-clock timeout (default 30s, thread-based so it also works on Windows), raising `TimeoutError` on expiry. This bounds the known DoS/zip-bomb vectors — it is not a sandbox; see [Security Model](../developer/security-model.md).

---

### `compare_documents`

```python
compare_documents(path_a: str, path_b: str, max_chars: int = 4000) -> dict
```

Extract text from two files and return a unified diff between them. Uses the same extraction path as `extract_text`, so it works on PDF, Office, and Outlook `.msg` files as well as plain text. Each side is truncated to `max_chars` before diffing; the diff therefore reflects only the extracted (possibly truncated) text.

Typical use case: comparing successive versions of a document (e.g. two COPIL slide decks).

!!! note
    The effective cap per side is `min(max_chars, MAX_SNIPPET_CHARS)`. Both paths are checked against `ALLOWLIST_DIRS` (via `effective_allowlist_dirs()` — defaults to `[TARGET_DIR]` when unset) and then against the `TARGET_DIR`/server-cwd confinement before extraction. Each side's extraction is bounded the same way as `extract_text` — see the "Bounded extraction (S5)" note above.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `path_a` | str | Absolute path to the first file |
| `path_b` | str | Absolute path to the second file |
| `max_chars` | int | Maximum characters to extract per side (default 4000) |

**Returns:**

| Field | Type | Description |
|---|---|---|
| `path_a` | str | Absolute path of the first file |
| `path_b` | str | Absolute path of the second file |
| `identical` | bool | `true` when the extracted texts match exactly |
| `diff` | str | Unified diff string (empty when `identical` is `true`) |

**Safety category:** Read-only — no filesystem writes.

!!! note "Not available in ORGANIZE mode (P6)"
    `compare_documents` is in `ORGANIZE_DENIED_TOOLS` — the ORGANIZE-phase model cannot call it, since the corpus (including duplicate/version comparison via `find_duplicates`/`find_modified_documents`) was already analyzed before the loop starts. It remains available in query mode via `QUERY_ALLOWED_TOOLS`.

---

### `compute_checksum`

```python
compute_checksum(path: str) -> dict
```

Compute the sha256 checksum of a file (chunk-streamed, memory-safe). This checksum is used as the document's unique identity in the registry.

**Returns:** `{path, checksum}` — `checksum` is a 64-character hex string.

---

### `read_file_batch`

```python
read_file_batch(paths: list[str], max_chars: int = 4000) -> dict
```

Batch form of `read_file`: fetch text for many files in one MCP round trip instead of one call per file — the basis for analyzing several documents in a single LLM turn.

**Returns:** `{path: content | {"error": message}}`, keyed by the exact path strings passed in. A failure reading one file never fails the whole batch — the caller must discriminate a successful entry (a `str`) from a failed one (a `{"error": ...}` dict).

!!! note
    Each path gets the same `ALLOWLIST_DIRS` (`effective_allowlist_dirs()`) and `check_within_root` checks as `read_file`, applied individually before that file is read; a path that fails either check never reaches `read_file` and appears in the result as `{"error": ...}` directly, without failing the other paths in the batch. The effective cap per file is `min(max_chars, MAX_SNIPPET_CHARS)`, same as `read_file`.

!!! note "Second caller (P5/P6)"
    As of the stateless per-batch analyzer (P5), this tool also has a second caller: `host/agent.py`'s `_fetch_batch_content`, invoked directly by host code (not by the model's own tool-calling decision) for the non-extractable files in a batch of newly-discovered documents. Same tool, same server-side guards and egress logging — just a new, host-orchestrated caller. As of P6, this analyzer is wired into `run_agent_loop`'s pre-loop analysis stage (after the deterministic pre-pass, before the ORGANIZE turn loop), and `ORGANIZE_DENIED_TOOLS` excludes `read_file_batch` from the ORGANIZE-phase model's own toolset — so in a live organize run, this host-orchestrated call is now the *only* way this tool is ever reached; the model itself can only reach it via query mode (`QUERY_ALLOWED_TOOLS`).

---

### `extract_text_batch`

```python
extract_text_batch(paths: list[str], max_chars: int = 4000) -> dict
```

Batch form of `extract_text`: extract text from many PDF/Office/Outlook `.msg` files in one round trip. Same return shape, per-path guard checks, and error semantics as `read_file_batch`. Each file's extraction is bounded the same way as the singular `extract_text` — see "Bounded extraction (S5)" above.

**Returns:** `{path: content | {"error": message}}`, keyed by the exact path strings passed in.

!!! note "Second caller (P5/P6)"
    Same as `read_file_batch` above: `_fetch_batch_content` (`host/agent.py`) also calls this directly, host-side, for the extractable files (`.pdf`/`.docx`/`.xlsx`/`.pptx`/`.msg`) in a stateless-analyzer batch — a file-type dispatch that used to be left entirely to the model's own judgement during the in-loop ANALYZE phase. As of P6, this analyzer is wired into `run_agent_loop` and `ORGANIZE_DENIED_TOOLS` excludes `extract_text_batch` from the ORGANIZE-phase model's own toolset, so in a live organize run this host-orchestrated call is now the *only* way this tool is ever reached; the model itself can only reach it via query mode (`QUERY_ALLOWED_TOOLS`).

---

### `compute_checksum_batch`

```python
compute_checksum_batch(paths: list[str]) -> dict
```

Batch form of `compute_checksum`: sha256 for many files in one round trip.

**Returns:** `{path: checksum_hex | {"error": message}}`, keyed by the exact path strings passed in. Unlike `read_file_batch`/`extract_text_batch`, a successful entry is the checksum hex string directly, not a `{path, checksum}` dict (matching what the singular `compute_checksum` nests under its `checksum` field).

!!! note "Second caller (P4/P6)"
    `host/agent.py`'s `run_prepass` calls this tool directly, host-side, to checksum the WHOLE discovered corpus (not just new documents) as part of deterministic pre-analysis discovery — this call is unconditional and never subject to the cost-approval gate, since it's needed just to tell already-known documents from new ones. `ORGANIZE_DENIED_TOOLS` excludes `compute_checksum_batch` from the ORGANIZE-phase model's own toolset, so in a live organize run this host-orchestrated call is the *only* way this tool is ever reached; the model itself can only reach it via query mode (`QUERY_ALLOWED_TOOLS`).

---

## Plan management tools

Tools that create, inspect, and transition plans without executing file operations.

### `create_plan`

```python
create_plan() -> dict
```

Create a new, empty plan in the `pending` state. Returns the full plan dict including a fresh `plan_id` (UUID).

---

### `get_plan`

```python
get_plan(plan_id: str) -> dict
```

Load and return a plan by its UUID. Includes all staged ops with their current status.

---

### `list_plans`

```python
list_plans() -> list[dict]
```

Return all persisted plans sorted by `created_at` (oldest first).

---

### `review_plan`

```python
review_plan(plan_id: str) -> dict
```

Read-only pre-flight check. Detects:

- **Duplicate ops** — same `(src, op_type)` pair proposed more than once
- **Missing sources** — `src` paths that no longer exist on disk

**Returns:**

| Field | Type | Description |
|---|---|---|
| `plan_id` | str | UUID of the plan |
| `total_ops` | int | Total ops in the plan |
| `duplicates` | list | Duplicate op groups `{src, op_type, op_ids}` |
| `missing_sources` | list | Missing file entries `{op_id, op_type, src}` — `create_dir` ops are exempt, since their `src` is the not-yet-created destination directory rather than a path expected to already exist |
| `is_valid` | bool | True when no duplicates and no missing sources |

Does not modify the plan.

---

### `approve_plan`

```python
approve_plan(plan_id: str) -> dict
```

Transition a plan from `pending` → `approved`. Must be called before `execute_plan`. The host calls this automatically after the user approves in the modal.

---

### `set_plan_rationale`

```python
set_plan_rationale(plan_id: str, rationale: str) -> dict
```

Attach the agent's plain-language rationale to a plan — a short paragraph explaining the plan's philosophy (how documents were grouped, renamed, and quarantined, and why). The host's `ApprovalModal` displays it above the op checklist when the user reviews the plan. The agent is expected to call this after `review_plan` and before `execute_plan`.

Passing an empty or whitespace-only `rationale` clears it (stored as `""`). Not itself gated by `APPROVAL_MODE` — it only mutates plan metadata, no filesystem write.

**Returns:** the full updated plan dict (same shape as `get_plan`).

**Safety category:** Plan-building / plan-mutation — writes to the plan file on disk but performs no filesystem operation outside `.organizer/plans/`.

---

### `set_plan_folder_notes`

```python
set_plan_folder_notes(plan_id: str, notes: dict) -> dict
```

Attach agent-supplied per-folder purpose notes to a plan. `notes` maps a target folder path to a short one-line purpose note (e.g. `{"01_decisions": "Formal decision records", "_quarantine": "Duplicates and superseded drafts"}`). The host's `ApprovalModal` renders these beside each folder in the plan's target-layout tree preview, between the rationale and the op checklist. The agent is expected to call this after `set_plan_rationale`, as part of the same organize-phase step.

Non-string keys/values are coerced to `str`; folder keys or notes that are blank after stripping are dropped. A target folder with no matching note simply renders as a bare tree node — the tree itself is derived from the plan's `move`/`quarantine` op destinations, not from `folder_notes`.

**Returns:** the full updated plan dict (same shape as `get_plan`), including `folder_notes`.

**Safety category:** Plan-building / plan-mutation — writes to the plan file on disk but performs no filesystem operation outside `.organizer/plans/`.

---

## Plan-building tools

Append proposed file operations to an existing `pending` plan. Each call performs an eager collision check at proposal time — no operation will overwrite an existing file.

### `propose_rename`

```python
propose_rename(path: str, new_name: str, plan_id: str) -> dict
```

Stage a rename of `path` to `new_name` (basename only, not a full path). Raises `FileExistsError` if `{parent}/{new_name}` already exists.

**Returns:** `{plan_id, op_id, op_type, src, dst, status, ops_count}`

---

### `propose_move`

```python
propose_move(path: str, dest_dir: str, plan_id: str) -> dict
```

Stage moving `path` into `dest_dir`. Raises `FileExistsError` if `dest_dir/filename` already exists. Raises `ValueError` if `dest_dir` is not an existing directory **and** no `propose_create_dir` op for that exact path is already queued earlier in the same pending plan — this lets the agent propose "create a folder, then move a file into it" within a single plan, regardless of the two ops' relative order: `execute_plan` runs every `create_dir` op before any other op type, so the dependent `move` always finds its destination already created (and self-heals via its own `mkdir` even if it doesn't).

---

### `propose_quarantine`

```python
propose_quarantine(path: str, plan_id: str, reason: str = "") -> dict
```

Stage moving `path` to `QUARANTINE_DIR`. Unlike `propose_rename` and `propose_move`, collision is handled by **suffixing** the destination name (e.g. `report_1.pdf`, `report_2.pdf`) rather than raising — quarantine should never block. `reason` (V10) is a short, concrete justification — duplicate of X, superseded by Y, unreadable *and* superfluous, etc.; the system prompt no longer accepts "unreadable" alone as sufficient. It is stored on the op and shown beside the file at approval time (a blank reason renders as "no reason given" rather than looking indistinguishable from a justified one); the server itself does not validate or require it.

---

### `propose_create_file`

```python
propose_create_file(path: str, content: str, plan_id: str) -> dict
```

Stage writing a brand-new file at `path` with `content`. Raises `FileExistsError` if `path` already exists — eagerly at proposal time, and again at execution time in case a file appears in between.

**Returns:** `{plan_id, op_id, op_type, src, dst, status, ops_count}` — `dst` is always `""` (the op has no destination distinct from `src`); the file content travels in the op's internal `params`, not in the returned dict.

---

### `propose_update_file`

```python
propose_update_file(path: str, content: str, plan_id: str, overwrite: bool = False) -> dict
```

Stage writing `content` to `path`, whether or not it already exists. Refuses to replace an existing file unless `overwrite=True` is passed explicitly — pass it only for a deliberate overwrite; the flag is visible to the user at approval. The same check is re-applied at execution time, so a file that appears between proposal and execution is still caught.

**Returns:** same shape as `propose_create_file`.

---

### `propose_create_dir`

```python
propose_create_dir(path: str, plan_id: str) -> dict
```

Stage creating a directory (and any missing parents) at `path`. Idempotent and collision-safe at execution time — creating a directory that already exists is a no-op, not an error. Raises `ValueError` at proposal time if `path` already exists as a file.

**Returns:** same shape as `propose_create_file`.

---

### `propose_archive_document`

```python
propose_archive_document(checksum: str, plan_id: str, reason: str = "") -> dict
```

Stage withdrawing a document from active memory ("retirer de la mémoire"). Eagerly validates that `checksum` is recorded in the registry — raises `ValueError` if not. At `execute_plan` time the op reuses the standalone `archive_document()` logic (see [Archived-documents journal tools](#archived-documents-journal-tools)): the document's file, if it still exists, is moved to `QUARANTINE_DIR` (collision-safe) and the move is recorded in the **undo journal**; the registry record's `status` is flipped to `archived`; an entry is appended to the archive log (readable via `list_archived`). The document is never deleted.

**Returns:** `{plan_id, op_id, op_type, src, dst, status, ops_count}` — `src` is the document's currently recorded path; `dst` is its precomputed quarantine destination, or `""` if the file is already gone from disk.

---

### `propose_compress_quarantine`

```python
propose_compress_quarantine(plan_id: str, delete_originals: bool = True) -> dict
```

Stage losslessly bundling all loose top-level files in `QUARANTINE_DIR` into a single timestamped, verified ZIP archive, optionally deleting the originals afterward to reclaim space. A single global op — not tied to one file.

At `execute_plan` time the op reuses the standalone `compress_quarantine()` function:

1. Collects every regular file at the top level of `QUARANTINE_DIR` (skipping any archive this tool already produced — files matching `quarantine_*.zip`).
2. Computes a sha256 checksum for each source file and writes them all into a new `quarantine_<UTC timestamp>.zip` (ZIP_DEFLATED) alongside a `_telcontar_manifest.json` recording each file's name and checksum.
3. Verifies the archive byte-for-byte (`testzip` CRC check + per-file re-hash) before touching any original — verification failure raises `OSError` and no originals are deleted.
4. Only if `delete_originals` is `True` (the default) are the source files then deleted.
5. Appends a `compress` entry to the undo journal — self-journaling, distinct from the generic per-op journal entry `execute_plan` writes for other op types.

Idempotent: a run with no loose files is a no-op. Never overwrites an existing archive (collision-safe naming appends `_1`, `_2`, …).

**Returns:** `{plan_id, op_id, op_type, src, dst, status, ops_count}` — `src` is `QUARANTINE_DIR`, `dst` is `""`. The created archive's path is not part of `execute_plan`'s return value; inspect `QUARANTINE_DIR` (e.g. via `list_dir`) or the undo journal to find it.

---

## Gated execution tools

Execute operations or write output. `execute_plan` is the sole tool subject to `APPROVAL_MODE` — it applies every kind of staged op (`rename`, `move`, `quarantine`, `create_file`, `update_file`, `create_dir`, `archive_document`, `compress_quarantine`), and is gated in `always` and `destructive_only`, auto-approved in `never`. As of the plan-flow security hardening (M1), **every filesystem-mutating tool is staged via a `propose_*` call and applied only through `execute_plan`** — there is no tool left that mutates the filesystem directly. `write_index`, `write_summary`, and `write_folder_readme` write output directly and are never gated, in any mode.

### `execute_plan`

```python
execute_plan(plan_id: str) -> dict
```

Apply all operations in an `approved` plan.

- Each op is retried up to **3 times** on transient OS errors
- Non-retryable errors (`ValueError`, `FileNotFoundError`, `FileExistsError`) fail immediately
- More than **3 cumulative failures** trigger a **hard stop** — execution halts, a `hard_stop` entry is appended to the journal, and the plan transitions to `stopped`
- On success, each op is appended to the undo journal and the registry is path-reconciled
- `archive_document` and `compress_quarantine` ops reuse the standalone functions of the same name, which self-journal under their own `op_type` (`quarantine` and `compress` respectively) instead of the generic per-op entry `execute_plan` writes for other op types
- Ops chained within the same run resolve correctly: if an earlier op already relocated a file (e.g. a `rename` followed by a `move` on the same original path), the later op is applied to the file's current location, not its original path
- Execution order is not strictly plan order: all `create_dir` ops run first (each group keeping its authored relative order), then every other op type — so a `move` into a not-yet-existing folder always finds it created regardless of how the two ops were interleaved when proposed. The `move` executor also creates its destination directory itself (`mkdir(parents=True, exist_ok=True)`) before checking for collision, as a second line of defense. Neither the persisted plan file nor the approval-modal display order is affected — only this run's internal iteration order

**Returns:**

| Field | Type | Description |
|---|---|---|
| `plan_id` | str | UUID |
| `state` | str | Final plan state (`done`, `failed`, `stopped`) |
| `ops_completed` | int | Successfully executed ops |
| `ops_failed` | int | Failed ops |
| `hard_stop` | bool | True if execution was cut short |
| `ops` | list | Full op list with per-op status and error |

**Safety category:** Gated execution — the host routes this call through the approval callback in `always` and `destructive_only`; in `never` it is auto-approved. This is the only tool ever subject to `APPROVAL_MODE`.

---

### `write_index`

```python
write_index(path: str) -> dict
```

Walk the directory at `path` and emit:

- `INDEX.md` — ASCII tree + changelog from the undo journal
- `manifest.json` — structured file metadata

Skips `INDEX.md`, `manifest.json`, `SUMMARY.md`, and `.organizer` (the run's own memory, P2) from the tree. The quarantine folder is deliberately **not** skipped — it stays visible in the written `INDEX.md` so a human reviewing results can see it; only agent-facing discovery (`walk_tree`) hides it.

**Returns:** `{index, manifest}` — absolute paths of the two files written.

---

### `write_summary`

```python
write_summary(path: str, content: str) -> dict
```

Write `content` (LLM-composed prose) to the active output sink(s) declared in the profile's `[sinks] default` list. The agent calls this after composing the summary narrative itself.

The built-in `local_markdown` sink persists the content as `SUMMARY.md` in the directory at `path`. External sinks are gated behind `EGRESS_ALLOW_EXTERNAL_SINKS` and are provided as separate MCP integrations — they are not built into this codebase.

**Returns:** When a single sink is active, returns that sink's result dict directly. When multiple sinks are active, returns `{"sinks": [<result per sink>, ...]}`.

**Safety category:** Writes to disk and/or external destinations. Not gated by `APPROVAL_MODE` — runs immediately in every mode.

---

### `write_folder_readme`

```python
write_folder_readme(path: str, content: str) -> dict
```

Write `content` (LLM-composed prose) to the active output sink(s) declared in the profile's `[sinks] default` list. Called once per meaningful folder during the SYNTHESIZE phase — the agent composes one or two paragraphs naming what the folder holds and its role in the organized tree, drawn from the documents recorded there.

The built-in `local_markdown` sink writes to `README.md` inside the folder at `path`. Behaviour of the local sink:

- Overwrites any existing `README.md` in the folder (idempotent re-runs safe)
- Creates the folder and any missing parent directories if they do not exist
- Skips empty or trivial folders — it is the agent's responsibility not to call this for them

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `path` | str | Absolute path to the folder that should receive a `README.md` (for the local sink) |
| `content` | str | Markdown prose composed by the LLM |

**Returns:** When a single sink is active, returns that sink's result dict directly (`{written}` for `local_markdown`). When multiple sinks are active, returns `{"sinks": [<result per sink>, ...]}`.

**Safety category:** Writes to disk and/or external destinations. Not gated by `APPROVAL_MODE` — runs immediately in every mode.

---

## Recovery

`undo_last` is **not** an MCP tool — it is not registered with the server, so the agent has no way to call it. As of the plan-flow security hardening (M1), undo is a direct, user-triggered action in the TUI only: the operations-journal viewer (`JournalScreen`, opened with the **j** key in the Organizer screen) has a **u** ("Undo last") keybinding that calls `server.tools.undo_last` directly, bypassing the MCP layer entirely.

`undo_last(journal_path, plans_dir) -> dict` reverts the most recent journaled operation by inverting it and removing the journal entry:

| `op_type` | Reversal action |
|---|---|
| `rename` | Rename back to original name |
| `move` | Move back to original directory |
| `quarantine` | Move back from quarantine to original path |
| `create_file` / `update_file` | Delete the file this op wrote (does not restore prior content an `overwrite=True` update replaced) |
| `create_dir` | No-op — idempotent by design, the directory is left in place |
| `hard_stop` | Entry removed (no file operation needed; failed ops were never executed) |
| `compress` | Each original file is restored from the archive into its recorded `src` path, then the zip is deleted. All targets are pre-checked for collisions before any file is written — a mid-way collision cannot leave files in a half-restored state. |

Raises if the target path already exists (no-overwrite guarantee applies to undo as well). For `compress` undo, if the archive file is missing and originals were deleted, an error is returned.

**Returns:** `{undone: <original entry>}` on success, or `{undone: null, error: "..."}` on failure.

See [Security Model](../developer/security-model.md) (finding S1) for why undo was moved out of the agent's reach, and [Plan Lifecycle](../developer/internals/plan-lifecycle.md) for the full reversal mechanics.

---

## Document registry tools

### `record_document`

```python
record_document(
    checksum: str,
    path: str,
    title: str,
    type: str,
    summary: str,
    provenance: str,
    date: str | None = None,
    entities: list[dict] | None = None,
    attributes: dict | None = None,
    status: str = "active",
) -> dict
```

Upsert an analyzed document into the registry. Validates `type` against the active profile's document type vocabulary and validates entity `role` values against the profile's role taxonomy.

`entities` is a list of `{name, role, kind}` dicts. `role` must be one of the profile's `role_taxonomy` values.

---

### `record_document_batch`

```python
record_document_batch(documents: list[dict]) -> dict
```

Batch form of `record_document`: upsert many analyzed documents into the registry in one MCP round trip instead of one call per document. Each item in `documents` has the same shape as `record_document`'s parameters (`checksum`, `path`, `title`, `type`, `summary`, `provenance`, `date`, `entities`, `attributes`, `status`). Validated with the exact same rules and error strings as `record_document` (shared via an internal `_validate_and_build_record` helper). One invalid document — bad `type`, bad entity `role`, or an entity missing `name` — never fails the whole batch: its error is collected instead of raised, keyed by its positional `index` in the input list (a failure may carry a missing or blank checksum/path, so position is the only safe correlation key).

**Returns:** `{"recorded": [record_dict, ...], "errors": [{"index", "checksum", "path", "error"}, ...]}`.

!!! note
    The registry is loaded once and saved once for the whole batch, not once per document — a deliberate efficiency trade-off; a mid-batch crash persists nothing. This differs from `read_file_batch`/`extract_text_batch`/`compute_checksum_batch`: this is a **mutating** tool, so it is *not* in `QUERY_ALLOWED_TOOLS` (query mode is read-only). Its per-document path confinement check also behaves differently from the read-only batch tools: `record_document_batch` runs `check_within_root` for every document's `path` *before* calling into `server.tools`, and a `PermissionError` on any one path raises immediately and aborts the whole call — it does not degrade to a per-item `{"error": ...}` entry the way a disallowed path does in `read_file_batch`/`extract_text_batch`/`compute_checksum_batch`.

!!! note "Second caller (P5/P6)"
    As of the stateless per-batch analyzer (P5), `host/agent.py`'s `_analyze_new_documents` also calls this tool — once per analyzed batch, with the model-derived fields (title/type/summary/provenance/date/entities) rejoined to host-authoritative `checksum`/`path` by positional index, never by any identifier the model itself returns. No new registry-write code was added for this; it reuses this same tool. As of P6, this analyzer is wired into `run_agent_loop`, and `ORGANIZE_DENIED_TOOLS` excludes `record_document_batch` from the ORGANIZE-phase model's own toolset — so in a live organize run this host-orchestrated call is now the *only* way this tool is ever reached (it was never in `QUERY_ALLOWED_TOOLS` either, being a mutating tool).

---

### `get_document`

```python
get_document(checksum: str) -> dict | None
```

Return a single registry record by checksum, or `null` if not found.

---

### `lookup_documents`

```python
lookup_documents(checksums: list[str]) -> dict
```

Batch form of `get_document`: look up many checksums against the registry in one round trip instead of one `get_document` call per checksum. Takes no `path` argument, so it needs no confinement/egress guard — it is a pure registry read, the same trust level as `get_document`/`list_documents` (P3).

**Returns:** `{checksum: record | null}`, keyed by the exact checksum strings passed in — mirrors `compute_checksum_batch`'s keyed-by-input contract.

---

### `rehome_documents`

```python
rehome_documents(paths: dict[str, str]) -> dict
```

Update registry records' recorded `path` directly by checksum (P4). `paths` maps `checksum -> new_path`. Unlike `execute_plan`'s automatic reconciliation (which matches a record by its file's *old* path after a `rename`/`move`/`quarantine` op), `rehome_documents` looks the record up directly by checksum and rewrites it — used by the deterministic host pre-pass (`host/agent.py`'s `run_prepass`) to reconcile a document whose on-disk location no longer matches what the registry has recorded (e.g. moved manually between runs), independently of any plan.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `paths` | dict[str, str] | Maps a document's checksum to its new on-disk path |

**Returns:** `{"updated": [checksum, ...], "missing": [checksum, ...]}` — `updated` lists every checksum that had a matching registry record (now rewritten); `missing` lists any checksum with no record.

!!! note
    Every value in `paths` (the new path) is checked with `check_within_root` before the call proceeds — same confinement floor as every other path-taking tool (see "Path confinement" above).

**Safety category:** Registry mutation — a pure metadata update (like `record_document`/`record_document_batch`), not a filesystem operation. No plan, no journal entry, and not gated by `APPROVAL_MODE`: it reconciles bookkeeping data, it never touches a file on disk.

---

### `list_documents`

```python
list_documents() -> list[dict]
```

Return all document records, oldest first (by `first_seen`).

---

### `get_registry`

```python
get_registry() -> dict
```

Return the entire registry as `{documents: {checksum: record, ...}}`. Useful for the agent to reason holistically over all analyzed documents.

---

### `find_duplicates`

```python
find_duplicates() -> list[list[dict]]
```

Return clusters of candidate duplicate documents. Two documents are clustered if:

- Their normalized titles are identical (exact match), **or**
- They share the same `type` and their title-token Jaccard similarity ≥ 0.6

Clusters have size > 1. The agent judges which to keep or quarantine — the server provides candidates, not verdicts.

---

### `find_modified_documents`

```python
find_modified_documents() -> list[list[dict]]
```

Return groups of documents sharing a normalized title but with **different checksums** (same content family, different versions). The agent uses this to identify the latest version and quarantine older ones.

---

## Archived-documents journal tools

Inspect the archive log — the durable record of *why a document left active memory*, distinct from the undo journal (reversible file ops) and the event journal (project narrative). Withdrawing a document is staged via [`propose_archive_document`](#propose_archive_document) (see Plan-building tools above) and only takes effect through `execute_plan` — `archive_document` itself is not directly callable by the agent.

### `list_archived`

```python
list_archived() -> list[dict]
```

Return all entries from the archive log at `ARCHIVE_PATH` in chronological (append) order. Returns an empty list if no documents have been archived yet.

**Returns:** list of `{checksum, title, reason, src, dst, archived_at}` records.

- `src`: original file path at the time of archiving
- `dst`: path in quarantine, or `null` if the file was not present
- `archived_at`: ISO 8601 UTC timestamp

---

## Event journal tools

Record and retrieve the project narrative log. Each event is a short, verb-led sentence stamped with the date it occurred. The event journal is distinct from the undo journal: it captures *what happened in the project*, not reversible file operations.

### `create_event`

```python
create_event(sentence: str, date: str | None = None) -> dict
```

Append a verb-led project event to the event journal at `EVENTS_PATH`. The file is created (including parent dirs) on first write.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `sentence` | str | Short, verb-led statement of the event (non-empty) |
| `date` | str \| None | ISO `YYYY-MM-DD` date the event occurred; `null` if unknown |

**Returns:** the full event record `{event_id, sentence, date, created_at}`.

- `event_id`: UUID (str)
- `created_at`: ISO 8601 UTC timestamp when the event was recorded

---

### `list_events`

```python
list_events() -> list[dict]
```

Return all recorded project events from `EVENTS_PATH` in chronological (append) order. Returns an empty list when no events have been recorded yet.

**Returns:** list of `{event_id, sentence, date, created_at}` records.

---

## Knowledge graph tools

A derived, reproducible projection of the document registry and event journal into a node/edge graph persisted at `GRAPH_PATH` (default `.organizer/graph.json`). The graph holds no independent state — it can be rebuilt at any time from the registry and events.

**Node kinds:**

| Kind | Id format | What it represents |
|---|---|---|
| `document` | `doc:{checksum}` | One node per registry record |
| `entity` | `entity:{normalized_name}` | Deduplicated person/org; carries the union of all roles it appears under across documents |
| `event` | `event:{event_id}` | One node per recorded project event |

**Edge types:**

| Type | Direction | Description |
|---|---|---|
| *(role value, e.g. `author`, `mentioned`)* | doc → entity | Links a document to each of its entities; `type` is the entity's role on that document |
| `co_occurrence` | entity ↔ entity | Connects pairs of entities that appear on the same document; `weight` = number of shared documents |
| `mentions` | event → entity | Links an event to any entity whose normalized name appears in the event sentence |

### `build_graph`

```python
build_graph() -> dict
```

Rebuild the knowledge graph from the current registry and event journal, persist it to `GRAPH_PATH`, and return the result. Safe to call repeatedly — each call produces a deterministic result and overwrites the previous file.

**Returns:** `{nodes, edges}` where each node is a dict with at least `{id, kind}` and each edge is `{src, dst, type}` (plus `weight` for `co_occurrence` edges).

---

### `get_graph`

```python
get_graph() -> dict
```

Return the most recently persisted graph without rebuilding it. Returns `{nodes: [], edges: []}` if `build_graph` has never been called.

**Returns:** `{nodes, edges}` — same shape as `build_graph`.

---

### `get_actors`

```python
get_actors() -> list[dict]
```

Return the project's main actors — entity nodes ranked by centrality, capped at the active profile's `salient_cap`. `build_graph` must be called first; if no graph has been persisted the list is empty.

**Ranking criteria** (applied in order, all ties break deterministically on lowercased name):

1. Number of documents referencing the entity (`document_count`) — primary signal
2. Total co-occurrence weight across all shared-document entity pairs (`cooccurrence_weight`)
3. Number of event sentences containing the entity's normalized name (`mention_count`)

**Returns:** list of actor dicts, most central first:

| Field | Type | Description |
|---|---|---|
| `id` | str | Node id (`entity:{normalized_name}`) |
| `name` | str | Display name as recorded in the registry |
| `entity_kind` | str | `"person"` or `"org"` (profile-defined) |
| `roles` | list[str] | Union of all roles this entity appears under across documents |
| `document_count` | int | Number of documents that reference this entity |
| `cooccurrence_weight` | int | Sum of co-occurrence edge weights involving this entity |
| `mention_count` | int | Number of event sentences that contain the entity's normalized name |

The list is capped at `salient_cap` from the active profile (`[entities]` section). A `salient_cap` of `0` or negative returns all actors without a cap.

---

## Tool availability by phase

| Tool | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 | Phase 7 | Phase 8 | Phase 9 |
|---|---|---|---|---|---|---|---|---|
| `list_dir`, `read_file`, `extract_text` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `compare_documents` | — | — | — | — | — | — | ✓ | ✓ |
| `compute_checksum` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `create_plan`, `get_plan`, `list_plans`, `approve_plan` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `propose_rename`, `propose_move`, `propose_quarantine` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `execute_plan`, `review_plan` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `record_document`, `get_document`, `list_documents` | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `get_registry`, `find_duplicates`, `find_modified_documents` | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `write_index`, `write_summary` | — | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| `write_folder_readme` | — | — | — | — | — | — | ✓ | ✓ |
| `create_event`, `list_events` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `build_graph`, `get_graph` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `get_actors` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `list_archived` | — | — | — | — | — | ✓ | ✓ | ✓ |

!!! note "Since removed / restructured (M1 security hardening)"
    This table predates the plan-flow gating change and does not carry phase columns for it. `move_file`, `rename_file`, `create_file`, `update_file`, `create_dir`, `archive_document`, `compress_quarantine`, and `undo_last` were removed from the agent-callable surface entirely. Their functionality now goes through `propose_create_file`, `propose_update_file`, `propose_create_dir`, and `propose_archive_document`/`propose_compress_quarantine` (staged like every other op, applied only via `execute_plan`); `undo_last` is now a TUI-only user action (see [Recovery](#recovery) above).
