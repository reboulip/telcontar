# MCP Tools Reference

Complete reference for all tools exposed by the telcontar MCP server. Tools are grouped by safety category and execution flow.

## Read-Only Tools

Safe tools that inspect the filesystem without modification. May run without approval depending on APPROVAL_MODE.

### list_dir

**Signature:**
```python
def list_dir(path: str) -> dict
```

**Description:**
Enumerate directory entries with size, type, and modification time.

**Inputs:**
- path (str): Absolute path to the directory.

**Outputs:**
- path (str): Resolved directory path.
- entries (list): Array of entry objects with keys: name, path, type (dir|file|unknown), size (bytes), mtime (Unix timestamp).

**Safety:** Safe (read-only).

---

### read_file

**Signature:**
```python
def read_file(path: str, max_chars: int) -> str
```

**Description:**
Return text content of a file up to max_chars characters.

**Inputs:**
- path (str): Absolute path to the file.
- max_chars (int): Maximum characters to return.

**Outputs:**
- Text content, truncated if it exceeds max_chars. Truncation is noted with "[... content truncated ...]".

**Safety:** Safe (read-only).

---

### extract_text

**Signature:**
```python
def extract_text(path: str, max_chars: int) -> str
```

**Description:**
Extract plain text from PDF or Office files (docx, xlsx, pptx, etc.) via markitdown.

**Inputs:**
- path (str): Absolute path to the PDF or Office file.
- max_chars (int): Maximum characters to return.

**Outputs:**
- Extracted text content, truncated to max_chars if necessary.

**Safety:** Safe (read-only).

---

## Plan-Building Tools (v0.3.0)

Tools that propose operations without executing them. Accumulate in a pending plan.

### propose_rename (v0.3.0)

**Signature:**
```python
def propose_rename(path: str, new_name: str) -> dict
```

**Description:**
Propose renaming a file. Checks for destination collision eagerly.

**Inputs:**
- path (str): Absolute path to the file.
- new_name (str): New name (not a full path; e.g., "clean_name.txt").

**Outputs:**
- plan_id (str): UUID of the plan this operation belongs to.
- op_id (int): Sequential index of this operation within the plan.
- op_type (str): "rename".
- src (str): Source path.
- new_name (str): New name.
- status (str): Always "pending".

**Safety:** Plan-only (no execution, eager no-overwrite check).

---

### propose_move (v0.3.0)

**Signature:**
```python
def propose_move(path: str, dest_dir: str) -> dict
```

**Description:**
Propose moving a file to a destination directory. Checks for destination collision eagerly.

**Inputs:**
- path (str): Absolute path to the file.
- dest_dir (str): Absolute path to the destination directory.

**Outputs:**
- plan_id (str): UUID of the plan.
- op_id (int): Sequential index.
- op_type (str): "move".
- src (str): Source path.
- dest_dir (str): Destination directory.
- status (str): Always "pending".

**Safety:** Plan-only (no execution, eager no-overwrite check).

---

### propose_quarantine (v0.3.0)

**Signature:**
```python
def propose_quarantine(path: str) -> dict
```

**Description:**
Propose quarantining a file (moving it to QUARANTINE_DIR). Handles name collisions by suffixing.

**Inputs:**
- path (str): Absolute path to the file.

**Outputs:**
- plan_id (str): UUID of the plan.
- op_id (int): Sequential index.
- op_type (str): "quarantine".
- src (str): Source path.
- quarantine_path (str): Safe destination path in QUARANTINE_DIR (may include suffix if collision detected).
- status (str): Always "pending".

**Safety:** Plan-only (no execution).

---

## Gated Execution Tools (v0.3.0)

Tools that execute or write output. Subject to APPROVAL_MODE.

### execute_plan (v0.3.0)

**Signature:**
```python
def execute_plan(plan_id: str) -> dict
```

**Description:**
Apply all approved operations in a plan. Must be in approved state. Retries each op up to 2 times. If >3 ops fail in a single run, triggers hard stop.

**Inputs:**
- plan_id (str): UUID of the plan to execute.

**Outputs:**
- plan_id (str): The plan ID.
- state (str): Final state (done or stopped).
- total_ops (int): Total operations in the plan.
- successful (int): Operations that succeeded.
- failed (int): Operations that failed.
- summary (str): Human-readable summary.
- failures (list, if stopped): Array of failed operations with error details.

**Safety:** Gated (requires APPROVAL_MODE approval).

