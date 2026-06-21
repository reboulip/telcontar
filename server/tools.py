"""Tool implementations — called by the MCP server handlers in main.py."""
from __future__ import annotations

import shutil
from pathlib import Path

from server import plan as _plan
from server.extract import extract as _extract
from server.guards import check_no_overwrite, safe_quarantine_path


def list_dir(path: str) -> dict:
    """Enumerate directory entries with size, type, and mtime."""
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"Not a directory: {path}")
    entries = []
    for entry in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name)):
        try:
            st = entry.stat()
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except OSError:
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "type": "unknown",
                "size": None,
                "mtime": None,
            })
    return {"path": str(p), "entries": entries}


def read_file(path: str, max_chars: int) -> str:
    """Return file text up to max_chars characters."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text


def extract_text(path: str, max_chars: int) -> str:
    """Extract plain text from a PDF or Office file via markitdown."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    return _extract(p, max_chars)


def move_file(path: str, dest_dir: str) -> dict:
    """Move a file to dest_dir, raising if the destination already exists."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"Not a file: {path}")
    dst_dir = Path(dest_dir)
    if not dst_dir.is_dir():
        raise ValueError(f"Not a directory: {dest_dir}")
    dest = dst_dir / src.name
    check_no_overwrite(dest)
    shutil.move(str(src), str(dest))
    return {"moved": str(dest)}


def rename_file(path: str, new_name: str) -> dict:
    """Rename a file in place, raising if the new name already exists."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Not found: {path}")
    dest = src.parent / new_name
    check_no_overwrite(dest)
    src.rename(dest)
    return {"renamed": str(dest)}


def create_file(path: str, content: str) -> dict:
    """Write content to path; raises if the file already exists."""
    p = Path(path)
    check_no_overwrite(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"created": str(p)}


def update_file(path: str, content: str) -> dict:
    """Overwrite or create content at path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"updated": str(p)}


# ── Plan management ──────────────────────────────────────────────────────────

def create_plan(plans_dir: Path) -> dict:
    """Create a new empty plan and persist it to disk."""
    p = _plan.Plan.new()
    _plan.save(p, plans_dir)
    return p.to_dict()


def get_plan(plan_id: str, plans_dir: Path) -> dict:
    """Load and return a plan by plan_id."""
    return _plan.load(plan_id, plans_dir).to_dict()


def list_plans(plans_dir: Path) -> list[dict]:
    """Return all plans sorted by creation time."""
    return [p.to_dict() for p in _plan.list_all(plans_dir)]


def review_plan(plan_id: str, plans_dir: Path) -> dict:
    """Read-only deduplication and pre-flight check; never modifies the plan."""
    p = _plan.load(plan_id, plans_dir)

    seen: dict[tuple[str, str], list[str]] = {}
    missing: list[dict] = []

    for op in p.ops:
        key = (op.src, op.op_type)
        seen.setdefault(key, []).append(op.op_id)
        if not Path(op.src).exists():
            missing.append({"op_id": op.op_id, "op_type": op.op_type, "src": op.src})

    duplicates = [
        {"src": src, "op_type": op_type, "op_ids": ids}
        for (src, op_type), ids in seen.items()
        if len(ids) > 1
    ]

    return {
        "plan_id": plan_id,
        "total_ops": len(p.ops),
        "duplicates": duplicates,
        "missing_sources": missing,
        "is_valid": not duplicates and not missing,
    }


def approve_plan(plan_id: str, plans_dir: Path) -> dict:
    """Transition a plan from pending → approved."""
    p = _plan.load(plan_id, plans_dir)
    p.transition("approved")
    _plan.save(p, plans_dir)
    return p.to_dict()


# ── v0.3.0 stubs ────────────────────────────────────────────────────────────

def propose_rename(path: str, new_name: str, plan_id: str, plans_dir: Path) -> dict:
    """Append a rename op to an existing pending plan; eager collision check."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Not found: {path}")
    dest = src.parent / new_name
    check_no_overwrite(dest)
    p = _plan.load(plan_id, plans_dir)
    if p.state != "pending":
        raise ValueError(f"Plan must be in 'pending' state to add ops; current state: {p.state!r}")
    op = _plan.PlanOp.new("rename", str(src), new_name)
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {"plan_id": plan_id, "op_id": op.op_id, "op_type": "rename",
            "src": str(src), "dst": new_name, "status": op.status, "ops_count": len(p.ops)}


