"""Tests for host/web/theme.py — no NiceGUI import, plain pytest."""

from __future__ import annotations

from pathlib import Path

from host.web.theme import (
    FAVICON_SVG,
    LOGO_SVG,
    PALETTE,
    css,
    font_face_css,
    window_title,
)

# Exactly the keyword set nicegui.app.colors() accepts.
_QUASAR_COLOR_TOKENS = {
    "primary",
    "secondary",
    "accent",
    "dark",
    "dark_page",
    "positive",
    "negative",
    "info",
    "warning",
}


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


# ── PALETTE ────────────────────────────────────────────────────────────────


def test_palette_matches_quasar_color_tokens_exactly() -> None:
    assert set(PALETTE.keys()) == _QUASAR_COLOR_TOKENS


def test_palette_values_are_hex_colors() -> None:
    for value in PALETTE.values():
        assert value.startswith("#")
        assert len(value) in (4, 7)  # "#abc" or "#aabbcc"


def test_palette_positive_and_negative_stay_in_their_hue_families() -> None:
    # Approve/Reject must never be re-hued gold/silver — the approval
    # dialog is the highest-trust screen in the product.
    assert PALETTE["positive"] not in (PALETTE["primary"], PALETTE["secondary"])
    assert PALETTE["negative"] not in (PALETTE["primary"], PALETTE["secondary"])


# ── favicon ───────────────────────────────────────────────────────────────


def test_favicon_svg_is_a_raw_svg_string() -> None:
    assert FAVICON_SVG.strip().startswith("<svg")
    assert FAVICON_SVG.strip().endswith("</svg>")


def test_favicon_svg_uses_the_white_tree_palette() -> None:
    # X13: tab and header must show one consistent mark — silver
    # trunk/branches, gold stars, same two hex values as LOGO_SVG below.
    assert "#AEB6C4" in FAVICON_SVG
    assert "#C8A951" in FAVICON_SVG


# ── header logo (X13) ────────────────────────────────────────────────────


def test_logo_svg_is_a_raw_svg_string() -> None:
    assert LOGO_SVG.strip().startswith("<svg")
    assert LOGO_SVG.strip().endswith("</svg>")


def test_logo_svg_uses_the_white_tree_palette() -> None:
    assert "#AEB6C4" in LOGO_SVG
    assert "#C8A951" in LOGO_SVG


def test_logo_svg_has_no_background_fill() -> None:
    # Unlike the favicon (a standalone tile), the header logo sits directly
    # on the nav header's own dark surface — no <rect> background.
    assert "<rect" not in LOGO_SVG


def test_css_sizes_the_header_logo() -> None:
    assert ".tc-logo" in css()


# ── chat text selectability (X2, defense-in-depth) ──────────────────────────


def test_css_makes_chat_message_text_selectable() -> None:
    """X2 layer 3: a rule on the message content beats an inherited
    `user-select: none` — belt-and-braces on top of the native-window
    text_select fix (host/web/main.py) and the resize-drag pointer-capture
    fix (host/web/shell.py's _RESIZE_JS)."""
    result = css()

    assert ".q-message-text" in result
    assert "user-select: text" in result


# ── tree connectors and density (X7) ────────────────────────────────────────


def test_css_styles_sidebar_tree_connectors() -> None:
    result = css()

    assert ".tc-tree" in result
    assert "q-tree__node" in result


def test_css_styles_plan_tree_guides() -> None:
    result = css()

    assert ".tc-tree-guide" in result
    assert ".tc-tree-row" in result


# ── font_face_css / css ──────────────────────────────────────────────────


def test_font_face_css_empty_when_font_file_absent(tmp_path: Path) -> None:
    assert font_face_css(tmp_path) == ""


def test_font_face_css_present_when_font_file_exists(tmp_path: Path) -> None:
    (tmp_path / "cinzel-latin-600.woff2").write_bytes(b"fake-woff2-bytes")

    result = font_face_css(tmp_path)

    assert "@font-face" in result
    assert "Cinzel" in result
    assert "cinzel-latin-600.woff2" in result


def test_css_always_contains_fallback_stack_regardless_of_font_file(
    tmp_path: Path,
) -> None:
    result = css(tmp_path)

    assert "Trajan Pro" in result
    assert "Palatino Linotype" in result
    assert "Georgia" in result


def test_css_contains_button_contrast_fix() -> None:
    result = css()

    assert ".q-btn.bg-primary" in result
    assert "#0E1116" in result


def test_css_binds_display_font_to_quasar_heading_classes() -> None:
    result = css()

    assert ".text-h1" in result
    assert ".text-h6" in result
    assert "Cinzel" in result


def test_css_binds_display_font_to_tc_display_and_chat_message_name() -> None:
    # V13c: a couple of deliberate, non-heading spots also get the display
    # face — the `.tc-display` utility class and Quasar's chat-message
    # sender-name slot (`.q-message-name`) — without a broad selector change.
    result = css()

    assert ".tc-display" in result
    assert ".q-message-name" in result


def test_css_includes_font_face_when_file_present(tmp_path: Path) -> None:
    (tmp_path / "cinzel-latin-600.woff2").write_bytes(b"fake-woff2-bytes")

    assert "@font-face" in css(tmp_path)
