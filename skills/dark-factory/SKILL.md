---
name: dark-factory
description: Start, stop, monitor, or dry-run the Dark Factory issue-delivery controller.
---

# Dark Factory

Use the plugin binary from the target project:

- Start: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" start --workspace "$PWD"`
- Stop gracefully: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" stop --workspace "$PWD"`
- Monitor durable state: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" monitor --workspace "$PWD"`
- Preview selection without provider, PR, or merge writes: `"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" run --dry-run --workspace "$PWD"`

Starting requires `.dark-factory/policy.json` and grants the detached controller
the policy-defined delivery authority. Stop preserves controller state and
evidence so a later start can resume.
