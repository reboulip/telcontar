"""Tests for read-only server tools: list_dir, read_file, extract_text, compute_checksum."""

from __future__ import annotations

import hashlib
import pytest
from pathlib import Path

from server.tools import compare_documents, compute_checksum, list_dir, read_file, extract_text


class TestListDir:
    def test_returns_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        result = list_dir(str(tmp_path))
        names = {e["name"] for e in result["entries"]}
        assert "a.txt" in names
        assert "sub" in names

    def test_entry_has_required_fields(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("x")
        result = list_dir(str(tmp_path))
        entry = next(e for e in result["entries"] if e["name"] == "f.txt")
        assert entry["type"] == "file"
        assert isinstance(entry["size"], int)
        assert isinstance(entry["mtime"], float)

    def test_dirs_sorted_before_files(self, tmp_path: Path) -> None:
        (tmp_path / "z_file.txt").write_text("x")
        (tmp_path / "a_dir").mkdir()
        result = list_dir(str(tmp_path))
        types = [e["type"] for e in result["entries"]]
        assert types.index("dir") < types.index("file")

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = list_dir(str(tmp_path))
        assert result["entries"] == []

    def test_raises_for_file(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="Not a directory"):
            list_dir(str(f))

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a directory"):
            list_dir(str(tmp_path / "missing"))


class TestReadFile:
    def test_reads_content(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        assert read_file(str(f), 100) == "hello world"

    def test_truncates_at_max_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("a" * 200)
        result = read_file(str(f), 10)
        assert result.startswith("a" * 10)
        assert "[... content truncated ...]" in result

    def test_no_truncation_indicator_when_fits(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("short")
        result = read_file(str(f), 100)
        assert "truncated" not in result

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_file(str(tmp_path), 100)

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_file(str(tmp_path / "missing.txt"), 100)

    def test_handles_non_utf8_gracefully(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.txt"
        f.write_bytes(b"\xff\xfe hello")
        result = read_file(str(f), 1000)
        assert isinstance(result, str)


class TestExtractText:
    def test_extracts_plain_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "note.txt"
        f.write_text("plain text content")
        result = extract_text(str(f), 1000)
        assert "plain text content" in result

    def test_truncates_at_max_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("x" * 500)
        result = extract_text(str(f), 10)
        assert "[... content truncated ...]" in result

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            extract_text(str(tmp_path), 100)

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            extract_text(str(tmp_path / "missing.pdf"), 100)

    def test_extracts_real_pdf(self, tmp_path: Path) -> None:
        # Minimal hand-built single-page PDF with a text-drawing content stream.
        # Regression guard for the markitdown[pdf] extra (pdfminer.six) — without
        # it, extraction raises MissingDependencyException instead of returning text.
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/MediaBox [0 0 200 200] /Contents 5 0 R >>\nendobj\n"
            b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"5 0 obj\n<< /Length 44 >>\nstream\n"
            b"BT /F1 24 Tf 20 100 Td (Hello PDF) Tj ET\n"
            b"endstream\nendobj\n"
            b"xref\n0 6\n0000000000 65535 f \ntrailer\n<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n0\n%%EOF"
        )
        f = tmp_path / "doc.pdf"
        f.write_bytes(pdf_bytes)
        result = extract_text(str(f), 1000)
        assert "Hello PDF" in result

    def test_extracts_real_xlsx(self, tmp_path: Path) -> None:
        # Regression guard for the markitdown[xlsx] extra (openpyxl) — without it,
        # extraction raises MissingDependencyException instead of returning text.
        openpyxl = pytest.importorskip("openpyxl")
        f = tmp_path / "sheet.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "Hello"
        ws["B1"] = "World"
        wb.save(f)
        result = extract_text(str(f), 1000)
        assert "Hello" in result
        assert "World" in result


class TestComputeChecksum:
    def test_matches_hashlib_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_bytes(b"some bytes here")
        result = compute_checksum(str(f))
        assert result["checksum"] == hashlib.sha256(b"some bytes here").hexdigest()
        assert result["path"] == str(f)

    def test_identical_content_same_checksum(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"dup")
        (tmp_path / "b.txt").write_bytes(b"dup")
        assert (
            compute_checksum(str(tmp_path / "a.txt"))["checksum"]
            == compute_checksum(str(tmp_path / "b.txt"))["checksum"]
        )

    def test_different_content_different_checksum(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"one")
        (tmp_path / "b.txt").write_bytes(b"two")
        assert (
            compute_checksum(str(tmp_path / "a.txt"))["checksum"]
            != compute_checksum(str(tmp_path / "b.txt"))["checksum"]
        )

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            compute_checksum(str(tmp_path))

    def test_raises_for_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            compute_checksum(str(tmp_path / "missing.txt"))


class TestCompareDocuments:
    def test_diff_shows_changes(self, tmp_path: Path) -> None:
        a = tmp_path / "v1.txt"
        b = tmp_path / "v2.txt"
        a.write_text("line one\nline two\nline three\n")
        b.write_text("line one\nline TWO changed\nline three\n")
        result = compare_documents(str(a), str(b), 1000)
        assert result["identical"] is False
        assert "-line two" in result["diff"]
        assert "+line TWO changed" in result["diff"]

    def test_identical_files_empty_diff(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("same content\nsecond line\n")
        b.write_text("same content\nsecond line\n")
        result = compare_documents(str(a), str(b), 1000)
        assert result["identical"] is True
        assert result["diff"] == ""

    def test_truncates_each_side(self, tmp_path: Path) -> None:
        a = tmp_path / "big_a.txt"
        b = tmp_path / "big_b.txt"
        a.write_text("x" * 500)
        b.write_text("y" * 500)
        result = compare_documents(str(a), str(b), 10)
        assert "[... content truncated ...]" in result["diff"]

    def test_raises_if_first_missing(self, tmp_path: Path) -> None:
        b = tmp_path / "b.txt"
        b.write_text("x")
        with pytest.raises(ValueError, match="Not a file"):
            compare_documents(str(tmp_path / "missing.txt"), str(b), 100)

    def test_raises_if_second_missing(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        a.write_text("x")
        with pytest.raises(ValueError, match="Not a file"):
            compare_documents(str(a), str(tmp_path / "missing.txt"), 100)
