from __future__ import annotations

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
]


class RepositoryConsistencyTests(unittest.TestCase):
    def test_default_package_spec_is_consistent(self) -> None:
        specs_by_path: dict[str, set[str]] = {}
        for relative_path in PACKAGE_SPEC_FILES:
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            specs_by_path[relative_path] = set(PACKAGE_SPEC_PATTERN.findall(content))

        all_specs = set().union(*specs_by_path.values())

        self.assertEqual(all_specs, {"vexcalibur==0.1.1"}, specs_by_path)


if __name__ == "__main__":
    unittest.main()
