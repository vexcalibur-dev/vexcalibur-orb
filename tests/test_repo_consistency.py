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

        self.assertEqual(all_specs, {"vexcalibur==0.3.0"}, specs_by_path)

    def test_acceptance_fixtures_are_local_and_valid_json(self) -> None:
        config = (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
        self.assertNotIn("--allow-public-osv", config)
        self.assertIn("--format\n            cyclonedx", config)
        self.assertIn("--format\n            openvex", config)
        self.assertIn("--format\n            csaf", config)
        self.assertIn("--csaf-document-id\n            VEXCALIBUR-ORB-ACCEPTANCE", config)
        self.assertIn(
            "--output\n            artifacts/acceptance/vexcalibur-orb-acceptance.json",
            config,
        )

        csaf_example = (REPO_ROOT / "src/examples/generate_csaf.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--offline", csaf_example)
        self.assertNotIn("--allow-public-osv", csaf_example)
        self.assertIn("--format\n              csaf", csaf_example)
        self.assertIn(
            "--csaf-document-id\n              EXAMPLE-CSAF-VEX-2026-001",
            csaf_example,
        )
        for option in (
            "--csaf-document-title",
            "--csaf-publisher-name",
            "--csaf-publisher-namespace",
            "--csaf-publisher-category",
            "--csaf-document-status",
        ):
            self.assertIn(option, csaf_example)
        self.assertIn(
            "--output\n              artifacts/example-csaf-vex-2026-001.json",
            csaf_example,
        )

        for relative_path in (
            "tests/fixtures/cyclonedx-sbom.json",
            "tests/fixtures/local-findings.json",
        ):
            with (REPO_ROOT / relative_path).open(encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)


if __name__ == "__main__":
    unittest.main()
