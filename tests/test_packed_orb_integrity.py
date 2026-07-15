from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def integrity_command(name: str) -> str:
    config = yaml.safe_load(
        (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
    )
    return config["commands"][name]["steps"][0]["run"]["command"]


def run_command(command: str, working_directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command],
        cwd=working_directory,
        check=False,
        capture_output=True,
        text=True,
    )


class PackedOrbIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record_command = integrity_command("record-packed-orb")
        self.verify_command = integrity_command("verify-packed-orb")

    def test_records_and_verifies_exact_packed_orb(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dist = root / "dist"
            dist.mkdir()
            (dist / "orb.yml").write_text("version: 2.1\n", encoding="utf-8")

            recorded = run_command(self.record_command, root)
            verified = run_command(self.verify_command, root)

            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            manifest = (dist / "orb.yml.sha256").read_text(encoding="utf-8")
            digest, name = manifest.rstrip("\n").split("  ", maxsplit=1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(name, "orb.yml")

    def test_rejects_packed_orb_changed_after_checksum_was_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dist = root / "dist"
            dist.mkdir()
            orb = dist / "orb.yml"
            orb.write_text("version: 2.1\n", encoding="utf-8")
            self.assertEqual(run_command(self.record_command, root).returncode, 0)
            orb.write_text("version: 2.1\n# changed\n", encoding="utf-8")

            result = run_command(self.verify_command, root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)

    def test_rejects_manifest_with_an_extra_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dist = root / "dist"
            dist.mkdir()
            (dist / "orb.yml").write_text("version: 2.1\n", encoding="utf-8")
            self.assertEqual(run_command(self.record_command, root).returncode, 0)
            manifest = dist / "orb.yml.sha256"
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + f"{'0' * 64}  other.yml\n",
                encoding="utf-8",
            )

            result = run_command(self.verify_command, root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one line", result.stderr)

    def test_rejects_symlinked_packed_orb(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dist = root / "dist"
            dist.mkdir()
            target = root / "target.yml"
            target.write_text("version: 2.1\n", encoding="utf-8")
            (dist / "orb.yml").symlink_to(target)

            result = run_command(self.record_command, root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("regular, non-symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
