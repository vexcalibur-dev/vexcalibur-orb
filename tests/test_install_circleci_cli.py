from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install-circleci-cli.sh"
VERSION = "0.1.38646"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CircleCIInstallerFixture:
    def __init__(self, root: Path, *, os_name: str = "linux", arch: str = "amd64") -> None:
        self.root = root
        self.release_dir = root / "release"
        self.fake_bin = root / "fake-bin"
        self.install_dir = root / "install"
        self.curl_log = root / "curl.log"
        self.os_name = os_name
        self.arch = arch
        self.release_dir.mkdir()
        self.fake_bin.mkdir()
        self._write_fake_curl()
        self._write_fake_uname()

    @property
    def archive_name(self) -> str:
        return f"circleci-cli_{VERSION}_{self.os_name}_{self.arch}.tar.gz"

    @property
    def checksums_name(self) -> str:
        return f"circleci-cli_{VERSION}_checksums.txt"

    @property
    def archive_path(self) -> Path:
        return self.release_dir / self.archive_name

    @property
    def checksums_path(self) -> Path:
        return self.release_dir / self.checksums_name

    def _write_fake_curl(self) -> None:
        script = self.fake_bin / "curl"
        script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                destination=""
                url=""
                while (($#)); do
                  case "$1" in
                    --output)
                      destination="$2"
                      shift 2
                      ;;
                    https://*)
                      url="$1"
                      shift
                      ;;
                    *)
                      shift
                      ;;
                  esac
                done

                [[ -n "$destination" && -n "$url" ]]
                asset_name="${url##*/}"
                cp "$FAKE_RELEASE_DIR/$asset_name" "$destination"
                printf '%s\n' "$asset_name" >> "$FAKE_CURL_LOG"
                """
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)

    def _write_fake_uname(self) -> None:
        script = self.fake_bin / "uname"
        script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                case "${1:-}" in
                  -s)
                    printf '%s\n' "$FAKE_UNAME_S"
                    ;;
                  -m)
                    printf '%s\n' "$FAKE_UNAME_M"
                    ;;
                  *)
                    exit 2
                    ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)

    def build_release(
        self,
        *,
        reported_version: str = VERSION,
        binary_name: str = "circleci",
    ) -> str:
        source = self.root / "circleci"
        source.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s\\n' '{reported_version}+test (release)'\n",
            encoding="utf-8",
        )
        source.chmod(0o755)

        archive_root = self.archive_name.removesuffix(".tar.gz")
        with tarfile.open(self.archive_path, "w:gz") as archive:
            archive.add(source, arcname=f"{archive_root}/{binary_name}")

        self.checksums_path.write_text(
            f"{sha256(self.archive_path)}  {self.archive_name}\n",
            encoding="utf-8",
        )
        return sha256(self.checksums_path)

    def environment(self, checksums_sha256: str) -> dict[str, str]:
        return {
            "CIRCLECI_CLI_CHECKSUMS_SHA256": checksums_sha256,
            "CIRCLECI_CLI_INSTALL_DIR": str(self.install_dir),
            "CIRCLECI_CLI_VERSION": VERSION,
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_RELEASE_DIR": str(self.release_dir),
            "FAKE_UNAME_M": "x86_64" if self.arch == "amd64" else "aarch64",
            "FAKE_UNAME_S": "Linux",
            "HOME": os.environ.get("HOME", ""),
            "PATH": f"{self.fake_bin}:{os.environ.get('PATH', '')}",
            "TMPDIR": str(self.root),
        }

    def run(self, checksums_sha256: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER)],
            check=False,
            capture_output=True,
            env=self.environment(checksums_sha256),
            text=True,
        )


class CircleCIInstallerTests(unittest.TestCase):
    def test_installs_verified_linux_amd64_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            checksums_sha256 = fixture.build_release()

            result = fixture.run(checksums_sha256)

            self.assertEqual(result.returncode, 0, result.stderr)
            installed = fixture.install_dir / "circleci"
            self.assertTrue(installed.is_file())
            self.assertTrue(os.access(installed, os.X_OK))
            self.assertEqual(
                subprocess.run(
                    [installed, "version"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                f"{VERSION}+test (release)\n",
            )
            self.assertEqual(
                fixture.curl_log.read_text(encoding="utf-8").splitlines(),
                [fixture.checksums_name, fixture.archive_name],
            )

    def test_installs_verified_linux_arm64_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir), arch="arm64")
            checksums_sha256 = fixture.build_release()

            result = fixture.run(checksums_sha256)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((fixture.install_dir / "circleci").is_file())

    def test_rejects_untrusted_checksum_manifest_before_archive_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            fixture.build_release()

            result = fixture.run("0" * 64)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("manifest did not match", result.stderr)
            self.assertFalse(fixture.install_dir.exists())
            self.assertEqual(
                fixture.curl_log.read_text(encoding="utf-8").splitlines(),
                [fixture.checksums_name],
            )

    def test_rejects_archive_that_does_not_match_verified_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            checksums_sha256 = fixture.build_release()
            fixture.archive_path.write_bytes(fixture.archive_path.read_bytes() + b"tampered")

            result = fixture.run(checksums_sha256)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive did not match", result.stderr)
            self.assertFalse(fixture.install_dir.exists())

    def test_rejects_verified_manifest_without_selected_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            fixture.build_release()
            fixture.checksums_path.write_text(
                f"{'1' * 64}  another-archive.tar.gz\n",
                encoding="utf-8",
            )

            result = fixture.run(sha256(fixture.checksums_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one entry", result.stderr)
            self.assertFalse(fixture.install_dir.exists())
            self.assertEqual(
                fixture.curl_log.read_text(encoding="utf-8").splitlines(),
                [fixture.checksums_name],
            )

    def test_rejects_binary_with_unexpected_reported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            checksums_sha256 = fixture.build_release(reported_version="9.9.9")

            result = fixture.run(checksums_sha256)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"did not report version {VERSION}", result.stderr)
            self.assertFalse(fixture.install_dir.exists())

    def test_rejects_archive_without_expected_binary_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            checksums_sha256 = fixture.build_release(binary_name="not-circleci")

            result = fixture.run(checksums_sha256)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must contain exactly one", result.stderr)
            self.assertFalse(fixture.install_dir.exists())

    def test_rejects_unsupported_architecture_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = CircleCIInstallerFixture(Path(tmpdir))
            checksums_sha256 = fixture.build_release()
            environment = fixture.environment(checksums_sha256)
            environment["FAKE_UNAME_M"] = "riscv64"

            result = subprocess.run(
                ["bash", str(INSTALLER)],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported architecture: riscv64", result.stderr)
            self.assertFalse(fixture.curl_log.exists())
            self.assertFalse(fixture.install_dir.exists())


if __name__ == "__main__":
    unittest.main()
