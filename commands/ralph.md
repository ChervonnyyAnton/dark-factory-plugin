---
description: Start an in-session Dark Factory Ralph loop
argument-hint: "ISSUE-OR-PROMPT [--max-iterations N] [--completion-promise TEXT]"
allowed-tools: ["Bash(python3:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/dark-factory monitor:*)"]
---

# Ralph

Start a workspace-local Ralph loop from `$ARGUMENTS`.

1. Parse `--max-iterations N` (default `0`, unlimited) and
   `--completion-promise TEXT` (default `null`). Reject a missing prompt,
   unknown options, or a max that is not a non-negative integer.
2. If the prompt identifies an issue by `#N`, bare issue number, or GitHub
   issue URL, inspect `.dark-factory/controller.json`. When its current issue
   has the same number and `.dark-factory/controller.lock` is actively held
   (verify with a non-blocking `fcntl.flock` attempt), do not create Ralph
   state. Run
   `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" monitor --workspace "$PWD"` and tell
   the user to monitor or stop the detached controller before starting Ralph.
3. Otherwise, use Python's standard library to atomically write
   `.dark-factory/ralph-session.json` with:

```json
{
  "prompt": "<the parsed prompt>",
  "iteration": 1,
  "max_iterations": 0,
  "completion_promise": null,
  "session_id": "<CLAUDE_CODE_SESSION_ID when available>"
}
```

Preserve the parsed max and promise values in place of the defaults. Then work
on the prompt. Output `<promise>TEXT</promise>` only when the configured
completion promise is completely true.
