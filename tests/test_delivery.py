import copy
import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_delivery", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)

open_pr = dark_factory.open_pr
watch_ci = dark_factory.watch_ci
merge_ready = dark_factory.merge_ready
merge_pr = dark_factory.merge_pr
repair_or_handoff = dark_factory.repair_or_handoff
_controller_iteration = dark_factory._controller_iteration
StateStore = dark_factory.StateStore

POLICY = {
    "queue": {"assignees": [], "labels": ["dark-factory"], "match": "any"},
    "repositories": ["org/repo"],
    "merge": {"mode": "manual"},
    "providers": {"implement": ["claude"], "review": ["claude"]},
    "limits": {"max_turns": 40, "max_check_repairs": 5},
}

ISSUE = {
    "number": 42,
    "title": "Ship widget",
    "url": "https://github.com/org/repo/issues/42",
    "createdAt": "2026-07-01T00:00:00Z",
    "repository": "org/repo",
    "assignees": [],
    "labels": [{"name": "dark-factory"}],
    "run_id": "issue-42-abc123",
}


class Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def base_state(**overrides):
    state = copy.deepcopy(dark_factory.DEFAULT_STATE)
    state.update(
        issue=copy.deepcopy(ISSUE),
        branch="dark-factory/issue-42",
    )
    state.update(overrides)
    return state


def write_policy(workspace, policy=POLICY):
    directory = Path(workspace) / ".dark-factory"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "policy.json").write_text(json.dumps(policy))


CHECKS_FIELDS = "name,state,bucket"
PR_VIEW_FIELDS = "number,url,state,headRefOid,headRefName"


class OpenPrTests(unittest.TestCase):
    def test_creates_pull_request_with_closes_and_advances_to_ci_wait(self):
        commands = []

        def run(command):
            commands.append(command)
            if command[:3] == ["gh", "pr", "list"]:
                return Result(json.dumps([]))
            if command[:3] == ["gh", "pr", "create"]:
                return Result("https://github.com/org/repo/pull/7\n")
            if command[:3] == ["gh", "pr", "view"]:
                return Result(json.dumps({
                    "number": 7, "url": "https://github.com/org/repo/pull/7",
                    "state": "OPEN", "headRefOid": "sha123", "headRefName": "dark-factory/issue-42",
                }))
            raise AssertionError(f"unexpected command: {command}")

        state = base_state(pr_title="Ship widget", pr_body="Implemented the widget")

        result = open_pr(run, state)

        self.assertEqual(result["phase"], "ci_wait")
        self.assertEqual(result["pr"], 7)
        self.assertEqual(result["head_sha"], "sha123")
        self.assertIn("Closes #42", result["pr_body"])
        create_command = next(c for c in commands if c[:3] == ["gh", "pr", "create"])
        self.assertIn("Closes #42", create_command[create_command.index("--body") + 1])

    def test_reuses_existing_open_pull_request_for_branch(self):
        def run(command):
            if command[:3] == ["gh", "pr", "list"]:
                return Result(json.dumps([{
                    "number": 9, "url": "https://github.com/org/repo/pull/9",
                    "state": "OPEN", "headRefOid": "shaXYZ", "headRefName": "dark-factory/issue-42",
                }]))
            raise AssertionError(f"unexpected command: {command}")

        state = base_state()

        result = open_pr(run, state)

        self.assertEqual(result["pr"], 9)
        self.assertEqual(result["phase"], "ci_wait")

    def test_resuming_open_pr_by_number_short_circuits_creation(self):
        def run(command):
            if command[:3] == ["gh", "pr", "view"]:
                return Result(json.dumps({
                    "number": 9, "url": "https://github.com/org/repo/pull/9",
                    "state": "OPEN", "headRefOid": "shaABC", "headRefName": "dark-factory/issue-42",
                }))
            raise AssertionError(f"unexpected command: {command}")

        state = base_state(pr=9, pr_url="https://github.com/org/repo/pull/9")

        result = open_pr(run, state)

        self.assertEqual(result["head_sha"], "shaABC")
        self.assertEqual(result["phase"], "ci_wait")

    def test_missing_branch_raises(self):
        state = base_state(branch=None)
        with self.assertRaises(ValueError):
            open_pr(lambda command: Result(), state)


