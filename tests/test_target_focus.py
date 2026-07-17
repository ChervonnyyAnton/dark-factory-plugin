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
