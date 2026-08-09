"""UI-agnostic formatters, shared by the Textual and web UIs.

Extracted from host/app.py (Phase 18 S1) with zero behaviour change. Rich/
Textual markup stays embedded in the formatters' output by default (``markup``
keyword defaults to ``True``, preserving exact TUI behaviour); the web UI
passes ``markup=False`` to get plain text instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from host.paths import is_op_out_of_scope


def fmt_exc(exc: BaseException) -> str:
    """Format an exception with its type so errors are actionable, not just a message.

    anyio/asyncio TaskGroups (the MCP session, the LLM HTTP client) wrap any child
    failure in an ExceptionGroup whose own message is just "unhandled errors in a
    TaskGroup (N sub-exception(s))" — useless on its own. Drill into `.exceptions`
    (recursively, since groups can nest) to surface the real leaf error(s) instead.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(fmt_exc(sub) for sub in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def fmt_journal_entry(entry: dict, *, markup: bool = True) -> str:
    ts = entry.get("timestamp", "?")
    op_type = entry.get("op_type", "?")
    if op_type == "hard_stop":
        reason = entry.get("reason", "")
        failed = entry.get("failed_count", len(entry.get("failed_ops", [])))
        header = f"{ts}  HARD STOP  ({failed} op(s) failed) — {reason}"
        if markup:
            header = f"[bold red]{ts}  HARD STOP[/bold red]  ({failed} op(s) failed) — {reason}"
        lines = [header]
        for fop in entry.get("failed_ops", []):
            entry_line = (
                f"    ✗ {fop.get('op_type', '?')}  {fop.get('src', '?')}  — {fop.get('error', '')}"
            )
            if markup:
                entry_line = (
                    f"    [red]✗ {fop.get('op_type', '?')}[/red]  {fop.get('src', '?')}"
                    f"  — {fop.get('error', '')}"
                )
            lines.append(entry_line)
        return "\n".join(lines)
    src = entry.get("src", "?")
    dst = entry.get("dst")
    target = f"  →  {dst}" if dst else ""
    ts_part = f"[dim]{ts}[/dim]" if markup else ts
    return f"{ts_part}  {op_type:<10}  {src}{target}"


def fmt_progress(progress: dict) -> str:
    """Format a ``"progress"`` AgentEvent's data dict as a short status string.

    Expects the ``{"analyzed": int, "total": int, "current": list[str]}`` shape
    emitted by ``host.agent``'s pre-pass/analyzer progress events (V8a). All
    keys are read defensively via ``.get`` — ``current`` may be absent (older
    progress events, e.g. the pre-pass snapshot, don't carry it) or empty
    (between batches, or the batch-completion event) — so a partial dict still
    renders a reasonable ``"analyzed/total"`` string instead of raising.
    When ``current`` has entries, the first filename is appended, with a
    ``"+N"`` suffix if more than one file is in flight.
    """
    analyzed = progress.get("analyzed", 0)
    total = progress.get("total", 0)
    label = f"{analyzed}/{total}"
    current = progress.get("current") or []
    if not current:
        return label
    extra = f" +{len(current) - 1}" if len(current) > 1 else ""
    return f"{label} — {current[0]}{extra}"


_MAX_QUARANTINE_REASON_CHARS = 120


def quarantine_reason(op: dict) -> str:
    """Format a quarantine op's stated reason (V10) for display, capped at
    ``_MAX_QUARANTINE_REASON_CHARS`` — the full text is always available
    verbatim in ``plan_ops.json`` via ``ops_json_path``. A blank/missing
    reason renders as an explicit "no reason given" rather than silently
    looking indistinguishable from a properly-justified quarantine."""
    reason = ((op.get("params") or {}).get("reason") or "").strip()
    if not reason:
        return "no reason given"
    if len(reason) > _MAX_QUARANTINE_REASON_CHARS:
        return reason[: _MAX_QUARANTINE_REASON_CHARS - 1] + "…"
    return reason


def fmt_op(op: dict, target: Path | None = None, *, markup: bool = True) -> str:
    op_type = op.get("op_type", "?")
    src = Path(op.get("src", "")).name
    dst = op.get("dst", "")
    match op_type:
        case "rename":
            label = f"RENAME   {src}  →  {dst}"
        case "move":
            label = f"MOVE     {src}  →  {dst}"
        case "quarantine":
            reason = quarantine_reason(op)
            reason_part = f"  [dim]— {reason}[/dim]" if markup else f"  — {reason}"
            label = f"QUARANTINE  {src}{reason_part}"
        case "update_file":
            # Subtle, not alarming (M4's discreet-styling convention): the
            # overwrite flag matters to the approver but isn't a red-banner risk.
            # Parens, not square brackets — Textual's markup parser treats
            # `[...]` as a style tag and silently drops unrecognized names.
            has_overwrite = (op.get("params") or {}).get("overwrite")
            if has_overwrite:
                overwrite_flag = "  [dim](overwrite)[/dim]" if markup else "  (overwrite)"
            else:
                overwrite_flag = ""
            label = f"UPDATE   {src}{overwrite_flag}"
        case _:
            label = f"{op_type.upper()}  {src}"
    if is_op_out_of_scope(op, target):
        # Same discreet convention as the overwrite flag above — a quiet cue,
        # not a red banner, per the explicit "keep this subtle" instruction (S4).
        label += "  [dim](outside target)[/dim]" if markup else "  (outside target)"
    return label


def target_folders(ops: list[dict]) -> list[str]:
    """Distinct target folders a plan will populate (move dests + quarantine dir)."""
    folders: set[str] = set()
    for op in ops:
        dst = op.get("dst") or ""
        if not dst:
            continue
        if op.get("op_type") == "move":
            folders.add(dst)
        elif op.get("op_type") == "quarantine":
            folders.add(str(Path(dst).parent))
    return sorted(folders)


def note_for(folder: str, notes: dict) -> str:
    """Best-effort match of a (possibly absolute) folder path to a folder note.

    The agent may key notes by a short relative folder name (``"01_decisions"``)
    while the plan ops carry absolute destinations, so match on the full path, its
    forward-slashed form, its basename, or a path suffix.
    """
    if not notes:
        return ""
    norm = folder.replace("\\", "/").rstrip("/")
    name = Path(folder).name
    for key in (folder, norm, name):
        if key in notes:
            return str(notes[key])
    for key, val in notes.items():
        k = str(key).replace("\\", "/").strip("/")
        if k and (norm == k or norm.endswith("/" + k) or name == Path(k).name):
            return str(val)
    return ""


def render_target_layout(ops: list[dict], folder_notes: dict) -> list[str]:
    """Render the plan's target folder tree with per-folder purpose notes (L5).

    Returns tree lines (box-drawing connectors) or an empty list when the plan
    has no folder destinations. Folders with no note render as bare nodes.
    """
    folders = target_folders(ops)
    if not folders:
        return []
    norm = [f.replace("\\", "/").rstrip("/") for f in folders]
    try:
        base = (
            str(Path(norm[0]).parent).replace("\\", "/")
            if len(norm) == 1
            else os.path.commonpath(norm).replace("\\", "/")
        )
    except ValueError:  # e.g. paths on different drives — no common base
        base = ""

    tree: dict = {}
    note_at: dict[tuple[str, ...], str] = {}
    for folder in norm:
        rel = folder[len(base) :].strip("/") if base and folder.startswith(base) else folder
        parts = [p for p in rel.split("/") if p]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        note = note_for(folder, folder_notes)
        if note and parts:
            note_at[tuple(parts)] = note

    lines: list[str] = [f"{Path(base).name or base or 'target'}/"]

    def _walk(node: dict, prefix: str, acc: tuple[str, ...]) -> None:
        items = sorted(node.items())
        for i, (name, children) in enumerate(items):
            last = i == len(items) - 1
            connector = "└── " if last else "├── "
            new_acc = acc + (name,)
            note = note_at.get(new_acc, "")
            suffix = f"  — {note}" if note else ""
            lines.append(f"{prefix}{connector}{name}/{suffix}")
            _walk(children, prefix + ("    " if last else "│   "), new_acc)

    _walk(tree, "", ())
    return lines


@dataclass(frozen=True)
class PlanTreeLine:
    """One line of a before/after plan tree (V3). ``depth`` drives visual
    indent (the web UI renders these in a single column, indented by
    depth — no ASCII connectors needed since nesting isn't done via raw
    text). ``op_id`` is set on every leaf file entry, before-tree and
    after-tree alike; the before tree is a read-only reference, so its
    caller simply never renders a checkbox from it — the dataclass itself
    doesn't need two shapes."""

    depth: int
    label: str
    op_id: str | None = None


def plan_tree_diff(
    ops: list[dict], folder_notes: dict | None = None, target: Path | None = None
) -> tuple[list[PlanTreeLine], list[PlanTreeLine], list[dict]]:
    """Derive a before/after file-tree pair from a plan's ops (V3) — no
    filesystem I/O, entirely from the ops list itself; the plan's ops ARE
    the diff. Returns ``(before_lines, after_lines, other_ops)``:
    ``other_ops`` holds every op with no clean before/after tree slot
    (``create_dir``, ``compress_quarantine``, ``update_file``, and any
    ``quarantine``/``archive_document`` with no destination computed) — each
    of these still needs its own checkbox in the approval dialog, just in
    an "Other operations" list instead of a tree node. No op is ever
    dropped: every op_id ends up in exactly one of before/after/other.

    ``folder_notes`` (model-authored, from ``set_plan_folder_notes``) is
    applied only to the after-tree's folder lines — it describes the
    *destination* structure, so it has no meaning on the before side.

    ``target``, when given, restores the ``(outside target)`` advisory
    marker (S4/M4 — ``is_op_out_of_scope``, the same check ``fmt_op`` uses)
    on both tree sides for any op whose *source* resolves outside it, and
    every ``quarantine`` op that lands on a tree node still carries its V10
    stated reason. Neither must silently disappear just because an op
    landed on a tree node instead of the "Other operations" fallback list,
    which is the only place still routed through ``fmt_op``.

    ``dst`` semantics, per ``server/plan.py``'s ``PlanOp`` docstring:
    ``rename`` -> bare new filename; ``move`` -> destination *directory*;
    ``quarantine`` -> *full* destination path; ``create_file``/
    ``update_file``/``create_dir``/``compress_quarantine`` -> ``""`` (the
    real path is in ``src``); ``archive_document`` -> quarantine path,
    possibly ``""``.
    """
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    other: list[dict] = []
    suffixes: dict[str, str] = {}

    for op in ops:
        op_id = str(op.get("op_id", ""))
        op_type = op.get("op_type", "")
        src = op.get("src", "")
        dst = op.get("dst", "")
        suffix = "  (outside target)" if is_op_out_of_scope(op, target) else ""
        if op_type == "rename":
            before[op_id] = src
            after[op_id] = str(Path(src).parent / dst) if dst else src
        elif op_type == "move":
            before[op_id] = src
            after[op_id] = str(Path(dst) / Path(src).name) if dst else src
        elif op_type in ("quarantine", "archive_document"):
            if dst:
                before[op_id] = src
                after[op_id] = dst
                if op_type == "quarantine":
                    suffix += f"  — {quarantine_reason(op)}"
            else:
                other.append(op)
        elif op_type == "create_file":
            after[op_id] = src  # new file — no prior location to show
        else:  # update_file, create_dir, compress_quarantine
            other.append(op)
        if suffix:
            suffixes[op_id] = suffix

    return (
        _render_file_tree(before, suffixes=suffixes),
        _render_file_tree(after, folder_notes, suffixes=suffixes),
        other,
    )


def _render_file_tree(
    paths: dict[str, str],
    folder_notes: dict | None = None,
    suffixes: dict[str, str] | None = None,
) -> list[PlanTreeLine]:
    """``paths``: op_id -> absolute file path. Groups by parent folder into
    a tree, files listed before subfolders at each level. ``folder_notes``,
    when given, annotates folder lines the same way ``render_target_layout``
    does (``note_for``'s fuzzy path/basename matching). ``suffixes``, when
    given, appends per-op_id extra text (the ``(outside target)`` marker,
    a quarantine reason) to that op's file line, the same way ``fmt_op``
    would for an op in the "Other operations" fallback list."""
    if not paths:
        return []
    suffixes = suffixes or {}
    norm = {op_id: p.replace("\\", "/") for op_id, p in paths.items()}
    dirs = [str(Path(p).parent).replace("\\", "/") for p in norm.values()]
    try:
        base = (
            str(Path(dirs[0]).parent).replace("\\", "/")
            if len(set(dirs)) == 1
            else os.path.commonpath(dirs).replace("\\", "/")
        )
    except ValueError:  # e.g. paths on different drives — no common base
        base = ""

    tree: dict = {}
    files_at: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    note_at: dict[tuple[str, ...], str] = {}
    noted_dirs: set[str] = set()
    for op_id, path in norm.items():
        parent = str(Path(path).parent).replace("\\", "/")
        rel = parent[len(base) :].strip("/") if base and parent.startswith(base) else parent
        parts = tuple(p for p in rel.split("/") if p)
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        files_at.setdefault(parts, []).append((op_id, Path(path).name))
        if folder_notes and parts and parent not in noted_dirs:
            noted_dirs.add(parent)
            note = note_for(parent, folder_notes)
            if note:
                note_at[parts] = note

    lines = [PlanTreeLine(0, f"{Path(base).name or base or 'target'}/")]

    def _walk(node: dict, depth: int, acc: tuple[str, ...]) -> None:
        for op_id, filename in sorted(files_at.get(acc, []), key=lambda t: t[1]):
            lines.append(PlanTreeLine(depth, f"{filename}{suffixes.get(op_id, '')}", op_id))
        for name, children in sorted(node.items()):
            new_acc = acc + (name,)
            note = note_at.get(new_acc, "")
            suffix = f"  — {note}" if note else ""
            lines.append(PlanTreeLine(depth, f"{name}/{suffix}"))
            _walk(children, depth + 1, new_acc)

    _walk(tree, 1, ())
    return lines
