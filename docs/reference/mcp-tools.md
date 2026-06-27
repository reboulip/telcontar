# MCP Tools Reference

Complete reference for all tools exposed by the telcontar MCP server. Tools are grouped by safety category and call order in the agent workflow.

The server registers tools via FastMCP (`server/main.py`); the implementations live in `server/tools.py`.

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

Extract plain text from a PDF or Office file (docx, xlsx, pptx…) via **markitdown**. Same truncation semantics as `read_file`.

---

### `compute_checksum`

```python
compute_checksum(path: str) -> dict
```

Compute the sha256 checksum of a file (chunk-streamed, memory-safe). This checksum is used as the document's unique identity in the registry.

**Returns:** `{path, checksum}` — `checksum` is a 64-character hex string.

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
| `missing_sources` | list | Missing file entries `{op_id, op_type, src}` |
| `is_valid` | bool | True when no duplicates and no missing sources |

Does not modify the plan.

---

### `approve_plan`

```python
approve_plan(plan_id: str) -> dict
```

Transition a plan from `pending` → `approved`. Must be called before `execute_plan`. The host calls this automatically after the user approves in the modal.

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

Stage moving `path` into `dest_dir`. Raises `FileExistsError` if `dest_dir/filename` already exists. Raises `ValueError` if `dest_dir` is not an existing directory.

---

### `propose_quarantine`

```python
propose_quarantine(path: str, plan_id: str) -> dict
```

Stage moving `path` to `QUARANTINE_DIR`. Unlike `propose_rename` and `propose_move`, collision is handled by **suffixing** the destination name (e.g. `report_1.pdf`, `report_2.pdf`) rather than raising — quarantine should never block.

---

## Gated execution tools

Execute operations or write output. Subject to `APPROVAL_MODE`.

### `execute_plan`

```python
execute_plan(plan_id: str) -> dict
```

Apply all operations in an `approved` plan.

- Each op is retried up to **3 times** on transient OS errors
- Non-retryable errors (`ValueError`, `FileNotFoundError`, `FileExistsError`) fail immediately
- More than **3 cumulative failures** trigger a **hard stop** — execution halts, a `hard_stop` entry is appended to the journal, and the plan transitions to `stopped`
- On success, each op is appended to the undo journal and the registry is path-reconciled

**Returns:**

| Field | Type | Description |
|---|---|---|
| `plan_id` | str | UUID |
| `state` | str | Final plan state (`done`, `failed`, `stopped`) |
| `ops_completed` | int | Successfully executed ops |
| `ops_failed` | int | Failed ops |
| `hard_stop` | bool | True if execution was cut short |
| `ops` | list | Full op list with per-op status and error |

---

### `write_index`

```python
write_index(path: str) -> dict
```

Walk the directory at `path` and emit:

- `INDEX.md` — ASCII tree + changelog from the undo journal
- `manifest.json` — structured file metadata

Skips `INDEX.md`, `manifest.json`, and `SUMMARY.md` themselves from the tree.

**Returns:** `{index, manifest}` — absolute paths of the two files written.

---

### `write_summary`

```python
write_summary(path: str, content: str) -> dict
```

Write `content` (LLM-composed prose) to `SUMMARY.md` in the directory at `path`. The agent calls this after composing the summary narrative itself.

---

## Recovery tools

### `undo_last`

```python
undo_last() -> dict
```

Revert the most recent journaled operation by inverting it and removing the journal entry.

| `op_type` | Reversal action |
|---|---|
| `rename` | Rename back to original name |
| `move` | Move back to original directory |
| `quarantine` | Move back from quarantine to original path |
| `hard_stop` | Entry removed (no file operation needed; failed ops were never executed) |

Raises if the target path already exists (no-overwrite guarantee applies to undo as well).

**Returns:** `{undone: <original entry>}` on success, or `{undone: null, error: "..."}` on failure.

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

### `get_document`

```python
get_document(checksum: str) -> dict | None
```

Return a single registry record by checksum, or `null` if not found.

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

## Direct file utilities

Lower-level tools used for writing index/summary files. Not normally called directly by the agent.

### `move_file` / `rename_file`

```python
move_file(path: str, dest_dir: str) -> dict
rename_file(path: str, new_name: str) -> dict
```

Direct filesystem operations without plan staging. Both enforce `check_no_overwrite`.

### `create_file` / `update_file`

```python
create_file(path: str, content: str) -> dict   # raises if file exists
update_file(path: str, content: str) -> dict   # overwrites or creates
```

Write text content to disk. `create_file` enforces `check_no_overwrite`; `update_file` does not.

---

## Tool availability by version

| Tool | v0.2 | v0.3 | v0.4 | v0.5 | v0.6 |
|---|---|---|---|---|---|
| `list_dir`, `read_file`, `extract_text` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `move_file`, `rename_file`, `create_file`, `update_file` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `compute_checksum` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `create_plan`, `get_plan`, `list_plans`, `approve_plan` | — | ✓ | ✓ | ✓ | ✓ |
| `propose_rename`, `propose_move`, `propose_quarantine` | — | ✓ | ✓ | ✓ | ✓ |
| `execute_plan`, `review_plan`, `undo_last` | — | ✓ | ✓ | ✓ | ✓ |
| `record_document`, `get_document`, `list_documents` | — | — | ✓ | ✓ | ✓ |
| `get_registry`, `find_duplicates`, `find_modified_documents` | — | — | ✓ | ✓ | ✓ |
| `write_index`, `write_summary` | — | — | — | ✓ | ✓ |
