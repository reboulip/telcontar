"""Tests for host/web/theme.py — no NiceGUI import, plain pytest."""

from __future__ import annotations

from pathlib import Path

from host.web.theme import window_title


def test_window_title_none_is_plain_telcontar() -> None:
    assert window_title(None) == "telcontar"


def test_window_title_no_arg_defaults_to_plain_telcontar() -> None:
    assert window_title() == "telcontar"


def test_window_title_uses_target_name() -> None:
    assert window_title(Path("invoices-2024")) == "telcontar — invoices-2024"


def test_window_title_uses_leaf_of_deep_path() -> None:
    assert window_title(Path("a/b/c/deep")) == "telcontar — deep"


def test_window_title_unicode_name_passes_through() -> None:
    assert window_title(Path("ñoño-folder")) == "telcontar — ñoño-folder"


def test_window_title_drive_root_falls_back_to_full_path() -> None:
    # Path("C:\\").name == "" on Windows — must not render a blank suffix.
    root = Path("C:\\")

    title = window_title(root)

    assert title != "telcontar — "
    assert title == f"telcontar — {root}"
