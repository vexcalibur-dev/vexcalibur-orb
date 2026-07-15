from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SPEC_PATTERN = re.compile(r"vexcalibur==\d+(?:\.\d+){1,2}(?:\.post\d+)?")
PACKAGE_SPEC_FILES = [
    "README.md",
    "docs/reference/orb.md",
    "src/commands/run_vexcalibur.yml",
    "src/jobs/run.yml",
    "src/scripts/run-vexcalibur.sh",
]


class RepositoryConsistencyTests(unittest.TestCase):
    def test_default_package_spec_is_consistent(self) -> None:
        specs_by_path: dict[str, set[str]] = {}
        for relative_path in PACKAGE_SPEC_FILES:
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            specs_by_path[relative_path] = set(PACKAGE_SPEC_PATTERN.findall(content))

        all_specs = set().union(*specs_by_path.values())

        self.assertEqual(all_specs, {"vexcalibur==0.2.0"}, specs_by_path)

    def test_acceptance_fixtures_are_local_and_valid_json(self) -> None:
        config = (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
        self.assertNotIn("--allow-public-osv", config)
        self.assertIn("--format\n            cyclonedx", config)
        self.assertIn("--format\n            openvex", config)

        for relative_path in (
            "tests/fixtures/cyclonedx-sbom.json",
            "tests/fixtures/local-findings.json",
        ):
            with (REPO_ROOT / relative_path).open(encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)


if __name__ == "__main__":
    unittest.main()
