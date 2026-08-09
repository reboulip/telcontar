"""Product identity — no NiceGUI import, matching the invariant already kept
by session.py/bridge.py/tree.py, so this stays testable in plain pytest.

`window_title()` (T7) is the browser tab title, computed fresh per page
build since it depends on the run's target directory — unlike `ui.run()`'s
own `title=` kwarg (used for the global default, before any run exists),
this must be pushed live via `ui.page_title()` inside the page body.

The rest (T8) is telcontar's visual identity — a Númenórean/human king
(Aragorn's Quenya name), his elven connections, and kingly metal: gold and
silver accents on a dark base. Applied through exactly two mechanisms, both
wired once in `run_web()`, never per-component:

- `PALETTE` -> `app.colors(**PALETTE)` — the *only* legal way to set colours
  here; a per-page `ui.colors()` call would silently override this global
  and re-fragment the identity across routes.
- `css()` -> `ui.add_css(css(), shared=True)` — one CSS layer covering the
  display typeface (applied to Quasar's own `.text-hN` heading classes, so
  every existing heading picks it up with no code changes elsewhere) and a
  mandatory button-contrast fix (see `css()`'s docstring).

`font_face_css()` degrades gracefully: the `@font-face` rule is only emitted
when the vendored woff2 actually exists on disk, but the fallback stack
("Trajan Pro" / "Palatino Linotype" / Georgia / serif) is always present
regardless, so a missing font file is a slightly-plainer heading, never a
404 or a blank page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# Exactly the keyword set `nicegui.app.colors()` accepts — verified against
# the installed version. Positive/negative stay in their own green/red hue
# families (desaturated to fit, never re-hued gold/silver): the approval
# dialog's Approve/Reject buttons are the highest-trust screen in the
# product and must stay unmistakable at a glance.
PALETTE: dict[str, str] = {
    "primary": "#C8A951",  # gold
    "secondary": "#AEB6C4",  # mithril silver
    "accent": "#8FA6BF",  # pale elven blue
    "dark": "#171B22",  # elevated surfaces: drawer, cards, dialogs
    "dark_page": "#0E1116",  # page background
    "positive": "#4E9A6B",  # Approve / Proceed
    "negative": "#B4544A",  # Reject / Cancel
    "info": "#7FA6C4",
    "warning": "#D9A441",
}

# Elendil's seven-pointed star, gold on the dark base — an inline SVG string
# is a legal `ui.run(favicon=...)` value (NiceGUI inlines it as a data URL;
# no file, no network request).
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#0E1116"/>'
    '<polygon points="16.0,3.0 18.5,10.8 26.2,7.9 21.7,14.7 28.7,18.9 20.5,19.6 '
    '21.6,27.7 16.0,21.8 10.4,27.7 11.5,19.6 3.3,18.9 10.3,14.7 5.8,7.9 13.5,10.8" '
    'fill="#C8A951"/>'
    "</svg>"
)

# Always present, whether or not the vendored font file exists — an
# elvish-flavoured but genuinely readable Roman-inscriptional face is the
# realistic best case on a Windows box even with zero bytes shipped.
_FALLBACK_FONT_STACK = '"Trajan Pro", "Palatino Linotype", "Book Antiqua", Georgia, serif'

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FONT_FILENAME = "cinzel-latin-600.woff2"
FONT_URL_PATH = "/tc-fonts"  # app.add_static_files mount point, run_web()

# V13b: ui.codemirror defaults to the "basicLight" theme regardless of the
# app's own dark palette above — every read-only codemirror in the product
# (the step-detail drawer, the prompt-inspection view) must pass this
# explicitly, or it renders as a jarring white panel on the dark shell.
# `Final` (not just a plain `str` annotation) keeps this narrowed to the
# literal `ui.codemirror(theme=...)` expects, rather than widening to `str`.
CODEMIRROR_THEME: Final = "basicDark"


def font_face_css(font_dir: Path | None = None) -> str:
    """`@font-face` for the vendored Cinzel woff2 — emitted only if the file
    actually exists on disk (``font_dir`` defaults to the real assets
    directory; overridable for tests). Returns ``""`` when absent so a
    missing font is silently a plainer heading, never a 404."""
    woff2 = (font_dir or FONT_DIR) / _FONT_FILENAME
    if not woff2.is_file():
        return ""
    return (
        "@font-face {\n"
        '  font-family: "Cinzel";\n'
        "  font-weight: 600;\n"
        "  font-display: swap;\n"
        f'  src: url("{FONT_URL_PATH}/{_FONT_FILENAME}") format("woff2");\n'
        "}\n"
    )


def css(font_dir: Path | None = None) -> str:
    """The one small CSS layer T8 calls for — display typeface bound to
    Quasar's own heading classes (so every existing `text-hN` label picks it
    up automatically, no per-component sprinkling), the `.tc-display`
    utility class, and Quasar's `.q-message-name` (chat sender-name slot) —
    a couple of further deliberate spots (V13c) — plus a mandatory
    contrast fix: Quasar renders a filled `color="primary"` button with a
    white label, and white-on-gold (`PALETTE["primary"]`) is roughly a
    2.2:1 contrast ratio — unreadable, and this is the approval dialog's
    button family, the highest-trust screen in the product.
    """
    return (
        font_face_css(font_dir) + ".text-h1, .text-h2, .text-h3, .text-h4, .text-h5, .text-h6,\n"
        ".tc-display, .q-message-name {\n"
        f"  font-family: 'Cinzel', {_FALLBACK_FONT_STACK};\n"
        "  letter-spacing: 0.02em;\n"
        "}\n"
        ".q-btn.bg-primary {\n"
        "  color: #0E1116 !important;\n"
        "}\n"
    )


def window_title(target: Path | None = None) -> str:
    """ "telcontar", or "telcontar — <name>" once a target directory is
    selected. Falls back to the full path string for a drive root (e.g.
    ``Path("C:\\\\").name == ""`` on Windows) so the title is never left
    with a blank, dangling suffix."""
    if target is None:
        return "telcontar"
    suffix = target.name or str(target)
    return f"telcontar — {suffix}"
