"""PostToolUse hook: catch a broken Python edit the moment it happens.

Reads the hook input JSON from stdin, and if the edited/written file is a
.py file, parses it with ast.parse. On a SyntaxError, emits a PostToolUse
block decision so the model sees the error immediately instead of finding
it several tool calls later (e.g. via a manual ast.parse or a test run).
"""

import ast
import json
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not path.endswith(".py"):
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return 0

    try:
        ast.parse(source, filename=path)
    except SyntaxError as exc:
        message = f"Syntax error in {path}: {exc}"
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": message,
                    "systemMessage": message,
                }
            )
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
