# Plan Lifecycle (v0.3.0)

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
execute_plan is running. Operations are applied one by one. If an operation fails, the system retries up to 2 times before marking it failed. Each successful operation is journaled immediately. If more than 3 operations fail in a single execution run, the plan transitions to stopped and a hard_stop journal entry is written.

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
- operations: List of proposed operations.
  - op_id: Sequential index within the plan (0, 1, 2, ...).
  - op_type: rename, move, or quarantine.
  - src: Absolute path to the source file or directory.
  - new_name (rename only): New name for the file (not a path).
  - dest_dir (move only): Absolute path to the destination directory.
  - proposed_at: ISO 8601 timestamp when the operation was proposed.
  - status: Current status of the operation within the plan. May be pending, done, or failed.

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

## Reviewing a Plan

### review_plan(plan_id: str) -> dict

Scan a plan for issues without modifying it.

**Processing:**
1. Load the plan file for plan_id.
2. Scan all operations for duplicate (src, op_type) pairs. Flag these as conflicts.
3. Validate that all source files still exist.
4. Return a report.

## Executing a Plan

### execute_plan(plan_id: str) -> dict

Apply all operations in an approved plan. Must be in approved state.

**Processing:**
1. Load the plan file.
2. Check state is approved; raise if not.
3. Transition plan state to executing and write to disk.
4. For each operation in the plan (in order):
   a. Attempt to execute it (rename, move, or quarantine).
   b. On success: update operation status to done, append a journal entry, update plan file.
   c. On failure: retry up to 2 times. After 2 failed retries, mark operation status as failed and continue.
5. After all operations:
   a. If failed count > 3, transition plan state to stopped, append a hard_stop journal entry.
   b. Otherwise, transition plan state to done and write to disk.
6. Return a summary.

## Undoing Operations

### undo_last() -> dict

Revert the most recent journaled operation.

**Processing:**
1. Load the journal.
2. Call last() to read the most recent entry without removing it. If empty, return an error.
3. If the entry is a hard_stop, skip it and return a note.
4. Invert the operation:
   - **rename**: rename back to original name
   - **move**: move back to original directory
   - **quarantine**: move back from quarantine path to original path
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
