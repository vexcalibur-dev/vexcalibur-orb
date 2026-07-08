from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "src" / "scripts" / "run-vexcalibur.sh"
BASH = shutil.which("bash")

if BASH is None:
    raise RuntimeError("bash is required to test the orb runner")


def base_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "")}
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    return env


def write_fake_python(path: Path, log_path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail

            printf '%s\\n' "$*" >> "{log_path}"
            if [[ "$*" == "-I -m venv "* ]]; then
              venv_dir="${{@: -1}}"
              mkdir -p "$venv_dir/bin"
              cat > "$venv_dir/bin/python" <<'PY'
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$*" >> "{log_path}"
            if [[ "$*" == "-I -m pip --isolated --no-cache-dir install "* ]]; then
              venv_dir="$(cd "$(dirname "$0")/.." && pwd)"
              cat > "$venv_dir/bin/vexcalibur" <<'VEX'
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$@" > "$VEXCALIBUR_FAKE_ARGS_LOG"
            VEX
              chmod +x "$venv_dir/bin/vexcalibur"
              exit 0
            fi
            exit 1
            PY
              chmod +x "$venv_dir/bin/python"
              exit 0
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


class RunVexcaliburScriptTests(unittest.TestCase):
    def test_rejects_missing_package_spec(self) -> None:
        env = base_env()

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("package_spec is required", result.stderr)

    def test_rejects_non_exact_release_by_default(self) -> None:
        env = base_env()
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "vexcalibur>=0.1.1"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("exact Vexcalibur release", result.stderr)

    def test_rejects_prerelease_by_default(self) -> None:
        env = base_env()
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "vexcalibur==0.1.1.dev1"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("exact Vexcalibur release", result.stderr)

    def test_rejects_pip_option_package_spec(self) -> None:
        env = base_env()
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "--find-links=/tmp/wheels"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("package_spec must not start with a pip option", result.stderr)

    def test_rejects_pip_option_without_echoing_marker(self) -> None:
        env = base_env()
        marker = "not-a-real-token"
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "--extra-index-url=https://user:" + marker + "@example.com/simple"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("package_spec must not start with a pip option", result.stderr)
        self.assertNotIn(marker, result.stderr)

    def test_rejects_credentialed_package_spec(self) -> None:
        env = base_env()
        env["VEXCALIBUR_ORB_ALLOW_DEVELOPMENT_PACKAGE_SPEC"] = "true"
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "https://user" + ":token@example.com/vexcalibur.tar.gz"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("package_spec must not contain embedded credentials", result.stderr)

    def test_rejects_missing_python_command(self) -> None:
        env = base_env()
        env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "vexcalibur==0.1.1"
        env["VEXCALIBUR_ORB_PYTHON"] = "vexcalibur-missing-python"

        result = run_script(env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("VEXCALIBUR_ORB_PYTHON is not executable", result.stderr)

    def test_rejects_non_executable_python_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            python_path = Path(tmpdir) / "python3"
            python_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            env = base_env()
            env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "vexcalibur==0.1.1"
            env["VEXCALIBUR_ORB_PYTHON"] = str(python_path)

            result = run_script(env)

            self.assertEqual(result.returncode, 2)
            self.assertIn("VEXCALIBUR_ORB_PYTHON is not executable", result.stderr)

    def test_runs_newline_split_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_python = root / "python3"
            python_log = root / "python.log"
            args_log = root / "args.log"
            write_fake_python(fake_python, python_log)

            env = base_env()
            env.update(
                {
                    "TMPDIR": str(root),
                    "VEXCALIBUR_FAKE_ARGS_LOG": str(args_log),
                    "VEXCALIBUR_ORB_PYTHON": str(fake_python),
                    "VEXCALIBUR_ORB_PACKAGE_SPEC": "vexcalibur==0.1.1",
                    "VEXCALIBUR_ORB_ARGS": "query-osv\n--\npkg:pypi/django@1.2\n",
                }
            )

            result = run_script(env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(args_log.read_text(encoding="utf-8"), "query-osv\n--\npkg:pypi/django@1.2\n")
            self.assertIn("-I -m venv", python_log.read_text(encoding="utf-8"))

    def test_constraints_file_is_passed_to_pip_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_python = root / "python3"
            python_log = root / "python.log"
            args_log = root / "args.log"
            constraints_file = root / "constraints.txt"
            constraints_file.write_text("httpx==0.28.1\n", encoding="utf-8")
            write_fake_python(fake_python, python_log)

            env = base_env()
            env.update(
                {
                    "TMPDIR": str(root),
                    "VEXCALIBUR_FAKE_ARGS_LOG": str(args_log),
                    "VEXCALIBUR_ORB_CONSTRAINTS_FILE": str(constraints_file),
                    "VEXCALIBUR_ORB_PYTHON": str(fake_python),
                    "VEXCALIBUR_ORB_PACKAGE_SPEC": "vexcalibur==0.1.1",
                    "VEXCALIBUR_ORB_ARGS": "--help",
                }
            )

            result = run_script(env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                f"-I -m pip --isolated --no-cache-dir install --constraint {constraints_file} vexcalibur==0.1.1",
                python_log.read_text(encoding="utf-8"),
            )

    def test_missing_constraints_file_fails_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_python = root / "python3"
            python_log = root / "python.log"
            write_fake_python(fake_python, python_log)

            env = base_env()
            env.update(
                {
                    "TMPDIR": str(root),
                    "VEXCALIBUR_ORB_CONSTRAINTS_FILE": str(root / "missing.txt"),
                    "VEXCALIBUR_ORB_PYTHON": str(fake_python),
                    "VEXCALIBUR_ORB_PACKAGE_SPEC": "vexcalibur==0.1.1",
                }
            )

            result = run_script(env)

            self.assertEqual(result.returncode, 2)
            self.assertIn("constraints_file does not exist or is not readable", result.stderr)
            self.assertFalse(python_log.exists())

    def test_relative_constraints_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            constraints_file = root / "constraints.txt"
            constraints_file.write_text("httpx==0.28.1\n", encoding="utf-8")

            env = base_env()
            env["VEXCALIBUR_ORB_CONSTRAINTS_FILE"] = "constraints.txt"
            env["VEXCALIBUR_ORB_PACKAGE_SPEC"] = "vexcalibur==0.1.1"

            result = run_script(env)

            self.assertEqual(result.returncode, 2)
            self.assertIn("constraints_file must be an absolute path", result.stderr)


if __name__ == "__main__":
    unittest.main()
