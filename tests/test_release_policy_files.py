from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


class ReleasePolicyFileTests(unittest.TestCase):
    def test_checked_in_release_policy_signature_verifies(self) -> None:
        policy_directory = ROOT / ".github/release-policy"
        attestation_path = policy_directory / "attestation.json"
        attestation = release.read_json(attestation_path)
        self.assertEqual(
            attestation_path.read_text(encoding="utf-8"),
            json.dumps(
                attestation,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        signature = base64.b64decode(
            b"".join((policy_directory / "attestation.sig").read_bytes().split()),
            validate=True,
        )
        with tempfile.NamedTemporaryFile() as signature_file:
            signature_file.write(signature)
            signature_file.flush()
            result = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(policy_directory / "public-key.pem"),
                    "-signature",
                    signature_file.name,
                    str(attestation_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Verified OK")

    def test_documented_public_key_fingerprint_matches_the_key(self) -> None:
        key_path = ROOT / ".github/release-policy/public-key.pem"
        result = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(key_path), "-outform", "DER"],
            check=False,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        actual = hashlib.sha256(result.stdout).hexdigest()
        reference = (ROOT / "docs/reference/release-policy.md").read_text(
            encoding="utf-8"
        )
        documented = re.search(
            r"DER-encoded SHA-256 fingerprint is `([0-9a-f]{64})`",
            reference,
        )

        self.assertIsNotNone(documented)
        assert documented is not None
        self.assertEqual(documented.group(1), actual)


if __name__ == "__main__":
    unittest.main()
