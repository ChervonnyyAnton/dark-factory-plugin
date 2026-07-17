# Dark Factory `--issue` / `--epic` Targeting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-run `--issue N` and `--epic N` targeting to `dark-factory start` / `run --dry-run`, including epic child delivery and epic-closure when all linked children are closed, without changing no-args queue behavior.

**Architecture:** Keep selection in `bin/dark-factory` as pure functions (`resolve_issue`, `discover_epic_children`, `select_with_focus`) fed by a small `Focus` (issue xor epic). Persist focus on the detached worker argv (`run --epic N`) so the loop keeps selecting under that epic until closure. Dry-run always recomputes from args + policy. Epic closure sets `issue["selection_intent"] = "epic_closure"` and, after a successful merge, runs `gh issue close` then idles (no global-queue fallback).

**Tech Stack:** Python 3 stdlib, `unittest`, `gh` CLI (mocked in tests), Claude Code plugin commands/skills.

**Spec:** `docs/superpowers/specs/2026-07-17-dark-factory-target-args-design.md`

## Global Constraints

- No-args queue selection must remain byte-compatible with current tests.
- `--issue` and `--epic` are mutually exclusive; valid only on `start` and `run`.
- Never fall back to the global queue when focus selection fails or epic work is finished.
- Assignees come from `resolve_assignees(policy)` (`@me` supported).
- Epic children: unassigned OR assigned to policy assignees; skip if only others.
- Children done = every **resolved** linked child issue is **closed** (option A).
- No sticky `queue.focus_*` policy fields.
- Do not greenfield-implement the epic while open children remain.
- Tests: `python3 -m unittest` from the plugin root (no pytest required).
- Bump plugin version `0.1.0` → `0.1.1` in the final docs/version task.

## File map

| File | Responsibility |
|------|----------------|
| `bin/dark-factory` | Focus parsing, issue resolve, epic child discovery/eligibility, selection, CLI, worker argv, dry-run recompute, merge→close epic, prompt flag |
| `tests/test_target_focus.py` | New unit tests for focus selection (mocked `run`) |
| `tests/test_cli_lifecycle.py` | Extend dry-run/start/worker argv coverage |
| `contracts/epic-closure.md` | Short contract appended for closure prompts |
| `commands/dark-factory.md`, `commands/dark-factory-dry-run.md` | Pass `$ARGUMENTS` |
| `skills/dark-factory/SKILL.md`, `README.md` | Document flags |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | Version `0.1.1` |

---

### Task 1: Resolve issue number across policy repositories

**Files:**
- Modify: `bin/dark-factory` (after `select_issue`)
- Create: `tests/test_target_focus.py`

**Interfaces:**
- Produces: `resolve_issue(run, policy, number: int) -> dict` with `repository` set; raises `ValueError` if missing or ambiguous. Loads fields `number,title,url,createdAt,assignees,labels,state,body`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_target_focus.py
import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_focus", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
df = importlib.util.module_from_spec(spec)
loader.exec_module(df)


class Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class ResolveIssueTests(unittest.TestCase):
    def test_resolves_from_matching_repository(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            if cmd[:3] == ["gh", "issue", "view"] and cmd[4] == "org/a":
                return Result(json.dumps({
                    "number": 58, "title": "child", "url": "u",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "assignees": [], "labels": [], "state": "OPEN", "body": "",
                }))
            return Result("", returncode=1)

        issue = df.resolve_issue(run, {"repositories": ["org/a", "org/b"]}, 58)
        self.assertEqual(issue["repository"], "org/a")
        self.assertEqual(issue["number"], 58)

    def test_ambiguity_across_repositories_raises(self):
        payload = {
            "number": 1, "title": "x", "url": "u",
            "createdAt": "2026-01-01T00:00:00Z",
            "assignees": [], "labels": [], "state": "OPEN", "body": "",
        }

        def run(cmd):
            return Result(json.dumps(payload))

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            df.resolve_issue(run, {"repositories": ["org/a", "org/b"]}, 1)

    def test_missing_issue_raises(self):
        def run(cmd):
            return Result("", returncode=1)

        with self.assertRaisesRegex(ValueError, "not found"):
            df.resolve_issue(run, {"repositories": ["org/a"]}, 99)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_target_focus.ResolveIssueTests -v`  
Expected: FAIL (`resolve_issue` missing)

- [ ] **Step 3: Implement `resolve_issue`**

```python
_ISSUE_FIELDS = "number,title,url,createdAt,assignees,labels,state,body"


def resolve_issue(run, policy, number):
    repositories = policy.get("repositories") or [_repository_from_origin(run)]
    found = []
    for repository in repositories:
        result = run([
            "gh", "issue", "view", str(number),
            "--repo", repository,
            "--json", _ISSUE_FIELDS,
        ])
        if getattr(result, "returncode", 0) != 0 or not (result.stdout or "").strip():
            continue
        found.append(dict(json.loads(result.stdout), repository=repository))
    if not found:
        raise ValueError(f"issue #{number} not found in policy repositories")
    if len(found) > 1:
        repos = ", ".join(item["repository"] for item in found)
        raise ValueError(f"issue #{number} is ambiguous across repositories: {repos}")
    return found[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_target_focus.ResolveIssueTests -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory tests/test_target_focus.py
git commit -m "feat(factory): resolve issue numbers across policy repositories"
```

---

### Task 2: `--issue` selection helper

**Files:**
- Modify: `bin/dark-factory`
- Modify: `tests/test_target_focus.py`

**Interfaces:**
- Consumes: `resolve_issue`, `resolve_assignees`
- Produces: `select_pinned_issue(run, policy, number: int) -> dict`  
  Requires open + assignee ∩ policy assignees. Does **not** apply labels.

- [ ] **Step 1: Write the failing tests**

```python
class PinIssueTests(unittest.TestCase):
    def _issue(self, **overrides):
        base = {
            "number": 58, "title": "slice", "url": "u",
            "createdAt": "2026-01-02T00:00:00Z",
            "assignees": [{"login": "alice"}], "labels": [],
            "state": "OPEN", "body": "", "repository": "org/a",
        }
        base.update(overrides)
        return base

    def test_pin_requires_assignee_ignores_labels(self):
        issue = self._issue(labels=[])  # would fail label filter if applied

        def run(cmd):
            if cmd[0:3] == ["gh", "issue", "view"]:
                return Result(json.dumps({k: v for k, v in issue.items() if k != "repository"}))
            raise AssertionError(cmd)

        policy = {
            "repositories": ["org/a"],
            "queue": {"assignees": ["alice"], "labels": ["dark-factory"], "match": "all"},
        }
        selected = df.select_pinned_issue(run, policy, 58)
        self.assertEqual(selected["number"], 58)

    def test_pin_rejects_unassigned(self):
        issue = self._issue(assignees=[])

        def run(cmd):
            return Result(json.dumps({k: v for k, v in issue.items() if k != "repository"}))

        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"], "labels": []}}
        with self.assertRaisesRegex(ValueError, "assigned"):
            df.select_pinned_issue(run, policy, 58)

    def test_pin_rejects_closed(self):
        issue = self._issue(state="CLOSED")

        def run(cmd):
            return Result(json.dumps({k: v for k, v in issue.items() if k != "repository"}))

        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"]}}
        with self.assertRaisesRegex(ValueError, "open"):
            df.select_pinned_issue(run, policy, 58)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_target_focus.PinIssueTests -v`  
Expected: FAIL

- [ ] **Step 3: Implement `select_pinned_issue`**

```python
def _assignee_logins(issue):
    return {a.get("login") for a in issue.get("assignees", []) if a.get("login")}


def select_pinned_issue(run, policy, number):
    issue = resolve_issue(run, policy, number)
    if str(issue.get("state", "")).upper() != "OPEN":
        raise ValueError(f"issue #{number} is not open")
    assignees = set(resolve_assignees(policy, run))
    if not (_assignee_logins(issue) & assignees):
        raise ValueError(f"issue #{number} is not assigned to the policy assignees")
    return issue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_target_focus.PinIssueTests -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory tests/test_target_focus.py
git commit -m "feat(factory): select pinned --issue with assignee gate"
```

---

### Task 3: Epic child discovery and eligibility

**Files:**
- Modify: `bin/dark-factory`
- Modify: `tests/test_target_focus.py`

**Interfaces:**
- Produces:
  - `discover_epic_child_numbers(run, epic: dict) -> list[int]` — trackedIssues via GraphQL, plus `#N` in `epic["body"]`, deduped, stable order (tracked first, then body order).
  - `epic_child_eligible(issue, policy, assignees: list[str]) -> bool` — open; assignees empty or ∩ policy; if `queue.labels` non-empty require label intersection; skip if only others assigned.

- [ ] **Step 1: Write the failing tests**

```python
class EpicDiscoveryTests(unittest.TestCase):
    def test_discovers_tracked_and_body_numbers(self):
        epic = {
            "number": 56, "repository": "org/a", "body": "- [ ] #58 — a\n- [ ] #59 — b\n",
            "state": "OPEN", "assignees": [{"login": "alice"}],
        }

        def run(cmd):
            if cmd[0] == "gh" and cmd[1] == "api" and "graphql" in cmd:
                return Result(json.dumps({
                    "data": {"repository": {"issue": {
                        "trackedIssues": {"nodes": [{"number": 60}]}
                    }}}
                }))
            raise AssertionError(cmd)

        numbers = df.discover_epic_child_numbers(run, epic)
        self.assertEqual(numbers, [60, 58, 59])

    def test_eligibility_allows_unassigned_and_skips_others(self):
        policy = {"queue": {"assignees": ["alice"], "labels": [], "match": "any"}}
        assignees = ["alice"]
        open_unassigned = {
            "state": "OPEN", "assignees": [], "labels": [], "number": 58,
        }
        open_other = {
            "state": "OPEN",
            "assignees": [{"login": "bob"}],
            "labels": [],
            "number": 59,
        }
        open_me = {
            "state": "OPEN",
            "assignees": [{"login": "alice"}],
            "labels": [],
            "number": 60,
        }
        self.assertTrue(df.epic_child_eligible(open_unassigned, policy, assignees))
        self.assertFalse(df.epic_child_eligible(open_other, policy, assignees))
        self.assertTrue(df.epic_child_eligible(open_me, policy, assignees))

    def test_eligibility_applies_labels_when_configured(self):
        policy = {"queue": {"assignees": ["alice"], "labels": ["dark-factory"], "match": "all"}}
        issue = {
            "state": "OPEN", "assignees": [], "labels": [{"name": "feature"}], "number": 1,
        }
        self.assertFalse(df.epic_child_eligible(issue, policy, ["alice"]))
        issue["labels"] = [{"name": "dark-factory"}]
        self.assertTrue(df.epic_child_eligible(issue, policy, ["alice"]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_target_focus.EpicDiscoveryTests -v`  
Expected: FAIL

- [ ] **Step 3: Implement discovery + eligibility**

```python
_CHILD_RE = re.compile(r"#(\d+)\b")


def discover_epic_child_numbers(run, epic):
    numbers = []
    seen = set()
    repository = epic["repository"]
    owner, name = repository.split("/", 1)
    query = (
        "query($owner:String!,$name:String!,$number:Int!){"
        "repository(owner:$owner,name:$name){issue(number:$number){"
        "trackedIssues(first:100){nodes{number}}}}}"
    )
    result = run([
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-f", f"owner={owner}",
        "-f", f"name={name}",
        "-F", f"number={epic['number']}",
    ])
    if getattr(result, "returncode", 0) == 0 and (result.stdout or "").strip():
        try:
            nodes = (
                json.loads(result.stdout)
                .get("data", {})
                .get("repository", {})
                .get("issue", {})
                .get("trackedIssues", {})
                .get("nodes", [])
            )
        except json.JSONDecodeError:
            nodes = []
        for node in nodes or []:
            number = node.get("number")
            if isinstance(number, int) and number not in seen:
                seen.add(number)
                numbers.append(number)
    for match in _CHILD_RE.finditer(epic.get("body") or ""):
        number = int(match.group(1))
        if number == epic["number"] or number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def epic_child_eligible(issue, policy, assignees):
    if str(issue.get("state", "")).upper() != "OPEN":
        return False
    logins = _assignee_logins(issue)
    assignee_set = set(assignees)
    if logins and not (logins & assignee_set):
        return False
    labels = policy.get("queue", {}).get("labels") or []
    if labels:
        issue_labels = {label.get("name") for label in issue.get("labels", [])}
        if not (issue_labels & set(labels)):
            return False
    return True
```

GraphQL argv shape may be adjusted to match whatever the test’s `run` mock asserts; keep parsing resilient if GraphQL fails (body-only discovery still works).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_target_focus.EpicDiscoveryTests -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory tests/test_target_focus.py
git commit -m "feat(factory): discover epic children and apply eligibility C"
```

---

### Task 4: `select_with_focus` (child delivery + closure + clean exits)

**Files:**
- Modify: `bin/dark-factory`
- Modify: `tests/test_target_focus.py`

**Interfaces:**
- Produces: `select_with_focus(run, policy, *, issue=None, epic=None) -> tuple[dict|None, str]`  
  Returns `(selected_issue_or_none, summary)`.  
  - `issue` set → pinned issue  
  - `epic` set → oldest eligible open child, else epic with `selection_intent="epic_closure"` if epic open and all resolved children closed, else `(None, summary)` if epic already closed / nothing to do  
  - neither → `(select_issue(list_matching_issues(...)), summary)`  
  Mutually exclusive args enforced by caller; this function raises if both set.

Selected issues for closure must include `selection_intent: "epic_closure"`.  
Unresolved child numbers are omitted from the done-check and mentioned in `summary`.

- [ ] **Step 1: Write the failing tests**

```python
class SelectWithFocusTests(unittest.TestCase):
    def _catalog(self):
        return {
            ("org/a", 56): {
                "number": 56, "title": "epic", "url": "u",
                "createdAt": "2026-01-01T00:00:00Z",
                "assignees": [{"login": "alice"}], "labels": [],
                "state": "OPEN",
                "body": "- [ ] #58\n- [ ] #59\n",
            },
            ("org/a", 58): {
                "number": 58, "title": "older", "url": "u",
                "createdAt": "2026-01-02T00:00:00Z",
                "assignees": [], "labels": [], "state": "OPEN", "body": "",
            },
            ("org/a", 59): {
                "number": 59, "title": "newer", "url": "u",
                "createdAt": "2026-01-03T00:00:00Z",
                "assignees": [], "labels": [], "state": "OPEN", "body": "",
            },
            ("org/a", 48): {
                "number": 48, "title": "other", "url": "u",
                "createdAt": "2025-01-01T00:00:00Z",
                "assignees": [{"login": "alice"}], "labels": [],
                "state": "OPEN", "body": "",
            },
        }

    def _run_for(self, catalog, *, tracked=None):
        tracked = tracked or []

        def run(cmd):
            if cmd[0:2] == ["gh", "api"] and "graphql" in " ".join(cmd):
                nodes = [{"number": n} for n in tracked]
                return Result(json.dumps({
                    "data": {"repository": {"issue": {"trackedIssues": {"nodes": nodes}}}}
                }))
            if cmd[0:3] == ["gh", "issue", "view"]:
                number = int(cmd[3])
                repo = cmd[cmd.index("--repo") + 1]
                payload = catalog.get((repo, number))
                if payload is None:
                    return Result("", returncode=1)
                return Result(json.dumps(payload))
            if cmd[0:3] == ["gh", "issue", "list"]:
                repo = cmd[cmd.index("--repo") + 1]
                issues = [
                    {k: v for k, v in issue.items()}
                    for (r, _), issue in catalog.items()
                    if r == repo and str(issue.get("state", "")).upper() == "OPEN"
                ]
                return Result(json.dumps(issues))
            raise AssertionError(cmd)

        return run

    def test_epic_selects_oldest_eligible_child(self):
        catalog = self._catalog()
        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"], "labels": []}}
        selected, summary = df.select_with_focus(
            self._run_for(catalog), policy, epic=56,
        )
        self.assertEqual(selected["number"], 58)
        self.assertIn("child #58", summary)

    def test_epic_closure_when_all_resolved_children_closed(self):
        catalog = self._catalog()
        catalog[("org/a", 58)]["state"] = "CLOSED"
        catalog[("org/a", 59)]["state"] = "CLOSED"
        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"], "labels": []}}
        selected, summary = df.select_with_focus(
            self._run_for(catalog), policy, epic=56,
        )
        self.assertEqual(selected["number"], 56)
        self.assertEqual(selected.get("selection_intent"), "epic_closure")
        self.assertIn("closure", summary)

    def test_epic_already_closed_returns_none_without_queue_fallback(self):
        catalog = self._catalog()
        catalog[("org/a", 56)]["state"] = "CLOSED"
        catalog[("org/a", 58)]["state"] = "CLOSED"
        catalog[("org/a", 59)]["state"] = "CLOSED"
        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"], "labels": []}}
        selected, summary = df.select_with_focus(
            self._run_for(catalog), policy, epic=56,
        )
        self.assertIsNone(selected)
        self.assertIn("already closed", summary)

    def test_no_focus_uses_queue(self):
        catalog = self._catalog()
        policy = {"repositories": ["org/a"], "queue": {"assignees": ["alice"], "labels": []}}
        selected, summary = df.select_with_focus(self._run_for(catalog), policy)
        self.assertEqual(selected["number"], 48)
        self.assertIn("queue selected", summary)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_target_focus.SelectWithFocusTests -v`  
Expected: FAIL

- [ ] **Step 3: Implement `select_with_focus`**

```python
def select_with_focus(run, policy, *, issue=None, epic=None):
    if issue is not None and epic is not None:
        raise ValueError("--issue and --epic are mutually exclusive")
    if issue is not None:
        selected = select_pinned_issue(run, policy, issue)
        return selected, f"pinned issue #{selected['number']}"
    if epic is None:
        selected = select_issue(list_matching_issues(run, policy))
        if selected is None:
            return None, "no eligible issue"
        return selected, f"queue selected issue #{selected['number']}"

    epic_issue = resolve_issue(run, policy, epic)
    assignees = resolve_assignees(policy, run)
    if str(epic_issue.get("state", "")).upper() != "OPEN":
        # If closed and children done / irrelevant: clean exit
        return None, f"epic #{epic} is already closed; nothing to deliver"
    if not (_assignee_logins(epic_issue) & set(assignees)):
        raise ValueError(f"epic #{epic} is not assigned to the policy assignees")

    child_numbers = discover_epic_child_numbers(run, epic_issue)
    resolved = []
    unresolved = []
    for number in child_numbers:
        try:
            resolved.append(resolve_issue(run, policy, number))
        except ValueError:
            unresolved.append(number)

    eligible = [
        child for child in resolved
        if epic_child_eligible(child, policy, assignees)
    ]
    if eligible:
        selected = select_issue(eligible)
        summary = f"epic #{epic} child #{selected['number']}"
        if unresolved:
            summary += f"; unresolved child refs ignored: {unresolved}"
        return selected, summary

    open_resolved = [c for c in resolved if str(c.get("state", "")).upper() == "OPEN"]
    if open_resolved:
        # Open but ineligible (assigned to others / label miss) — fail closed, no queue
        raise ValueError(
            f"epic #{epic} has open children that are not eligible under policy"
        )

    # All resolved children closed (or no children) → closure
    selected = dict(epic_issue, selection_intent="epic_closure")
    summary = f"epic #{epic} closure"
    if unresolved:
        summary += f"; unresolved child refs ignored: {unresolved}"
    return selected, summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_target_focus.SelectWithFocusTests -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory tests/test_target_focus.py
git commit -m "feat(factory): select with --issue/--epic including epic closure"
```

---

### Task 5: Wire CLI, dry-run recompute, and worker argv

**Files:**
- Modify: `bin/dark-factory` (`main`, `dry_run_controller`, `_worker_command`, `start_controller`, `run_controller`)
- Modify: `tests/test_cli_lifecycle.py`
- Modify: `tests/test_target_focus.py` (optional argparse mutual-exclusion test)

**Interfaces:**
- `main` accepts `--issue` / `--epic` (ints); errors if both or if used with `stop`/`monitor`.
- `dry_run_controller(root, run=..., issue=None, epic=None)` **always** recomputes via `select_with_focus` (ignore prior `state["issue"]`).
- `_worker_command(root, issue=None, epic=None)` appends focus flags.
- `start_controller(root, ..., issue=None, epic=None)` passes focus into worker.
- `run_controller(root, issue=None, epic=None)` stores focus on a module-level or state field `focus = {"issue": …, "epic": …}` used by `_select_next_issue`.

Persist on state (for monitor visibility):

```python
DEFAULT_STATE["focus"] = None  # {"issue": N} | {"epic": N} | None
```

- [ ] **Step 1: Write failing CLI/lifecycle tests**

Add to `tests/test_cli_lifecycle.py` (same module loader pattern already used there):

```python
def test_main_rejects_issue_and_epic_together(self):
    with tempfile.TemporaryDirectory() as tmp:
        with self.assertRaises(SystemExit):
            dark_factory.main([
                "run", "--dry-run", "--issue", "1", "--epic", "2",
                "--workspace", tmp,
            ])

def test_worker_command_includes_epic(self):
    cmd = dark_factory._worker_command(Path("/tmp/ws"), epic=56)
    self.assertEqual(cmd[-2:], ["--epic", "56"])

def test_dry_run_with_epic_selects_child(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".dark-factory").mkdir()
        (root / ".dark-factory" / "policy.json").write_text(json.dumps({
            "repositories": ["org/a"],
            "queue": {"assignees": ["alice"], "labels": []},
            "merge": {"mode": "manual"},
            "providers": {"implement": ["codex"], "review": ["codex"]},
        }))
        catalog = {
            ("org/a", 56): {
                "number": 56, "title": "epic", "url": "u",
                "createdAt": "2026-01-01T00:00:00Z",
                "assignees": [{"login": "alice"}], "labels": [],
                "state": "OPEN", "body": "#58\n",
            },
            ("org/a", 58): {
                "number": 58, "title": "child", "url": "u",
                "createdAt": "2026-01-02T00:00:00Z",
                "assignees": [], "labels": [], "state": "OPEN", "body": "",
            },
        }

        def run(cmd):
            if cmd[0:2] == ["gh", "api"]:
                return Result(json.dumps({
                    "data": {"repository": {"issue": {"trackedIssues": {"nodes": []}}}}
                }))
            if cmd[0:3] == ["gh", "issue", "view"]:
                number = int(cmd[3])
                repo = cmd[cmd.index("--repo") + 1]
                return Result(json.dumps(catalog[(repo, number)]))
            raise AssertionError(cmd)

        code = dark_factory.dry_run_controller(root, run=run, epic=56)
        self.assertEqual(code, 0)
        state = json.loads((root / ".dark-factory" / "controller.json").read_text())
        self.assertEqual(state["issue"]["number"], 58)
```

Ensure `Result`, `json`, `tempfile`, and `Path` imports exist in that test module (add if missing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cli_lifecycle tests.test_target_focus -v`  
Expected: new cases FAIL

- [ ] **Step 3: Implement wiring**

Helpers:

```python
def _focus_dict(issue=None, epic=None):
    if issue is not None:
        return {"issue": issue}
    if epic is not None:
        return {"epic": epic}
    return None


def _worker_command(root, issue=None, epic=None):
    command = [
        str(Path(__file__).resolve()),
        "run",
        "--workspace",
        str(Path(root).resolve()),
    ]
    if issue is not None:
        command.extend(["--issue", str(issue)])
    if epic is not None:
        command.extend(["--epic", str(epic)])
    return command
```

Argparse + dispatch in `main`:

```python
parser.add_argument("--issue", type=int)
parser.add_argument("--epic", type=int)
args = parser.parse_args(arguments)
if args.dry_run and args.action != "run":
    parser.error("--dry-run is only valid with run")
if args.issue is not None and args.epic is not None:
    parser.error("--issue and --epic are mutually exclusive")
if (args.issue is not None or args.epic is not None) and args.action not in ("start", "run"):
    parser.error("--issue/--epic are only valid with start or run")
root = Path(args.workspace).resolve()
if args.action == "start":
    return start_controller(root, issue=args.issue, epic=args.epic)
if args.action == "stop":
    return stop_controller(root)
if args.action == "monitor":
    monitor_controller(root)
    return 0
if args.dry_run:
    return dry_run_controller(root, issue=args.issue, epic=args.epic)
return run_controller(root, issue=args.issue, epic=args.epic)
```

Dry-run (always recompute; do not reuse prior `state["issue"]`):

```python
def dry_run_controller(root, run=_gh_run, issue=None, epic=None):
    root = Path(root).resolve()
    policy = load_policy(root)
    store = _state_for(root)
    state = store.load()
    selected, summary = select_with_focus(run, policy, issue=issue, epic=epic)
    focus = _focus_dict(issue, epic)
    if selected is None:
        state.update(
            requested="stopped",
            phase="idle",
            issue=None,
            focus=focus,
            dry_run_summary=f"dry-run: {summary}",
            dry_run_prompt=None,
            updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        store.save(state, force_requested="stopped")
        print(state["dry_run_summary"])
        return 0
    selected = dict(selected, run_id=f"dry-run-{selected['number']}-{uuid.uuid4().hex[:8]}")
    prompt = build_phase_prompt(root, "planning", selected, "planner")
    full_summary = (
        f"dry-run: {summary}; prepared {prompt}; "
        "no provider, PR, or merge commands ran"
    )
    state.update(
        requested="stopped",
        phase="dry_run",
        issue=selected,
        focus=focus,
        dry_run_prompt=str(prompt),
        dry_run_summary=full_summary,
        updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )
    store.save(state, force_requested="stopped")
    print(full_summary)
    return 0
```

`start_controller` / `run_controller`: accept `issue=` / `epic=`, save `focus` onto state when starting/running, and pass the same into `_worker_command` / selection.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cli_lifecycle tests.test_target_focus -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory tests/test_cli_lifecycle.py tests/test_target_focus.py
git commit -m "feat(factory): wire --issue/--epic through CLI, dry-run, and worker"
```

---

### Task 6: Controller loop focus behavior + close epic after merge

**Files:**
- Modify: `bin/dark-factory` (`_select_next_issue`, `_controller_iteration`, `merge_pr` or post-merge hook, `build_phase_prompt`)
- Create: `contracts/epic-closure.md`
- Modify: `tests/test_target_focus.py` and/or `tests/test_delivery.py`

**Interfaces:**
- `_select_next_issue(run, policy, focus)` uses `select_with_focus`.
- After `phase == "merged"` and `issue.get("selection_intent") == "epic_closure"`: `gh issue close N --repo …`, then clear issue and set phase idle / stop iterating for more queue work while focused (return False after save).
- After `merged` for `--issue` focus: clear and idle (single pin).
- After `merged` for `--epic` child (no closure intent): clear `issue` fields for next iteration but **keep** focus so the next `_select_next_issue` picks the next child or closure.
- `build_phase_prompt`: if `issue.get("selection_intent") == "epic_closure"`, prepend/append `contracts/epic-closure.md`.

`contracts/epic-closure.md` content:

```markdown
# Epic closure

You are closing out this epic after all linked child issues are closed.

- Review the epic implementation holistically against the epic goals and closed children.
- Fix gaps, regressions, and missing acceptance evidence on a new branch.
- Do not open new child scope or re-expand the epic.
- After a clean self-review, the supervisor merges and closes the epic issue.
```

- [ ] **Step 1: Write failing tests**

```python
class EpicClosureSideEffectTests(unittest.TestCase):
    def test_close_epic_if_needed_closes_on_merged_closure(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return Result(returncode=0)

        state = {
            "phase": "merged",
            "issue": {
                "number": 56,
                "repository": "org/a",
                "selection_intent": "epic_closure",
                "title": "epic",
            },
        }
        out = df.close_epic_if_needed(run, state)
        self.assertEqual(out["phase"], "idle")
        self.assertIsNone(out["issue"])
        self.assertEqual(calls[0][:3], ["gh", "issue", "close"])

    def test_build_phase_prompt_includes_epic_closure_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue = {
                "number": 56,
                "title": "epic",
                "run_id": "dry-run-56-test",
                "selection_intent": "epic_closure",
                "repository": "org/a",
            }
            path = df.build_phase_prompt(root, "planning", issue, "planner")
            text = path.read_text()
            self.assertIn("Epic closure", text)
            self.assertIn("holistically", text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`  
Expected: new cases FAIL

- [ ] **Step 3: Implement loop + close + prompt**

```python
def close_epic_if_needed(run, state):
    issue = state.get("issue") or {}
    if issue.get("selection_intent") != "epic_closure":
        return state
    if state.get("phase") != "merged":
        return state
    result = run([
        "gh", "issue", "close", str(issue["number"]),
        "--repo", issue["repository"],
        "--comment", "Closed by Dark Factory epic closure after merge.",
    ])
    if getattr(result, "returncode", 0) != 0:
        state["phase"] = "handoff"
        state["handoff_reason"] = "merged but failed to close epic"
        return state
    state["phase"] = "idle"
    state["issue"] = None
    return state
```

Call from `_controller_iteration` after merge / when entering `merged` before release note (or after release note — prefer after release note so the note still sees the epic issue).

For next-issue selection with focus still set after a normal child merge: when phase hits `merged` without closure intent, reset delivery fields (`issue=None`, pr fields cleared) but leave `focus` and continue the loop (`return True`) so the next iteration selects again under focus.

- [ ] **Step 4: Run full suite**

Run: `python3 -m unittest discover -s tests -v`  
Expected: OK (all existing + new)

- [ ] **Step 5: Commit**

```bash
git add bin/dark-factory contracts/epic-closure.md tests/
git commit -m "feat(factory): epic closure merge closes epic; keep epic focus across children"
```

---

### Task 7: Slash commands, skill, README, version bump

**Files:**
- Modify: `commands/dark-factory.md`
- Modify: `commands/dark-factory-dry-run.md`
- Modify: `skills/dark-factory/SKILL.md`
- Modify: `README.md`
- Modify: `.claude-plugin/plugin.json` (`version`: `0.1.1`)
- Modify: `.claude-plugin/marketplace.json` if it embeds version

- [ ] **Step 1: Update command bodies**

`commands/dark-factory.md`:

```markdown
---
description: Start the detached Dark Factory controller
argument-hint: "[--issue N | --epic N]"
---

Run:

```sh
"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" start --workspace "$PWD" $ARGUMENTS
```
```

`commands/dark-factory-dry-run.md`:

```markdown
---
description: Preview the next Dark Factory delivery without writes
argument-hint: "[--issue N | --epic N]"
---

Run:

```sh
"$CLAUDE_PLUGIN_ROOT/bin/dark-factory" run --dry-run --workspace "$PWD" $ARGUMENTS
```
```

Document the same in `skills/dark-factory/SKILL.md` and README Commands section.

- [ ] **Step 2: Bump version to 0.1.1** in plugin.json (and marketplace entry if present).

- [ ] **Step 3: Run full suite once more**

Run: `python3 -m unittest discover -s tests -v`  
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add commands skills README.md .claude-plugin
git commit -m "docs(factory): document --issue/--epic; bump plugin to 0.1.1"
```

- [ ] **Step 5: Push and note operator update**

```bash
git push origin main
```

Operator follow-up (not automated here): `claude plugin update dark-factory@dark-factory-plugin`.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| No-args unchanged | 4 (delegates), 5–6 |
| `--issue` pin, assignee, ignore labels | 2, 5 |
| `--epic` child discovery tracked+body | 3 |
| Eligibility C (unassigned or me) | 3 |
| Labels on children when configured | 3 |
| Oldest child tie-break | 4 (`select_issue`) |
| All resolved children closed → closure | 4, 6 |
| Close epic after merge | 6 |
| Already-closed epic clean exit, no queue fallback | 4 |
| Focus miss does not fall back | 4–6 |
| Dry-run recomputes | 5 |
| Worker keeps epic focus across children | 5–6 |
| Slash `$ARGUMENTS` | 7 |
| Prompt epic-closure contract | 6 |
| Version bump / docs | 7 |

## Placeholder / consistency self-review

- No TBD/TODO left in tasks.
- Function names stable: `resolve_issue`, `select_pinned_issue`, `discover_epic_child_numbers`, `epic_child_eligible`, `select_with_focus`, `close_epic_if_needed`.
- Focus passed as `issue: int | None`, `epic: int | None` everywhere; state mirror `focus: {"issue": N} | {"epic": N} | None`.
