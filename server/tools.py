"""Tool implementations — called by the MCP server handlers in main.py."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from server import plan as _plan
from server import registry as _registry
from server.extract import extract as _extract
from server.guards import check_no_overwrite, safe_quarantine_path
from server.profile import Profile as _Profile

_CHECKSUM_CHUNK = 65536


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


def compute_checksum(path: str) -> dict:
    """Compute the sha256 checksum of a file (chunk-streamed) as its unique id."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHECKSUM_CHUNK), b""):
            h.update(chunk)
    return {"path": str(p), "checksum": h.hexdigest()}


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


def execute_plan(
    plan_id: str,
    plans_dir: Path,
    journal_path: Path,
    registry_path: Path | None = None,
) -> dict:
    """Apply approved ops with per-op retry; hard-stop if >3 fail in one run.

    When ``registry_path`` points at a non-empty registry, each executed op also
    reconciles the matching document record's path (and status, for quarantine)
    so the checksum stays the identity while paths track moves. Registry-optional:
    a missing/empty registry is a silent no-op.
    """
    from datetime import datetime, timezone
    from server import journal as _journal

    p = _plan.load(plan_id, plans_dir)
    if p.state != "approved":
        raise ValueError(f"Plan must be in 'approved' state to execute; current state: {p.state!r}")

    p.transition("executing")
    _plan.save(p, plans_dir)

    reg: _registry.Registry | None = None
    if registry_path is not None:
        loaded = _registry.load(registry_path)
        if loaded.documents:
            reg = loaded

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
            if reg is not None and registry_path is not None and _reconcile_op(reg, op):
                _registry.save(reg, registry_path)
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


def _reconcile_op(reg: "_registry.Registry", op: "_plan.PlanOp") -> bool:
    """Update the registry record's location/status to match an executed op.

    Returns True if a record was updated; False when the op's source file was
    never recorded (registry-optional reconcile).
    """
    src = Path(op.src)
    if op.op_type == "rename":
        rec = reg.update_path(op.src, str(src.parent / op.dst))
    elif op.op_type == "move":
        rec = reg.update_path(op.src, str(Path(op.dst) / src.name))
    elif op.op_type == "quarantine":
        rec = reg.update_path(op.src, str(Path(op.dst)), status="quarantined")
    else:
        return False
    return rec is not None


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


