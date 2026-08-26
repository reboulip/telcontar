#!/usr/bin/env python3
"""REPL driver for telcontar's NiceGUI web UI.

telcontar has no chromium-cli in this environment, so this drives the app
directly with Playwright. It launches host.web.main.run_web() as a
subprocess and captures the per-launch auth token + port it generates
internally (V2 security hardening — never printed to stdout, see
host/web/security.py), then feeds a Chromium page through REPL commands
read from stdin. Meant to be run under tmux (send-keys / capture-pane) so
an agent can iterate without relaunching the app each time.

Run from the repo root:
    uv run --with playwright python .claude/skills/run-telcontar/driver.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHOT_DIR = Path(os.environ.get("SCREENSHOT_DIR", "/tmp/shots"))
SHOT_DIR.mkdir(parents=True, exist_ok=True)
CONN_FILE = Path(os.environ.get("TELCONTAR_CONN_FILE", "/tmp/telcontar_conn.json"))
APP_LOG = Path(os.environ.get("TELCONTAR_APP_LOG", "/tmp/telcontar_app.log"))

# This script relaunches itself (via sys.executable, same venv) as the app
# subprocess when invoked with this marker as argv[1] — see cmd_launch().
_LAUNCHER_MARKER = "--_internal-launch"


def _run_launcher(target: str | None) -> None:
    """Subprocess entry point: boots the NiceGUI app and writes its
    token+port to CONN_FILE. LLM_BASE_URL/LLM_API_KEY are stubbed so
    is_configured() passes and the setup wizard is skipped — no real
    endpoint is ever called by the flows this driver exercises (the
    starter pane's directory overview is code-generated, no LLM call;
    clicking "Start organizing" WOULD call this fake endpoint and fail)."""
    os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/unconfigured")
    os.environ.setdefault("LLM_API_KEY", "unconfigured-smoke-test-key")
    sys.path.insert(0, str(REPO_ROOT))
    from host.web import security as security_mod
    import host.web.main as web_main

    orig_configure = security_mod.configure

    def _capture(token: str, port: int) -> None:
        CONN_FILE.write_text(json.dumps({"token": token, "port": port}))
        orig_configure(token, port)

    security_mod.configure = _capture
    web_main.security.configure = _capture
    web_main.run_web(target=Path(target) if target else None, native=False)


if len(sys.argv) > 1 and sys.argv[1] == _LAUNCHER_MARKER:
    _run_launcher(sys.argv[2] if len(sys.argv) > 2 else None)
    sys.exit(0)


from playwright.sync_api import sync_playwright  # noqa: E402

_proc: subprocess.Popen | None = None
_pw = None
_browser = None
_page = None
_conn: dict | None = None


def _base_url() -> str:
    assert _conn is not None
    return f"http://127.0.0.1:{_conn['port']}"


def cmd_launch(arg: str) -> None:
    """launch [target-dir] — boot the app (optionally pre-selecting a
    target directory, skipping the landing page's picker) and open it in
    headless Chromium."""
    global _proc, _pw, _browser, _page, _conn
    if _proc is not None:
        print("already launched")
        return
    CONN_FILE.unlink(missing_ok=True)
    target = arg.strip() or None
    argv = [sys.executable, __file__, _LAUNCHER_MARKER, *([target] if target else [])]
    log = APP_LOG.open("w")
    _proc = subprocess.Popen(argv, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while not CONN_FILE.exists():
        if time.time() > deadline:
            print(f"TIMEOUT waiting for app to start — see {APP_LOG}")
            return
        if _proc.poll() is not None:
            print(f"app process exited early, code {_proc.returncode} — see {APP_LOG}")
            return
        time.sleep(0.3)
    _conn = json.loads(CONN_FILE.read_text())
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(args=["--no-sandbox"])
    _page = _browser.new_page()
    _page.on(
        "console",
        lambda m: print(f"[console:{m.type}]", m.text) if m.type == "error" else None,
    )
    url = f"{_base_url()}/?token={_conn['token']}"
    _page.goto(url, wait_until="networkidle", timeout=30000)
    print("launched.", _page.url)


def cmd_nav(arg: str) -> None:
    """nav <path> — navigate to an app-relative path, e.g. `nav /settings`."""
    if _page is None:
        print("ERROR: launch first")
        return
    path = arg.strip() or "/"
    _page.goto(f"{_base_url()}{path}", wait_until="networkidle", timeout=30000)
    print("nav ->", _page.url)


def cmd_ss(arg: str) -> None:
    """ss [name] — screenshot to SCREENSHOT_DIR/<name>.png (default /tmp/shots)."""
    if _page is None:
        print("ERROR: launch first")
        return
    name = arg.strip() or f"ss-{int(time.time())}"
    f = SHOT_DIR / f"{name}.png"
    _page.screenshot(path=str(f))
    print("screenshot:", f)


def cmd_click_text(arg: str) -> None:
    """click-text <exact text> — click the element with this exact visible
    text (NiceGUI upper-cases nav labels via CSS only — match the raw DOM
    text, e.g. "Settings" not "SETTINGS")."""
    if _page is None:
        print("ERROR: launch first")
        return
    try:
        _page.get_by_text(arg, exact=True).first.click(timeout=10000)
        print("click-text", repr(arg), "-> OK")
    except Exception as e:
        print("click-text", repr(arg), "-> ERROR", e)


def cmd_click(arg: str) -> None:
    """click <css-selector> — click the first matching element."""
    if _page is None:
        print("ERROR: launch first")
        return
    try:
        _page.locator(arg).first.click(timeout=10000)
        print("click", arg, "-> OK")
    except Exception as e:
        print("click", arg, "-> ERROR", e)


def cmd_fill(arg: str) -> None:
    """fill <css-selector> <text> — fill an input (goes through NiceGUI's
    Vue binding correctly, unlike an `eval el.value = ...` shortcut)."""
    if _page is None:
        print("ERROR: launch first")
        return
    sel, _, text = arg.partition(" ")
    try:
        _page.locator(sel).first.fill(text, timeout=10000)
        print("fill", sel, "-> OK")
    except Exception as e:
        print("fill", sel, "-> ERROR", e)


def cmd_text(arg: str) -> None:
    """text [css-selector] — print innerText (default: whole body)."""
    if _page is None:
        print("ERROR: launch first")
        return
    sel = arg.strip()
    try:
        loc = _page.locator(sel) if sel else _page.locator("body")
        print(loc.first.inner_text(timeout=10000))
    except Exception as e:
        print("ERROR", e)


def cmd_eval(arg: str) -> None:
    """eval <js-expression> — evaluate in the page, print JSON."""
    if _page is None:
        print("ERROR: launch first")
        return
    try:
        print(json.dumps(_page.evaluate(arg)))
    except Exception as e:
        print("ERROR", e)


def cmd_wait(arg: str) -> None:
    """wait <css-selector> — wait up to 10s for an element to appear."""
    if _page is None:
        print("ERROR: launch first")
        return
    try:
        _page.wait_for_selector(arg, timeout=10000)
        print("found:", arg)
    except Exception:
        print("TIMEOUT:", arg)


def cmd_quit(_arg: str) -> None:
    """quit — close the browser and stop the app subprocess."""
    global _proc, _pw, _browser
    if _browser is not None:
        _browser.close()
    if _pw is not None:
        _pw.stop()
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    print("quit")


def cmd_help(_arg: str) -> None:
    print("commands:", ", ".join(sorted(COMMANDS)))


COMMANDS = {
    "launch": cmd_launch,
    "nav": cmd_nav,
    "ss": cmd_ss,
    "click": cmd_click,
    "click-text": cmd_click_text,
    "fill": cmd_fill,
    "text": cmd_text,
    "eval": cmd_eval,
    "wait": cmd_wait,
    "quit": cmd_quit,
    "help": cmd_help,
}


def main() -> None:
    print("telcontar driver — 'help' for commands, 'launch [target-dir]' to start")
    print("driver> ", end="", flush=True)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            print("driver> ", end="", flush=True)
            continue
        cmd, _, rest = line.partition(" ")
        fn = COMMANDS.get(cmd)
        if fn is None:
            print("unknown:", cmd, "— try: help")
        else:
            try:
                fn(rest)
            except Exception as e:
                print("ERROR:", e)
        if cmd == "quit":
            break
        print("driver> ", end="", flush=True)


if __name__ == "__main__":
    main()
