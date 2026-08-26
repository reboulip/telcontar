"""Collision, overwrite, and quarantine guardrails."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path


def check_no_overwrite(dest: Path) -> None:
    """Raise if dest already exists — never clobber."""
    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")


def safe_quarantine_path(src: Path, quarantine_dir: Path) -> Path:
    """Return a non-colliding destination path inside quarantine_dir for src."""
    dest = quarantine_dir / src.name
    if not dest.exists():
        return dest
    stem = src.stem
    suffix = src.suffix
    counter = 1
    while True:
        candidate = quarantine_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def check_allowlist(path: Path, allowlist_dirs: list[Path]) -> None:
    """Raise PermissionError if path is not under any allowlisted directory."""
    if not allowlist_dirs:
        return
    resolved = path.resolve()
    for allowed in allowlist_dirs:
        try:
            resolved.relative_to(allowed.resolve())
            return
        except ValueError:
            continue
    raise PermissionError(
        f"{path} is not within an allowed directory. Allowed: {[str(d) for d in allowlist_dirs]}"
    )


def check_within_root(path: Path, roots: list[Path]) -> None:
    """Raise PermissionError if path does not resolve inside one of ``roots``.

    Unlike ``check_allowlist`` (opt-in, empty = no restriction), this is meant to
    be called unconditionally with a non-empty ``roots`` (the run's target
    directory plus the ``.organizer`` working dir) so the target directory is a
    real confinement boundary rather than advisory. ``.resolve()`` normalizes
    both absolute paths and ``..`` escapes before the containment check, so
    either is rejected the same way as any other out-of-bounds path.
    """
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return
        except ValueError:
            continue
    raise PermissionError(
        f"{path} is outside the confined directories. Allowed: {[str(r) for r in roots]}"
    )


# X8: names an agent-proposed taxonomy folder must not collide with — the
# configured quarantine dir itself (handled separately, via normalize_dir_name
# below) plus a tight set of known discard/quarantine words across the
# languages a real corpus is likely to use. Whole-normalized-basename match
# only (never substring), so a legitimate folder like "quarantaine_sanitaire"
# or "archives" stays usable — broader words like "archive"/"obsolete" are
# deliberately excluded, they're real taxonomy categories in many corpora.
_QUARANTINE_ALIASES = frozenset(
    {
        "quarantine",
        "quarantaine",
        "quarantena",
        "cuarentena",
        "trash",
        "corbeille",
        "poubelle",
        "a_supprimer",
        "to_delete",
        "a_jeter",
        "junk",
    }
)

_ORDERING_PREFIX_RE = re.compile(r"^\d+[\s._-]*")
_SEPARATOR_RUN_RE = re.compile(r"[\s._-]+")


def normalize_dir_name(name: str) -> str:
    """Fold a directory name to a locale/case-insensitive comparison key.

    NFKD-decomposes and drops combining marks (so accented variants fold
    together, e.g. "Quarantäine" ~ "quarantaine"), casefolds (locale-
    insensitive, unlike ``.lower()``), strips a leading ordering prefix
    ("01_", "2. ", ...), trims leading/trailing separators, and collapses
    inner separator runs to a single underscore.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    without_prefix = _ORDERING_PREFIX_RE.sub("", folded)
    trimmed = without_prefix.strip("_-. ")
    return _SEPARATOR_RUN_RE.sub("_", trimmed)


def is_quarantine_like_name(name: str, quarantine_dir_name: str) -> bool:
    """True when ``name`` normalizes to the configured quarantine folder's
    name, or to a known quarantine/discard alias (X8) — a whole-basename
    match only, never substring containment."""
    normalized = normalize_dir_name(name)
    return (
        normalized == normalize_dir_name(quarantine_dir_name) or normalized in _QUARANTINE_ALIASES
    )


