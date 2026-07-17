import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
HOOK = ROOT / "hooks" / "pretool-safety.sh"


class SafetyHookTests(unittest.TestCase):
    def run_hook(self, workspace, tool_name, tool_input):
        return subprocess.run(
            ["bash", str(HOOK)],
            cwd=workspace,
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            env=os.environ,
            text=True,
            capture_output=True,
        )

    def assert_denied(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertTrue(decision["permissionDecisionReason"])

    def test_blocks_force_push(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_hook(
                workspace, "Bash", {"command": "git push origin main --force"}
            )

            self.assert_denied(result)

    def test_blocks_hard_reset(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_hook(
                workspace, "Bash", {"command": "git reset HEAD~1 --hard"}
            )

            self.assert_denied(result)

    def test_blocks_force_push_shell_continuations(self):
        commands = (
            "git push origin main --force;",
            "git push origin main --force && echo ok",
            "git push origin main --force | tee /tmp/log",
            "git push origin main --force\n",
            "git push origin main -f;",
            "git push\norigin main --force",
        )
        with tempfile.TemporaryDirectory() as workspace:
            for command in commands:
                with self.subTest(command=command):
                    result = self.run_hook(workspace, "Bash", {"command": command})
                    self.assert_denied(result)

    def test_blocks_hard_reset_shell_continuations(self):
        commands = (
            "git reset HEAD~1 --hard;",
            "git reset HEAD~1 --hard && git status",
            "git reset HEAD~1 --hard | tee /tmp/log",
            "git reset HEAD~1 --hard\n",
            "git reset\nHEAD~1 --hard",
        )
        with tempfile.TemporaryDirectory() as workspace:
            for command in commands:
                with self.subTest(command=command):
                    result = self.run_hook(workspace, "Bash", {"command": command})
                    self.assert_denied(result)

    def test_allows_safe_git_command(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_hook(
                workspace, "Bash", {"command": "git status --short"}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_defaults_block_env_without_policy(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_hook(workspace, "Read", {"file_path": ".env"})

            self.assert_denied(result)

    def test_defaults_block_workflow_descendants(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = self.run_hook(
                workspace,
                "Write",
                {"file_path": ".github/workflows/release.yml", "content": "..."},
            )

            self.assert_denied(result)

    def test_policy_denied_paths_block_matching_files(self):
        with tempfile.TemporaryDirectory() as workspace:
            policy_directory = Path(workspace) / ".dark-factory"
            policy_directory.mkdir()
            (policy_directory / "policy.json").write_text(
                json.dumps({"denied_paths": ["secrets/**"]})
            )

            result = self.run_hook(
                workspace, "Edit", {"file_path": "secrets/token.txt"}
            )

            self.assert_denied(result)

    def test_invalid_policy_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as workspace:
            policy_directory = Path(workspace) / ".dark-factory"
            policy_directory.mkdir()
            (policy_directory / "policy.json").write_text("{invalid")

            result = self.run_hook(workspace, "Read", {"file_path": ".env.local"})

            self.assert_denied(result)


if __name__ == "__main__":
    unittest.main()