---

### write_index

**Signature:**
```python
def write_index(path: str) -> str
```

**Description:**
Emit INDEX.md (human-readable tree) and manifest.json (structured metadata) under the given path.

**Inputs:**
- path (str): Absolute path to the output directory.

**Outputs:**
- Summary of files written (paths).

**Safety:** Gated (subject to APPROVAL_MODE).

---

### write_summary

**Signature:**
```python
def write_summary(path: str) -> str
```

**Description:**
Emit SUMMARY.md describing the directory's contents and changes made during the organize session.

**Inputs:**
- path (str): Absolute path to the output directory.

**Outputs:**
- Summary of the file written (path).

**Safety:** Gated (subject to APPROVAL_MODE).

---

## Utility Tools (v0.2.0)

Lower-level file manipulation used for index/summary output. Also subject to approval gating.

### move_file

**Signature:**
```python
def move_file(path: str, dest_dir: str) -> dict
```

**Description:**
Move a file to dest_dir. Respects no-overwrite guard.

**Inputs:**
- path (str): Absolute path to the file.
- dest_dir (str): Absolute path to the destination directory.

**Outputs:**
- moved (str): Final absolute path of the moved file.

**Safety:** Direct execution (respects no-overwrite guard).

---

### rename_file

**Signature:**
```python
def rename_file(path: str, new_name: str) -> dict
```

**Description:**
Rename a file in place. Respects no-overwrite guard.

**Inputs:**
- path (str): Absolute path to the file.
- new_name (str): New name (not a path).

**Outputs:**
- renamed (str): Final absolute path of the renamed file.

**Safety:** Direct execution (respects no-overwrite guard).

---

### create_file

**Signature:**
```python
def create_file(path: str, content: str) -> dict
```

**Description:**
Write content to path. Raises if the file already exists. Creates parent directories as needed.

**Inputs:**
- path (str): Absolute path to the file.
- content (str): Text content to write.

**Outputs:**
- created (str): Absolute path of the file created.

**Safety:** Direct execution (no-overwrite guard prevents clobbering).

---

### update_file

**Signature:**
```python
def update_file(path: str, content: str) -> dict
```

**Description:**
Overwrite or create content at path. Creates parent directories as needed.

**Inputs:**
- path (str): Absolute path to the file.
- content (str): Text content to write.

**Outputs:**
- updated (str): Absolute path of the file updated.

**Safety:** Direct execution (may clobber existing files; use create_file to prevent overwrites).

---

## Recovery Tools (v0.3.0)

Undo and plan review.

### undo_last (v0.3.0)

**Signature:**
```python
def undo_last() -> dict
```

**Description:**
Revert the most recent journaled operation by inverting it and removing the journal entry. Skips hard_stop entries.

**Inputs:**
- None.

**Outputs:**
- reversed (bool): True if reversal succeeded.
- op_type (str): Type of the reverted operation (rename, move, quarantine).
- original_src (str): Original source path.
- reverted_to (str): Path after reversal.

**Safety:** Gated (may require approval depending on APPROVAL_MODE).

---

### review_plan (v0.3.0)

**Signature:**
```python
def review_plan(plan_id: str) -> dict
```

**Description:**
Scan a plan for issues (duplicate operations, missing source files) without modifying it.

**Inputs:**
- plan_id (str): UUID of the plan to review.

**Outputs:**
- plan_id (str): The plan ID.
- issues (list): Array of detected issues with severity, message, and details.
- valid (bool): True if no issues found.
- ready_to_execute (bool): True if the plan is ready to execute.

**Safety:** Safe (read-only; does not modify the plan).

---

## Tool Availability by v0.x

| Tool | v0.1 | v0.2 | v0.3 | v0.4+ |
|------|------|------|------|-------|
| list_dir | - | X | X | X |
| read_file | - | X | X | X |
| extract_text | - | X | X | X |
| move_file | - | X | X | X |
| rename_file | - | X | X | X |
| create_file | - | X | X | X |
| update_file | - | X | X | X |
| propose_rename | - | - | X | X |
| propose_move | - | - | X | X |
| propose_quarantine | - | - | X | X |
| execute_plan | - | - | X | X |
| review_plan | - | - | X | X |
| undo_last | - | - | X | X |
| write_index | - | - | (v0.5) | (v0.5) |
| write_summary | - | - | (v0.5) | (v0.5) |

Tools marked (v0.5) are stubs until v0.5.0.
