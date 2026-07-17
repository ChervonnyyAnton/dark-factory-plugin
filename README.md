# Dark Factory

Portable Claude Code plugin for GitHub-backed Dark Factory workflows: a detached issue queue supervisor, delivery-team agents, and in-session Ralph loops.

## Install

### Plugin manager

In Claude Code, run `/plugins`, add the marketplace that contains Dark Factory,
then install the `dark-factory` plugin.

### Local development

Run directly from a local checkout:

```bash
claude --plugin-dir /path/to/dark-factory-plugin
```

### Project policy

Copy the policy template into your target project:

```bash
mkdir -p .dark-factory
cp templates/policy.json .dark-factory/policy.json
```

Edit `.dark-factory/policy.json` for your queue filters, repositories, providers, and limits. Do not store secrets in policy.

The safety hook always blocks `git push --force` and `git reset --hard`. It also
blocks tool access to `denied_paths`; without a readable policy it falls back to
`.env`, `.env.*`, and `.github/workflows`.

### Merge and handoff

The supervisor's merge gate is deterministic: provider output cannot make a
pull request merge-ready or override failed gate evidence. On manual handoff or
after a verified merge, the factory releaser authors the human-facing note
under `.dark-factory/runs/` from the supervisor's final state.

## Commands

| Command | Description |
|---------|-------------|
| `/dark-factory` / `/dark-factory start` | Start the detached supervisor |
| `/dark-factory-stop` | Request a graceful stop |
| `/dark-factory-monitor` | Print durable controller JSON (phase, issue, wake) |
| `/dark-factory-dry-run` | Discover/resume and prepare prompts without writes |
| `/dark-factory:ralph …` | Start an in-session Ralph loop on one issue or task |
| `/dark-factory:cancel` | Cancel an active Ralph Stop-loop |

The `dark-factory` skill documents the same actions for non-slash invocation.

## Tooling roadmap

**v1 is Claude Code–first:** install via this plugin, commands, agents, and hooks. The supervisor, policy schema, queue/merge rules, and role contracts stay tool-agnostic so thin adapters for Codex, Cursor, Copilot, and similar CLIs can follow without rewriting the factory engine.

## License

MIT — see [LICENSE](LICENSE).
