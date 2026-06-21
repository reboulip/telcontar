"""Collision, overwrite, and quarantine guardrails."""
from __future__ import annotations

from pathlib import Path


def check_no_overwrite(dest: Path) -> None:
    """Raise if dest already exists — never clobber."""
    raise NotImplementedError


def safe_quarantine_path(src: Path, quarantine_dir: Path) -> Path:
    """Return a non-colliding destination path inside quarantine_dir for src."""
    raise NotImplementedError
