"""Folder-status engine for the sidebar tree (AB4a, #76) — NiceGUI-free.

Answers "how is telcontar managing this folder?" for every folder node, so the
tree can show a red / yellow / green indicator:

* ``unmanaged`` (red) — no ``.organizer/`` at or above the folder, or the
  ``.organizer/`` holds no registry yet.
* ``new_files`` (yellow) — managed, but a file in the folder's subtree is not
  in the registry yet.
* ``up_to_date`` (green) — managed and every file in the subtree is registered.
* ``pending`` / ``unknown`` — a scan has not finished / could not prove green.

Read-only by design: it never creates ``.organizer/``, builds an LLM client or
calls MCP. It reads ``server.registry`` directly, as ``host/web/corpus.py``
does. One scan per organizer root walks the whole root once and rolls the
result up to every folder; scans run on a single worker thread and are cached
in memory only (never persisted), so a deep tree costs one background walk,
not one per folder. "Unregistered" means the file's sha256 is not a registry
key — the same rule ``run_prepass`` uses for *new* files — with a root-relative
path match as a hash-free fast path.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

UNMANAGED = "unmanaged"
NEW_FILES = "new_files"
UP_TO_DATE = "up_to_date"
PENDING = "pending"
UNKNOWN = "unknown"

# state -> (Quasar icon, Quasar color). Shapes differ as well as colors, so the
# indicator never relies on red vs. green alone.
STATE_STYLE: dict[str, tuple[str, str]] = {
    UNMANAGED: ("radio_button_unchecked", "negative"),
    NEW_FILES: ("error_outline", "warning"),
    UP_TO_DATE: ("check_circle", "positive"),
    PENDING: ("hourglass_empty", "grey-6"),
    UNKNOWN: ("help_outline", "grey-6"),
}
STATE_LABEL: dict[str, str] = {
    UNMANAGED: "Not managed by telcontar",
    NEW_FILES: "Managed — some files are not analyzed yet",
    UP_TO_DATE: "Managed — everything is up to date",
    PENDING: "Checking…",
    UNKNOWN: "Status unknown",
}

SCAN_TTL = 30.0  # seconds before a cached scan is re-run
MIN_RESCAN_INTERVAL = 5.0  # the registry is rewritten after every analysis batch
MAX_FILES = 100_000  # walk cap; past it the unfinished part is UNKNOWN, never green
HASH_BUDGET_BYTES = 256 * 1024 * 1024  # bytes hashed per scan for path-unmatched files

# Test seam: run scans synchronously on the calling thread.
SCAN_INLINE = False


@dataclass
class RootScan:
    finished_at: float
    registry_mtime: tuple[float, int] | None
    # normcased dir paths whose subtree holds an unregistered file
    dirty: set[str] = field(default_factory=set)
    new_count: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


_lock = threading.Lock()
_scans: dict[str, RootScan] = {}
_in_flight: set[str] = set()
_last_started: dict[str, float] = {}
_root_memo: dict[str, tuple[float, str | None]] = {}
_executor: ThreadPoolExecutor | None = None


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def reset() -> None:
    """Drop every cache (tests; a clean slate)."""
    with _lock:
        _scans.clear()
        _in_flight.clear()
        _last_started.clear()
        _root_memo.clear()


def invalidate(root: Path | str | None = None) -> None:
    """Forget cached scans so the next poll re-scans (all roots when None)."""
    with _lock:
        if root is None:
            _scans.clear()
            _last_started.clear()
            _root_memo.clear()
        else:
            _scans.pop(_key(root), None)
            _last_started.pop(_key(root), None)


# ── Root resolution ──────────────────────────────────────────────────────────


def organizer_root(folder: Path | str) -> Path | None:
    """Nearest ancestor-or-self with a ``.organizer/`` directory. Walks the
    path as given (not ``.resolve()``d), memoized with a short TTL — one stat
    per folder per TTL."""
    start = Path(folder)
    k = _key(start)
    now = time.monotonic()
    with _lock:
        hit = _root_memo.get(k)
    if hit is not None and now - hit[0] < SCAN_TTL:
        return Path(hit[1]) if hit[1] else None
    found: Path | None = None
    current = start
    while True:
        try:
            if (current / ".organizer").is_dir():
                found = current
                break
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    with _lock:
        _root_memo[k] = (now, str(found) if found else None)
    return found


def _registry_path(root: Path) -> Path:
    from host.paths import resolve_registry_path

    return resolve_registry_path(root)


# ── The scan ─────────────────────────────────────────────────────────────────


def _load_checksums_and_paths(root: Path) -> tuple[set[str], set[str]] | None:
    """(checksums, root-relative normcased paths) from the registry, or None
    when unreadable (torn write mid-run — caller keeps the last good scan)."""
    try:
        from server.registry import load

        registry = load(_registry_path(root))
    except Exception:
        return None
    prefixes = {_key(root)}
    try:
        prefixes.add(_key(root.resolve()))
    except OSError:
        pass
    checksums: set[str] = set()
    rel_paths: set[str] = set()
    for rec in registry.documents.values():
        checksums.add(rec.checksum)
        recorded = _key(rec.path or "")
        for prefix in prefixes:
            if recorded.startswith(prefix + os.sep):
                rel_paths.add(recorded[len(prefix) + 1 :])
                break
    return checksums, rel_paths


def _quarantine_name(root: Path) -> str:
    try:
        from config.settings import Settings

        return os.path.normcase(Path(str(Settings().for_target(root).quarantine_dir)).name)
    except Exception:
        return os.path.normcase("_quarantine")


def scan_root(root: Path) -> RootScan | None:
    """Walk ``root`` once and roll unregistered files up to their folders.
    Returns None when the registry could not be read."""
    from host.agent import _DISCOVERY_SKIP_NAMES

    loaded = _load_checksums_and_paths(root)
    if loaded is None:
        return None
    checksums, rel_paths = loaded
    quarantine = _quarantine_name(root)
    root_key = _key(root)

    scan = RootScan(finished_at=time.monotonic(), registry_mtime=_registry_mtime(root))
    seen_files = 0
    hashed_bytes = 0
    stack: list[Path] = [root]
    while stack:
        folder = stack.pop()
        folder_new = 0
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name in _DISCOVERY_SKIP_NAMES or name.startswith("."):
                continue
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if os.path.normcase(name) == quarantine:
                        continue
                    # A nested organizer root gets its own scan.
                    if (Path(entry.path) / ".organizer").is_dir():
                        continue
                    stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            seen_files += 1
            if seen_files > MAX_FILES:
                scan.truncated = True
                stack.clear()
                break
            rel = _key(entry.path)[len(root_key) + 1 :]
            if rel in rel_paths:
                continue
            # Path-unmatched: a moved/duplicate copy may still be registered
            # by content, so hash it — within a budget; past it, count as new.
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            if hashed_bytes + size <= HASH_BUDGET_BYTES:
                hashed_bytes += size
                try:
                    from server.tools import compute_checksum

                    if compute_checksum(entry.path)["checksum"] in checksums:
                        continue
                except Exception:
                    pass
            folder_new += 1
        if folder_new:
            _mark(scan, folder, root_key, folder_new)
    return scan


def _mark(scan: RootScan, folder: Path, root_key: str, count: int) -> None:
    """Credit ``count`` new files to ``folder`` and every ancestor up to root."""
    current = folder
    while True:
        k = _key(current)
        scan.dirty.add(k)
        scan.new_count[k] = scan.new_count.get(k, 0) + count
        if k == root_key or current.parent == current:
            break
        current = current.parent


def _registry_mtime(root: Path) -> tuple[float, int] | None:
    from host.web import corpus

    return corpus.registry_mtime(root)


# ── Scheduling & lookup ──────────────────────────────────────────────────────


def _run_scan(root: Path) -> None:
    k = _key(root)
    try:
        scan = scan_root(root)
    except Exception:
        scan = None
    with _lock:
        _in_flight.discard(k)
        if scan is not None:
            _scans[k] = scan


def _schedule(root: Path) -> None:
    global _executor
    k = _key(root)
    now = time.monotonic()
    with _lock:
        if k in _in_flight:
            return
        if now - _last_started.get(k, -1e9) < MIN_RESCAN_INTERVAL:
            return
        _in_flight.add(k)
        _last_started[k] = now
        if not SCAN_INLINE and _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="folderstatus")
    if SCAN_INLINE:
        _run_scan(root)
    else:
        assert _executor is not None
        _executor.submit(_run_scan, root)


def _is_stale(root: Path, scan: RootScan) -> bool:
    if time.monotonic() - scan.finished_at > SCAN_TTL:
        return True
    return _registry_mtime(root) != scan.registry_mtime


def _state_in(scan: RootScan, folder_key: str) -> str:
    if folder_key in scan.dirty:
        return NEW_FILES
    return UNKNOWN if scan.truncated else UP_TO_DATE


def state_for(folder: Path | str) -> tuple[str, Path | None]:
    """Cache-only state of one folder (+ its organizer root). Schedules a
    background scan when the cache is missing or stale and keeps serving the
    old answer meanwhile. Never walks the tree on the calling thread."""
    root = organizer_root(folder)
    if root is None:
        return UNMANAGED, None
    try:
        if not _registry_path(root).is_file():
            return UNMANAGED, root
    except OSError:
        return UNMANAGED, root
    with _lock:
        scan = _scans.get(_key(root))
    if scan is None or _is_stale(root, scan):
        _schedule(root)
        with _lock:
            scan = _scans.get(_key(root))
    if scan is None:
        return PENDING, root
    return _state_in(scan, _key(folder)), root


def states_for(dir_ids: list[str]) -> dict[str, str]:
    return {i: state_for(i)[0] for i in dir_ids}


def new_count(folder: Path | str) -> int | None:
    """Number of unregistered files under ``folder`` from the cached scan, or
    None when no scan has finished for its root."""
    root = organizer_root(folder)
    if root is None:
        return None
    with _lock:
        scan = _scans.get(_key(root))
    if scan is None:
        return None
    return scan.new_count.get(_key(folder), 0)


# ── Tree decoration ──────────────────────────────────────────────────────────


def folder_ids(nodes: list[dict]) -> list[str]:
    """Ids of the folder nodes in a ``ui.tree`` node list: a node with
    children (real or the lazy placeholder) is a folder. No filesystem I/O."""
    out: list[str] = []
    for node in nodes:
        children = node.get("children") or []
        if children:
            out.append(node["id"])
            out.extend(folder_ids(children))
    return out


def decorate(nodes: list[dict], states: dict[str, str]) -> bool:
    """Set ``icon`` / ``iconColor`` on folder nodes **in place**; returns
    whether anything changed. Never replaces the list or any node, so a
    concurrent expand that spliced children in keeps them."""
    changed = False
    for node in nodes:
        state = states.get(node["id"])
        if state is not None:
            icon, color = STATE_STYLE[state]
            if node.get("icon") != icon or node.get("iconColor") != color:
                node["icon"] = icon
                node["iconColor"] = color
                changed = True
        if decorate(node.get("children") or [], states):
            changed = True
    return changed
