# Plan Lifecycle (Phase 3)

Design document for the plan + journal system that enables stateful, reversible operations across multiple concurrent sessions.

## Plan States

A plan moves through these states:

```
pending → approved → executing → done
        ↘ rejected
```

Or on failure:

```
executing → stopped (if >3 ops fail in a single run)
         → failed   (if execute_plan is called on a plan already in error state)
```

### State Descriptions

**pending**
Initial state after propose_* calls. The plan contains a list of proposed operations but has not been approved by the user. Multiple proposals may accumulate in a single pending plan via repeated propose_* calls with the same plan_id.

**approved**
User has reviewed the plan and explicitly approved it. Only plans in approved state may be executed. Approval is recorded but not persisted in this design—the host manages it in memory.

**executing**
execute_plan is running. Operations are applied one by one. If an operation fails, the system retries — 3 attempts total — before marking it failed. Each successful operation is journaled immediately. If more than 3 operations fail in a single execution run, the plan transitions to stopped and a hard_stop journal entry is written.

**done**
All operations in the plan executed successfully. The plan persists on disk but is no longer acted upon.

**stopped**
Execution encountered more than 3 failures in a single run. A detailed hard_stop journal entry documents the failure context. Requires manual intervention or undo_last to recover.

**failed**
Reserved for future use: if execute_plan is called on a plan that is already in a terminal state.

## Persistence

Plans and journal entries are stored as flat files on disk. They persist across crashes and restarts, allowing for safe long-running or resumed operations.

### Plan Files

Location: .organizer/plans/<plan_id>.json

Example plan file:

```json
{
  "plan_id": "uuid-12345-abcde",
  "created_at": "2026-06-21T14:23:45.123Z",
  "state": "pending",
  "operations": [
    {
      "op_id": 0,
      "op_type": "rename",
      "src": "C:\\Users\\user\\folder\\old_name.txt",
      "new_name": "new_name.txt",
      "proposed_at": "2026-06-21T14:23:45.123Z",
      "status": "pending"
    },
    {
      "op_id": 1,
      "op_type": "move",
      "src": "C:\\Users\\user\\folder\\file.docx",
      "dest_dir": "C:\\Users\\user\\folder\\Documents",
      "proposed_at": "2026-06-21T14:24:10.456Z",
      "status": "pending"
    },
    {
      "op_id": 2,
      "op_type": "quarantine",
      "src": "C:\\Users\\user\\folder\\junk.tmp",
      "proposed_at": "2026-06-21T14:24:15.789Z",
      "status": "pending"
    }
  ]
}
```

Fields:
- plan_id: Stable UUID identifying the plan. Multiple plans may be active concurrently.
- created_at: ISO 8601 timestamp when the plan was first created.
- state: Current state of the plan (pending, approved, executing, done, stopped, failed).
- rationale: Plain-language explanation of the plan's philosophy, set via `set_plan_rationale(plan_id, rationale)`. Empty string (`""`) by default and when not yet set. Displayed above the op list in the host's approval modal (not shown in the example below, which predates this field).
- folder_notes: Agent-supplied per-folder purpose notes, set via `set_plan_folder_notes(plan_id, notes)`. Maps a target folder path to a short one-line purpose note (e.g. `{"01_decisions": "Formal decision records"}`); blank keys/notes are dropped and non-string values coerced to str. Empty dict (`{}`) by default and when not yet set. Rendered beside each folder in the host's target-layout tree preview, shown between the rationale and the op list when the plan has any `move`/`quarantine` destinations (not shown in the example below, which predates this field).
- operations: List of proposed operations.
  - op_id: Sequential index within the plan (0, 1, 2, ...).
  - op_type: rename, move, quarantine, create_file, update_file, create_dir, archive_document, or compress_quarantine.
  - src: Absolute path to the source file or directory.
  - new_name (rename only): New name for the file (not a path).
  - dest_dir (move only): Absolute path to the destination directory.
  - params: Op-specific data that doesn't fit src/dst — e.g. `{"content": ...}` for create_file/update_file, `{"content": ..., "overwrite": ...}` for update_file, `{"checksum": ..., "reason": ...}` for archive_document, `{"delete_originals": ...}` for compress_quarantine. `null` for op types that carry no extra data (rename, move, quarantine, create_dir).
  - proposed_at: ISO 8601 timestamp when the operation was proposed.
  - status: Current status of the operation within the plan. May be pending, completed, or failed.

**All mutating tools are staged this way.** As of the M1 security-hardening pass, there is no tool that touches the filesystem directly — `propose_create_file`, `propose_update_file`, `propose_create_dir`, `propose_archive_document`, and `propose_compress_quarantine` stage ops onto a plan exactly like `propose_rename`/`propose_move`/`propose_quarantine`, and only `execute_plan` applies them. `archive_document` and `compress_quarantine` ops reuse the pre-existing standalone functions of the same name at execution time rather than duplicating their logic; both self-journal under their own `op_type` (`quarantine` and `compress` respectively) instead of the generic entry `execute_plan` writes for other op types.

