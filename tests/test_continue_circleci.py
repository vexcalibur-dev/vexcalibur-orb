from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/continue-circleci.sh"


class ContinueCircleCITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.payload = self.root / "payload.json"

        circleci = self.bin / "circleci"
        circleci.write_text(
            """#!/bin/bash
set -euo pipefail
case "$1 $2" in
  "orb pack")
    [[ "$3 $4" == "--skip-update-check src" ]]
    printf 'version: 2.1\n'
    ;;
  "orb validate"|"config validate")
    [[ "$3" == "--skip-update-check" ]]
    test -s "$4"
    ;;
  *)
    echo "unexpected circleci arguments: $*" >&2
    exit 99
    ;;
esac
""",
            encoding="utf-8",
        )
        circleci.chmod(0o755)

        yq = self.bin / "yq"
        yq.write_text(
            """#!/bin/bash
set -euo pipefail
[[ "$1" == '.orbs.vexcalibur = load(strenv(ORB_SOURCE))' ]]
[[ "$2" == ".circleci/test-deploy.yml" ]]
grep -Fxq 'version: 2.1' "${ORB_SOURCE}"
printf 'version: 2.1\norbs:\n  vexcalibur:\n    version: 2.1\n'
""",
            encoding="utf-8",
        )
        yq.chmod(0o755)

        curl = self.bin / "curl"
        curl.write_text(
            """#!/bin/bash
set -euo pipefail
[[ "$#" -eq 18 ]]
[[ "$1" == "--silent" ]]
[[ "$2" == "--show-error" ]]
[[ "$3" == "--output" ]]
[[ -n "$4" ]]
[[ "$5" == "--write-out" && "$6" == '%{http_code}' ]]
[[ "$7" == "--request" && "$8" == "POST" ]]
[[ "$9" == "--header" && "${10}" == "Accept: application/json" ]]
[[ "${11}" == "--header" && "${12}" == "Content-Type: application/json" ]]
[[ "${13}" == "--data-binary" && "${14}" == @* ]]
[[ "${15}" == "--proto" && "${16}" == "=https" ]]
[[ "${17}" == "--tlsv1.2" ]]
[[ "${18}" == "https://circleci.com/api/v2/pipeline/continue" ]]
cp -- "${14#@}" "${FAKE_PAYLOAD}"
printf '{}\n' >"$4"
printf '%s' "${FAKE_STATUS}"
""",
            encoding="utf-8",
        )
        curl.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(
        self, *, token: str = "continuation-secret", status: str = "200"
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "CIRCLE_CONTINUATION_KEY": token,
            "FAKE_PAYLOAD": str(self.payload),
            "FAKE_STATUS": status,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_sends_validated_configuration_without_logging_key(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(self.payload.read_text(encoding="utf-8"))
        self.assertEqual(document["continuation-key"], "continuation-secret")
        configuration = yaml.safe_load(document["configuration"])
        self.assertEqual(configuration["orbs"]["vexcalibur"], {"version": 2.1})
        self.assertNotIn(
            "continuation-secret", result.stdout + result.stderr
        )

    def test_missing_key_or_failed_request_stops_continuation(self) -> None:
        missing = self.run_script(token="")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("CIRCLE_CONTINUATION_KEY is required", missing.stderr)

        failed = self.run_script(status="503")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("HTTP 503", failed.stderr)


if __name__ == "__main__":
    unittest.main()
