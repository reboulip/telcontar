"""F2 — robust error handling: bad paths, permission errors, and partial plan failures.

These tests assert that filesystem tools fail *gracefully and informatively*
rather than surfacing raw tracebacks:

- bad paths raise clear, typed errors (``ValueError`` / ``FileNotFoundError``);
- permission / lock failures are re-raised with a human-readable message via
  ``format_io_error`` (and, inside ``execute_plan``, keep their exception type so
  a transient ``PermissionError`` stays retryable while a missing source fails fast).

Permission errors are simulated with mocks so the suite is deterministic and
cross-platform (no reliance on real read-only files or OS-specific chmod).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from server.guards import format_io_error
from server.plan import Plan, PlanOp, load, save
from server.tools import (
    compute_checksum,
    execute_plan,
    read_file,
    write_summary,
)


# ── format_io_error helper ────────────────────────────────────────────────────


class TestFormatIoError:
    def test_permission_denied_hint(self) -> None:
        msg = format_io_error("write", "C:/x/y.txt", PermissionError("access is denied"))
        assert "Could not write C:/x/y.txt" in msg
        assert "permission denied" in msg

    def test_windows_lock_hint_from_message(self) -> None:
        exc = OSError(
            "The process cannot access the file because it is being used by another process"
        )
        msg = format_io_error("move", "C:/x/y.txt", exc)
        assert "open in another program" in msg

    def test_windows_lock_hint_from_winerror(self) -> None:
        class _Locked(OSError):
            winerror = 32  # simulate WinError 32 without touching the OS descriptor

        msg = format_io_error("move", "C:/x/y.txt", _Locked("blocked"))
        assert "open in another program" in msg

    def test_plain_oserror_has_context_but_no_hint(self) -> None:
        msg = format_io_error("read", "/tmp/z", OSError("No space left on device"))
        assert "Could not read /tmp/z" in msg
        assert "No space left on device" in msg
        assert "permission denied" not in msg


# ── bad paths raise clear, typed errors ───────────────────────────────────────


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "journal.jsonl"


def _approved_plan_with_op(op_type: str, src: str, dst: str, plans_dir: Path, **params) -> Plan:
    p = Plan.new()
    p.transition("approved")
    p.ops.append(PlanOp.new(op_type, src, dst, params=params or None))  # type: ignore[arg-type]
    save(p, plans_dir)
    return p


class TestBadPaths:
    def test_read_file_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_file(str(tmp_path / "missing.txt"), 100)

    def test_compute_checksum_on_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            compute_checksum(str(tmp_path))

    def test_create_dir_over_existing_file(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        p = _approved_plan_with_op("create_dir", str(f), "", plans_dir)
        result = execute_plan(p.plan_id, plans_dir, journal_path)
        assert result["ops_failed"] == 1
        reloaded = load(p.plan_id, plans_dir)
        assert "not a directory" in (reloaded.ops[0].error or "").lower()


# ── permission errors on read/write tools (mocked) ────────────────────────────


class TestPermissionErrors:
    def test_read_file_wraps_permission_error(self, tmp_path: Path) -> None:
        f = tmp_path / "locked.txt"
        f.write_text("secret")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with pytest.raises(OSError, match="Could not read") as ei:
                read_file(str(f), 100)
        assert "permission denied" in str(ei.value)

    def test_compute_checksum_wraps_permission_error(self, tmp_path: Path) -> None:
        f = tmp_path / "locked.bin"
        f.write_bytes(b"data")
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with pytest.raises(OSError, match="Could not read"):
                compute_checksum(str(f))

    def test_create_file_wraps_permission_error(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        target = tmp_path / "sub" / "new.txt"
        p = _approved_plan_with_op("create_file", str(target), "", plans_dir, content="content")
        original_write_text = Path.write_text

        def flaky_write_text(self, *args, **kwargs):
            if self == target:
                raise PermissionError("denied")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", flaky_write_text):
            execute_plan(p.plan_id, plans_dir, journal_path)
        reloaded = load(p.plan_id, plans_dir)
        assert "permission denied" in (reloaded.ops[0].error or "").lower()

    def test_write_summary_wraps_permission_error(self, tmp_path: Path) -> None:
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            with pytest.raises(OSError, match="Could not write"):
                write_summary(str(tmp_path), "hi")

    def test_create_dir_wraps_permission_error(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        target = tmp_path / "sub"
        p = _approved_plan_with_op("create_dir", str(target), "", plans_dir)
        original_mkdir = Path.mkdir

        def flaky_mkdir(self, *args, **kwargs):
            if self == target:
                raise PermissionError("denied")
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", flaky_mkdir):
            execute_plan(p.plan_id, plans_dir, journal_path)
        reloaded = load(p.plan_id, plans_dir)
        assert "could not create directory" in (reloaded.ops[0].error or "").lower()


# ── partial plan failures: permission errors during execution ─────────────────


class TestExecutePlanPermissionErrors:
    def _approved_move(self, tmp_path: Path, plans_dir: Path) -> Plan:
        src = tmp_path / "file.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        p = Plan.new()
        p.transition("approved")
        p.ops.append(PlanOp.new("move", str(src), str(dst_dir)))
        save(p, plans_dir)
        return p

    def test_permission_error_is_retried_and_message_is_clear(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = self._approved_move(tmp_path, plans_dir)

        locked = PermissionError(
            "The process cannot access the file because it is being used by another process"
        )
        with patch("server.tools.shutil.move", side_effect=locked):
            result = execute_plan(p.plan_id, plans_dir, journal_path)

        assert result["state"] == "failed"
        assert result["ops_failed"] == 1

        reloaded = load(p.plan_id, plans_dir)
        # A lock is transient → retried (not fail-fast), so all 3 attempts are spent.
        assert reloaded.ops[0].retries == 3
        assert reloaded.ops[0].error is not None
        assert "Could not move" in reloaded.ops[0].error
        assert "open in another program" in reloaded.ops[0].error

    def test_permission_error_does_not_hard_stop_single_op(
        self, tmp_path: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        p = self._approved_move(tmp_path, plans_dir)
        with patch("server.tools.shutil.move", side_effect=PermissionError("denied")):
            result = execute_plan(p.plan_id, plans_dir, journal_path)
        # One failing op must not trip the >3-failure hard stop.
        assert result["hard_stop"] is False