### Journal (JSONL)

Location: .organizer/journal.jsonl (or value of JOURNAL_PATH)

Format: One JSON object per line (JSONL). Each entry represents a single executed operation or a system event (hard stop).

Example journal entries:

```jsonl
{"timestamp": "2026-06-21T14:25:00.111Z", "plan_id": "uuid-12345-abcde", "op_id": 0, "op_type": "rename", "src": "C:\\Users\\user\\folder\\old_name.txt", "new_name": "new_name.txt", "status": "done"}
{"timestamp": "2026-06-21T14:25:01.222Z", "plan_id": "uuid-12345-abcde", "op_id": 1, "op_type": "move", "src": "C:\\Users\\user\\folder\\file.docx", "dest_dir": "C:\\Users\\user\\folder\\Documents", "final_path": "C:\\Users\\user\\folder\\Documents\\file.docx", "status": "done"}
{"timestamp": "2026-06-21T14:25:05.333Z", "plan_id": "uuid-12345-abcde", "op_id": 2, "op_type": "quarantine", "src": "C:\\Users\\user\\folder\\junk.tmp", "quarantine_path": "C:\\Users\\user\\folder\\_quarantine\\junk.tmp", "status": "done"}
```

Fields in normal operation entries:
- timestamp: ISO 8601 timestamp when the operation was executed.
- plan_id: References the plan this operation belongs to (not present for compress entries).
- op_id: Index within the plan (not present for compress entries).
- op_type: rename, move, quarantine, or compress.
- src: Source path (rename, move, quarantine).
- new_name (rename only): New name.
- dest_dir (move only): Destination directory.
- final_path (move only): Absolute path to the file after the move.
- quarantine_path (quarantine only): Absolute path to the file in quarantine.
- archive (compress only): Absolute path of the created zip archive.
- quarantine_dir (compress only): Absolute path of the quarantine directory that was compressed.
- files (compress only): List of `{name, src, sha256, size}` dicts — one per bundled file.
- deleted_originals (compress only): Boolean — whether the source files were deleted after verification.
- status: Always done for successful journal entries (not present for compress entries).

Fields in hard_stop entries:
- timestamp: When the hard stop was triggered.
- plan_id: References the plan that encountered the failure.
- op_type: Literally "hard_stop".
- failed_count: Total operations that failed in this run.
- stopped_at_op_id: Index of the operation that triggered the hard stop.
- error_summary: Brief human-readable summary.
- details: Array of failed operations with their errors.

## Proposing Operations

Proposal tools append operations to a plan without executing them.

### propose_rename(path: str, new_name: str) -> dict

Propose renaming a file.

**Inputs:**
- path: Absolute path to the file.
- new_name: New name (not a path; e.g., "clean_filename.txt").

**Processing:**
1. Validate that path is a file that exists.
2. Check for collision: if a file named new_name already exists in the same directory, raise FileExistsError. This eager guard prevents invalid plans.
3. Create or load the plan file for the given plan_id (from context or environment).
4. Append a new operation object to the operations list.
5. Write the plan file back to disk.

**Output:**
```json
{
  "plan_id": "uuid-12345-abcde",
  "op_id": 2,
  "op_type": "rename",
  "src": "C:\\Users\\user\\folder\\old_name.txt",
  "new_name": "new_name.txt",
  "status": "pending"
}
```

### propose_move(path: str, dest_dir: str) -> dict

Propose moving a file to a different directory.

**Inputs:**
- path: Absolute path to the file.
- dest_dir: Absolute path to the destination directory.

**Processing:**
1. Validate that path is a file and dest_dir is a directory.
2. Check for collision: if a file with the same name already exists in dest_dir, raise FileExistsError.
3. Append the operation to the plan.
4. Write the plan file.

### propose_quarantine(path: str) -> dict

Propose moving a file to the quarantine directory.

**Inputs:**
- path: Absolute path to the file.

**Processing:**
1. Validate that path is a file.
2. Generate a safe destination path in QUARANTINE_DIR using safe_quarantine_path (handles collisions by suffixing).
3. Append the operation to the plan.
4. Write the plan file.

### propose_create_file(path, content, plan_id) -> dict / propose_update_file(path, content, plan_id, overwrite=False) -> dict / propose_create_dir(path, plan_id) -> dict / propose_archive_document(checksum, plan_id, reason="") -> dict / propose_compress_quarantine(plan_id, delete_originals=True) -> dict

Added by the M1 security-hardening pass, these stage the create_file, update_file, create_dir, archive_document, and compress_quarantine op types the same way: an eager check at proposal time (collision check for create_file/create_dir; existence check for update_file unless `overwrite=True`; registry lookup for archive_document), then append the op — with any op-specific data in `params` (see the op schema above) — to the plan and write it back to disk. None of the five touches the filesystem at proposal time; all five only take effect when the plan is approved and executed.

