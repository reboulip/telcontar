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

# X13: the White Tree of Gondor — a branching silver trunk/roots with seven
# gold stars arced above it, echoing Gondor's own banner. Both this favicon
# and LOGO_SVG below (the full-size header mark) are the same motif at two
# sizes, so tab and header read as one identity — this replaces the earlier
# Elendil's-star-only mark. An inline SVG string is a legal
# `ui.run(favicon=...)` value (NiceGUI inlines it as a data URL; no file, no
# network request); native mode additionally applies it as the taskbar icon
# when it resolves to a local file, which this deliberately does not — see
# `host/web/assets/telcontar.ico` (unchanged, a follow-up to regenerate).
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#0E1116"/>'
    '<g fill="none" stroke="#AEB6C4" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<path d="M16,26 L16,15"/>'
    '<path d="M16,15 L11,10 M11,10 L8,6.5 M11,10 L13,5.5"/>'
    '<path d="M16,15 L21,10 M21,10 L24,6.5 M21,10 L19,5.5"/>'
    '<path d="M16,26 L16,28 M16,28 L12,30 M16,28 L20,30"/>'
    "</g>"
    '<g fill="#C8A951">'
    '<polygon points="16,2.2 17.4,3.6 16,5 14.6,3.6"/>'
    '<polygon points="9,5 10.2,6.2 9,7.4 7.8,6.2"/>'
    '<polygon points="23,5 24.2,6.2 23,7.4 21.8,6.2"/>'
    "</g>"
    "</svg>"
)

# X13: the full-size header mark — same White Tree motif, more branch/root
# detail and the full seven-star arc (Gondor's banner is a white tree under
# seven stars). Transparent background: it sits directly on the nav
# header's own dark surface, not a standalone tile like the favicon.
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="32" height="32">'
    '<g fill="none" stroke="#AEB6C4" stroke-width="2.2" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<path d="M32,52 L32,30"/>'
    '<path d="M32,30 L22,20 M22,20 L16,13 M22,20 L26,11"/>'
    '<path d="M32,30 L42,20 M42,20 L48,13 M42,20 L38,11"/>'
    '<path d="M32,52 L32,56 M32,56 L24,60 M32,56 L40,60 M32,56 L28,60 M32,56 L36,60"/>'
    "</g>"
    '<g fill="#C8A951">'
    '<polygon points="8,10.2 9.8,12 8,13.8 6.2,12"/>'
    '<polygon points="17,6 19,8 17,10 15,8"/>'
    '<polygon points="26,2.8 28.2,5 26,7.2 23.8,5"/>'
    '<polygon points="32,1.1 34.4,3.5 32,5.9 29.6,3.5"/>'
    '<polygon points="38,2.8 40.2,5 38,7.2 35.8,5"/>'
    '<polygon points="47,6 49,8 47,10 45,8"/>'
    '<polygon points="56,10.2 57.8,12 56,13.8 54.2,12"/>'
    "</g>"
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
        ".tc-logo {\n"
        "  display: inline-flex;\n"
        "  align-items: center;\n"
        "}\n"
        # X2 defense-in-depth: a rule on the message content beats an
        # inherited `user-select: none` (no `!important` needed — a more
        # specific descendant selector always wins) — belt-and-braces on
        # top of the native-window text_select fix (host/web/main.py) and
        # the resize-drag pointer-capture fix (this module's _RESIZE_JS).
        # `.q-message-text`/`.q-message-text-content` are Quasar's own
        # QChatMessage content classes — verify they still exist if a
        # future Quasar upgrade ever changes this.
        ".q-message-text, .q-message-text-content {\n"
        "  user-select: text;\n"
        "  -webkit-user-select: text;\n"
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
