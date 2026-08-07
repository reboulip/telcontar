"""Tests for host/web/tree.py — NiceGUI-free directory-tree node builder."""

from __future__ import annotations

from pathlib import Path

from host.web.tree import build_nodes


def test_build_nodes_returns_single_root_node(tmp_path: Path) -> None:
    nodes = build_nodes(tmp_path)

    assert len(nodes) == 1
    assert nodes[0]["id"] == str(tmp_path)
    assert nodes[0]["label"] == tmp_path.name
    assert nodes[0]["children"] == []


def test_build_nodes_falls_back_to_full_path_for_drive_root() -> None:
    # A drive root (e.g. "C:\\") has an empty .name — the label must not be
    # blank.
    root = Path("C:\\")

    nodes = build_nodes(root)

    assert nodes[0]["label"] == str(root)
