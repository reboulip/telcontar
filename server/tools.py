"""Tool implementations — called by the MCP server handlers in main.py."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from server import archive as _archive
from server import events as _events
from server import graph as _graph
from server import plan as _plan
from server import registry as _registry
from server.extract import extract as _extract
from server.guards import check_no_overwrite, format_io_error, safe_quarantine_path
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
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "type": "dir" if entry.is_dir() else "file",
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        except OSError:
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "type": "unknown",
                    "size": None,
                    "mtime": None,
                }
            )
    return {"path": str(p), "entries": entries}


def walk_tree(
    path: str, max_depth: int = 3, hidden_names: frozenset[str] | None = None
) -> dict:
    """Recursively enumerate a directory tree up to ``max_depth`` levels deep.

    Complements ``list_dir`` (a single level) for the ANALYZE pass: the agent can
    see nested subfolders in one call and redesign the whole layout, not just the
    top level. Depth is counted from the root — the root's immediate entries are
    depth 1. Each directory entry carries a nested ``children`` list until
    ``max_depth`` is reached; deeper directories are returned with
    ``children: null`` and ``truncated: true`` so the agent knows more lies below
    (and can call ``walk_tree`` again on that subpath). Files carry ``size`` and
    ``mtime`` like ``list_dir``; unreadable entries are marked ``type: "unknown"``.

    ``hidden_names`` (P2) excludes entries by basename at every level — used to
    keep a run's own `.organizer` memory and quarantine folder out of discovery,
    now that both live inside the target directory.
    """
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"Not a directory: {path}")
    if max_depth < 1:
        raise ValueError(f"max_depth must be >= 1, got {max_depth}")

    hidden = hidden_names or frozenset()

    def _walk(directory: Path, depth: int) -> list[dict]:
        try:
            children = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name))
        except OSError:
            return []
        entries: list[dict] = []
        for entry in children:
            if entry.name in hidden:
                continue
            try:
                st = entry.stat()
                if entry.is_dir():
                    node = {
                        "name": entry.name,
                        "path": str(entry),
                        "type": "dir",
                        "size": None,
                        "mtime": st.st_mtime,
                    }
                    if depth < max_depth:
                        node["children"] = _walk(entry, depth + 1)
                        node["truncated"] = False
                    else:
                        node["children"] = None
                        node["truncated"] = True
                    entries.append(node)
                else:
                    entries.append(
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "type": "file",
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                        }
                    )
            except OSError:
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "type": "unknown",
                        "size": None,
                        "mtime": None,
                    }
                )
        return entries

    return {"path": str(p), "max_depth": max_depth, "entries": _walk(p, 1)}


def read_file(path: str, max_chars: int) -> str:
    """Return file text up to max_chars characters."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(format_io_error("read", p, exc)) from exc
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text


def extract_text(
    path: str,
    max_chars: int,
    max_file_bytes: int = 200_000_000,
    timeout_secs: float = 30.0,
) -> str:
    """Extract plain text from a PDF, Office, or Outlook email (.msg) file."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    return _extract(p, max_chars, max_file_bytes, timeout_secs)


def compare_documents(
    path_a: str,
    path_b: str,
    max_chars: int,
    max_file_bytes: int = 200_000_000,
    timeout_secs: float = 30.0,
) -> dict:
    """Extract text from two files and return a unified diff between them.

    Reuses the markitdown/pypdf extraction path, so it works on PDF/Office as
    well as plain text. Each side is truncated to ``max_chars`` before diffing,
    so the diff reflects only the extracted (possibly truncated) text — handy for
    comparing successive versions (e.g. two COPIL slide decks). ``identical`` is
    True when the extracted texts match exactly.
    """
    import difflib

    pa, pb = Path(path_a), Path(path_b)
    if not pa.is_file():
        raise ValueError(f"Not a file: {path_a}")
    if not pb.is_file():
        raise ValueError(f"Not a file: {path_b}")
    text_a = _extract(pa, max_chars, max_file_bytes, timeout_secs)
    text_b = _extract(pb, max_chars, max_file_bytes, timeout_secs)
    diff = "\n".join(
        difflib.unified_diff(
            text_a.splitlines(),
            text_b.splitlines(),
            fromfile=path_a,
            tofile=path_b,
            lineterm="",
        )
    )
    return {
        "path_a": str(pa),
        "path_b": str(pb),
        "identical": text_a == text_b,
        "diff": diff,
    }


def compute_checksum(path: str) -> dict:
    """Compute the sha256 checksum of a file (chunk-streamed) as its unique id."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(_CHECKSUM_CHUNK), b""):
                h.update(chunk)
    except OSError as exc:
        raise OSError(format_io_error("read", p, exc)) from exc
    return {"path": str(p), "checksum": h.hexdigest()}


