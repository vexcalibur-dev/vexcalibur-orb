from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publish-production-orb.sh"


class PublishProductionOrbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.candidate = self.dist / "orb.yml"
        self.candidate.write_text("version: 2.1\n", encoding="utf-8")
        self.registry = self.root / "registry.yml"
        self.log = self.root / "circleci.log"
        fake = self.bin / "circleci"
        fake.write_text(
            """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >>"${FAKE_LOG}"
if [[ "$1 $2" == "orb source" ]]; then
  source_count=0
  [[ ! -f "${FAKE_SOURCE_COUNT}" ]] \
    || source_count="$(cat "${FAKE_SOURCE_COUNT}")"
  source_count=$((source_count + 1))
  printf '%s\n' "${source_count}" >"${FAKE_SOURCE_COUNT}"
  if [[ "${FAKE_MODE}" == "final-check" ]]; then
    [[ "${source_count}" -ge 14 ]] || exit 1
    cat dist/orb.yml
    exit 0
  fi
  [[ -f "${FAKE_REGISTRY}" ]] || exit 1
  cat "${FAKE_REGISTRY}"
  exit 0
fi
if [[ "$1 $2" == "orb publish" ]]; then
  if [[ "${FAKE_MODE}" != "success-no-registry" \
    && "${FAKE_MODE}" != "final-check" ]]; then
    cp dist/orb.yml "${FAKE_REGISTRY}"
  fi
  [[ "${FAKE_MODE}" != "lost-response" ]] || exit 1
  echo "published"
  exit 0
fi
exit 2
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        fake_sleep = self.bin / "sleep"
        fake_sleep.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        fake_sleep.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(
        self, *, mode: str, release_tag: str = "v0.1.0"
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "CIRCLE_TOKEN": "test-token",
            "FAKE_LOG": str(self.log),
            "FAKE_MODE": mode,
            "FAKE_REGISTRY": str(self.registry),
            "FAKE_SOURCE_COUNT": str(self.root / "source-count"),
            "ORB_RELEASE_TAG": release_tag,
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

    def test_existing_exact_version_is_verified_without_publish(self) -> None:
        self.registry.write_bytes(self.candidate.read_bytes() + b"\n")

        result = self.run_script(mode="success")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified existing immutable orb", result.stdout)
        self.assertNotIn("orb publish", self.log.read_text(encoding="utf-8"))

    def test_lost_publish_response_recovers_from_exact_registry_source(self) -> None:
        result = self.run_script(mode="lost-response")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("publish response failed", result.stdout)
        self.assertEqual(self.registry.read_bytes(), self.candidate.read_bytes())

    def test_existing_mismatched_version_fails_without_publish(self) -> None:
        self.registry.write_text("version: 2.0\n", encoding="utf-8")

        result = self.run_script(mode="success")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the packed release", result.stderr)
        self.assertNotIn("orb publish", self.log.read_text(encoding="utf-8"))

    def test_successful_publish_without_registry_evidence_fails(self) -> None:
        result = self.run_script(mode="success-no-registry")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not publish or verify", result.stderr)

    def test_registry_is_checked_at_the_full_retry_deadline(self) -> None:
        result = self.run_script(mode="final-check")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "source-count").read_text(encoding="utf-8"), "14\n"
        )

    def test_release_tag_override_must_be_an_exact_version(self) -> None:
        for value in ("", "0.1.0", "v01.1.0", "v0.1", "v0.1.0-rc.1"):
            with self.subTest(value=value):
                result = self.run_script(mode="success", release_tag=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exact vMAJOR.MINOR.PATCH", result.stderr)


if __name__ == "__main__":
    unittest.main()
