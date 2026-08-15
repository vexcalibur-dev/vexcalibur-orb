from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest


from tests.release_test_support import (  # noqa: E402
    BOT_EMAIL,
    BOT_NAME,
    ROOT,
    RepositoryTestCase,
    commit,
    git,
    working_directory,
)

import release  # noqa: E402


class PublishedReleaseVerificationTests(RepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tag = "v0.1.0"
        self.prepare_release(self.initial_commit)

    def prepare_release(self, release_commit: str) -> None:
        self.release_commit = release_commit
        self.notes = release.release_notes(
            tag=self.tag,
            commit=release_commit,
            previous_tag="",
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        self.message = release.tag_message(
            tag=self.tag,
            commit=release_commit,
            previous_tag="",
            notes_format=release.CURRENT_NOTES_FORMAT,
            notes_sha256=hashlib.sha256(self.notes.encode()).hexdigest(),
        )
        self.release_json = self.root / "release.json"
        self.release_json.write_text(
            json.dumps(
                {
                    "tag_name": self.tag,
                    "target_commitish": release_commit,
                    "name": self.tag,
                    "body": self.notes,
                    "assets": [],
                    "draft": False,
                    "prerelease": False,
                    "immutable": True,
                    "author": {"login": BOT_NAME},
                }
            ),
            encoding="utf-8",
        )

    def create_tag(self, message: str) -> None:
        git(
            self.repo,
            "tag",
            "--annotate",
            self.tag,
            self.release_commit,
            "--message",
            message,
        )

    def create_raw_tag(self, message: bytes) -> None:
        tagger = git(self.repo, "var", "GIT_COMMITTER_IDENT")
        document = (
            "\n".join(
                (
                    f"object {self.release_commit}",
                    "type commit",
                    f"tag {self.tag}",
                    f"tagger {tagger}",
                    "",
                )
            ).encode()
            + b"\n"
            + message
        )
        result = subprocess.run(
            ["git", "mktag"],
            cwd=self.repo,
            check=False,
            capture_output=True,
            input=document,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        tag_object = result.stdout.decode().strip()
        git(self.repo, "update-ref", f"refs/tags/{self.tag}", tag_object)

    def verify(self, *, main_commit: str | None = None) -> None:
        with working_directory(self.repo):
            release.verify_published_release(
                release_json=self.release_json,
                tag=self.tag,
                commit=self.release_commit,
                main_commit=main_commit or self.release_commit,
                expected_author=BOT_NAME,
                expected_tagger_name=BOT_NAME,
                expected_tagger_email=BOT_EMAIL,
            )

    def test_canonical_automation_tag_and_release_are_accepted(self) -> None:
        self.create_tag(self.message)

        self.verify()

    def test_published_release_cli_verifies_the_real_tag(self) -> None:
        self.create_tag(self.message)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/release.py"),
                "verify-published-release",
                "--release-json",
                str(self.release_json),
                "--tag",
                self.tag,
                "--commit",
                self.release_commit,
                "--main-commit",
                self.release_commit,
                "--expected-author",
                BOT_NAME,
                "--expected-tagger-name",
                BOT_NAME,
                "--expected-tagger-email",
                BOT_EMAIL,
            ],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"verified published GitHub Release {self.tag}", result.stdout)

    def test_unexpected_tagger_is_rejected(self) -> None:
        git(self.repo, "config", "user.name", "Unexpected Tagger")
        git(self.repo, "config", "user.email", "unexpected@example.com")
        self.create_tag(self.message)

        with self.assertRaisesRegex(release.ReleaseError, "automation identity"):
            self.verify()

    def test_reformatted_tag_metadata_is_rejected(self) -> None:
        reformatted = json.dumps(json.loads(self.message), indent=2, sort_keys=True)
        self.create_tag(reformatted)

        with self.assertRaisesRegex(release.ReleaseError, "canonical metadata"):
            self.verify()

    def test_crlf_tag_annotation_is_rejected(self) -> None:
        self.create_raw_tag(self.message.encode() + b"\r\n")

        with self.assertRaisesRegex(release.ReleaseError, "canonical metadata"):
            self.verify()

    def test_nonexistent_predecessor_is_rejected(self) -> None:
        previous_tag = "v0.0.1"
        notes = release.release_notes(
            tag=self.tag,
            commit=self.release_commit,
            previous_tag=previous_tag,
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        message = release.tag_message(
            tag=self.tag,
            commit=self.release_commit,
            previous_tag=previous_tag,
            notes_format=release.CURRENT_NOTES_FORMAT,
            notes_sha256=hashlib.sha256(notes.encode()).hexdigest(),
        )
        self.create_tag(message)

        with self.assertRaisesRegex(release.ReleaseError, "canonical metadata"):
            self.verify()

    def test_reference_name_must_match_the_embedded_tag(self) -> None:
        other_tag = "v0.2.0"
        other_notes = release.release_notes(
            tag=other_tag,
            commit=self.release_commit,
            previous_tag="",
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        other_message = release.tag_message(
            tag=other_tag,
            commit=self.release_commit,
            previous_tag="",
            notes_format=release.CURRENT_NOTES_FORMAT,
            notes_sha256=hashlib.sha256(other_notes.encode()).hexdigest(),
        )
        git(
            self.repo,
            "tag",
            "--annotate",
            other_tag,
            self.release_commit,
            "--message",
            other_message,
        )
        tag_object = git(self.repo, "rev-parse", f"refs/tags/{other_tag}")
        git(self.repo, "update-ref", f"refs/tags/{self.tag}", tag_object)
        git(self.repo, "update-ref", "-d", f"refs/tags/{other_tag}")

        with self.assertRaisesRegex(release.ReleaseError, "named commit"):
            self.verify()

    def test_tag_must_be_reachable_from_expected_main(self) -> None:
        feature_commit = commit(self.repo, "feat: off-main release", "feature.txt")
        self.prepare_release(feature_commit)
        self.create_tag(self.message)

        with self.assertRaisesRegex(release.ReleaseError, "not reachable"):
            self.verify(main_commit=self.initial_commit)

    def test_stale_head_does_not_reject_tag_on_expected_main(self) -> None:
        release_commit = commit(self.repo, "feat: release", "release.txt")
        self.prepare_release(release_commit)
        self.create_tag(self.message)
        git(self.repo, "checkout", "--detach", self.initial_commit)

        self.verify(main_commit=release_commit)


if __name__ == "__main__":
    unittest.main()
