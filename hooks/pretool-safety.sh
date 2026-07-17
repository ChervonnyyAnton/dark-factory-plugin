#!/bin/bash

python3 -c '
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_PATHS = [".env", ".env.*", ".github/workflows"]


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


try:
    hook = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(0)

tool_input = hook.get("tool_input")
if not isinstance(tool_input, dict):
    raise SystemExit(0)

policy = {}
try:
    loaded = json.loads(Path(".dark-factory/policy.json").read_text())
    if isinstance(loaded, dict):
        policy = loaded
except (OSError, ValueError):
    pass

command = tool_input.get("command", "")
if hook.get("tool_name") == "Bash" and isinstance(command, str):
    dangerous = (
        r"\bgit\s+push\b[^;&\n]*(?:--force(?:=true)?|-f)(?=\s|$)",
        r"\bgit\s+reset\b[^;&\n]*--hard(?=\s|$)",
    )
    denied_commands = policy.get("denied_commands", [])
    if any(re.search(pattern, command) for pattern in dangerous) or (
        isinstance(denied_commands, list)
        and any(
            isinstance(pattern, str) and fnmatch.fnmatch(command, pattern)
            for pattern in denied_commands
        )
    ):
        deny(f"Blocked dangerous command: {command}")
        raise SystemExit(0)

denied_paths = policy.get("denied_paths", DEFAULT_PATHS)
if not isinstance(denied_paths, list):
    denied_paths = DEFAULT_PATHS

candidate = next(
    (
        tool_input[key]
        for key in ("file_path", "notebook_path", "path")
        if isinstance(tool_input.get(key), str)
    ),
    None,
)
if candidate is None:
    raise SystemExit(0)

path = Path(candidate)
if path.is_absolute():
    try:
        path = path.relative_to(Path.cwd())
    except ValueError:
        pass
normalized = os.path.normpath(str(path)).removeprefix("./")

for pattern in denied_paths:
    if not isinstance(pattern, str):
        continue
    clean_pattern = os.path.normpath(pattern).removeprefix("./")
    if (
        fnmatch.fnmatch(normalized, clean_pattern)
        or normalized.startswith(clean_pattern.rstrip("/") + "/")
    ):
        deny(f"Blocked path by policy: {candidate}")
        break
' 
