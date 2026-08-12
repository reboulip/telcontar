"""Tests for host/web/tree.py — NiceGUI-free directory-tree node builder."""

from __future__ import annotations

import os
from pathlib import Path

from host.web.tree import (
    PLACEHOLDER_SUFFIX,
    build_nodes,
    find_node,
    list_drive_roots,
    load_children,
    needs_loading,
    rebuild_nodes,
)


def test_build_nodes_returns_single_root_node_with_loaded_children(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x")

    nodes = build_nodes(tmp_path)

    assert len(nodes) == 1
    root = nodes[0]
    assert root["id"] == str(tmp_path)
    assert root["label"] == tmp_path.name
    labels = {c["label"] for c in root["children"]}
    assert labels == {"sub", "file.txt"}


def test_build_nodes_falls_back_to_full_path_for_drive_root() -> None:
    # A drive root (e.g. "C:\\") has an empty .name — the label must not be
    # blank.
    root = Path("C:\\")

    nodes = build_nodes(root)

    assert nodes[0]["label"] == str(root)


def test_load_children_lists_files_and_folders(tmp_path: Path) -> None:
    (tmp_path / "b_dir").mkdir()
    (tmp_path / "a_file.txt").write_text("x")

    children = load_children(tmp_path)

    labels = [c["label"] for c in children]
    assert set(labels) == {"b_dir", "a_file.txt"}


def test_load_children_sorts_folders_before_files_then_alphabetically(tmp_path: Path) -> None:
    (tmp_path / "z_dir").mkdir()
    (tmp_path / "a_dir").mkdir()
    (tmp_path / "b_file.txt").write_text("x")
    (tmp_path / "a_file.txt").write_text("x")

    children = load_children(tmp_path)

    assert [c["label"] for c in children] == ["a_dir", "z_dir", "a_file.txt", "b_file.txt"]


def test_load_children_hides_dotfiles_but_shows_quarantine(tmp_path: Path) -> None:
    (tmp_path / ".organizer").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "_quarantine").mkdir()

    children = load_children(tmp_path)

    labels = {c["label"] for c in children}
    assert labels == {"_quarantine"}


def test_load_children_skips_symlinks(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # symlink privilege not available in this environment — skip

    children = load_children(tmp_path)

    labels = {c["label"] for c in children}
    assert labels == {"real"}


def test_load_children_returns_empty_list_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    def _raise_iterdir(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", _raise_iterdir)

    assert load_children(tmp_path) == []


def test_load_children_returns_empty_list_for_missing_directory(tmp_path: Path) -> None:
    assert load_children(tmp_path / "does-not-exist") == []


def test_entry_node_for_directory_carries_placeholder_child(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()

    [node] = load_children(tmp_path)

    assert node["children"] == [{"id": str(sub) + PLACEHOLDER_SUFFIX, "label": "…", "children": []}]


def test_entry_node_for_file_has_no_children(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")

    [node] = load_children(tmp_path)

    assert node["children"] == []


def test_needs_loading_true_for_placeholder_only_node() -> None:
    node = {"id": "d", "children": [{"id": "d" + PLACEHOLDER_SUFFIX, "label": "…", "children": []}]}

    assert needs_loading(node) is True


def test_needs_loading_false_once_real_children_are_loaded() -> None:
    node = {"id": "d", "children": [{"id": "d/x", "label": "x", "children": []}]}

    assert needs_loading(node) is False


def test_needs_loading_false_for_leaf_node() -> None:
    assert needs_loading({"id": "f", "children": []}) is False


def test_find_node_locates_nested_node() -> None:
    nodes = [
        {
            "id": "root",
            "children": [{"id": "child", "children": [{"id": "grandchild", "children": []}]}],
        }
    ]

    found = find_node(nodes, "grandchild")

    assert found is not None
    assert found["id"] == "grandchild"


def test_find_node_returns_none_when_absent() -> None:
    assert find_node([{"id": "a", "children": []}], "missing") is None


def test_list_drive_roots_returns_empty_or_paths() -> None:
    # Platform-agnostic: on Windows this returns at least one drive; on any
    # platform without os.listdrives it must degrade to an empty list rather
    # than raising.
    result = list_drive_roots()

    assert isinstance(result, list)
    assert all(isinstance(p, Path) for p in result)


# ── rebuild_nodes (U4) ────────────────────────────────────────────────────────


def test_rebuild_nodes_matches_build_nodes_with_no_expanded_ids(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x")

    assert rebuild_nodes(tmp_path, set()) == build_nodes(tmp_path)


def test_rebuild_nodes_eagerly_loads_an_expanded_directory(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x")

    [root] = rebuild_nodes(tmp_path, {str(sub)})

    [sub_node] = [c for c in root["children"] if c["id"] == str(sub)]
    assert sub_node["children"] == [
        {"id": str(sub / "nested.txt"), "label": "nested.txt", "children": []}
    ]


def test_rebuild_nodes_leaves_unexpanded_directories_as_placeholders(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x")

    [root] = rebuild_nodes(tmp_path, set())

    [sub_node] = [c for c in root["children"] if c["id"] == str(sub)]
    assert needs_loading(sub_node)


def test_rebuild_nodes_recurses_into_nested_expanded_directories(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    nested = sub / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_text("x")

    [root] = rebuild_nodes(tmp_path, {str(sub), str(nested)})

    [sub_node] = [c for c in root["children"] if c["id"] == str(sub)]
    [nested_node] = [c for c in sub_node["children"] if c["id"] == str(nested)]
    assert nested_node["children"] == [
        {"id": str(nested / "deep.txt"), "label": "deep.txt", "children": []}
    ]


def test_rebuild_nodes_tolerates_a_stale_expanded_id_for_a_path_that_no_longer_exists(
    tmp_path: Path,
) -> None:
    # A directory the user had expanded may have been renamed/moved away by
    # the very op that triggered this refresh — its id in expanded_ids no
    # longer matches anything under root, and must not raise.
    nodes = rebuild_nodes(tmp_path, {str(tmp_path / "renamed-away")})

    assert nodes
