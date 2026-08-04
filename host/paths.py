"""UI-agnostic path/discovery helpers, shared by the Textual and web UIs.

Extracted from host/app.py (Phase 18 S1) with zero behaviour change — these
functions have no Textual dependency and were only reachable through a Pilot
test before this move.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_organizer_root(start: Path) -> Path | None:
    """Find the nearest directory at or above ``start`` containing `.organizer`
    (P2 Query-mode resolution): per-directory memory means a folder the user
    picks for Query may be a subfolder of what was actually organized, so this
    walks up from ``start`` until it finds a `.organizer`, or hits the
    filesystem root without finding one."""
    current = start.resolve()
    while True:
        if (current / ".organizer").is_dir():
            return current
        if current.parent == current:
            return None
        current = current.parent


def resolve_journal_path(target: Path) -> Path:
    """Resolve the undo-journal path for ``target`` from settings."""
    from config.settings import Settings

    return Settings().for_target(target).journal_path


def resolve_plans_dir(target: Path) -> Path:
    """Resolve the plans directory for ``target`` from settings."""
    from config.settings import Settings

    return Settings().for_target(target).plans_dir


def quarantine_basename() -> str:
    """Basename of the configured quarantine dir, for discovery-hiding (P2).

    Falls back to the default name on any settings error — this only feeds a
    display nicety (the starter-pane overview), never a safety guard.
    """
    from config.settings import Settings

    try:
        return Settings().quarantine_dir.name
    except Exception:
        return "_quarantine"


def directory_overview(target: Path, max_entries: int = 5000) -> str:
    """Code-generated, deterministic one-glance summary of a directory (L3).

    Reads only names and structure — no file contents, no LLM, no latency —
    counting files, subfolders and the most common file types. Bounded by
    ``max_entries`` so a huge tree cannot stall the UI (the count then reads
    ``N+``). Shown as the opening telcontar turn before ANALYZE so the user can
    steer the run instead of it auto-organizing.
    """
    hidden_names = {".organizer", quarantine_basename()}
    file_count = 0
    dir_count = 0
    ext_counts: dict[str, int] = {}
    truncated = False
    for _root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in hidden_names]
        dir_count += len(dirs)
        for name in files:
            if file_count >= max_entries:
                truncated = True
                break
            file_count += 1
            ext = Path(name).suffix.lower() or "(no extension)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        if truncated:
            break

    count_label = f"{file_count}{'+' if truncated else ''}"
    lines = [
        f"Target directory: {target}",
        f"{count_label} file(s) across {dir_count} subfolder(s).",
    ]
    if ext_counts:
        top = sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        breakdown = ", ".join(f"{n}× {ext}" for ext, n in top)
        lines.append(f"Most common types: {breakdown}.")
    else:
        lines.append("No files found at or below this folder.")
    return "\n".join(lines)


def is_op_out_of_scope(op: dict, target: Path | None) -> bool:
    """Best-effort UI check: does this op's source resolve outside ``target``?

    Advisory only — the server's own `check_within_root` guard (M2) is the real
    enforcement boundary. This just makes the existing risk visible to the
    approver. Defensive: any resolution error (missing src, bad path) reads as
    in-scope rather than raising, so a malformed op never crashes the modal.
    """
    if target is None:
        return False
    src = op.get("src") or ""
    if not src:
        return False
    try:
        Path(src).resolve().relative_to(target.resolve())
        return False
    except (ValueError, OSError):
        return True