# ── Batch document-content tools (O1) ─────────────────────────────────────────
# Fetch content/checksums for many files in one round trip so the host can
# factor several documents into one LLM turn instead of one-doc-per-turn.
# Each result is keyed by the exact path string passed in (no normalization,
# so the caller can correlate results back to its input list). A per-file
# failure never fails the whole batch: the value for that key is
# ``{"error": message}`` instead of raising, so callers must discriminate
# success (a plain str) from failure (a dict) — this contract is relied on by
# host/agent.py's `_wrap_untrusted_content`.


def read_file_batch(paths: list[str], max_chars: int) -> dict[str, str | dict]:
    """Batch form of `read_file`: `{path: text | {"error": msg}}`."""
    results: dict[str, str | dict] = {}
    for path in paths:
        try:
            results[path] = read_file(path, max_chars)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results


def extract_text_batch(
    paths: list[str],
    max_chars: int,
    max_file_bytes: int = 200_000_000,
    timeout_secs: float = 30.0,
) -> dict[str, str | dict]:
    """Batch form of `extract_text`: `{path: text | {"error": msg}}`."""
    results: dict[str, str | dict] = {}
    for path in paths:
        try:
            results[path] = extract_text(path, max_chars, max_file_bytes, timeout_secs)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results


def compute_checksum_batch(paths: list[str]) -> dict[str, str | dict]:
    """Batch form of `compute_checksum`: `{path: checksum_hex | {"error": msg}}`."""
    results: dict[str, str | dict] = {}
    for path in paths:
        try:
            results[path] = compute_checksum(path)["checksum"]
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results


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


def set_plan_rationale(plan_id: str, rationale: str, plans_dir: Path) -> dict:
    """Attach the agent's plain-language rationale to a plan (shown at approval).

    A short paragraph explaining the plan's philosophy — how the taxonomy was
    chosen, why documents were grouped/renamed/quarantined the way they were —
    surfaced above the raw op list in the approval modal (F8).
    """
    p = _plan.load(plan_id, plans_dir)
    p.rationale = (rationale or "").strip()
    _plan.save(p, plans_dir)
    return p.to_dict()


def set_plan_folder_notes(plan_id: str, notes: dict, plans_dir: Path) -> dict:
    """Attach agent-supplied per-folder purpose notes to a plan (L5).

    ``notes`` maps a target folder path to a short one-line purpose note. They are
    shown beside each folder in the approval view's target-layout preview so the
    user sees, at a glance, what the organized tree will look like and why each
    folder exists. Non-string keys/values are coerced to str and blank notes are
    dropped; a folder with no note simply renders as a bare tree node.
    """
    p = _plan.load(plan_id, plans_dir)
    cleaned: dict[str, str] = {}
    for folder, note in (notes or {}).items():
        key = str(folder).strip()
        text = str(note).strip()
        if key and text:
            cleaned[key] = text
    p.folder_notes = cleaned
    _plan.save(p, plans_dir)
    return p.to_dict()


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
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "rename",
        "src": str(src),
        "dst": new_name,
        "status": op.status,
        "ops_count": len(p.ops),
    }


