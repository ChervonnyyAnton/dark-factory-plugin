import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
HOOK = ROOT / "hooks" / "stop-ralph.sh"
CANCEL_COMMAND = ROOT / "commands" / "cancel.md"


class RalphHookTests(unittest.TestCase):
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
