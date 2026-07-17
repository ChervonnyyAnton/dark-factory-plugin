import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
ROLES = (
    "planner",
    "implementer",
    "tester",
    "reviewer",
    "security-reviewer",
    "repairer",
    "pr-author",
    "releaser",
)
HEADINGS = ("Goal", "Constraints", "Required output", "Forbidden actions")


class ContractTests(unittest.TestCase):
    def test_every_role_contract_exists_with_required_headings(self):
        for role in ROLES:
            with self.subTest(role=role):
                contract = ROOT / "contracts" / f"{role}.md"
                self.assertTrue(contract.is_file(), f"missing {contract}")
                lines = contract.read_text().splitlines()
                for heading in HEADINGS:
                    self.assertIn(f"## {heading}", lines)


if __name__ == "__main__":
    unittest.main()