## Reviewing a Plan

### review_plan(plan_id: str) -> dict

Scan a plan for issues without modifying it.

**Processing:**
1. Load the plan file for plan_id.
2. Scan all operations for duplicate (src, op_type) pairs. Flag these as conflicts.
3. Validate that all source files still exist.
4. Return a report.

## Executing a Plan

### execute_plan(plan_id, plans_dir, journal_path, registry_path=None, quarantine_dir=None, archive_path=None) -> dict

Apply all operations in an approved plan. Must be in approved state. `quarantine_dir` and `archive_path` are only required if the plan contains an `archive_document` or `compress_quarantine` op; the MCP-exposed tool signature is just `execute_plan(plan_id: str) -> dict` — the server fills in the rest from config.

**Processing:**
1. Load the plan file.
2. Check state is approved; raise if not.
3. Transition plan state to executing and write to disk.
4. For each operation in the plan (in order):
   a. Resolve the op's source: if an earlier op in this same run already relocated the file (see below), use its current path; otherwise use the op's original `src`.
   b. Attempt to execute it against the resolved source: `rename`/`move`/`quarantine`/`create_file`/`update_file`/`create_dir` are applied directly; `archive_document`/`compress_quarantine` are delegated to the pre-existing standalone functions of the same name (they self-journal and are skipped by step c's generic journal append).
   c. On success: update operation status to completed, append a journal entry for non-self-journaling op types (recording the resolved source, not necessarily the original `src`), record the file's new location for later ops, update plan file.
   d. On failure: retry — 3 attempts total. After the 3rd failed attempt, mark operation status as failed and continue.
5. After all operations:
   a. If failed count > 3, transition plan state to stopped, append a hard_stop journal entry.
   b. Otherwise, transition plan state to done and write to disk.
6. Return a summary.

**Chained operations within a single run**

Ops are staged against the file's original path (`src` at propose time). Within one `execute_plan` run, an earlier op may relocate a file before a later op that was staged against that same original path runs — the canonical case is a `rename` followed by a `move` on the same file. `execute_plan` tracks each file's current on-disk location in an in-memory map, keyed by the op's original `src`, and resolves that map before applying every op. So a rename A → B followed by a move (both staged against original path A) moves the *renamed* file B into the destination, landing at `dest/B` — not a stale reference to A. Each journal entry records the resolved (effective) source path actually used for that op, so `undo_last` continues to reverse operations against the correct on-disk locations, in reverse order.

## Undoing Operations

### undo_last(journal_path, plans_dir) -> dict

Revert the most recent journaled operation.

**Not an MCP tool.** As of the M1 security-hardening pass, `undo_last` is no longer registered with the server, so the agent has no way to call it. It exists purely as a plain function in `server/tools.py`, invoked directly (bypassing MCP) from the TUI's `JournalScreen` — press **j** in the Organizer screen to open it, then **u** to trigger undo. This makes undo a deliberate, user-only action.

**Processing:**
1. Load the journal.
2. Call last() to read the most recent entry without removing it. If empty, return an error.
3. If the entry is a hard_stop, skip it and return a note.
4. Invert the operation:
   - **rename**: rename back to original name
   - **move**: move back to original directory
   - **quarantine**: move back from quarantine path to original path
   - **create_file** / **update_file**: delete the file this op wrote (an `overwrite=True` update cannot restore the content it replaced — undo only removes what this op itself wrote)
   - **create_dir**: no-op — idempotent by design, the directory is left in place rather than risk deleting something created into it since
   - **compress**: restore each original file from the archive into its recorded `src` path, then delete the zip. All targets are pre-checked for collisions before any file is written. If `deleted_originals` was `False` (originals were kept), only the zip is deleted.
5. On success, call pop_last() to remove the entry from the journal.
6. Return the inverted operation.

**Edge cases:**
- If the original file no longer exists at the expected location, return an error.
- If the destination of the undo operation already exists, raise FileExistsError and do not remove the journal entry.
- Hard stops are skipped and never undone; the user must manually assess the situation.
- For compress undo: if `deleted_originals` was `True` and the archive is missing, an error is returned without removing the journal entry.

## Journal Module

Low-level append-only JSONL helpers in server/journal.py.

### append(journal_path: Path, entry: dict) -> None

Append a single JSON object as a line to the journal. Creates the journal file and parent directory if they do not exist.

### last(journal_path: Path) -> dict | None

Read the file, parse the last line as JSON, and return it. Returns None if the journal is empty or does not exist.

### pop_last(journal_path: Path) -> dict | None

Remove the last line from the journal file, returning it. On empty journal, returns None. This is used by undo_last to commit the reversal.
