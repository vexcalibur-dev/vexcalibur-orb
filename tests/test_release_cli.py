from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


from tests.release_test_support import (  # noqa: E402
    ROOT,
    RepositoryTestCase,
    git,
    working_directory,
)

import release  # noqa: E402


class ReleaseCliTests(RepositoryTestCase):
    def test_git_failure_includes_the_command_and_diagnostic(self) -> None:
        with working_directory(self.repo):
            with self.assertRaisesRegex(
                release.ReleaseError,
                "git command-that-does-not-exist.*is not a git command",
            ):
                release.git("command-that-does-not-exist")

    def test_git_failure_redacts_credentials_from_command_and_diagnostic(self) -> None:
        username = "visible-user"
        opaque_value = "redaction-test-value"
        credentialed_remote = f"https://{username}:{opaque_value}@example.invalid/repo"
        with working_directory(self.repo):
            with self.assertRaises(release.ReleaseError) as raised:
                release.remote_reference(
                    credentialed_remote,
                    "refs/heads/main",
                )

        message = str(raised.exception)
        self.assertNotIn(username, message)
        self.assertNotIn(opaque_value, message)
        self.assertIn("<redacted>@", message)

    def test_local_reference_distinguishes_absence_from_git_failure(self) -> None:
        with working_directory(self.repo):
            self.assertFalse(release.local_reference_exists("refs/tags/v0.1.0"))
            git(self.repo, "tag", "v0.1.0")
            self.assertTrue(release.local_reference_exists("refs/tags/v0.1.0"))

        outside_repository = Path(self.temporary.name) / "outside"
        outside_repository.mkdir()
        with working_directory(outside_repository):
            with self.assertRaisesRegex(release.ReleaseError, "git show-ref"):
                release.local_reference_exists("refs/tags/v0.1.0")

    def test_release_source_requires_the_requested_current_main_commit(self) -> None:
        commit = "a" * 40
        release.verify_source_commit(
            expected_source=commit,
            workflow_source=commit,
            current_main=commit,
        )

        with self.assertRaisesRegex(release.ReleaseError, "requested release commit"):
            release.verify_source_commit(
                expected_source="b" * 40,
                workflow_source=commit,
                current_main=commit,
            )
        with self.assertRaisesRegex(release.ReleaseError, "current main"):
            release.verify_source_commit(
                expected_source=commit,
                workflow_source=commit,
                current_main="b" * 40,
            )

    def test_plan_subcommand_emits_outputs(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/release.py"),
                "plan",
                "--tag",
                "v0.1.0",
            ],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("operation=publish", result.stdout)
        self.assertIn("tag=v0.1.0", result.stdout)
        outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(len(outputs), 10)
        self.assertEqual(
            set(outputs),
            {
                "operation",
                "tag",
                "previous_tag",
                "bump",
                "sha",
                "notes_format",
                "expected_notes_sha256",
                "expected_tag_graph_sha256",
                "make_latest",
                "reason",
            },
        )


if __name__ == "__main__":
    unittest.main()
