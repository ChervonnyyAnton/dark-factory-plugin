import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)

load_policy = dark_factory.load_policy
resolve_assignees = dark_factory.resolve_assignees
issue_matches = dark_factory.issue_matches


class PolicyTests(unittest.TestCase):
    def test_resolve_assignees_expands_at_me(self):
        policy = {"queue": {"assignees": ["@me", "alice"]}}
        run = lambda cmd: type("R", (), {"stdout": "bob\n", "returncode": 0})()
        self.assertEqual(resolve_assignees(policy, run), ["bob", "alice"])

    def test_issue_matches_any_assignee_or_label(self):
        policy = {"queue": {"assignees": ["bob"], "labels": ["dark-factory"], "match": "any"}}
        issue = {"assignees": [{"login": "carol"}], "labels": [{"name": "dark-factory"}]}
        self.assertTrue(issue_matches(issue, policy, ["bob"]))

    def test_issue_matches_all_requires_both(self):
        policy = {"queue": {"assignees": ["bob"], "labels": ["dark-factory"], "match": "all"}}
        issue = {"assignees": [{"login": "bob"}], "labels": []}
        self.assertFalse(issue_matches(issue, policy, ["bob"]))


if __name__ == "__main__":
    unittest.main()
