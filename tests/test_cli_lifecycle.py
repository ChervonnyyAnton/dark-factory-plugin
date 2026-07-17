import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_lifecycle", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)


POLICY = {
    "queue": {"assignees": ["factory"], "labels": [], "match": "any"},
    "repositories": ["org/repo"],
    "providers": {"implement": ["claude"], "review": ["claude"]},
}


class Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0
        self.stderr = ""


def write_policy(workspace):
    directory = Path(workspace) / ".dark-factory"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "policy.json").write_text(json.dumps(POLICY))


class LifecycleTests(unittest.TestCase):
    def test_stop_preserves_delivery_state(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = dark_factory.StateStore(workspace)
            store.save({"phase": "implementing", "issue": {"number": 42}})

            self.assertEqual(dark_factory.stop_controller(workspace), 0)

            state = store.load()
            self.assertEqual(state["requested"], "stop")
            self.assertEqual(state["phase"], "implementing")
            self.assertEqual(state["issue"]["number"], 42)

    def test_worker_honors_stop_before_advancing_delivery(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = dark_factory.StateStore(workspace)
            store.save({
                "requested": "stop",
                "phase": "implementing",
                "issue": {"number": 42},
            }, force_requested="stop")

            continued = dark_factory._controller_iteration(
                store, lambda command: self.fail(f"command ran: {command}"),
            )

            self.assertFalse(continued)
            self.assertEqual(store.load()["phase"], "stopped")

    def test_monitor_prints_next_wake(self):
        with tempfile.TemporaryDirectory() as workspace:
            dark_factory.StateStore(workspace).save({
                "issue": {"number": 42},
                "next_wake_at": "2026-07-17T10:00:00Z",
            })
            output = io.StringIO()

            with redirect_stdout(output):
                result = dark_factory.monitor_controller(workspace)

            self.assertIn("next wake: 2026-07-17T10:00:00Z", result)
            self.assertEqual(result + "\n", output.getvalue())

    def test_dry_run_selects_without_provider_pr_or_merge(self):
        with tempfile.TemporaryDirectory() as workspace:
            write_policy(workspace)
            commands = []

            def run(command):
                commands.append(command)
                if command[:3] == ["gh", "issue", "list"]:
                    return Result(json.dumps([{
                        "number": 42,
                        "title": "Ship widget",
                        "url": "https://github.com/org/repo/issues/42",
                        "createdAt": "2026-07-01T00:00:00Z",
                        "assignees": [{"login": "factory"}],
                        "labels": [],
                    }]))
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(dark_factory, "run_provider") as provider:
                result = dark_factory.dry_run_controller(workspace, run=run)

            state = dark_factory.StateStore(workspace).load()
            self.assertEqual(result, 0)
            self.assertEqual(state["phase"], "dry_run")
            self.assertEqual(state["issue"]["number"], 42)
            self.assertTrue(Path(state["dry_run_prompt"]).is_file())
            provider.assert_not_called()
            self.assertFalse(any(command[:3] in (
                ["gh", "pr", "create"], ["gh", "pr", "merge"],
            ) for command in commands))

    def test_start_refuses_workspace_without_policy(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(RuntimeError, "missing policy"):
                dark_factory.start_controller(workspace)

    @mock.patch.object(dark_factory.subprocess, "Popen")
    def test_start_waits_for_worker_pid_handshake(self, popen):
        with tempfile.TemporaryDirectory() as workspace:
            write_policy(workspace)
            store = dark_factory.StateStore(workspace)

            class Process:
                pid = 4321

                def poll(self):
                    state = store.load()
                    state.update(requested="running", worker_pid=self.pid)
                    store.save(state, force_requested="running")
                    return None

            popen.return_value = Process()

            self.assertEqual(dark_factory.start_controller(workspace, handshake_timeout=1), 0)
            self.assertEqual(store.load()["worker_pid"], 4321)
            command = popen.call_args.args[0]
            self.assertEqual(command[1:], ["run", "--workspace", str(Path(workspace).resolve())])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
