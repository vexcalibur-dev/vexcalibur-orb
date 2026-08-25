from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pack-orb.sh"


class PackOrbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "src"
        self.source.mkdir()
        (self.source / "@orb.yml").write_text("version: 2.1\n", encoding="utf-8")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "circleci"
        fake.write_text(
            """#!/bin/bash
set -euo pipefail
if [[ "$1 $2" == "orb pack" ]]; then
  printf 'version: 2.1\n'
  exit 0
fi
if [[ "$1 $2" == "orb validate" ]]; then
  [[ "${FAKE_VALIDATE}" == "success" ]]
  exit
fi
exit 2
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(
        self, *, validate: str = "success"
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "FAKE_VALIDATE": validate,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_packs_and_validates_before_replacing_candidate(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "dist/orb.yml").read_text(encoding="utf-8"),
            "version: 2.1\n",
        )
        self.assertRegex(result.stdout, r"[0-9a-f]{64}")

    def test_validation_failure_preserves_existing_candidate(self) -> None:
        candidate = self.root / "dist/orb.yml"
        candidate.parent.mkdir()
        candidate.write_text("existing\n", encoding="utf-8")

        result = self.run_script(validate="failure")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(candidate.read_text(encoding="utf-8"), "existing\n")
        self.assertEqual(list(candidate.parent.glob("*.tmp.*")), [])

    def test_rejects_missing_or_symlinked_source(self) -> None:
        for mode in ("missing", "symlink"):
            with self.subTest(mode=mode):
                self.source.rename(self.root / "source-target")
                if mode == "symlink":
                    self.source.symlink_to(self.root / "source-target")

                result = self.run_script()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not a symlink", result.stderr)
                if self.source.is_symlink():
                    self.source.unlink()
                (self.root / "source-target").rename(self.source)


if __name__ == "__main__":
    unittest.main()
