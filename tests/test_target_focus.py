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
            if cmd[:3] == ["gh", "issue", "view"] and cmd[5] == "org/a":
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
                    for (repository, _), issue in catalog.items()
                    if repository == repo and str(issue.get("state", "")).upper() == "OPEN"
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
