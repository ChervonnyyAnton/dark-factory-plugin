# Dark Factory target args (`--issue` / `--epic`)

Date: 2026-07-17  
Status: approved

## Problem

With no arguments, the detached supervisor correctly selects the oldest open
issue that matches `.dark-factory/policy.json` queue filters. Operators also
need to pin a single issue or scope work to one epic (for example B2) without
changing sticky policy fields.

## Goals

- Keep **no-args** behavior unchanged.
- Add explicit per-run targeting via CLI flags on `start` and `run`.
- Always respect policy assignees (`queue.assignees`, including `@me`).
- For epics: deliver **children** while any remain open; when all linked
  children are closed, run an **epic closure** delivery that reviews, fixes,
  merges, and closes the epic.
- Pass the same flags through Claude slash commands via `$ARGUMENTS`.

## Non-goals

- Implementing the epic as a greenfield work item while child issues are still
  open (one PR that “builds the whole epic”).
- Sticky policy focus fields such as `queue.focus_epic` / `queue.focus_issue`.
- Auto-assigning unassigned children to the operator.
- Changing Ralph’s existing prompt-based issue targeting beyond documenting
  parity.

## CLI surface

```text
dark-factory start [--issue N | --epic N] [--workspace PATH]
dark-factory run --dry-run [--issue N | --epic N] [--workspace PATH]
```

- `--issue` and `--epic` are mutually exclusive.
- Valid only for `start` and `run` (including `run --dry-run`).
- `stop` / `monitor` unchanged.

Slash commands forward `$ARGUMENTS` unchanged, for example:

- `/dark-factory start --epic 56`
- `/dark-factory-dry-run --issue 58`

## Selection rules

### Shared

- Resolve issue numbers against policy `repositories`. If the number exists in
  more than one configured repository, fail with an explicit ambiguity error.
- If the number is missing from all configured repositories, fail.
- Assignees come from `resolve_assignees(policy)` (same as today, including
  `@me` expansion).

### No args

Unchanged: `list_matching_issues` + `select_issue` (oldest by
`createdAt`, then repository, then number).

### `--issue N`

1. Load issue `N` from the matching policy repository.
2. Require state **open**.
3. Require at least one assignee login in the policy assignee set.
4. **Do not** apply `queue.labels` (explicit pin).
5. Use that issue as the selected delivery target (dry-run prompt or live run).

### `--epic N`

1. Load epic `N`; require **open** and assigned to the policy assignee set.
2. Discover child numbers (dedupe, preserve discovery for errors):
   - GitHub sub-issues / tracked issues for the epic when available;
   - `#NN` references in the epic body (checklist links and plain `#NN`).
3. For each child number, load the issue from policy repositories.

#### Child delivery (any linked child still open)

Eligible child:

- state **open**;
- in a policy repository;
- assignees empty **or** intersect policy assignees;
- if assigned only to others → skip;
- if `queue.labels` is non-empty → child must match labels under the existing
  `queue.match` semantics for the label side only (assignee side already
  handled above). When `queue.labels` is empty, no label filter.

Select the oldest eligible child with the same tie-breakers as today.

#### Children done / closure phase

“All children done” means: every **successfully resolved** linked child issue
is **closed**. Checklist text is discovery only; unchecked boxes do not block
closure if the issue is closed. Child numbers that cannot be resolved in any
policy repository are ignored for the done check (logged in dry-run summary)
so stale `#NN` typos do not permanently block epic closure.

When no open eligible children remain and the epic is still open:

1. Select the **epic itself** as the delivery target in phase intent
   `epic_closure` (distinct from normal feature delivery).
2. Open a new branch; run comprehensive review of the epic’s implementation
   against epic goals / closed child outcomes.
3. Fix findings; self-review until clean (existing repair/review loop).
4. Merge according to `merge.mode`.
5. Close the epic issue (mark done).

If the epic is already closed when `--epic` is used and no open children
remain: exit cleanly with a summary; do **not** fall back to the global queue.

If `--epic` is set and selection fails for any other reason (epic not assigned,
epic missing, ambiguity): fail; do **not** fall back to the global queue.

## Controller / resume behavior

- Live controller resume (existing lock + `controller.json` issue) still wins
  over a new `--issue` / `--epic` on a second `start` while the controller is
  live for another issue — same exclusivity model as today.
- Dry-run always recomputes selection from args + policy and writes planning
  artifacts without provider/PR/merge side effects. For `--epic` closure,
  dry-run prepares the epic-closure planning prompt and states that the next
  live run would close the epic after a successful merge gate.

## Prompt / contract touchpoints

- Child delivery uses existing planner/implementer contracts with the selected
  child issue payload.
- Epic closure uses the same delivery team roles but the planner/reviewer
  prompts must state: review the epic implementation holistically, fix gaps,
  do not open new child scope, and the releaser/supervisor closes the epic
  after merge.

Minimal change: a dedicated contract snippet or phase flag
`epic_closure=true` injected into `build_phase_prompt` when the selected issue
is the epic in closure mode.

## Testing

- Unit: mutual exclusion of flags; `--issue` assignee/label behavior;
  `--epic` child discovery (tracked + body); eligibility C; oldest child;
  all-children-closed → closure target; already-closed epic → clean exit;
  no global-queue fallback on focus miss.
- Dry-run integration against fixtures (no network) with mocked `gh` payloads
  shaped like CRA-sec epic #56 checklists.

## Rollout

- Implement in `dark-factory-plugin`, bump plugin patch version, push, then
  `claude plugin update dark-factory@dark-factory-plugin` (or reinstall) on
  operator machines.
- CRA-workspace policy stays as-is; operators pass `--epic 56` when desired.
