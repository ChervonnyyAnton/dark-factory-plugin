---
name: dark-factory
description: Start, stop, monitor, or dry-run the Dark Factory issue-delivery controller.
---

# Dark Factory

Use the plugin binary from the target project:

- Start: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" start --workspace "$PWD" [--issue N | --epic N]`
- Stop gracefully: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" stop --workspace "$PWD"`
- Monitor durable state: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" monitor --workspace "$PWD"`
- Preview selection without provider, PR, or merge writes: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" run --dry-run --workspace "$PWD" [--issue N | --epic N]`

Optional targeting (mutually exclusive):

- `--issue N` — pin a single open issue assigned to the policy assignee set (ignores queue labels).
- `--epic N` — deliver linked open children oldest-first; when all children are closed, run epic closure.

Slash commands forward `$ARGUMENTS` unchanged, for example `/dark-factory start --epic 56` or `/dark-factory-dry-run --issue 58`.

Starting requires `.dark-factory/policy.json` and grants the detached controller
the policy-defined delivery authority. Stop preserves controller state and
evidence so a later start can resume.
