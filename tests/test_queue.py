import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_queue", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)

list_matching_issues = getattr(dark_factory, "list_matching_issues", lambda run, policy: [])
select_issue = getattr(dark_factory, "select_issue", lambda issues: None)
reconcile = getattr(dark_factory, "reconcile", lambda state, run, policy: dict(state))


class Result:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


class QueueTests(unittest.TestCase):
    def test_lists_open_issues_and_filters_each_repository_in_python(self):
        policy = {
            "repositories": ["org/second", "org/first"],
            "queue": {
                "assignees": ["alice"],
                "labels": ["factory"],
                "match": "any",
            },
        }
        commands = []

        def run(command):
            commands.append(command)
            repository = command[command.index("--repo") + 1]
            issue = {
                "number": 2 if repository == "org/second" else 1,
                "title": "work",
                "url": f"https://github.com/{repository}/issues/1",
                "createdAt": "2026-07-01T00:00:00Z",
                "assignees": [],
                "labels": [{"name": "factory"}] if repository == "org/first" else [],
            }
            return Result(json.dumps([issue]))

        issues = list_matching_issues(run, policy)

        self.assertEqual([issue["repository"] for issue in issues], ["org/first"])
        self.assertEqual(
            commands,
            [
                [
                    "gh", "issue", "list", "--repo", "org/second", "--state", "open",
                    "--json", "number,title,url,createdAt,assignees,labels",
                ],
                [
                    "gh", "issue", "list", "--repo", "org/first", "--state", "open",
                    "--json", "number,title,url,createdAt,assignees,labels",
                ],
            ],
        )

    def test_defaults_repository_from_origin_and_applies_all_filter(self):
        policy = {
            "repositories": [],
            "queue": {
                "assignees": ["@me"],
                "labels": ["factory"],
                "match": "all",
            },
        }

        def run(command):
            if command == ["git", "remote", "get-url", "origin"]:
                return Result("git@github.com:org/repo.git\n")
            if command == ["gh", "api", "user", "-q", ".login"]:
                return Result("alice\n")
            return Result(json.dumps([
                {
                    "number": 7,
                    "title": "matching",
                    "url": "https://github.com/org/repo/issues/7",
                    "createdAt": "2026-07-01T00:00:00Z",
                    "assignees": [{"login": "alice"}],
                    "labels": [{"name": "factory"}],
                },
                {
                    "number": 8,
                    "title": "wrong label",
                    "url": "https://github.com/org/repo/issues/8",
                    "createdAt": "2026-07-01T00:00:00Z",
                    "assignees": [{"login": "alice"}],
                    "labels": [],
                },
            ]))

        self.assertEqual(
            [issue["number"] for issue in list_matching_issues(run, policy)],
            [7],
        )

    def test_selects_oldest_issue_with_stable_tie_breakers(self):
        issues = [
            {"createdAt": "2026-07-02T00:00:00Z", "repository": "a/repo", "number": 1},
            {"createdAt": "2026-07-01T00:00:00Z", "repository": "b/repo", "number": 1},
            {"createdAt": "2026-07-01T00:00:00Z", "repository": "a/repo", "number": 2},
            {"createdAt": "2026-07-01T00:00:00Z", "repository": "a/repo", "number": 1},
        ]

        self.assertEqual(select_issue(issues), issues[-1])
        self.assertIsNone(select_issue([]))

    def test_reconcile_pauses_when_current_issue_no_longer_matches(self):
        state = {
            "phase": "implementing",
            "issue": {"repository": "org/repo", "number": 3, "title": "old"},
        }
        policy = {
            "queue": {
                "assignees": ["alice"],
                "labels": ["factory"],
                "match": "all",
            },
        }

        def run(command):
            self.assertEqual(
                command,
                [
                    "gh", "issue", "view", "3", "--repo", "org/repo",
                    "--json", "number,title,url,createdAt,assignees,labels",
                ],
            )
            return Result(json.dumps({
                "number": 3,
                "title": "old",
                "url": "https://github.com/org/repo/issues/3",
                "createdAt": "2026-07-01T00:00:00Z",
                "assignees": [],
                "labels": [{"name": "factory"}],
            }))

        result = reconcile(state, run, policy)

        self.assertEqual(result["phase"], "paused")
        self.assertEqual(state["phase"], "implementing")


if __name__ == "__main__":
    unittest.main()
