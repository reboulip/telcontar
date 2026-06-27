"""Tests for compress_quarantine + its undo (server/tools.py)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from server import journal as _journal
from server import tools


def _qfile(qdir: Path, name: str, data: bytes) -> Path:
    qdir.mkdir(parents=True, exist_ok=True)
    p = qdir / name
    p.write_bytes(data)
    return p


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "_quarantine",
        tmp_path / ".organizer" / "journal.jsonl",
        tmp_path / ".organizer" / "plans",
    )


# ── Happy path ────────────────────────────────────────────────────────────────


def test_compress_creates_verified_archive_and_removes_originals(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")
    _qfile(qdir, "b.bin", b"\x00\x01\x02beta")

    result = tools.compress_quarantine(qdir, journal)

    assert result["files"] == 2
    assert result["verified"] is True
    assert result["deleted_originals"] is True
    archive = Path(result["archive"])
    assert archive.is_file()
    # originals are gone, only the archive remains
    assert not (qdir / "a.txt").exists()
    assert not (qdir / "b.bin").exists()


def test_archive_is_lossless(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    payloads = {"a.txt": b"alpha" * 100, "b.bin": bytes(range(256))}
    for name, data in payloads.items():
        _qfile(qdir, name, data)

    result = tools.compress_quarantine(qdir, journal)

    with zipfile.ZipFile(result["archive"], "r") as zf:
        for name, data in payloads.items():
            assert zf.read(name) == data


def test_compress_journals_a_compress_entry(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")

    tools.compress_quarantine(qdir, journal)

    entry = _journal.last(journal)
    assert entry is not None
    assert entry["op_type"] == "compress"
    assert entry["deleted_originals"] is True
    assert len(entry["files"]) == 1
    assert entry["files"][0]["sha256"] == hashlib.sha256(b"alpha").hexdigest()


def test_compress_keeps_originals_when_flag_false(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")

    result = tools.compress_quarantine(qdir, journal, delete_originals=False)

    assert result["deleted_originals"] is False
    assert (qdir / "a.txt").exists()
    assert Path(result["archive"]).is_file()


# ── No-op / idempotency ───────────────────────────────────────────────────────


def test_compress_no_files_is_noop(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    qdir.mkdir(parents=True)

    result = tools.compress_quarantine(qdir, journal)

    assert result["files"] == 0
    assert result["archive"] is None
    assert _journal.last(journal) is None


def test_compress_missing_dir_is_noop(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)

    result = tools.compress_quarantine(qdir, journal)

    assert result["files"] == 0
    assert result["archive"] is None


def test_compress_skips_its_own_archives(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")
    tools.compress_quarantine(qdir, journal)  # produces quarantine_*.zip

    # second run has no loose files left → no-op, the prior archive is left alone
    result = tools.compress_quarantine(qdir, journal)
    assert result["files"] == 0
    archives = list(qdir.glob("quarantine_*.zip"))
    assert len(archives) == 1


# ── Collision safety ──────────────────────────────────────────────────────────


def test_compress_archive_name_is_collision_safe(tmp_path: Path) -> None:
    qdir, journal, _ = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")
    first = Path(tools.compress_quarantine(qdir, journal)["archive"])

    # a new loose file + an archive named identically already on disk
    _qfile(qdir, "b.txt", b"beta")
    clash = qdir / first.name
    clash.write_bytes(b"not a real zip")

    second = Path(tools.compress_quarantine(qdir, journal)["archive"])
    assert second != clash
    assert second.is_file()
    assert clash.read_bytes() == b"not a real zip"  # never overwritten


# ── Undo ──────────────────────────────────────────────────────────────────────


def test_undo_compress_restores_files_and_removes_archive(tmp_path: Path) -> None:
    qdir, journal, plans = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")
    _qfile(qdir, "b.bin", b"beta-bytes")
    archive = Path(tools.compress_quarantine(qdir, journal)["archive"])

    undone = tools.undo_last(journal, plans)

    assert undone["undone"]["op_type"] == "compress"
    assert (qdir / "a.txt").read_bytes() == b"alpha"
    assert (qdir / "b.bin").read_bytes() == b"beta-bytes"
    assert not archive.exists()
    assert _journal.last(journal) is None


def test_undo_compress_kept_originals_only_removes_archive(tmp_path: Path) -> None:
    qdir, journal, plans = _paths(tmp_path)
    _qfile(qdir, "a.txt", b"alpha")
    archive = Path(tools.compress_quarantine(qdir, journal, delete_originals=False)["archive"])

    undone = tools.undo_last(journal, plans)

    assert undone["undone"]["op_type"] == "compress"
    assert (qdir / "a.txt").read_bytes() == b"alpha"
    assert not archive.exists()
    assert _journal.last(journal) is None