class WatchCiTests(unittest.TestCase):
    def test_green_checks_mark_ci_checks_green_without_repair(self):
        def run(command):
            return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))

        state = base_state(pr=7)

        result = watch_ci(run, state, POLICY)

        self.assertEqual(result["phase"], "ci_wait")
        self.assertEqual(result["ci_checks"], "green")

    def test_pending_checks_stay_in_ci_wait(self):
        def run(command):
            return Result(json.dumps([{"name": "build", "state": "PENDING", "bucket": "pending"}]))

        result = watch_ci(run, base_state(pr=7), POLICY)

        self.assertEqual(result["phase"], "ci_wait")
        self.assertEqual(result["ci_checks"], "pending")

    def test_failing_check_moves_to_repairing(self):
        def run(command):
            return Result(json.dumps([{"name": "unit-tests", "state": "FAILURE", "bucket": "fail"}]))

        result = watch_ci(run, base_state(pr=7), POLICY)

        self.assertEqual(result["phase"], "repairing")
        self.assertEqual(result["failed_check"], "unit-tests")
        self.assertEqual(result["attempts"]["ci:unit-tests"], 1)

    def test_fifth_failure_of_same_check_hands_off(self):
        def run(command):
            return Result(json.dumps([{"name": "unit-tests", "state": "FAILURE", "bucket": "fail"}]))

        state = base_state(pr=7, attempts={"ci:unit-tests": 4})

        result = watch_ci(run, state, POLICY)

        self.assertEqual(result["phase"], "handoff")
        self.assertEqual(result["attempts"]["ci:unit-tests"], 5)


class RepairOrHandoffTests(unittest.TestCase):
    def test_repairs_within_budget(self):
        state = repair_or_handoff(base_state(), "lint", POLICY)
        self.assertEqual(state["phase"], "repairing")
        self.assertEqual(state["attempts"]["ci:lint"], 1)

    def test_hands_off_once_policy_limit_reached(self):
        tight_policy = {**POLICY, "limits": {"max_check_repairs": 2}}
        state = base_state(attempts={"ci:lint": 1})

        state = repair_or_handoff(state, "lint", tight_policy)

        self.assertEqual(state["phase"], "handoff")
        self.assertIn("lint", state["handoff_reason"])

    def test_defaults_to_five_repairs_when_policy_omits_limit(self):
        state = base_state(attempts={"ci:lint": 3})
        state = repair_or_handoff(state, "lint", {})
        self.assertEqual(state["phase"], "repairing")
        state = repair_or_handoff(state, "lint", {})
        self.assertEqual(state["phase"], "handoff")


def ready_state(**overrides):
    state = base_state(
        pr=7,
        pr_url="https://github.com/org/repo/pull/7",
        head_sha="sha123",
        self_review={"status": "pass"},
        security_review={"status": "pass"},
    )
    state.update(overrides)
    return state


class MergeReadyTests(unittest.TestCase):
    def _run(self, head_sha="sha123", review_decision=None, bucket="pass"):
        def run(command):
            if command[:3] == ["gh", "pr", "view"]:
                return Result(json.dumps({
                    "number": 7, "url": "https://github.com/org/repo/pull/7",
                    "state": "OPEN", "headRefOid": head_sha, "reviewDecision": review_decision,
                }))
            if command[:3] == ["gh", "pr", "checks"]:
                return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": bucket}]))
            raise AssertionError(f"unexpected command: {command}")
        return run

    def test_ready_when_every_gate_passes(self):
        self.assertTrue(merge_ready(self._run(), ready_state()))

    def test_not_ready_when_head_sha_is_stale(self):
        run = self._run(head_sha="stale")
        self.assertFalse(merge_ready(run, ready_state()))

    def test_not_ready_when_changes_are_requested(self):
        run = self._run(review_decision="CHANGES_REQUESTED")
        self.assertFalse(merge_ready(run, ready_state()))

    def test_not_ready_when_checks_are_not_green(self):
        run = self._run(bucket="fail")
        self.assertFalse(merge_ready(run, ready_state()))

    def test_not_ready_without_recorded_security_review(self):
        run = self._run()
        self.assertFalse(merge_ready(run, ready_state(security_review={"status": "fail"})))


class MergePrTests(unittest.TestCase):
    def test_merges_with_squash_delete_branch_and_head_match_when_ready(self):
        commands = []

        def run(command):
            commands.append(command)
            if command[:3] == ["gh", "pr", "view"]:
                return Result(json.dumps({
                    "number": 7, "url": "https://github.com/org/repo/pull/7",
                    "state": "OPEN", "headRefOid": "sha123", "reviewDecision": None,
                }))
            if command[:3] == ["gh", "pr", "checks"]:
                return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
            if command[:3] == ["gh", "pr", "merge"]:
                return Result("Merged\n")
            raise AssertionError(f"unexpected command: {command}")

        result = merge_pr(run, ready_state())

        self.assertEqual(result["phase"], "merged")
        merge_command = next(c for c in commands if c[:3] == ["gh", "pr", "merge"])
        self.assertIn("--squash", merge_command)
        self.assertIn("--delete-branch", merge_command)
        self.assertEqual(merge_command[merge_command.index("--match-head-commit") + 1], "sha123")

    def test_does_not_merge_when_gate_is_not_satisfied(self):
        commands = []

        def run(command):
            commands.append(command)
            if command[:3] == ["gh", "pr", "view"]:
                return Result(json.dumps({
                    "number": 7, "url": "https://github.com/org/repo/pull/7",
                    "state": "OPEN", "headRefOid": "stale", "reviewDecision": None,
                }))
            if command[:3] == ["gh", "pr", "checks"]:
                return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
            raise AssertionError(f"unexpected command: {command}")

        result = merge_pr(run, ready_state(head_sha="sha123"))

        self.assertNotIn(True, [c[:3] == ["gh", "pr", "merge"] for c in commands])
        self.assertNotEqual(result["phase"], "merged")