def write_index(target_dir: str, journal_path: Path) -> dict:
    """Walk target_dir, emit INDEX.md (tree + journal changelog) and manifest.json."""
    from datetime import datetime, timezone
    import json as _json
    from server import journal as _journal

    root = Path(target_dir)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {target_dir}")

    now = datetime.now(timezone.utc)
    generated = now.isoformat(timespec="seconds")

    # Skip output files we're about to write
    _SKIP = {"INDEX.md", "manifest.json", "SUMMARY.md"}

    files: list[dict] = []
    dirs: list[str] = []

    def _walk(path: Path, prefix: str, lines: list[str]) -> None:
        try:
            entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name))
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            if entry.name in _SKIP and entry.parent == root:
                continue
            connector = "└── " if i == len(entries) - 1 else "├── "
            child_prefix = prefix + ("    " if i == len(entries) - 1 else "│   ")
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                dirs.append(str(entry))
                _walk(entry, child_prefix, lines)
            else:
                try:
                    st = entry.stat()
                    size_kb = st.st_size / 1024
                    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                    files.append({
                        "path": str(entry.relative_to(root)).replace("\\", "/"),
                        "abs_path": str(entry),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
                    lines.append(f"{prefix}{connector}{entry.name} ({size_str})")
                except OSError:
                    lines.append(f"{prefix}{connector}{entry.name}")

    tree_lines: list[str] = [f"{root.name}/"]
    _walk(root, "", tree_lines)

    # Journal summary
    all_entries = _journal.all_entries(journal_path)
    op_counts: dict[str, int] = {}
    for entry in all_entries:
        op_type = entry.get("op_type", "unknown")
        if op_type != "hard_stop":
            op_counts[op_type] = op_counts.get(op_type, 0) + 1

    changelog_lines: list[str] = []
    if op_counts:
        for op_type, count in sorted(op_counts.items()):
            changelog_lines.append(f"- {count} {op_type}{'s' if count != 1 else ''}")
        changelog_lines.append(f"- **Total operations:** {sum(op_counts.values())}")
    else:
        changelog_lines.append("- No operations recorded")

    index_content = (
        f"# Directory Index\n\n"
        f"Generated: {generated}\n\n"
        f"## Tree\n\n"
        f"```\n{chr(10).join(tree_lines)}\n```\n\n"
        f"## Changes Made\n\n"
        f"{chr(10).join(changelog_lines)}\n"
    )

    manifest = {
        "generated": generated,
        "target": str(root),
        "files": files,
        "dirs": dirs,
        "journal_summary": {"total_ops": sum(op_counts.values()), "by_type": op_counts},
    }

    index_path = root / "INDEX.md"
    manifest_path = root / "manifest.json"
    index_path.write_text(index_content, encoding="utf-8")
    manifest_path.write_text(_json.dumps(manifest, indent=2), encoding="utf-8")

    return {"index": str(index_path), "manifest": str(manifest_path)}


def write_summary(target_dir: str, content: str) -> dict:
    """Write LLM-composed prose to SUMMARY.md inside target_dir."""
    p = Path(target_dir) / "SUMMARY.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"written": str(p)}


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


# ── Document registry ─────────────────────────────────────────────────────────

def record_document(
    checksum: str,
    path: str,
    title: str,
    type: str,
    summary: str,
    provenance: str,
    date: str | None,
    entities: list[dict] | None,
    attributes: dict | None,
    status: str,
    registry_path: Path,
    profile: _Profile,
) -> dict:
    """Upsert an analyzed document into the registry, validated against the profile.

    ``type`` must be one of the active profile's document types. Entity ``role``
    values, when present, must belong to the profile's role taxonomy. The author
    guardrail (only include people explicitly named, never inferred) is a prompt
    instruction, not enforced here.
    """
    valid_types = profile.document_type_ids()
    if type not in valid_types:
        raise ValueError(
            f"Invalid document type {type!r}; profile {profile.name!r} allows: {valid_types}"
        )
    valid_roles = set(profile.entity_roles())
    norm_entities: list[dict] = []
    for e in entities or []:
        name = e.get("name")
        if not name:
            raise ValueError(f"Entity missing 'name': {e!r}")
        role = e.get("role", "")
        if role and valid_roles and role not in valid_roles:
            raise ValueError(
                f"Invalid entity role {role!r}; profile {profile.name!r} allows: "
                f"{sorted(valid_roles)}"
            )
        norm_entities.append({"name": name, "role": role, "kind": e.get("kind", "person")})

    reg = _registry.load(registry_path)
    rec = _registry.DocumentRecord.new(
        checksum=checksum,
        path=path,
        title=title,
        type=type,
        summary=summary,
        provenance=provenance,
        date=date,
        entities=norm_entities,
        attributes=attributes or {},
        status=status or "active",  # type: ignore[arg-type]
    )
    reg.upsert(rec)
    _registry.save(reg, registry_path)
    return rec.to_dict()


def get_registry(registry_path: Path) -> dict:
    """Return the entire registry as a dict for the host to reason over."""
    return _registry.load(registry_path).to_dict()


def list_documents(registry_path: Path) -> list[dict]:
    """Return all document records, oldest first."""
    return [r.to_dict() for r in _registry.load(registry_path).records()]


def get_document(checksum: str, registry_path: Path) -> dict | None:
    """Return a single document record by checksum, or None if absent."""
    rec = _registry.load(registry_path).get(checksum)
    return rec.to_dict() if rec is not None else None


def find_duplicates(registry_path: Path) -> list[list[dict]]:
    """Return fuzzy candidate-duplicate clusters for the host to judge."""
    reg = _registry.load(registry_path)
    return [[r.to_dict() for r in group] for group in reg.find_duplicates()]


def find_modified_documents(registry_path: Path) -> list[list[dict]]:
    """Return groups sharing a title but differing in content (modified docs)."""
    reg = _registry.load(registry_path)
    return [[r.to_dict() for r in group] for group in reg.find_modified()]
