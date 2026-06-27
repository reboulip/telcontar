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

### `compare_documents`

```python
compare_documents(path_a: str, path_b: str, max_chars: int = 4000) -> dict
```

Extract text from two files and return a unified diff between them. Uses the same markitdown/pypdf extraction path as `extract_text`, so it works on PDF and Office files as well as plain text. Each side is truncated to `max_chars` before diffing; the diff therefore reflects only the extracted (possibly truncated) text.

Typical use case: comparing successive versions of a document (e.g. two COPIL slide decks).

!!! note
    The effective cap per side is `min(max_chars, MAX_SNIPPET_CHARS)`. Both paths are checked against `ALLOWLIST_DIRS` before extraction.

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

Write `content` (LLM-composed prose) to the active output sink(s) declared in the profile's `[sinks] default` list. The agent calls this after composing the summary narrative itself.

The built-in `local_markdown` sink persists the content as `SUMMARY.md` in the directory at `path`. External sinks are gated behind `EGRESS_ALLOW_EXTERNAL_SINKS` and are provided as separate MCP integrations — they are not built into this codebase.

**Returns:** When a single sink is active, returns that sink's result dict directly. When multiple sinks are active, returns `{"sinks": [<result per sink>, ...]}`.

**Safety category:** Gated execution (writes to disk and/or external destinations). Subject to `APPROVAL_MODE`.

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

**Safety category:** Gated execution (writes to disk and/or external destinations). Subject to `APPROVAL_MODE`.

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
| `compress` | Each original file is restored from the archive into its recorded `src` path, then the zip is deleted. All targets are pre-checked for collisions before any file is written — a mid-way collision cannot leave files in a half-restored state. |

Raises if the target path already exists (no-overwrite guarantee applies to undo as well). For `compress` undo, if the archive file is missing and originals were deleted, an error is returned.

**Returns:** `{undone: <original entry>}` on success, or `{undone: null, error: "..."}` on failure.

---

### `compress_quarantine`

```python
compress_quarantine(delete_originals: bool = True) -> dict
```

Losslessly bundle all loose top-level files in `QUARANTINE_DIR` into a single timestamped ZIP_DEFLATED archive and (optionally) reclaim space by deleting the originals.

**What it does:**

1. Collects every regular file at the top level of `QUARANTINE_DIR`, skipping any archive this tool already produced (files matching `quarantine_*.zip`).
2. Computes a sha256 checksum for each source file.
3. Writes all files into a new `quarantine_<UTC timestamp>.zip` (ZIP_DEFLATED) together with a `_telcontar_manifest.json` inside the archive recording each file's name and sha256.
4. Verifies the archive byte-for-byte: runs `testzip` (CRC check) and re-hashes each member against the recorded sha256. Verification failure raises `OSError` — no originals are touched.
5. Only after verification passes, if `delete_originals` is `True`, the source files are deleted.
6. Appends a `compress` entry to the undo journal so `undo_last` can fully reverse the operation.

**Idempotent:** if there are no loose files, the call is a no-op (`files: 0`). Never overwrites an existing archive (collision-safe naming appends `_1`, `_2`, … to the stem).

This is the only tool that removes files from the working set besides quarantine itself — and it stays fully reversible via `undo_last`.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `delete_originals` | bool | `True` | Delete the source files from quarantine after the archive is verified. Set to `False` to produce the archive without removing originals. |

**Returns:**

| Field | Type | Description |
|---|---|---|
| `archive` | str \| null | Absolute path of the created zip file, or `null` when no-op |
| `files` | int | Number of files bundled |
| `original_bytes` | int | Total uncompressed size in bytes |
| `compressed_bytes` | int | Size of the resulting archive in bytes |
| `deleted_originals` | bool | Whether the source files were deleted after verification |
| `verified` | bool | Always `true` when an archive is created (the op would have raised otherwise) |
| `note` | str | Present only when the call is a no-op; explains why (e.g. `"No loose files to compress"`) |

**Safety category:** Recovery / space reclaim — journaled and reversible via `undo_last`. Destructive only after verified archive is written.

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

## Archived-documents journal tools

Withdraw documents from active memory and inspect the archive log. The archive journal is distinct from the undo journal (reversible file ops) and the event journal (project narrative) — it is the durable record of *why a document left active memory*.

### `archive_document`

```python
archive_document(checksum: str, reason: str = "") -> dict
```

Withdraw a document from active memory ("retirer de la mémoire"). Takes three actions atomically:

1. Looks up the registry record for `checksum`; raises `ValueError` if not found.
2. If the file exists at its recorded path, moves it to `QUARANTINE_DIR` (collision-safe via `safe_quarantine_path`) and appends a `quarantine` entry to the **undo journal** — so the move stays reversible via `undo_last`.
3. Flips the registry record's `status` to `archived`.
4. Appends an entry to the archive log at `ARCHIVE_PATH`.

The document is never deleted. If the file is already gone when `archive_document` is called, steps 2's file move is skipped; the status flip and log entry still happen (`moved` is `null` in the response).

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `checksum` | str | sha256 of the document to archive (the document's registry identity) |
| `reason` | str | Human-readable reason for archiving; stored in the archive log (default `""`) |

**Returns:**

| Field | Type | Description |
|---|---|---|
| `checksum` | str | sha256 of the archived document |
| `status` | str | Always `"archived"` |
| `moved` | str \| null | Absolute path of the file in quarantine, or `null` if the file was already gone |
| `archived` | dict | Full `ArchiveEntry` record: `{checksum, title, reason, src, dst, archived_at}` |

---

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

### `create_dir`

```python
create_dir(path: str) -> dict
```

Create a directory and any missing parents. Idempotent and collision-safe: if the directory already exists it is returned without error. Raises `ValueError` if `path` already exists as a file.

**Returns:** `{created, existed}` — `created` is the absolute path of the directory; `existed` is `true` if the directory was already present, `false` if it was newly created.

---

## Tool availability by version

| Tool | v0.2 | v0.3 | v0.4 | v0.5 | v0.6 | v0.7 | v0.8 | v0.9 |
|---|---|---|---|---|---|---|---|---|
| `list_dir`, `read_file`, `extract_text` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `compare_documents` | — | — | — | — | — | — | ✓ | ✓ |
| `move_file`, `rename_file`, `create_file`, `update_file` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `create_dir` | — | — | — | — | — | — | ✓ | ✓ |
| `compute_checksum` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `create_plan`, `get_plan`, `list_plans`, `approve_plan` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `propose_rename`, `propose_move`, `propose_quarantine` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `execute_plan`, `review_plan`, `undo_last` | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `record_document`, `get_document`, `list_documents` | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `get_registry`, `find_duplicates`, `find_modified_documents` | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `write_index`, `write_summary` | — | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| `write_folder_readme` | — | — | — | — | — | — | ✓ | ✓ |
| `create_event`, `list_events` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `build_graph`, `get_graph` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `get_actors` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `archive_document`, `list_archived` | — | — | — | — | — | ✓ | ✓ | ✓ |
| `compress_quarantine` | — | — | — | — | — | — | — | ✓ |
