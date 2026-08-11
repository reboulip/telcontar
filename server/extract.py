"""Text extraction from binary formats (PDF, Office) via markitdown.

S5 hardening: untrusted documents are bounded before/during parsing rather than
handed to markitdown unconditionally — a crash/DoS/zip-bomb guard, not a sandbox
(this is a local, single-user tool; full process isolation is out of scope).
"""

from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from pathlib import Path

import extract_msg
from markitdown import MarkItDown

_md = MarkItDown()

# .docx/.xlsx/.pptx are zip containers; a crafted entry with an extreme
# compressed:uncompressed ratio can exhaust memory/CPU on decompression
# (a "zip bomb") long before the overall *input* file size looks suspicious.
_ZIP_BASED_SUFFIXES = frozenset({".docx", ".xlsx", ".pptx", ".zip"})
_MAX_ZIP_RATIO = 100
# Below this uncompressed size, even a large ratio is unremarkable (e.g. a
# few bytes of repeated whitespace) — avoids false positives on tiny files.
_ZIP_RATIO_MIN_UNCOMPRESSED_BYTES = 10_000_000


def _check_not_a_zip_bomb(path: Path) -> None:
    if path.suffix.lower() not in _ZIP_BASED_SUFFIXES:
        return
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if (
                    info.file_size >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > _MAX_ZIP_RATIO
                ):
                    raise ValueError(
                        f"Refusing to extract {path}: archive entry {info.filename!r} "
                        f"has a suspicious compression ratio "
                        f"({info.file_size / info.compress_size:.0f}x, possible zip bomb)"
                    )
    except zipfile.BadZipFile:
        # Not actually a valid zip despite the extension — let markitdown's own
        # parsing surface the real error rather than raising a confusing one here.
        return


def _extract_msg(path: Path) -> str:
    """Parse an Outlook .msg file, preserving sender/recipients/date/subject
    as headers ahead of the body rather than lossily converting via markitdown."""
    msg = extract_msg.openMsg(str(path))
    try:
        headers = [
            f"From: {msg.sender or ''}",  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            f"To: {msg.to or ''}",  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        ]
        if msg.cc:  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            headers.append(f"Cc: {msg.cc}")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        if msg.bcc:  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            headers.append(f"Bcc: {msg.bcc}")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        headers.append(f"Date: {msg.date or ''}")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        headers.append(f"Subject: {msg.subject or ''}")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        return "\n".join(headers) + "\n\n" + (msg.body or "")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    finally:
        msg.close()


def extract(
    path: Path,
    max_chars: int,
    max_file_bytes: int = 200_000_000,
    timeout_secs: float = 30.0,
) -> str:
    """Return up to max_chars of plain text extracted from path.

    Raises ``ValueError`` if the input exceeds ``max_file_bytes`` or looks like a
    zip bomb, and ``TimeoutError`` if parsing exceeds ``timeout_secs`` wall clock.
    """
    size = path.stat().st_size
    if size > max_file_bytes:
        raise ValueError(
            f"Refusing to extract {path}: file is {size:,} bytes, "
            f"exceeding the {max_file_bytes:,}-byte limit"
        )
    _check_not_a_zip_bomb(path)

    def _parse() -> str:
        if path.suffix.lower() == ".msg":
            return _extract_msg(path)
        return _md.convert(str(path)).text_content

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_parse)
        try:
            text = future.result(timeout=timeout_secs)
        except _FutureTimeoutError as exc:
            raise TimeoutError(
                f"Extraction timed out after {timeout_secs:.0f}s: {path} "
                "(the file may be corrupted, pathological, or simply very large)"
            ) from exc

    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... content truncated ...]"
    return text
