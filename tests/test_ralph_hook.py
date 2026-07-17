import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
HOOK = ROOT / "hooks" / "stop-ralph.sh"
START = ROOT / "hooks" / "start-ralph.py"
RALPH_COMMAND = ROOT / "commands" / "ralph.md"
CANCEL_COMMAND = ROOT / "commands" / "cancel.md"


class RalphHookTests(unittest.TestCase):
    def run_start(self, workspace, arguments, **environment):
        return subprocess.run(
            ["python3", str(START), arguments],
            cwd=workspace,
            env=os.environ | environment,
            text=True,
            capture_output=True,
        )

    def hold_controller_lock(self, workspace, issue):
        script = """
import fcntl, json, os, pathlib, sys
root = pathlib.Path(sys.argv[1]) / ".dark-factory"
root.mkdir()
lock = root / "controller.lock"
with lock.open("w") as stream:
    json.dump({"pid": os.getpid(), "started_at": "now"}, stream)
    stream.flush()
    fcntl.flock(stream, fcntl.LOCK_EX)
    (root / "controller.json").write_text(json.dumps({"issue": %s}))
    print("ready", flush=True)
    sys.stdin.read()
""" % json.dumps(issue)
        process = subprocess.Popen(
            ["python3", "-c", script, workspace],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(process.stdout.readline().strip(), "ready")
        self.addCleanup(process.communicate, "")
        return process

    def run_hook(self, workspace, transcript_entries, **hook_input):
        transcript = Path(workspace) / "transcript.jsonl"
        transcript.write_text(
            "".join(json.dumps(entry) + "\n" for entry in transcript_entries)
        )
        return subprocess.run(
            ["bash", str(HOOK)],
            cwd=workspace,
            input=json.dumps({"transcript_path": str(transcript)} | hook_input),
            text=True,
            capture_output=True,
        )

    @staticmethod
    def write_state(workspace, **overrides):
        state = {
            "prompt": "Keep fixing the issue",
            "iteration": 1,
            "max_iterations": 5,
            "completion_promise": "DONE",
        }
        state.update(overrides)
        path = Path(workspace) / ".dark-factory" / "ralph-session.json"
        path.parent.mkdir()
        path.write_text(json.dumps(state))
        return path

    def test_active_loop_blocks_stop_and_reprints_prompt(self):
        with tempfile.TemporaryDirectory() as workspace:
            state_path = self.write_state(workspace)

            result = self.run_hook(
                workspace,
                [{"message": {"role": "assistant", "content": [
                    {"type": "text", "text": "Still working"}
                ]}}],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["decision"], "block")
            self.assertEqual(output["reason"], "Keep fixing the issue")
            self.assertIn("Ralph iteration 2", output["systemMessage"])
            self.assertEqual(json.loads(state_path.read_text())["iteration"], 2)

    def test_start_command_creates_complete_atomic_session(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_start(
                workspace,
                'Fix issue #42 --max-iterations 3 --completion-promise "ALL DONE"',
                CLAUDE_CODE_SESSION_ID="session-42",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_dir = Path(workspace) / ".dark-factory"
            self.assertEqual(
                json.loads((state_dir / "ralph-session.json").read_text()),
                {
                    "prompt": "Fix issue #42",
                    "iteration": 1,
                    "max_iterations": 3,
                    "completion_promise": "ALL DONE",
                    "session_id": "session-42",
                },
            )
            self.assertEqual(list(state_dir.glob(".ralph-session.json.*")), [])
            self.assertRegex(RALPH_COMMAND.read_text(), r"```!\n.*start-ralph\.py")

    def test_start_refuses_same_issue_held_by_controller(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.hold_controller_lock(workspace, {"number": 42})

            result = self.run_start(workspace, "Fix #42")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dark-factory monitor", result.stderr)
            self.assertIn("stop the detached controller", result.stderr)
            self.assertFalse(
                (Path(workspace) / ".dark-factory" / "ralph-session.json").exists()
            )

    def test_start_refuses_live_controller_when_prompt_issue_is_unknown(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.hold_controller_lock(workspace, {"number": 42})

            result = self.run_start(workspace, "Refactor the active work")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dark-factory monitor", result.stderr)

    def test_start_allows_different_issue_from_live_controller(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.hold_controller_lock(workspace, 42)

            result = self.run_start(workspace, "Fix issue 43")

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(
                (Path(workspace) / ".dark-factory" / "ralph-session.json").read_text()
            )
            self.assertEqual(state["prompt"], "Fix issue 43")

    def test_completion_promise_allows_stop_and_clears_state(self):
        with tempfile.TemporaryDirectory() as workspace:
            state_path = self.write_state(workspace)

            result = self.run_hook(
                workspace,
                [{"message": {"role": "assistant", "content": [
                    {"type": "text", "text": "Finished\n<promise>DONE</promise>"}
                ]}}],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_path.exists())
            self.assertNotIn('"decision": "block"', result.stdout)

    def test_max_iterations_allows_stop_and_clears_state(self):
        with tempfile.TemporaryDirectory() as workspace:
            state_path = self.write_state(workspace, max_iterations=1)

            result = self.run_hook(workspace, [])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_path.exists())
            self.assertIn("reached max iterations (1)", result.stdout)

    def test_session_owned_by_another_hook_is_ignored(self):
        with tempfile.TemporaryDirectory() as workspace:
            state_path = self.write_state(workspace, session_id="session-one")

            result = self.run_hook(
                workspace,
                [],
                session_id="session-two",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(state_path.read_text())["iteration"], 1)

    def test_cancel_command_removes_session_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            state_path = self.write_state(workspace)
            command = re.search(
                r"```!\n(.*?)\n```", CANCEL_COMMAND.read_text(), re.DOTALL
            ).group(1)

            result = subprocess.run(
                ["bash", "-c", command],
                cwd=workspace,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_path.exists())
            self.assertIn("Cancelled Ralph loop", result.stdout)


if __name__ == "__main__":
    unittest.main()