def propose_move(path: str, dest_dir: str, plan_id: str, plans_dir: Path) -> dict:
    """Append a move op to an existing pending plan; eager collision check."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"Not a file: {path}")
    dst_dir = Path(dest_dir)
    if not dst_dir.is_dir():
        raise ValueError(f"Not a directory: {dest_dir}")
    dest = dst_dir / src.name
    check_no_overwrite(dest)
    p = _plan.load(plan_id, plans_dir)
    if p.state != "pending":
        raise ValueError(f"Plan must be in 'pending' state to add ops; current state: {p.state!r}")
    op = _plan.PlanOp.new("move", str(src), str(dst_dir))
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {"plan_id": plan_id, "op_id": op.op_id, "op_type": "move",
            "src": str(src), "dst": str(dst_dir), "status": op.status, "ops_count": len(p.ops)}


def propose_quarantine(path: str, plan_id: str, plans_dir: Path, quarantine_dir: Path) -> dict:
    """Append a quarantine op to an existing pending plan; picks collision-safe dest."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"Not a file: {path}")
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    safe_dest = safe_quarantine_path(src, quarantine_dir)
    p = _plan.load(plan_id, plans_dir)
    if p.state != "pending":
        raise ValueError(f"Plan must be in 'pending' state to add ops; current state: {p.state!r}")
    op = _plan.PlanOp.new("quarantine", str(src), str(safe_dest))
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {"plan_id": plan_id, "op_id": op.op_id, "op_type": "quarantine",
            "src": str(src), "dst": str(safe_dest), "status": op.status, "ops_count": len(p.ops)}


def execute_plan(plan_id: str, plans_dir: Path, journal_path: Path) -> dict:
    """Apply approved ops with per-op retry; hard-stop if >3 fail in one run."""
    from datetime import datetime, timezone
    from server import journal as _journal

    p = _plan.load(plan_id, plans_dir)
    if p.state != "approved":
        raise ValueError(f"Plan must be in 'approved' state to execute; current state: {p.state!r}")

    p.transition("executing")
    _plan.save(p, plans_dir)

    completed_count = 0
    failed_ops: list[dict] = []

    for op in p.ops:
        if op.status != "pending":
            continue

        success = False
        last_error: str | None = None

        for attempt in range(3):
            try:
                _apply_op(op)
                success = True
                break
            except _NON_RETRYABLE_ERRORS as exc:
                last_error = str(exc)
                break
            except OSError as exc:
                op.retries = attempt + 1
                last_error = str(exc)

        if success:
            op.status = "completed"
            completed_count += 1
            _journal.append(journal_path, {
                "op_type": op.op_type,
                "plan_id": plan_id,
                "op_id": op.op_id,
                "src": op.src,
                "dst": op.dst,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            op.status = "failed"
            op.error = last_error
            failed_ops.append({"op_id": op.op_id, "op_type": op.op_type,
                                "src": op.src, "error": last_error})

        _plan.save(p, plans_dir)

        if len(failed_ops) > 3:
            _journal.append(journal_path, {
                "op_type": "hard_stop",
                "plan_id": plan_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "failed_count": len(failed_ops),
                "failed_ops": failed_ops,
                "reason": "Exceeded 3 failures; stopping plan execution",
            })
            p.transition("stopped")
            _plan.save(p, plans_dir)
            return {**p.to_dict(), "ops_completed": completed_count,
                    "ops_failed": len(failed_ops), "hard_stop": True}

    p.transition("failed" if failed_ops else "done")
    _plan.save(p, plans_dir)
    return {**p.to_dict(), "ops_completed": completed_count,
            "ops_failed": len(failed_ops), "hard_stop": False}


_NON_RETRYABLE_ERRORS = (ValueError, FileNotFoundError, FileExistsError)


def _apply_op(op: "_plan.PlanOp") -> None:
    """Execute a single planned op against the filesystem."""
    src = Path(op.src)
    if op.op_type == "rename":
        dest = src.parent / op.dst
        check_no_overwrite(dest)
        src.rename(dest)
    elif op.op_type == "move":
        dst_dir = Path(op.dst)
        dest = dst_dir / src.name
        check_no_overwrite(dest)
        shutil.move(str(src), str(dest))
    elif op.op_type == "quarantine":
        dest = Path(op.dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        check_no_overwrite(dest)
        shutil.move(str(src), str(dest))
    else:
        raise ValueError(f"Unknown op_type: {op.op_type!r}")


def write_index(path: str) -> str:
    raise NotImplementedError


def write_summary(path: str) -> str:
    raise NotImplementedError


def undo_last(journal_path: Path, plans_dir: Path) -> dict:
    """Revert the most recent journaled op; only removes the entry on success."""
    from server import journal as _journal

    entry = _journal.last(journal_path)
    if entry is None:
        return {"undone": None, "error": "No operations to undo"}

    op_type = entry.get("op_type")

    if op_type == "hard_stop":
        _journal.pop_last(journal_path)
        return {"undone": "hard_stop",
                "note": "Hard-stop entry removed; failed ops were never executed"}

    src = entry["src"]
    dst = entry["dst"]
    target = Path(src)  # every undo restores the file to its original path

    try:
        check_no_overwrite(target)
        if op_type == "rename":
            current = Path(src).parent / dst
            current.rename(target)
        elif op_type == "move":
            current = Path(dst) / Path(src).name
            shutil.move(str(current), str(target))
        elif op_type == "quarantine":
            shutil.move(str(dst), str(target))
        else:
            return {"undone": None, "error": f"Unknown op_type: {op_type!r}"}
    except (FileNotFoundError, FileExistsError, OSError) as exc:
        return {"undone": None, "error": str(exc)}

    _journal.pop_last(journal_path)
    return {"undone": entry}
