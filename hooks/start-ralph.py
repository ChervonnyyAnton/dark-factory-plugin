#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path


def parse_arguments(raw):
    tokens = shlex.split(raw)
    prompt = []
    maximum = 0
    promise = None
    seen = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("--max-iterations", "--completion-promise"):
            if token in seen or index + 1 == len(tokens):
                raise ValueError(f"invalid {token}")
            seen.add(token)
            index += 1
            value = tokens[index]
            if token == "--max-iterations":
                try:
                    maximum = int(value)
                except ValueError as error:
                    raise ValueError("--max-iterations must be a non-negative integer") from error
                if maximum < 0:
                    raise ValueError("--max-iterations must be a non-negative integer")
            else:
                promise = value
        elif token.startswith("--"):
            raise ValueError(f"unknown option: {token}")
        else:
            prompt.append(token)
        index += 1
    if not prompt:
        raise ValueError("Ralph requires an issue or prompt")
    return " ".join(prompt), maximum, promise


def issue_number(prompt):
    for pattern in (
        r"https?://github\.com/[^/\s]+/[^/\s]+/issues/(\d+)",
        r"#(\d+)\b",
        r"\bissue\s+(\d+)\b",
        r"^\s*(\d+)\s*$",
    ):
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def pid_is_live(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def controller_lock_is_live(root):
    path = root / ".dark-factory" / "controller.lock"
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        try:
            record = json.loads(os.read(descriptor, 65536))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record = {}
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = record.get("pid")
            return (
                isinstance(pid, int)
                and not isinstance(pid, bool)
                and pid > 0
                and pid_is_live(pid)
            )
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def controller_issue(root):
    try:
        issue = json.loads(
            (root / ".dark-factory" / "controller.json").read_text()
        ).get("issue")
    except (AttributeError, FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if isinstance(issue, dict):
        issue = issue.get("number")
    return issue if isinstance(issue, int) and not isinstance(issue, bool) else None


def write_session(root, prompt, maximum, promise):
    state_dir = root / ".dark-factory"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "ralph-session.json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=state_dir,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                {
                    "prompt": prompt,
                    "iteration": 1,
                    "max_iterations": maximum,
                    "completion_promise": promise,
                    "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
                },
                temporary,
                indent=2,
                sort_keys=True,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 2:
        raise ValueError("usage: start-ralph.py 'ISSUE-OR-PROMPT [OPTIONS]'")
    prompt, maximum, promise = parse_arguments(sys.argv[1])
    root = Path.cwd()
    requested_issue = issue_number(prompt)
    if controller_lock_is_live(root) and (
        requested_issue is None or requested_issue == controller_issue(root)
    ):
        raise RuntimeError(
            "a detached controller owns this work; run "
            f"dark-factory monitor --workspace {root} and monitor or stop the "
            "detached controller before starting Ralph"
        )
    write_session(root, prompt, maximum, promise)
    print("Started Ralph loop at iteration 1")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"ralph: {error}", file=sys.stderr)
        raise SystemExit(1)
