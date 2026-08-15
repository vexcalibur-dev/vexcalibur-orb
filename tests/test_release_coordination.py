from __future__ import annotations

import unittest


from tests.release_test_support import (  # noqa: E402
    BOT_EMAIL,
    BOT_NAME,
    RepositoryTestCase,
    git,
    working_directory,
)

import release_coordination as coordination  # noqa: E402
from release_common import ReleaseError, TagState, Version  # noqa: E402


class CoordinationValidationTests(RepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.commit = self.initial_commit
        self.tree = git(self.repo, "rev-parse", "HEAD^{tree}")
        self.latest = TagState(Version(0, 1, 0), "b" * 40, self.commit)
        self.reference = "refs/vexcalibur-test/coordination"

    def make_commit(
        self,
        *,
        tree: str | None = None,
        parent: str | None = None,
        message: str | None = None,
    ) -> str:
        arguments = ["commit-tree", tree or self.tree]
        if parent is not None:
            arguments.extend(("-p", parent))
        return git(
            self.repo,
            *arguments,
            input_text=(message or coordination.coordination_message(self.latest))
            + "\n",
        )

    def verify(self, object_id: str) -> None:
        git(self.repo, "update-ref", self.reference, object_id)
        with working_directory(self.repo):
            coordination.verify_coordination_reference(
                self.reference,
                latest=self.latest,
                expected_author_name=BOT_NAME,
                expected_author_email=BOT_EMAIL,
            )

    def test_valid_coordination_commit_is_accepted(self) -> None:
        with working_directory(self.repo):
            object_id = coordination.create_coordination_commit(self.latest)

        self.verify(object_id)

    def test_noncommit_parent_tree_and_metadata_are_rejected(self) -> None:
        empty_tree = git(self.repo, "mktree", input_text="")
        cases = (
            ("point to a commit", self.tree),
            ("conflicting parent", self.make_commit()),
            (
                "conflicting tree",
                self.make_commit(tree=empty_tree, parent=self.commit),
            ),
            (
                "conflicting protected metadata",
                self.make_commit(parent=self.commit, message='{"schema_version":1}'),
            ),
        )

        for expected, object_id in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ReleaseError, expected):
                    self.verify(object_id)

    def test_unexpected_author_and_committer_are_rejected(self) -> None:
        git(self.repo, "config", "user.name", "Unexpected Author")
        git(self.repo, "config", "user.email", "unexpected@example.com")
        object_id = self.make_commit(parent=self.commit)

        with self.assertRaisesRegex(ReleaseError, "automation identity"):
            self.verify(object_id)

    def test_conflicting_remote_coordination_state_is_rejected(self) -> None:
        remote = self.root / "remote.git"
        git(self.repo, "init", "--bare", "--initial-branch=main", str(remote))
        conflicting = self.make_commit(
            parent=self.commit,
            message=(
                '{"commit":"'
                + self.commit
                + '","schema_version":1,"tag":"v0.2.0",'
                + '"tag_object":"'
                + ("c" * 40)
                + '"}'
            ),
        )
        git(
            self.repo,
            "push",
            str(remote),
            f"{conflicting}:{coordination.RELEASE_COORDINATION_REF}",
        )

        with working_directory(self.repo):
            with self.assertRaisesRegex(ReleaseError, "protected metadata"):
                coordination.verify_remote_coordination(
                    remote=str(remote),
                    object_id=conflicting,
                    latest=self.latest,
                    expected_author_name=BOT_NAME,
                    expected_author_email=BOT_EMAIL,
                )


if __name__ == "__main__":
    unittest.main()