def check_not_quarantine_collision(dest: Path, quarantine_dir: Path) -> None:
    """Raise ``ValueError`` if ``dest`` collides with the server-managed
    quarantine folder (X8) — either its basename reads as quarantine-like,
    or ``dest`` resolves inside ``quarantine_dir`` itself (catches a nested
    taxonomy folder proposed *under* quarantine, e.g. ``_quarantine/drafts``).

    Proposal-time only (never from ``_apply_op``), and never wraps
    ``propose_quarantine``/``propose_archive_document``/
    ``propose_compress_quarantine`` — those legitimately target the
    quarantine dir, and guarding them would break quarantine outright.
    """
    if is_quarantine_like_name(dest.name, quarantine_dir.name):
        raise ValueError(
            f"{dest} collides with the server-managed quarantine folder "
            f"({quarantine_dir.name}). Use propose_quarantine(path, reason) to set a "
            "document aside; choose a different taxonomy folder name."
        )
    dest_norm = os.path.normcase(os.path.normpath(str(dest)))
    quarantine_norm = os.path.normcase(os.path.normpath(str(quarantine_dir)))
    if dest_norm == quarantine_norm or dest_norm.startswith(quarantine_norm + os.sep):
        raise ValueError(
            f"{dest} is inside the server-managed quarantine folder ({quarantine_dir}). "
            "Use propose_quarantine(path, reason) to set a document aside; choose a "
            "different taxonomy folder name."
        )


def normkey(path: Path) -> str:
    """Case/separator-normalized comparison key for a resolved path — used to
    compare paths for equality/membership across platforms (Windows path
    comparisons are case-insensitive)."""
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def is_dir_empty(path: Path) -> bool:
    """True if ``path`` is a directory with zero entries. False (never
    raises) if ``path`` doesn't exist, isn't a directory, or can't be listed."""
    try:
        return next(path.iterdir(), None) is None
    except OSError:
        return False


def safe_quarantine_dir_path(src: Path, quarantine_dir: Path) -> Path:
    """Like ``safe_quarantine_path`` but for directories: suffixes the WHOLE
    basename on collision (``2024.backup`` -> ``2024.backup_1``) instead of
    splitting a stem/suffix, which would mangle a dotted directory name."""
    dest = quarantine_dir / src.name
    if not dest.exists():
        return dest
    counter = 1
    while True:
        candidate = quarantine_dir / f"{src.name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def empty_marker_path(folder: Path, prefix: str = "_empty_") -> Path:
    """Collision-safe in-place rename target for a folder emptied by
    ``execute_plan`` moves (Y6) that can't be quarantined."""
    dest = folder.parent / f"{prefix}{folder.name}"
    if not dest.exists():
        return dest
    counter = 1
    while True:
        candidate = folder.parent / f"{prefix}{folder.name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def is_sweep_protected(
    path: Path,
    *,
    target_root: Path,
    quarantine_dir: Path,
    organizer_dir: Path,
    plan_created_dirs: set[str],
) -> bool:
    """True if ``path`` must never be auto-disposed of by the empty-folder
    sweep (Y6): the target root itself, anything outside it, the quarantine
    dir (or an ancestor/descendant of it), the ``.organizer`` working dir
    (likewise), and any directory this same plan run created via a
    ``create_dir`` op (``plan_created_dirs``, pre-normalized via ``normkey``)."""
    resolved = path.resolve()
    key = normkey(path)

    target_resolved = target_root.resolve()
    if key == normkey(target_root):
        return True
    try:
        resolved.relative_to(target_resolved)
    except ValueError:
        return True  # outside the target root entirely

    for protected_dir in (quarantine_dir, organizer_dir):
        protected_resolved = protected_dir.resolve()
        if key == normkey(protected_dir):
            return True
        try:
            protected_resolved.relative_to(resolved)
            return True  # `path` is an ancestor of this protected dir
        except ValueError:
            pass
        try:
            resolved.relative_to(protected_resolved)
            return True  # `path` is inside this protected dir
        except ValueError:
            pass

    return key in plan_created_dirs


def format_io_error(action: str, path: Path | str, exc: OSError) -> str:
    """Return a clear, consistent message for a failed filesystem operation.

    Wraps a raw ``OSError``/``PermissionError`` with the attempted action and the
    path it targeted, and surfaces the two operator-actionable cases in plain
    language: a Windows "file is in use" lock (WinError 32) and a plain
    permission denial. Callers re-raise with this message while preserving the
    original exception *type* (so retry classification is unaffected).
    """
    detail = str(exc) or exc.__class__.__name__
    winerr = getattr(exc, "winerror", None)
    locked = winerr == 32 or "used by another process" in detail.lower()
    if locked:
        hint = " (the file is open in another program — close it and retry)"
    elif isinstance(exc, PermissionError):
        hint = " (permission denied)"
    else:
        hint = ""
    return f"Could not {action} {path}: {detail}{hint}"
