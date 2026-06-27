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
