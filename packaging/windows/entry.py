"""PyInstaller entry point for the frozen telcontar builds.

Both executables in the bundle (``telcontar`` — windowed, ``telcontar-cli`` —
console) run this script; they differ only in the subsystem flag set in
``telcontar.spec``.
"""

import multiprocessing

from host.main import main

if __name__ == "__main__":
    # NiceGUI's native window runs pywebview in a spawned child process. A frozen
    # exe re-launches itself for that child, and this call routes it to the
    # child's work instead of starting a second telcontar. It must run first.
    multiprocessing.freeze_support()
    main()
