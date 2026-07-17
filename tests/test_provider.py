import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_provider", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)

build_phase_prompt = getattr(dark_factory, "build_phase_prompt", lambda *args: None)
run_provider = getattr(dark_factory, "run_provider", lambda *args, **kwargs: None)
_parse_pr_title_and_body = getattr(dark_factory, "_parse_pr_title_and_body", lambda text: ("", ""))


class ProviderTests(unittest.TestCase):
    def test_build_phase_prompt_includes_contract_and_issue_number(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            issue = {"run_id": "run-1", "number": 42, "title": "Ship runner"}

            prompt = build_phase_prompt(workspace, "planning", issue, "planner")

            self.assertEqual(
                prompt,
                workspace / ".dark-factory" / "runs" / "run-1" / "planning.prompt.md",
            )
            contents = prompt.read_text()
            self.assertIn("# Factory Planner", contents)
            self.assertIn("Issue #42", contents)
            self.assertIn("Ship runner", contents)

    def test_build_phase_prompt_repairing_includes_failed_check_and_review_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            issue = {"run_id": "run-repair", "number": 42, "title": "Ship widget"}
            review_dir = workspace / ".dark-factory" / "runs" / "issue-42-abc123-self_review-0"
            review_dir.mkdir(parents=True)
            (review_dir / "stdout.txt").write_text('{"status": "pass"}\n')
            state = {
                "failed_check": "unit-tests",
                "pr": 7,
                "self_review": {
                    "status": "pass",
                    "provider_status": "success",
                    "run_dir": str(review_dir),
                },
            }

            prompt = build_phase_prompt(workspace, "repairing", issue, "repairer", state=state)
            contents = prompt.read_text()

            self.assertIn("# Factory Repairer", contents)
            self.assertIn("Failed CI check: `unit-tests`", contents)
            self.assertIn("Pull request: #7", contents)
            self.assertIn("## self_review", contents)
            self.assertIn('"status": "pass"', contents)
            self.assertIn('{"status": "pass"}', contents)

    def test_parse_pr_title_and_body_uses_labeled_sections(self):
        text = (
            "## Commit message\n\n"
            "feat(widget): ship end-to-end\n\n"
            "## Pull request title\n\n"
            "Ship widget\n\n"
            "## Pull request body\n\n"
            "Implements the widget end to end.\n\n"
            "Closes #42\n"
        )
        title, body = _parse_pr_title_and_body(text)
        self.assertEqual(title, "Ship widget")
        self.assertIn("Implements the widget end to end.", body)
        self.assertNotIn("feat(widget)", title)

    def test_parse_pr_title_and_body_legacy_first_line_title(self):
        title, body = _parse_pr_title_and_body("Ship widget\n\nImplements the widget.\n")
        self.assertEqual(title, "Ship widget")
        self.assertEqual(body, "Implements the widget.")

    @mock.patch("subprocess.run")
    def test_success_records_provider_artifacts(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="implemented\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            prompt = workspace / "prompt.md"
            prompt.write_text("Do the work")

            result = run_provider(
                workspace, "claude", prompt, "run-2", max_turns=7
            )

            run_dir = workspace / ".dark-factory" / "runs" / "run-2"
            self.assertEqual(result.status, "success")
            self.assertEqual((run_dir / "stdout.txt").read_text(), "implemented\n")
            self.assertEqual((run_dir / "stderr.txt").read_text(), "")
            self.assertEqual((run_dir / "handoff.md").read_text(), "implemented\n")
            record = json.loads((run_dir / "result.json").read_text())
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["provider"], "claude")
            self.assertEqual(record["max_turns"], 7)
            command = run.call_args.args[0]
            self.assertEqual(
                command,
                ["claude", "-p", "--dangerously-skip-permissions"],
            )
            self.assertNotIn("dark-factory", command)

    @mock.patch("subprocess.run")
    def test_capacity_stderr_returns_capacity_exhausted(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="You have exhausted your usage capacity",
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            prompt = workspace / "prompt.md"
            prompt.write_text("Do the work")

            result = run_provider(
                workspace, "codex", prompt, "run-3", max_turns=3
            )

            self.assertEqual(result.status, "capacity_exhausted")
            self.assertEqual(
                json.loads(result.result_path.read_text())["status"],
                "capacity_exhausted",
            )

    @mock.patch("subprocess.run")
    def test_provider_subcommand_returns_provider_exit_code(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=4, stdout="", stderr="failed"
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            prompt = workspace / "prompt.md"
            prompt.write_text("Do the work")

            returncode = dark_factory.main([
                "provider", str(workspace), "codex", str(prompt), "run-4",
                "--max-turns", "5",
            ])

            self.assertEqual(returncode, 4)


if __name__ == "__main__":
    unittest.main()
