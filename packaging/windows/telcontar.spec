# PyInstaller spec for the Windows release build (also builds on Linux/macOS,
# which is how CI-free smoke tests of the freeze logic are done).
#
#   uv sync --group build
#   uv run pyinstaller --noconfirm packaging/windows/telcontar.spec
#
# Output: dist/telcontar/ (one-dir). One-dir, not one-file: one-file unpacks itself
# to %TEMP% on every start, which is slower and is what antivirus products and
# application-control policies flag most often.
#
# Two executables share one set of libraries:
#   telcontar.exe      windowed (no console) — what a user double-clicks
#   telcontar-cli.exe  console — stdio for the MCP server subprocess, --version,
#                      --auto-class, and any terminal use
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))  # noqa: F821 (PyInstaller global)
IS_WIN = sys.platform == "win32"

datas = [
    # Mirror the source layout: code resolves both from __file__, which inside a
    # bundle is <bundle>/host/web/..., so project_root becomes the bundle root.
    (os.path.join(ROOT, "profiles"), "profiles"),
    (os.path.join(ROOT, "host", "web", "assets"), os.path.join("host", "web", "assets")),
]
binaries = []
hiddenimports = []

# First-party packages: host.web.main and server.main are imported lazily
# (inside functions), and tools register themselves on import.
for pkg in ("host", "server", "config"):
    hiddenimports += collect_submodules(pkg)

# Third-party packages that ship data files, native models, or load plugins by
# name at runtime — static import analysis misses all of that.
collect = [
    "nicegui",  # static JS/CSS served to the UI
    "magika",  # markitdown's file-type detector, with its model files
    "markitdown",
    "extract_msg",
    "pptx",  # default slide templates
    "openpyxl",
    "onnxruntime",
    "keyring",  # backends are discovered by name
    "plyer",  # platform notification backends are imported by name
]
if IS_WIN:
    collect.append("webview")  # pywebview: native window (Windows-only dependency)
for pkg in collect:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# importlib.metadata lookups at runtime (version string, keyring/markitdown entry points).
for dist in ("telcontar", "keyring", "markitdown"):
    datas += copy_metadata(dist)

a = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821

icon = os.path.join(ROOT, "host", "web", "assets", "telcontar.ico") if IS_WIN else None


def _exe(name: str, console: bool):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=icon,
        upx=False,  # UPX-packed binaries are a classic antivirus false-positive trigger
    )


gui = _exe("telcontar", console=False)
cli = _exe("telcontar-cli", console=True)

coll = COLLECT(  # noqa: F821
    gui,
    cli,
    a.binaries,
    a.datas,
    upx=False,
    name="telcontar",
)