class ControllerIterationTests(unittest.TestCase):
    def _store(self, workspace, initial=None):
        write_policy(workspace)
        store = StateStore(workspace)
        if initial is not None:
            store.save(initial)
        return store

    def _attempt(self, workspace, status, stdout="", run_dir_name="run"):
        run_dir = Path(workspace) / ".dark-factory" / "runs" / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.txt"
        stdout_path.write_text(stdout)
        return dark_factory.AttemptResult(status, 0, run_dir, stdout_path, run_dir / "stderr.txt", run_dir / "result.json", run_dir / "handoff.md")

    def test_selects_issue_and_enters_planning_when_none_selected(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace)

            def run(command):
                if command[:3] == ["gh", "issue", "list"]:
                    return Result(json.dumps([dict(ISSUE, run_id=None)]))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            state = store.load()
            self.assertTrue(continued)
            self.assertEqual(state["phase"], "planning")
            self.assertEqual(state["issue"]["number"], 42)
            self.assertTrue(state["issue"]["run_id"])

    def test_idle_when_no_matching_issue(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace)

            def run(command):
                if command[:3] == ["gh", "issue", "list"]:
                    return Result(json.dumps([]))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            self.assertFalse(continued)
            self.assertEqual(store.load()["phase"], "idle")

    def test_planning_success_advances_to_implementing(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace, base_state(phase="planning"))
            attempt = self._attempt(workspace, "success")

            continued = _controller_iteration(
                store, lambda c: Result(),
                run_provider_fn=lambda *a, **k: attempt,
            )

            self.assertTrue(continued)
            self.assertEqual(store.load()["phase"], "implementing")

    def test_security_review_fail_returns_to_implementing_within_budget(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace, base_state(phase="security_review"))
            attempt = self._attempt(workspace, "success", stdout='{"status": "fail"}\n')

            continued = _controller_iteration(
                store, lambda c: Result(),
                run_provider_fn=lambda *a, **k: attempt,
            )

            state = store.load()
            self.assertTrue(continued)
            self.assertEqual(state["phase"], "implementing")
            self.assertEqual(state["attempts"]["security_review"], 1)

    def test_security_review_fail_exhausting_budget_hands_off(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = base_state(phase="security_review", attempts={"security_review": 4})
            store = self._store(workspace, state)
            attempt = self._attempt(workspace, "success", stdout='{"status": "fail"}\n')

            continued = _controller_iteration(
                store, lambda c: Result(),
                run_provider_fn=lambda *a, **k: attempt,
            )

            state = store.load()
            self.assertFalse(continued)
            self.assertEqual(state["phase"], "handoff")
            self.assertEqual(state["attempts"]["security_review"], 5)

    def test_ready_for_pr_drafts_then_opens_pull_request(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace, base_state(phase="ready_for_pr"))
            attempt = self._attempt(
                workspace, "success", stdout="Ship widget\n\nImplements the widget.\n"
            )
            commands = []

            def run(command):
                commands.append(command)
                if command[:3] == ["gh", "pr", "list"]:
                    return Result(json.dumps([]))
                if command[:3] == ["gh", "pr", "create"]:
                    return Result("https://github.com/org/repo/pull/7\n")
                if command[:3] == ["gh", "pr", "view"]:
                    return Result(json.dumps({
                        "number": 7, "url": "https://github.com/org/repo/pull/7",
                        "state": "OPEN", "headRefOid": "sha123", "headRefName": "dark-factory/issue-42",
                    }))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run, run_provider_fn=lambda *a, **k: attempt)

            state = store.load()
            self.assertTrue(continued)
            self.assertEqual(state["phase"], "ci_wait")
            create_command = next(c for c in commands if c[:3] == ["gh", "pr", "create"])
            self.assertIn("Closes #42", create_command[create_command.index("--body") + 1])

    def test_manual_merge_mode_stops_at_handoff_after_green_ci(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace, ready_state(phase="ci_wait"))
            commands = []

            def run(command):
                commands.append(command)
                if command[:3] == ["gh", "pr", "checks"]:
                    return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            state = store.load()
            self.assertFalse(continued)
            self.assertEqual(state["phase"], "handoff")
            self.assertNotIn(["gh", "pr", "merge"], [c[:3] for c in commands])

    def test_auto_merge_mode_merges_after_green_ci_and_strict_gate(self):
        auto_policy = {**POLICY, "merge": {"mode": "auto"}}
        with tempfile.TemporaryDirectory() as workspace:
            write_policy(workspace, auto_policy)
            store = StateStore(workspace)
            store.save(ready_state(phase="ci_wait"))
            commands = []

            def run(command):
                commands.append(command)
                if command[:3] == ["gh", "pr", "checks"]:
                    return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
                if command[:3] == ["gh", "pr", "view"]:
                    return Result(json.dumps({
                        "number": 7, "url": "https://github.com/org/repo/pull/7",
                        "state": "OPEN", "headRefOid": "sha123", "reviewDecision": None,
                    }))
                if command[:3] == ["gh", "pr", "merge"]:
                    return Result("Merged\n")
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            state = store.load()
            self.assertFalse(continued)
            self.assertEqual(state["phase"], "merged")
            self.assertIn(["gh", "pr", "merge"], [c[:3] for c in commands])

    def test_auto_merge_mode_hands_off_when_gate_fails(self):
        auto_policy = {**POLICY, "merge": {"mode": "auto"}}
        with tempfile.TemporaryDirectory() as workspace:
            write_policy(workspace, auto_policy)
            store = StateStore(workspace)
            store.save(ready_state(phase="ci_wait", head_sha="stale"))
            commands = []

            def run(command):
                commands.append(command)
                if command[:3] == ["gh", "pr", "checks"]:
                    return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
                if command[:3] == ["gh", "pr", "view"]:
                    return Result(json.dumps({
                        "number": 7, "url": "https://github.com/org/repo/pull/7",
                        "state": "OPEN", "headRefOid": "sha123", "reviewDecision": None,
                    }))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            state = store.load()
            self.assertFalse(continued)
            self.assertEqual(state["phase"], "handoff")
            self.assertNotIn(["gh", "pr", "merge"], [c[:3] for c in commands])

    def test_ci_repair_loop_hands_off_on_fifth_failure(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = base_state(phase="ci_wait", pr=7, attempts={"ci:unit-tests": 4})
            store = self._store(workspace, state)

            def run(command):
                if command[:3] == ["gh", "pr", "checks"]:
                    return Result(json.dumps([{"name": "unit-tests", "state": "FAILURE", "bucket": "fail"}]))
                raise AssertionError(f"unexpected command: {command}")

            continued = _controller_iteration(store, run)

            state = store.load()
            self.assertFalse(continued)
            self.assertEqual(state["phase"], "handoff")
            self.assertEqual(state["attempts"]["ci:unit-tests"], 5)

    @mock.patch("subprocess.run")
    def test_full_delivery_walks_planning_through_manual_handoff(self, run_subprocess):
        with tempfile.TemporaryDirectory() as workspace:
            store = self._store(workspace)

            def gh_run(command):
                if command[:3] == ["gh", "issue", "list"]:
                    return Result(json.dumps([dict(ISSUE, run_id=None)]))
                if command[:3] == ["gh", "pr", "list"]:
                    return Result(json.dumps([]))
                if command[:3] == ["gh", "pr", "create"]:
                    return Result("https://github.com/org/repo/pull/7\n")
                if command[:3] == ["gh", "pr", "view"]:
                    return Result(json.dumps({
                        "number": 7, "url": "https://github.com/org/repo/pull/7",
                        "state": "OPEN", "headRefOid": "sha123", "headRefName": "dark-factory/issue-42",
                    }))
                if command[:3] == ["gh", "pr", "checks"]:
                    return Result(json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass"}]))
                raise AssertionError(f"unexpected gh command: {command}")

            def provider_process(command, cwd, input, text, capture_output, check):
                phase_output = {
                    "# Factory Planner": "planned\n",
                    "# Factory Implementer": "implemented\n",
                    "# Factory Tester": '{"status": "pass"}\n',
                    "# Factory Security Reviewer": '{"status": "pass"}\n',
                    "# Factory Reviewer": '{"status": "pass"}\n',
                    "# Factory PR Author": "Ship widget\n\nImplements the widget end to end.\n",
                }
                for marker, stdout in phase_output.items():
                    if marker in input:
                        return subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout, stderr="")
                raise AssertionError(f"unrecognized prompt: {input[:80]!r}")

            run_subprocess.side_effect = provider_process

            phases_seen = []
            for _ in range(20):
                continued = _controller_iteration(store, gh_run)
                phases_seen.append(store.load()["phase"])
                if not continued:
                    break

            self.assertEqual(store.load()["phase"], "handoff")
            self.assertIn("ready_for_pr", phases_seen)
            self.assertIn("ci_wait", phases_seen)


if __name__ == "__main__":
    unittest.main()
