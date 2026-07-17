# Dark Factory

Portable Claude Code plugin for GitHub-backed Dark Factory workflows: a detached issue queue supervisor, delivery-team agents, and in-session Ralph loops.

## Install

This repository is both the plugin and a single-plugin marketplace
(`.claude-plugin/marketplace.json`).

### Marketplace (recommended)

CLI:

```bash
claude plugin marketplace add ChervonnyyAnton/dark-factory-plugin
claude plugin install dark-factory@dark-factory-plugin --scope user
```

Or in Claude Code UI: `/plugins` → **Marketplaces** → **Add** →
`ChervonnyyAnton/dark-factory-plugin`, then install `dark-factory` from
**Discover**.

Update later with:

```bash
claude plugin marketplace update dark-factory-plugin
claude plugin update dark-factory@dark-factory-plugin
```

Or **Installed** → **Update now**.

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
| `/dark-factory start --issue N` | Pin a single open issue (assignee gate; ignores queue labels) |
| `/dark-factory start --epic N` | Deliver epic children oldest-first; epic closure when all children close |
| `/dark-factory-stop` | Request a graceful stop |
| `/dark-factory-monitor` | Print durable controller JSON (phase, issue, wake) |
| `/dark-factory-dry-run` | Discover/resume and prepare prompts without writes |
| `/dark-factory-dry-run --issue N` / `--epic N` | Dry-run with the same targeting as `start` |
| `/dark-factory:ralph …` | Start an in-session Ralph loop on one issue or task |
| `/dark-factory:cancel` | Cancel an active Ralph Stop-loop |

`--issue` and `--epic` are mutually exclusive. Slash commands pass trailing args via `$ARGUMENTS` (for example `/dark-factory-dry-run --issue 58`).

The `dark-factory` skill documents the same actions for non-slash invocation.

## Tooling roadmap

**v1 is Claude Code–first:** install via this plugin, commands, agents, and hooks. The supervisor, policy schema, queue/merge rules, and role contracts stay tool-agnostic so thin adapters for Codex, Cursor, Copilot, and similar CLIs can follow without rewriting the factory engine.

## License

MIT — see [LICENSE](LICENSE).
