#!/bin/bash

set -euo pipefail

python3 -c '
import json
import os
import re
import sys
from pathlib import Path

state_path = Path(".dark-factory/ralph-session.json")
if not state_path.is_file():
    raise SystemExit(0)

try:
    state = json.loads(state_path.read_text())
    hook = json.load(sys.stdin)
    prompt = state["prompt"]
    iteration = state["iteration"]
    maximum = state["max_iterations"]
    promise = state.get("completion_promise")
    if (
        not isinstance(prompt, str)
        or not prompt
        or not isinstance(iteration, int)
        or isinstance(iteration, bool)
        or iteration < 1
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum < 0
    ):
        raise ValueError("invalid Ralph session fields")
except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
    print(f"Ralph session is invalid; stopping loop: {error}", file=sys.stderr)
    state_path.unlink(missing_ok=True)
    raise SystemExit(0)

state_session = state.get("session_id")
if state_session and state_session != hook.get("session_id"):
    raise SystemExit(0)

last_output = ""
transcript_path = Path(hook.get("transcript_path", ""))
try:
    for line in transcript_path.read_text().splitlines():
        entry = json.loads(line)
        message = entry.get("message", entry)
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            last_output = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    last_output = block.get("text", "")
except (OSError, json.JSONDecodeError):
    pass

if isinstance(promise, str) and promise:
    match = re.search(r"<promise>(.*?)</promise>", last_output, re.DOTALL)
    if match and " ".join(match.group(1).split()) == promise:
        print(f"Ralph loop completed: {promise}")
        state_path.unlink(missing_ok=True)
        raise SystemExit(0)

if maximum and iteration >= maximum:
    print(f"Ralph loop reached max iterations ({maximum})")
    state_path.unlink(missing_ok=True)
    raise SystemExit(0)

state["iteration"] = iteration + 1
temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
os.replace(temporary, state_path)

message = f"Ralph iteration {state['\''iteration'\'']}"
if promise:
    message += f" | To stop: output <promise>{promise}</promise> only when true"
print(json.dumps({
    "decision": "block",
    "reason": prompt,
    "systemMessage": message,
}))
' <<< "$(cat)"
