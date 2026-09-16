from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publish-development-orb.sh"


class PublishDevelopmentOrbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "dist/orb.yml"
        self.candidate.parent.mkdir()
        self.candidate.write_text("version: 2.1\n", encoding="utf-8")
        self.registry = self.root / "registry.yml"
        self.log = self.root / "circleci.log"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "circleci"
        fake.write_text(
            """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >>"${FAKE_LOG}"
if [[ "$1 $2" == "orb publish" ]]; then
  [[ "${CIRCLECI_CLI_TOKEN:-}" == "test-token" ]] || exit 3
  cp dist/orb.yml "${FAKE_REGISTRY}"
  [[ "${FAKE_MODE}" != "lost-response" ]] || exit 1
  exit 0
fi
if [[ "$1 $2" == "orb source" ]]; then
  source_count=0
  [[ ! -f "${FAKE_SOURCE_COUNT}" ]] \
    || source_count="$(cat "${FAKE_SOURCE_COUNT}")"
  source_count=$((source_count + 1))
  printf '%s\n' "${source_count}" >"${FAKE_SOURCE_COUNT}"
  if [[ "${FAKE_MODE}" == "delayed" && "${source_count}" -lt 3 ]] \
    || [[ "${FAKE_MODE}" == "final-check" && "${source_count}" -lt 13 ]]; then
    exit 1
  elif [[ "${FAKE_MODE}" == "mismatch" ]]; then
    printf 'version: 2.0\n'
  else
    cat "${FAKE_REGISTRY}"
  fi
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
        self,
        *,
        reference: str = "dev:" + "a" * 40,
        token: str = "test-token",
        mode: str = "success",
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "CIRCLE_TOKEN": token,
            "FAKE_LOG": str(self.log),
            "FAKE_MODE": mode,
            "FAKE_REGISTRY": str(self.registry),
            "FAKE_SOURCE_COUNT": str(self.root / "source-count"),
            "ORB_DEVELOPMENT_REFERENCE": reference,
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

    def test_publishes_and_verifies_exact_commit_reference(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Published and verified", result.stdout)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("@dev:" + "a" * 40, log)
        self.assertNotIn("test-token", log + result.stdout + result.stderr)

    def test_alpha_is_the_only_named_development_reference(self) -> None:
        self.assertEqual(self.run_script(reference="dev:alpha").returncode, 0)
        for reference in ("dev:beta", "dev:a", "0.1.0", "dev:" + "A" * 40):
            with self.subTest(reference=reference):
                result = self.run_script(reference=reference)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Development reference must", result.stderr)

    def test_missing_token_or_registry_mismatch_fails(self) -> None:
        missing = self.run_script(token="")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("CIRCLE_TOKEN is required", missing.stderr)

        mismatch = self.run_script(mode="mismatch")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("Could not publish or verify", mismatch.stderr)

    def test_retries_registry_read_and_recovers_a_lost_publish_response(self) -> None:
        delayed = self.run_script(mode="delayed")
        self.assertEqual(delayed.returncode, 0, delayed.stderr)
        self.assertEqual(
            (self.root / "source-count").read_text(encoding="utf-8"), "3\n"
        )

        (self.root / "source-count").unlink()
        final_check = self.run_script(mode="final-check")
        self.assertEqual(final_check.returncode, 0, final_check.stderr)
        self.assertEqual(
            (self.root / "source-count").read_text(encoding="utf-8"), "13\n"
        )

        (self.root / "source-count").unlink()
        lost = self.run_script(mode="lost-response")
        self.assertEqual(lost.returncode, 0, lost.stderr)
        self.assertIn("publish response failed", lost.stdout)


if __name__ == "__main__":
    unittest.main()