def propose_move(path: str, dest_dir: str, plan_id: str, plans_dir: Path) -> dict:
    """Append a move op to an existing pending plan; eager collision check."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"Not a file: {path}")
    dst_dir = Path(dest_dir)
    p = _plan.load(plan_id, plans_dir)
    if p.state != "pending":
        raise ValueError(f"Plan must be in 'pending' state to add ops; current state: {p.state!r}")
    queued_for_creation = any(
        op.op_type == "create_dir" and Path(op.src) == dst_dir for op in p.ops
    )
    if not dst_dir.is_dir() and not queued_for_creation:
        raise ValueError(f"Not a directory: {dest_dir}")
    dest = dst_dir / src.name
    check_no_overwrite(dest)
    op = _plan.PlanOp.new("move", str(src), str(dst_dir))
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "move",
        "src": str(src),
        "dst": str(dst_dir),
        "status": op.status,
        "ops_count": len(p.ops),
    }


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
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "quarantine",
        "src": str(src),
        "dst": str(safe_dest),
        "status": op.status,
        "ops_count": len(p.ops),
    }


def _load_pending_plan(plan_id: str, plans_dir: Path) -> "_plan.Plan":
    p = _plan.load(plan_id, plans_dir)
    if p.state != "pending":
        raise ValueError(f"Plan must be in 'pending' state to add ops; current state: {p.state!r}")
    return p


def propose_create_file(path: str, content: str, plan_id: str, plans_dir: Path) -> dict:
    """Append a create_file op to an existing pending plan; eager collision check."""
    dest = Path(path)
    check_no_overwrite(dest)
    p = _load_pending_plan(plan_id, plans_dir)
    op = _plan.PlanOp.new("create_file", str(dest), "", params={"content": content})
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "create_file",
        "src": str(dest),
        "dst": "",
        "status": op.status,
        "ops_count": len(p.ops),
    }


def propose_update_file(
    path: str, content: str, plan_id: str, plans_dir: Path, overwrite: bool = False
) -> dict:
    """Append an update_file op to an existing pending plan.

    Eagerly rejects a collision at proposal time unless ``overwrite=True`` — the
    same rule ``_apply_op`` re-checks at execution time, so a file that appears
    between proposal and execution is still caught (M3).
    """
    dest = Path(path)
    if dest.exists() and not overwrite:
        raise FileExistsError(
            f"Destination already exists (pass overwrite=True to replace it): {dest}"
        )
    p = _load_pending_plan(plan_id, plans_dir)
    op = _plan.PlanOp.new(
        "update_file", str(dest), "", params={"content": content, "overwrite": overwrite}
    )
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "update_file",
        "src": str(dest),
        "dst": "",
        "status": op.status,
        "ops_count": len(p.ops),
    }


def propose_create_dir(path: str, plan_id: str, plans_dir: Path) -> dict:
    """Append a create_dir op to an existing pending plan; idempotent, collision-safe."""
    dest = Path(path)
    if dest.is_file():
        raise ValueError(f"Path exists and is a file, not a directory: {path}")
    p = _load_pending_plan(plan_id, plans_dir)
    op = _plan.PlanOp.new("create_dir", str(dest), "")
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "create_dir",
        "src": str(dest),
        "dst": "",
        "status": op.status,
        "ops_count": len(p.ops),
    }


def propose_archive_document(
    checksum: str,
    reason: str,
    plan_id: str,
    plans_dir: Path,
    registry_path: Path,
    quarantine_dir: Path,
) -> dict:
    """Append an archive_document op; eagerly validates the checksum is recorded."""
    reg = _registry.load(registry_path)
    rec = reg.get(checksum)
    if rec is None:
        raise ValueError(f"No document recorded for checksum {checksum!r}")
    src = Path(rec.path)
    dest_str = ""
    if src.is_file():
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest_str = str(safe_quarantine_path(src, quarantine_dir))
    p = _load_pending_plan(plan_id, plans_dir)
    op = _plan.PlanOp.new(
        "archive_document",
        rec.path,
        dest_str,
        params={"checksum": checksum, "reason": reason or ""},
    )
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "archive_document",
        "src": rec.path,
        "dst": dest_str,
        "status": op.status,
        "ops_count": len(p.ops),
    }


def propose_compress_quarantine(
    plan_id: str, plans_dir: Path, quarantine_dir: Path, delete_originals: bool = True
) -> dict:
    """Append a compress_quarantine op; a single global op, not tied to one file."""
    p = _load_pending_plan(plan_id, plans_dir)
    op = _plan.PlanOp.new(
        "compress_quarantine",
        str(quarantine_dir),
        "",
        params={"delete_originals": delete_originals},
    )
    p.add_op(op)
    _plan.save(p, plans_dir)
    return {
        "plan_id": plan_id,
        "op_id": op.op_id,
        "op_type": "compress_quarantine",
        "src": str(quarantine_dir),
        "dst": "",
        "status": op.status,
        "ops_count": len(p.ops),
    }


_SELF_JOURNALING_OP_TYPES = frozenset({"archive_document", "compress_quarantine"})


def execute_plan(
    plan_id: str,
    plans_dir: Path,
    journal_path: Path,
    registry_path: Path | None = None,
    quarantine_dir: Path | None = None,
    archive_path: Path | None = None,
) -> dict:
    """Apply approved ops with per-op retry; hard-stop if >3 fail in one run.

    When ``registry_path`` points at a non-empty registry, each executed op also
    reconciles the matching document record's path (and status, for quarantine)
    so the checksum stays the identity while paths track moves. Registry-optional:
    a missing/empty registry is a silent no-op. ``quarantine_dir``/``archive_path``
    are only required if the plan contains an ``archive_document`` or
    ``compress_quarantine`` op (M1) — those op types reuse the standalone
    ``archive_document``/``compress_quarantine`` functions, which self-journal with
    the same ``op_type`` strings ``undo_last`` already understands ("quarantine" /
    "compress"), so this loop skips its generic journal append for them.
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
    # Tracks each file's current on-disk path as ops relocate it within this run,
    # keyed by the op's original src, so chained ops (rename then move) resolve (F7).
    moved: dict[str, str] = {}

    # Run all create_dir ops before any other op (each group keeping its relative
    # order), so a deselected/failed create_dir can't cascade into a hard stop for
    # a move that targets it — the move executor also self-heals via mkdir, but
    # running create_dir first keeps directory creation itself in the journal.
    ordered = [op for op in p.ops if op.op_type == "create_dir"] + [
        op for op in p.ops if op.op_type != "create_dir"
    ]

    for op in ordered:
        if op.status != "pending":
            continue

        # A file may have been relocated by an earlier op in this same run (e.g. a
        # rename before a move). Ops reference the file's ORIGINAL path, so resolve
        # that through ``moved`` to the file's current on-disk location.
        src_path = moved.get(op.src, op.src)

        success = False
        last_error: str | None = None
        new_location: str | None = None

        for attempt in range(3):
            try:
                if op.op_type == "archive_document":
                    if registry_path is None or quarantine_dir is None or archive_path is None:
                        raise ValueError(
                            "registry_path, quarantine_dir and archive_path are required "
                            "to execute an archive_document op"
                        )
                    params = op.params or {}
                    result = archive_document(
                        params["checksum"],
                        params.get("reason", ""),
                        registry_path,
                        quarantine_dir,
                        journal_path,
                        archive_path,
                    )
                    new_location = result.get("moved") or src_path
                    # archive_document loads/mutates/saves its own registry copy —
                    # reload ours so subsequent ops/final save see the fresh state.
                    if reg is not None:
                        reg = _registry.load(registry_path)
                elif op.op_type == "compress_quarantine":
                    params = op.params or {}
                    result = compress_quarantine(
                        Path(op.src), journal_path, bool(params.get("delete_originals", True))
                    )
                    new_location = result.get("archive") or op.src
                else:
                    new_location = _apply_op(op, src_path)
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
            moved[op.src] = new_location  # type: ignore[assignment]
            if op.op_type not in _SELF_JOURNALING_OP_TYPES:
                _journal.append(
                    journal_path,
                    {
                        "op_type": op.op_type,
                        "plan_id": plan_id,
                        "op_id": op.op_id,
                        "src": src_path,
                        "dst": op.dst,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            if reg is not None and registry_path is not None and _reconcile_op(reg, op, src_path):
                _registry.save(reg, registry_path)
        else:
            op.status = "failed"
            op.error = last_error
            failed_ops.append(
                {"op_id": op.op_id, "op_type": op.op_type, "src": op.src, "error": last_error}
            )

        _plan.save(p, plans_dir)

        if len(failed_ops) > 3:
            _journal.append(
                journal_path,
                {
                    "op_type": "hard_stop",
                    "plan_id": plan_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "failed_count": len(failed_ops),
                    "failed_ops": failed_ops,
                    "reason": "Exceeded 3 failures; stopping plan execution",
                },
            )
            p.transition("stopped")
            _plan.save(p, plans_dir)
            return {
                **p.to_dict(),
                "ops_completed": completed_count,
                "ops_failed": len(failed_ops),
                "hard_stop": True,
            }

    p.transition("failed" if failed_ops else "done")
    _plan.save(p, plans_dir)
    return {
        **p.to_dict(),
        "ops_completed": completed_count,
        "ops_failed": len(failed_ops),
        "hard_stop": False,
    }


_NON_RETRYABLE_ERRORS = (ValueError, FileNotFoundError, FileExistsError)


def _reconcile_op(reg: "_registry.Registry", op: "_plan.PlanOp", src_path: str) -> bool:
    """Update the registry record's location/status to match an executed op.

    ``src_path`` is the file's on-disk location *before* this op (which may differ
    from ``op.src`` if an earlier op in the run already relocated it), so the record
    is matched by where it currently is. Returns True if a record was updated; False
    when the source file was never recorded (registry-optional reconcile).
    """
    src = Path(src_path)
    if op.op_type == "rename":
        rec = reg.update_path(src_path, str(src.parent / op.dst))
    elif op.op_type == "move":
        rec = reg.update_path(src_path, str(Path(op.dst) / src.name))
    elif op.op_type == "quarantine":
        rec = reg.update_path(src_path, str(Path(op.dst)), status="quarantined")
    else:
        return False
    return rec is not None


def _apply_op(op: "_plan.PlanOp", src_path: str) -> str:
    """Execute a single planned op against the filesystem; return the new path.

    ``src_path`` is the file's current on-disk location (which may differ from
    ``op.src`` when an earlier op in the same run relocated it — e.g. a move that
    follows a rename). On an I/O failure the raw ``OSError`` is re-raised with a
    clearer message (via ``format_io_error``) but the **same exception type**, so
    ``execute_plan`` keeps classifying it — a transient ``PermissionError`` (e.g.
    a Windows file lock) stays retryable, a ``FileNotFoundError`` stays fail-fast.
    """
    src = Path(src_path)
    if op.op_type == "rename":
        dest = src.parent / op.dst
        check_no_overwrite(dest)
        try:
            src.rename(dest)
        except OSError as exc:
            raise type(exc)(format_io_error("rename", src, exc)) from exc
        return str(dest)
    elif op.op_type == "move":
        dst_dir = Path(op.dst)
        dest = dst_dir / src.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        check_no_overwrite(dest)
        try:
            shutil.move(str(src), str(dest))
        except OSError as exc:
            raise type(exc)(format_io_error("move", src, exc)) from exc
        return str(dest)
    elif op.op_type == "quarantine":
        dest = Path(op.dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        check_no_overwrite(dest)
        try:
            shutil.move(str(src), str(dest))
        except OSError as exc:
            raise type(exc)(format_io_error("quarantine", src, exc)) from exc
        return str(dest)
    elif op.op_type == "create_file":
        content = (op.params or {}).get("content", "")
        check_no_overwrite(src)
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise type(exc)(format_io_error("create", src, exc)) from exc
        return str(src)
    elif op.op_type == "update_file":
        params = op.params or {}
        content = params.get("content", "")
        overwrite = bool(params.get("overwrite", False))
        if src.exists() and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing file without overwrite=True: {src}"
            )
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise type(exc)(format_io_error("update", src, exc)) from exc
        return str(src)
    elif op.op_type == "create_dir":
        if src.is_file():
            raise ValueError(f"Path exists and is a file, not a directory: {src}")
        try:
            src.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise type(exc)(format_io_error("create directory", src, exc)) from exc
        return str(src)
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

    # Skip output files we're about to write, and the run's own memory folder
    # (P2) — the quarantine folder stays visible here, it's meaningful to a
    # human reviewing results, just hidden from agent discovery (walk_tree).
    _SKIP = {"INDEX.md", "manifest.json", "SUMMARY.md", ".organizer"}

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
                    files.append(
                        {
                            "path": str(entry.relative_to(root)).replace("\\", "/"),
                            "abs_path": str(entry),
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                        }
                    )
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
    try:
        index_path.write_text(index_content, encoding="utf-8")
        manifest_path.write_text(_json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        raise OSError(format_io_error("write", root, exc)) from exc

    return {"index": str(index_path), "manifest": str(manifest_path)}


def write_summary(target_dir: str, content: str) -> dict:
    """Write LLM-composed prose to SUMMARY.md inside target_dir."""
    p = Path(target_dir) / "SUMMARY.md"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OSError(format_io_error("write", p, exc)) from exc
    return {"written": str(p)}


def write_folder_readme(folder: str, content: str) -> dict:
    """Write LLM-composed prose to README.md inside a folder of the arborescence.

    Thin sink, like ``write_summary``: the host composes a short description of
    what the folder holds and its role in the organized tree; this just persists
    it. Overwrites any existing README and creates the folder if needed.
    """
    p = Path(folder) / "README.md"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OSError(format_io_error("write", p, exc)) from exc
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
        return {
            "undone": "hard_stop",
            "note": "Hard-stop entry removed; failed ops were never executed",
        }

    if op_type == "compress":
        return _undo_compress(entry, journal_path)

    if op_type in ("create_file", "update_file"):
        # overwrite=True on update_file loses the prior content forever — undo
        # can only remove what this op wrote, not restore what it replaced.
        target = Path(entry["src"])
        try:
            if target.is_file():
                target.unlink()
        except OSError as exc:
            return {"undone": None, "error": str(exc)}
        _journal.pop_last(journal_path)
        return {"undone": entry}

    if op_type == "create_dir":
        # Idempotent by design; leave the directory in place rather than risk
        # deleting something created into it since.
        _journal.pop_last(journal_path)
        return {"undone": entry, "note": "create_dir is idempotent; directory left in place"}

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


# ── Quarantine compression ────────────────────────────────────────────────────

_MANIFEST_NAME = "_telcontar_manifest.json"


def _sha256_file(path: Path) -> str:
    """Stream a file through sha256, returning its hex digest."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHECKSUM_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_archive_path(directory: Path, name: str) -> Path:
    """Return a non-colliding path for ``name`` inside ``directory`` — never clobber."""
    dest = directory / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _verify_archive(archive: Path, files_meta: list[dict]) -> None:
    """Raise OSError unless every file in the archive matches its recorded checksum."""
    with zipfile.ZipFile(archive, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise OSError(f"Archive verification failed (CRC error) for {bad!r} in {archive}")
        for f in files_meta:
            if hashlib.sha256(zf.read(f["name"])).hexdigest() != f["sha256"]:
                raise OSError(f"Archive verification failed: checksum mismatch for {f['name']!r}")


def compress_quarantine(
    quarantine_dir: Path,
    journal_path: Path,
    delete_originals: bool = True,
) -> dict:
    """Losslessly bundle loose quarantined files into a verified zip archive.

    Collects every top-level regular file currently in ``quarantine_dir`` (skipping
    archives this tool itself produced), writes them into a single ZIP_DEFLATED
    archive together with a checksum manifest, and verifies the archive byte-for-byte
    against the originals. Only once verification passes are the originals optionally
    removed to reclaim space. The whole operation is journaled so ``undo_last`` can
    restore every file and delete the archive. Nothing is deleted before the archive
    is verified, and existing archives are never overwritten.

    Idempotent: re-running with no new loose files is a no-op.
    """
    from datetime import datetime, timezone

    from server import journal as _journal

    qdir = Path(quarantine_dir)
    if not qdir.is_dir():
        return {"archive": None, "files": 0, "note": "Quarantine directory does not exist"}

    sources = sorted(
        p
        for p in qdir.iterdir()
        if p.is_file() and not (p.suffix == ".zip" and p.name.startswith("quarantine_"))
    )
    if not sources:
        return {"archive": None, "files": 0, "note": "No loose files to compress"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = _safe_archive_path(qdir, f"quarantine_{stamp}.zip")

    files_meta = [
        {"name": src.name, "src": str(src), "sha256": _sha256_file(src), "size": src.stat().st_size}
        for src in sources
    ]
    manifest = {f["name"]: {"src": f["src"], "sha256": f["sha256"]} for f in files_meta}

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files_meta:
            zf.write(f["src"], arcname=f["name"])
        zf.writestr(_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    # Verify before deleting anything — bail out leaving originals untouched on failure.
    _verify_archive(archive, files_meta)

    deleted = False
    if delete_originals:
        for f in files_meta:
            Path(f["src"]).unlink()
        deleted = True

    _journal.append(
        journal_path,
        {
            "op_type": "compress",
            "archive": str(archive),
            "quarantine_dir": str(qdir),
            "files": files_meta,
            "deleted_originals": deleted,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    original_bytes = sum(f["size"] for f in files_meta)
    return {
        "archive": str(archive),
        "files": len(files_meta),
        "original_bytes": original_bytes,
        "compressed_bytes": archive.stat().st_size,
        "deleted_originals": deleted,
        "verified": True,
    }


def _undo_compress(entry: dict, journal_path: Path) -> dict:
    """Reverse a ``compress`` op: restore originals from the archive, drop the zip."""
    from server import journal as _journal

    archive = Path(entry["archive"])
    files = entry.get("files", [])
    deleted = entry.get("deleted_originals", False)

    try:
        if deleted:
            if not archive.is_file():
                return {"undone": None, "error": f"Archive missing, cannot restore: {archive}"}
            # Pre-check every target so a mid-way collision can't half-restore.
            for f in files:
                check_no_overwrite(Path(f["src"]))
            with zipfile.ZipFile(archive, "r") as zf:
                for f in files:
                    target = Path(f["src"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(f["name"]) as srcf, target.open("wb") as out:
                        shutil.copyfileobj(srcf, out)
        if archive.is_file():
            archive.unlink()
    except (FileNotFoundError, FileExistsError, OSError, KeyError) as exc:
        return {"undone": None, "error": str(exc)}

    _journal.pop_last(journal_path)
    return {"undone": entry}


# ── Document registry ─────────────────────────────────────────────────────────


def _validate_and_build_record(doc: dict, profile: _Profile) -> _registry.DocumentRecord:
    """Validate one document dict against the profile and build its `DocumentRecord`.

    Shared by `record_document` and `record_document_batch` so both enforce the
    exact same rules with the exact same error strings. Raises `ValueError` on
    any validation failure — callers decide whether that aborts the whole call
    (singular) or is caught and collected per-item (batch).
    """
    type_ = doc.get("type", "")
    valid_types = profile.document_type_ids()
    if type_ not in valid_types:
        raise ValueError(
            f"Invalid document type {type_!r}; profile {profile.name!r} allows: {valid_types}"
        )
    valid_roles = set(profile.entity_roles())
    norm_entities: list[dict] = []
    for e in doc.get("entities") or []:
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

    return _registry.DocumentRecord.new(
        checksum=doc.get("checksum", ""),
        path=doc.get("path", ""),
        title=doc.get("title", ""),
        type=type_,
        summary=doc.get("summary", ""),
        provenance=doc.get("provenance", ""),
        date=doc.get("date"),
        entities=norm_entities,
        attributes=doc.get("attributes") or {},
        status=doc.get("status") or "active",
    )


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
    rec = _validate_and_build_record(
        {
            "checksum": checksum,
            "path": path,
            "title": title,
            "type": type,
            "summary": summary,
            "provenance": provenance,
            "date": date,
            "entities": entities,
            "attributes": attributes,
            "status": status,
        },
        profile,
    )
    reg = _registry.load(registry_path)
    reg.upsert(rec)
    _registry.save(reg, registry_path)
    return rec.to_dict()


def record_document_batch(
    documents: list[dict],
    registry_path: Path,
    profile: _Profile,
) -> dict:
    """Upsert many analyzed documents into the registry in one call.

    Each item in ``documents`` has the same shape as `record_document`'s
    parameters. One invalid document never fails the whole batch — its
    validation error is collected in ``errors`` instead, keyed by its
    positional ``index`` in the input list (a failure may carry a missing or
    blank checksum/path, so position is the only safe correlation key).
    Returns ``{"recorded": [record_dict, ...], "errors": [{"index", "checksum",
    "path", "error"}, ...]}``. The registry is loaded once and saved once at
    the end (a mid-batch crash persists nothing — an accepted trade-off for
    the round-trip savings over one load/save per document).
    """
    reg = _registry.load(registry_path)
    recorded: list[dict] = []
    errors: list[dict] = []
    for index, doc in enumerate(documents):
        try:
            rec = _validate_and_build_record(doc, profile)
        except ValueError as exc:
            errors.append(
                {
                    "index": index,
                    "checksum": doc.get("checksum", ""),
                    "path": doc.get("path", ""),
                    "error": str(exc),
                }
            )
            continue
        reg.upsert(rec)
        recorded.append(rec.to_dict())
    _registry.save(reg, registry_path)
    return {"recorded": recorded, "errors": errors}


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


def lookup_documents(checksums: list[str], registry_path: Path) -> dict[str, dict | None]:
    """Batch form of `get_document`: `{checksum: record | None}` (P3).

    Lets the host pre-pass partition a corpus into known/new documents with a
    single call instead of one `get_document` round trip per checksum.
    """
    reg = _registry.load(registry_path)
    result: dict[str, dict | None] = {}
    for checksum in checksums:
        rec = reg.get(checksum)
        result[checksum] = rec.to_dict() if rec is not None else None
    return result


def rehome_documents(paths: dict[str, str], registry_path: Path) -> dict:
    """Update registry records' recorded path directly by checksum (P4).

    ``paths`` maps checksum -> new on-disk path. Pure registry mutation — no
    plan, no journal entry, no approval gate — used by the deterministic host
    pre-pass to reconcile records whose on-disk location changed outside a
    plan-tracked op (e.g. moved manually between runs). Returns
    ``{"updated": [checksum, ...], "missing": [checksum, ...]}``.
    """
    reg = _registry.load(registry_path)
    updated: list[str] = []
    missing: list[str] = []
    for checksum, new_path in paths.items():
        rec = reg.rehome(checksum, new_path)
        if rec is not None:
            updated.append(checksum)
        else:
            missing.append(checksum)
    if updated:
        _registry.save(reg, registry_path)
    return {"updated": updated, "missing": missing}


def find_duplicates(registry_path: Path) -> list[list[dict]]:
    """Return fuzzy candidate-duplicate clusters for the host to judge."""
    reg = _registry.load(registry_path)
    return [[r.to_dict() for r in group] for group in reg.find_duplicates()]


def find_modified_documents(registry_path: Path) -> list[list[dict]]:
    """Return groups sharing a title but differing in content (modified docs)."""
    reg = _registry.load(registry_path)
    return [[r.to_dict() for r in group] for group in reg.find_modified()]


# ── Project event journal ──────────────────────────────────────────────────────


def create_event(sentence: str, date: str | None, events_path: Path) -> dict:
    """Append a verb-led, dated event to the project event journal.

    Distinct from the undo journal: this records project narrative (what happened
    and when), not reversible file operations. ``sentence`` should be a short,
    verb-led statement; ``date`` is the ISO YYYY-MM-DD the event occurred (null
    if unknown).
    """
    text = (sentence or "").strip()
    if not text:
        raise ValueError("Event sentence must be a non-empty string")
    ev = _events.Event.new(sentence=text, date=date)
    _events.append(events_path, ev)
    return ev.to_dict()


def list_events(events_path: Path) -> list[dict]:
    """Return all recorded project events in chronological order."""
    return [e.to_dict() for e in _events.all_events(events_path)]


# ── Knowledge graph ────────────────────────────────────────────────────────────


def build_graph(registry_path: Path, events_path: Path, graph_path: Path) -> dict:
    """Rebuild the knowledge graph from the registry + event journal and persist it.

    Pure projection: the result depends only on the current registry and events,
    so it can be regenerated at any time. Returns the graph as {nodes, edges}.
    """
    reg = _registry.load(registry_path)
    evs = _events.all_events(events_path)
    g = _graph.build(reg, evs)
    _graph.save(g, graph_path)
    return g.to_dict()


def get_graph(graph_path: Path) -> dict:
    """Return the persisted knowledge graph as {nodes, edges}; empty if never built."""
    return _graph.load(graph_path).to_dict()


def get_actors(graph_path: Path, salient_cap: int) -> list[dict]:
    """Return the project's main actors — top entities ranked from the persisted
    graph, capped at ``salient_cap``. Build the graph first (build_graph)."""
    return _graph.rank_actors(_graph.load(graph_path), salient_cap)


# ── Archived-documents journal ──────────────────────────────────────────────────


def archive_document(
    checksum: str,
    reason: str,
    registry_path: Path,
    quarantine_dir: Path,
    journal_path: Path,
    archive_path: Path,
) -> dict:
    """Withdraw a document from active memory ("retirer de la mémoire").

    Flips the registry record's status to ``archived``, moves its file to the
    quarantine dir (collision-safe, recorded in the undo journal so it stays
    reversible via ``undo_last``), and appends an entry to the archive log. The
    document is never deleted. If the file is already gone, the status flip and
    log entry still happen (no move). Raises if the checksum is not recorded.
    """
    from datetime import datetime, timezone
    from server import journal as _journal

    reg = _registry.load(registry_path)
    rec = reg.get(checksum)
    if rec is None:
        raise ValueError(f"No document recorded for checksum {checksum!r}")

    original_path = rec.path
    src = Path(original_path)
    moved_dst: str | None = None

    if src.is_file():
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_quarantine_path(src, quarantine_dir)
        check_no_overwrite(dest)
        shutil.move(str(src), str(dest))
        moved_dst = str(dest)
        _journal.append(
            journal_path,
            {
                "op_type": "quarantine",
                "src": original_path,
                "dst": moved_dst,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "archive_document",
            },
        )
        rec.path = moved_dst

    reg.set_status(checksum, "archived")
    _registry.save(reg, registry_path)

    entry = _archive.ArchiveEntry.new(
        checksum=checksum,
        title=rec.title,
        reason=reason or "",
        src=original_path,
        dst=moved_dst,
    )
    _archive.append(archive_path, entry)

    return {
        "checksum": checksum,
        "status": "archived",
        "moved": moved_dst,
        "archived": entry.to_dict(),
    }


def list_archived(archive_path: Path) -> list[dict]:
    """Return all archived-document log entries in chronological order."""
    return _archive.all_entries(archive_path)
