"""Collision, overwrite, and quarantine guardrails."""

from __future__ import annotations

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
