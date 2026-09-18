"""Persistent per-directory memory file — ``.organizer/memory.md`` (Z5).

Holds notes that carry across sessions for the target directory: hand-written
by the user, or appended by the agent via the plan-gated ``memory_note`` op
(``server/tools.py``'s ``propose_memory_note``/``execute_plan``). This module
owns only the file format — read/render/append/truncate — never journaling
or plan state; that split mirrors ``server/events.py``'s relationship to the
project event journal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_HEADER = (
    "# telcontar memory\n\n"
    "Notes that carry across sessions for this directory. "
    "You can edit this file by hand.\n\n"
)


def read_memory(path: Path, max_chars: int) -> str:
    """Return memory.md's text, head-truncated to ``max_chars``.

    Returns "" for a missing file or any read failure — this feeds prompt
    composition and must never raise or block a run over a memory-file
    problem."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text


def render_block(note: str, *, first_write: bool) -> str:
    """Render one note as the text to append: the file header on the very
    first write to a new/empty file, then always one dated, provenance-tagged
    line — so a later read can tell agent-written notes from hand-written
    ones."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = f"- [{date}] (telcontar) {note}\n"
    return (_HEADER + line) if first_write else line


def append_block(path: Path, block: str) -> tuple[int, int]:
    """Append ``block`` to ``path``, returning ``(offset_before, bytes_written)``
    for the undo journal.

    Binary append of pre-encoded UTF-8 bytes — never text mode, which would
    translate ``\\n`` -> ``\\r\\n`` on Windows and desync the byte offset from
    what ``undo_last`` truncates back to. If the existing file doesn't already
    end in a newline, one is prepended to ``block`` and counted in
    ``bytes_written``. On a partial-write OSError, truncates back to
    ``offset_before`` before re-raising, so ``execute_plan``'s up-to-3
    retries can't duplicate a partial append."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(exist_ok=True)

    offset_before = p.stat().st_size
    data = block.encode("utf-8")
    if offset_before > 0:
        with p.open("rb") as f:
            f.seek(-1, 2)
            trailing = f.read(1)
        if trailing != b"\n":
            data = b"\n" + data

    try:
        with p.open("ab") as f:
            f.write(data)
    except OSError:
        truncate_to(p, offset_before)
        raise
    return offset_before, len(data)


def truncate_to(path: Path, offset: int) -> None:
    """Truncate ``path`` back to ``offset`` bytes — the undo for a
    ``memory_note`` op (and the recovery step for a failed append)."""
    with Path(path).open("r+b") as f:
        f.truncate(offset)


def contains_note(text: str, note: str) -> bool:
    """True if ``note`` already appears in ``text``, ignoring case and
    whitespace differences — used at proposal time to skip re-adding a note
    that's already recorded."""

    def _normalize(s: str) -> str:
        return " ".join(s.split()).casefold()

    return _normalize(note) in _normalize(text)
