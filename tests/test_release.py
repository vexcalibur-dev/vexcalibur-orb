from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


from tests.release_test_support import (  # noqa: E402
    BOT_EMAIL,
    BOT_NAME,
    ROOT,
    RepositoryTestCase,
    commit,
    configure_git,
    git,
    working_directory,
)

import release  # noqa: E402
import release_policy  # noqa: E402


FORMAT_ONE_NOTES_SHA256 = (
    "68c03b1f669fcf8fef25b0630898fc29"  # pragma: allowlist secret
    "5e98d6caec9d45ef2cf364a98ffc0bab"  # pragma: allowlist secret
)
FORMAT_TWO_NOTES_SHA256 = (
    "3c570e327839b1a32113d48763393f0a"  # pragma: allowlist secret
    "c00e47dd2253499d2847beb6d450ca23"  # pragma: allowlist secret
)


def annotate(
    repo: Path,
    tag: str,
    commit_sha: str,
    message: str | None = None,
    previous_tag: str = "",
) -> None:
    if message is None:
        notes = release.release_notes(
            tag=tag,
            commit=commit_sha,
            previous_tag=previous_tag,
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        message = release.tag_message(
            tag=tag,
            commit=commit_sha,
            previous_tag=previous_tag,
            notes_format=release.CURRENT_NOTES_FORMAT,
            notes_sha256=hashlib.sha256(notes.encode()).hexdigest(),
        )
    git(repo, "tag", "--annotate", tag, commit_sha, "--message", message)


class GitRepositoryTest(RepositoryTestCase):
    def plan(self, tag: str = "") -> release.ReleasePlan:
        with working_directory(self.repo):
            return release.plan_release(tag)


class ReleasePlanningTests(GitRepositoryTest):
    def test_first_release_requires_explicit_dispatch_tag(self) -> None:
        skipped = self.plan()
        explicit = self.plan("v0.1.0")

        self.assertEqual(skipped.operation, "skip")
        self.assertIn("explicit dispatch", skipped.reason)
        self.assertEqual(explicit.operation, "publish")
        self.assertEqual(explicit.tag, "v0.1.0")
        self.assertEqual(explicit.bump, "initial")
        self.assertEqual(explicit.commit, self.initial_commit)

    def test_conventional_commits_select_highest_bump(self) -> None:
        annotate(self.repo, "v1.2.3", self.initial_commit)
        commit(self.repo, "fix: patch behavior", "patch.txt")
        self.assertEqual(self.plan().tag, "v1.2.4")
        commit(self.repo, "feat: add behavior", "feature.txt")
        self.assertEqual(self.plan().tag, "v1.3.0")
        commit(
            self.repo,
            "docs: explain migration\n\nBREAKING CHANGE: replace old input",
            "breaking.txt",
        )
        plan = self.plan()
        self.assertEqual(plan.tag, "v2.0.0")
        self.assertEqual(plan.bump, "major")

    def test_breaking_marker_must_be_a_subject_marker_or_footer(self) -> None:
        body_marker = "docs: explain syntax\nBREAKING CHANGE: illustrative text"
        footer_marker = "docs: explain migration\n\nBREAKING CHANGE: replace input"

        self.assertEqual(release.classify_commit(body_marker), "skip")
        self.assertEqual(release.classify_commit(footer_marker), "major")
        self.assertEqual(release.classify_commit("feat!: replace input"), "major")

    def test_non_release_and_suppressed_commits_skip(self) -> None:
        annotate(self.repo, "v1.0.0", self.initial_commit)
        commit(self.repo, "docs: clarify examples", "docs.txt")
        self.assertEqual(self.plan().operation, "skip")
        commit(self.repo, "fix: internal release tooling [skip release]", "tool.txt")
        plan = self.plan()
        self.assertEqual(plan.operation, "skip")
        self.assertIn("suppression", plan.reason)

    def test_orb_publisher_migration_never_accumulates_a_release_bump(self) -> None:
        annotate(self.repo, "v0.1.1", self.initial_commit)
        commit(
            self.repo,
            "ci: publish Orbs from GitHub Actions [skip release]",
            "publisher.txt",
        )
        self.assertEqual(self.plan().operation, "skip")

        commit(self.repo, "docs: record migration state", "status.txt")
        plan = self.plan()
        self.assertEqual(plan.operation, "skip")
        self.assertIn("no releasable", plan.reason)

    def test_existing_tag_enters_recovery(self) -> None:
        annotate(self.repo, "v0.1.0", self.initial_commit)

        current = self.plan()
        explicit = self.plan("v0.1.0")

        self.assertEqual(current.operation, "recover")
        self.assertEqual(explicit.operation, "recover")
        self.assertEqual(explicit.commit, self.initial_commit)

    def test_manual_new_tag_is_rejected_after_first_release(self) -> None:
        annotate(self.repo, "v0.1.0", self.initial_commit)
        commit(self.repo, "fix: behavior", "fix.txt")

        with self.assertRaisesRegex(release.ReleaseError, "only for the first"):
            self.plan("v0.2.0")

    def test_malformed_lightweight_and_duplicate_tags_are_rejected(self) -> None:
        for tag in ("v01.0.0", "v1.0"):
            with self.subTest(tag=tag):
                git(self.repo, "tag", tag, self.initial_commit)
                with self.assertRaises(release.ReleaseError):
                    self.plan()
                git(self.repo, "tag", "--delete", tag)

        git(self.repo, "tag", "v1.0.0", self.initial_commit)
        with self.assertRaisesRegex(release.ReleaseError, "annotated"):
            self.plan()
        git(self.repo, "tag", "--delete", "v1.0.0")

        annotate(self.repo, "v1.0.0", self.initial_commit)
        annotate(self.repo, "v1.0.1", self.initial_commit)
        with self.assertRaisesRegex(release.ReleaseError, "same commit"):
            self.plan()

    def test_nested_v_tag_outside_the_protected_namespace_is_ignored(self) -> None:
        git(self.repo, "tag", "v/unprotected", self.initial_commit)

        self.assertEqual(self.plan("v0.1.0").tag, "v0.1.0")

    def test_forward_release_rejects_malformed_predecessor_metadata(self) -> None:
        annotate(
            self.repo,
            "v1.0.0",
            self.initial_commit,
            message="not canonical release metadata",
        )
        commit(self.repo, "fix: candidate", "candidate.txt")

        with self.assertRaisesRegex(release.ReleaseError, "annotation is not valid"):
            self.plan()

    def test_predecessor_metadata_contract_rejects_field_drift(self) -> None:
        notes_sha256 = "a" * 64
        canonical = json.loads(
            release.tag_message(
                tag="v1.0.0",
                commit=self.initial_commit,
                previous_tag="",
                notes_format="1",
                notes_sha256=notes_sha256,
            )
        )
        cases = (
            ("missing field", lambda value: value.pop("notes_sha256")),
            ("extra field", lambda value: value.update({"extra": True})),
            ("schema", lambda value: value.update({"schema_version": 2})),
            ("format", lambda value: value.update({"notes_format": "3"})),
            ("digest", lambda value: value.update({"notes_sha256": "bad"})),
            ("predecessor", lambda value: value.update({"previous_tag": "v0.9.0"})),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                document = deepcopy(canonical)
                mutate(document)
                annotate(
                    self.repo,
                    "v1.0.0",
                    self.initial_commit,
                    message=json.dumps(document, sort_keys=True, separators=(",", ":")),
                )
                with self.assertRaises(release.ReleaseError):
                    self.plan()
                git(self.repo, "tag", "--delete", "v1.0.0")


class RemoteTagTests(GitRepositoryTest):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.root / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(self.remote)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "--set-upstream", "origin", "main")
        self.notes = release.release_notes(
            tag="v0.1.0",
            commit=self.initial_commit,
            previous_tag="",
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        self.notes_sha256 = hashlib.sha256(self.notes.encode()).hexdigest()
        self.empty_graph = release.tag_graph_sha256(())

    def reconcile(
        self,
        repo: Path,
        *,
        tag: str = "v0.1.0",
        commit_sha: str | None = None,
        previous_tag: str = "",
        notes_sha256: str | None = None,
        graph_sha256: str | None = None,
        expected_main_commit: str | None = None,
    ) -> str:
        with working_directory(repo):
            return release.reconcile_tag(
                remote="origin",
                expected_main_commit=(
                    expected_main_commit or git(repo, "rev-parse", "origin/main")
                ),
                expected_tag_graph_sha256=graph_sha256 or self.empty_graph,
                tag=tag,
                commit=commit_sha or self.initial_commit,
                previous_tag=previous_tag,
                notes_format=release.CURRENT_NOTES_FORMAT,
                notes_sha256=notes_sha256 or self.notes_sha256,
                expected_tagger_name=BOT_NAME,
                expected_tagger_email=BOT_EMAIL,
            )

    def clone(self, name: str) -> Path:
        destination = self.root / name
        subprocess.run(
            ["git", "clone", str(self.remote), str(destination)],
            check=True,
            capture_output=True,
            text=True,
        )
        configure_git(destination)
        return destination

    def test_create_and_recover_round_trip_without_local_release_tag(self) -> None:
        self.assertEqual(self.reconcile(self.repo), "created")
        self.assertEqual(git(self.repo, "tag", "--list", "v0.1.0"), "")
        with working_directory(self.repo):
            coordination = release.remote_reference(
                "origin", release.RELEASE_COORDINATION_REF
            )
            tags = release.remote_release_tags("origin")
            release.verify_remote_coordination(
                remote="origin",
                object_id=coordination,
                latest=tags[-1],
                expected_author_name=BOT_NAME,
                expected_author_email=BOT_EMAIL,
            )
        self.assertNotEqual(coordination, self.initial_commit)

        recovery = self.clone("recovery")
        with working_directory(recovery):
            plan = release.plan_release("v0.1.0")
        self.assertEqual(plan.operation, "recover")
        self.assertEqual(plan.notes_format, release.CURRENT_NOTES_FORMAT)
        self.assertEqual(plan.expected_notes_sha256, self.notes_sha256)
        self.assertEqual(
            self.reconcile(recovery, graph_sha256=plan.expected_tag_graph_sha256),
            "verified",
        )

    def test_successful_later_release_and_recovery_preserve_predecessor(self) -> None:
        self.reconcile(self.repo)
        with working_directory(self.repo):
            first_graph = release.tag_graph_sha256(
                release.remote_release_tags("origin")
            )
        candidate = commit(self.repo, "fix: second release", "second.txt")
        git(self.repo, "push", "origin", "main")
        notes = release.release_notes(
            tag="v0.1.1",
            commit=candidate,
            previous_tag="v0.1.0",
            notes_format=release.CURRENT_NOTES_FORMAT,
        )
        self.assertIn("/compare/v0.1.0...v0.1.1", notes)
        notes_sha256 = hashlib.sha256(notes.encode()).hexdigest()

        self.assertEqual(
            self.reconcile(
                self.repo,
                tag="v0.1.1",
                commit_sha=candidate,
                previous_tag="v0.1.0",
                notes_sha256=notes_sha256,
                graph_sha256=first_graph,
            ),
            "created",
        )

        recovery = self.clone("later-recovery")
        with working_directory(recovery):
            plan = release.plan_release("v0.1.1")
        self.assertEqual(plan.operation, "recover")
        self.assertEqual(plan.previous_tag, "v0.1.0")
        self.assertEqual(plan.expected_notes_sha256, notes_sha256)
        self.assertEqual(
            self.reconcile(recovery, graph_sha256=plan.expected_tag_graph_sha256),
            "verified",
        )

    def test_coordination_lease_rejects_two_new_tags_for_one_commit(self) -> None:
        def stage(tag: str, reference_prefix: str) -> tuple[str, str]:
            message = release.tag_message(
                tag=tag,
                commit=self.initial_commit,
                previous_tag="",
                notes_format=release.CURRENT_NOTES_FORMAT,
                notes_sha256="a" * 64,
            )
            tagger = git(self.repo, "var", "GIT_COMMITTER_IDENT")
            document = "\n".join(
                (
                    f"object {self.initial_commit}",
                    "type commit",
                    f"tag {tag}",
                    f"tagger {tagger}",
                    "",
                    message,
                    "",
                )
            )
            tag_object = git(self.repo, "mktag", input_text=document)
            tag_ref = f"refs/vexcalibur-test/{reference_prefix}/tag"
            coordination_ref = f"refs/vexcalibur-test/{reference_prefix}/coordination"
            git(self.repo, "update-ref", tag_ref, tag_object)
            with working_directory(self.repo):
                coordination = release.create_coordination_commit(
                    release.TagState(
                        release.Version.from_tag(tag),
                        tag_object,
                        self.initial_commit,
                    )
                )
            git(self.repo, "update-ref", coordination_ref, coordination)
            return tag_ref, coordination_ref

        first_tag_ref, first_coordination_ref = stage("v0.1.0", "first")
        second_tag_ref, second_coordination_ref = stage("v0.2.0", "second")
        with working_directory(self.repo):
            first = release.atomic_publish_release(
                remote="origin",
                tag_source=first_tag_ref,
                tag_destination="refs/tags/v0.1.0",
                coordination_source=first_coordination_ref,
                coordination_before="",
            )
            second = release.atomic_publish_release(
                remote="origin",
                tag_source=second_tag_ref,
                tag_destination="refs/tags/v0.2.0",
                coordination_source=second_coordination_ref,
                coordination_before="",
            )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/tags/v0.2.0"), "")

    def test_existing_tag_with_conflicting_metadata_is_rejected(self) -> None:
        self.reconcile(self.repo)
        recovery = self.clone("conflict")
        with working_directory(recovery):
            graph = release.tag_graph_sha256(release.release_tags())

        with self.assertRaisesRegex(release.ReleaseError, "expected release metadata"):
            self.reconcile(
                recovery,
                notes_sha256="f" * 64,
                graph_sha256=graph,
            )

    def test_stale_graph_is_rejected_before_a_second_tag_is_published(self) -> None:
        self.reconcile(self.repo)
        candidate = commit(self.repo, "feat: second release", "second.txt")
        git(self.repo, "push", "origin", "main")

        with self.assertRaisesRegex(release.ReleaseError, "graph changed"):
            self.reconcile(
                self.repo,
                tag="v0.2.0",
                commit_sha=candidate,
                previous_tag="v0.1.0",
                notes_sha256="a" * 64,
                graph_sha256=self.empty_graph,
            )
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/tags/v0.2.0"), "")

    def test_predecessor_must_match_the_observed_remote_graph(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "predecessor"):
            self.reconcile(self.repo, previous_tag="v0.0.1")
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/tags/v0.1.0"), "")

    def test_recovery_restores_a_missing_release_coordination_branch(self) -> None:
        self.reconcile(self.repo)
        git(self.repo, "push", "origin", "--delete", "release-coordination")
        recovery = self.clone("missing-coordination")
        with working_directory(recovery):
            graph = release.tag_graph_sha256(release.release_tags())

        self.assertEqual(self.reconcile(recovery, graph_sha256=graph), "verified")
        with working_directory(recovery):
            coordination = release.remote_reference(
                "origin", release.RELEASE_COORDINATION_REF
            )
            tags = release.remote_release_tags("origin")
            release.verify_remote_coordination(
                remote="origin",
                object_id=coordination,
                latest=tags[-1],
                expected_author_name=BOT_NAME,
                expected_author_email=BOT_EMAIL,
            )

    def test_new_tag_must_target_checked_out_commit(self) -> None:
        candidate = commit(self.repo, "fix: candidate", "candidate.txt")
        git(self.repo, "checkout", self.initial_commit)

        with self.assertRaisesRegex(release.ReleaseError, "checked-out candidate"):
            self.reconcile(
                self.repo,
                commit_sha=candidate,
            )

    def test_remote_main_change_rejects_the_atomic_publication(self) -> None:
        other = self.clone("main-updater")
        newer = commit(other, "docs: advance main", "newer.txt")
        git(other, "push", "origin", "main")

        with self.assertRaisesRegex(release.ReleaseError, "remote main changed"):
            self.reconcile(
                self.repo,
                expected_main_commit=self.initial_commit,
            )
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/tags/v0.1.0"), "")
        self.assertEqual(
            git(self.repo, "ls-remote", "origin", "refs/heads/main"),
            f"{newer}\trefs/heads/main",
        )

    def test_remote_lightweight_release_tag_is_rejected(self) -> None:
        git(self.repo, "tag", "v0.1.0", self.initial_commit)
        git(self.repo, "push", "origin", "refs/tags/v0.1.0")

        with working_directory(self.repo):
            with self.assertRaisesRegex(release.ReleaseError, "not annotated"):
                release.remote_release_tags("origin")


class ReleaseProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.notes = self.root / "notes.md"
        self.notes.write_text(
            release.release_notes(
                tag="v0.1.0",
                commit=self.commit,
                previous_tag="",
                notes_format=release.CURRENT_NOTES_FORMAT,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_format_one_release_notes_match_the_golden_contract(self) -> None:
        expected = (ROOT / "tests/fixtures/release-notes-format-1.md").read_text(
            encoding="utf-8"
        )
        rendered = release.release_notes(
            tag="v0.1.0",
            commit="a" * 40,
            previous_tag="",
            notes_format="1",
        )

        self.assertEqual(rendered, expected)
        self.assertEqual(
            hashlib.sha256(rendered.encode()).hexdigest(),
            FORMAT_ONE_NOTES_SHA256,
        )

    def test_format_two_release_notes_match_the_golden_contract(self) -> None:
        expected = (ROOT / "tests/fixtures/release-notes-format-2.md").read_text(
            encoding="utf-8"
        )
        rendered = release.release_notes(
            tag="v0.1.0",
            commit="a" * 40,
            previous_tag="",
            notes_format="2",
        )

        self.assertEqual(rendered, expected)
        self.assertEqual(
            hashlib.sha256(rendered.encode()).hexdigest(),
            FORMAT_TWO_NOTES_SHA256,
        )

    def test_every_supported_note_format_has_a_retained_renderer(self) -> None:
        self.assertEqual(
            set(release.NOTES_RENDERERS),
            set(release.SUPPORTED_NOTES_FORMATS),
        )
        self.assertIn(release.CURRENT_NOTES_FORMAT, release.NOTES_RENDERERS)

    def release_document(self) -> dict[str, object]:
        return {
            "tag_name": "v0.1.0",
            "target_commitish": self.commit,
            "name": "v0.1.0",
            "body": self.notes.read_text(encoding="utf-8"),
            "assets": [],
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "author": {"login": BOT_NAME},
        }

    def test_exact_immutable_release_is_accepted(self) -> None:
        path = self.root / "release.json"
        path.write_text(json.dumps(self.release_document()), encoding="utf-8")

        release.verify_release(
            release_json=path,
            notes_file=self.notes,
            tag="v0.1.0",
            commit=self.commit,
            expected_author=BOT_NAME,
        )

    def test_mutable_or_conflicting_release_is_rejected(self) -> None:
        for field, value in (
            ("immutable", False),
            ("immutable", 1),
            ("draft", 0),
            ("name", "other"),
            ("assets", [{}]),
        ):
            with self.subTest(field=field):
                document = self.release_document()
                document[field] = value
                path = self.root / f"{field}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(release.ReleaseError):
                    release.verify_release(
                        release_json=path,
                        notes_file=self.notes,
                        tag="v0.1.0",
                        commit=self.commit,
                        expected_author=BOT_NAME,
                    )

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "duplicate key"):
            release.strict_json(b'{"tag":"v0.1.0","tag":"v0.2.0"}', source="test")

    def test_malformed_json_is_distinct_from_invalid_utf8(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "not valid JSON"):
            release.strict_json(b"not JSON", source="test")

    def test_nonstandard_json_constants_are_rejected(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "unsupported JSON constant"):
            release.strict_json(b'{"value":NaN}', source="test")

    def test_non_utf8_json_is_rejected(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "not valid UTF-8"):
            release.strict_json('{"value":1}'.encode("utf-16"), source="test")

    def test_git_object_ids_have_an_exact_supported_length(self) -> None:
        self.assertEqual(release.require_sha("a" * 40, label="test"), "a" * 40)
        self.assertEqual(release.require_sha("a" * 64, label="test"), "a" * 64)
        for length in (39, 41, 63, 65):
            with self.subTest(length=length):
                with self.assertRaisesRegex(release.ReleaseError, "object ID"):
                    release.require_sha("a" * length, label="test")


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        base = {
            "id": 1,
            "target": "tag",
            "enforcement": "active",
            "source_type": "Repository",
            "source": release.REPOSITORY,
            "updated_at": "2026-08-15T12:00:00Z",
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        }
        self.immutable = {
            **base,
            "name": release_policy.IMMUTABLE_RULESET_NAME,
            "rules": [{"type": "update"}, {"type": "deletion"}],
        }
        self.creation = {
            **base,
            "id": 2,
            "name": release_policy.CREATION_RULESET_NAME,
            "rules": [{"type": "creation"}],
        }
        actor = {
            "actor_id": 4_250_150,
            "actor_type": "Integration",
            "bypass_mode": "always",
        }
        self.attestation = {
            "schema": 1,
            "repository": release.REPOSITORY,
            "app_id": 4_250_150,
            "immutable": {
                key: value
                for key, value in {**self.immutable, "bypass_actors": []}.items()
                if key
                in {
                    "id",
                    "name",
                    "source_type",
                    "source",
                    "updated_at",
                    "bypass_actors",
                }
            },
            "creation": {
                key: value
                for key, value in {**self.creation, "bypass_actors": [actor]}.items()
                if key
                in {
                    "id",
                    "name",
                    "source_type",
                    "source",
                    "updated_at",
                    "bypass_actors",
                }
            },
        }

    def verify(self) -> None:
        release.verify_attested_rulesets(
            immutable=self.immutable,
            creation=self.creation,
            attestation=self.attestation,
            repository=release.REPOSITORY,
            app_id=4_250_150,
        )

    def test_exact_owner_attested_policy_is_accepted(self) -> None:
        self.verify()

    def test_immutable_release_settings_require_json_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"enabled": True, "enforced_by_owner": True}),
                encoding="utf-8",
            )
            release.verify_immutable_release_settings(path)

            for field in ("enabled", "enforced_by_owner"):
                for value in (False, "true", 1, None):
                    with self.subTest(field=field, value=value):
                        document = {"enabled": True, "enforced_by_owner": True}
                        document[field] = value
                        path.write_text(json.dumps(document), encoding="utf-8")
                        with self.assertRaises(release.ReleaseError):
                            release.verify_immutable_release_settings(path)

    def test_boolean_policy_identity_values_are_rejected(self) -> None:
        for field in ("schema", "app_id"):
            with self.subTest(field=field):
                original = self.attestation[field]
                self.attestation[field] = True
                with self.assertRaises(release.ReleaseError):
                    self.verify()
                self.attestation[field] = original

    def test_ruleset_scope_revision_and_bypass_drift_are_rejected(self) -> None:
        cases = (
            ("scope", self.immutable, ("conditions", "ref_name", "include"), ["~ALL"]),
            (
                "revision",
                self.attestation,
                ("immutable", "updated_at"),
                "2026-08-15T12:00:01Z",
            ),
            ("bypass", self.attestation, ("immutable", "bypass_actors"), [{}]),
        )
        for label, document, path, value in cases:
            with self.subTest(label=label):
                original = deepcopy(document)
                target = document
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(release.ReleaseError):
                    self.verify()
                document.clear()
                document.update(original)

    def test_unhashable_rule_type_is_reported_as_release_error(self) -> None:
        self.immutable["rules"] = [{"type": {}}]
        with self.assertRaisesRegex(release.ReleaseError, "malformed rule type"):
            self.verify()


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.load(
            (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        cls.jobs = cls.workflow["jobs"]

    def step(self, job: str, name: str) -> dict[str, object]:
        return next(
            step for step in self.jobs[job]["steps"] if step.get("name") == name
        )

    def test_release_triggers_are_main_push_and_manual_dispatch(self) -> None:
        self.assertEqual(
            self.workflow["on"],
            {
                "push": {"branches": ["main"]},
                "workflow_dispatch": {
                    "inputs": {
                        "expected_source_sha": {
                            "description": (
                                "Exact main commit expected to run the release tooling."
                            ),
                            "required": "true",
                            "type": "string",
                        },
                        "tag": {
                            "description": (
                                "First vMAJOR.MINOR.PATCH tag, or an existing tag "
                                "to recover."
                            ),
                            "required": "false",
                            "type": "string",
                        },
                    }
                },
            },
        )
        self.assertEqual(
            self.workflow["concurrency"],
            {"group": "release-main", "cancel-in-progress": "false"},
        )

    def test_normal_workflow_token_is_read_only(self) -> None:
        self.assertEqual(
            self.workflow["permissions"],
            {"actions": "read", "contents": "read"},
        )
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("permissions", job)

    def test_workflow_reruns_cannot_reach_release_jobs(self) -> None:
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertIn("github.run_attempt == 1", job["if"])

        wait = self.step("wait-for-ci", "Wait for tooling and release CI")["run"]
        self.assertIn("--json attempt,conclusion,databaseId,event,headBranch", wait)
        self.assertIn('run["attempt"] == 1', wait)
        self.assertIn('run["event"] == "push"', wait)
        self.assertIn('run["headBranch"] == "main"', wait)

        decision_code_match = re.search(
            r"python -c '\n(?P<code>.*?)\n\s*' <<< \"\$\{run_json\}\"",
            wait,
            re.DOTALL,
        )
        self.assertIsNotNone(decision_code_match)
        decision_code = decision_code_match.group("code")
        cases = (
            (
                "nonqualifying",
                [{"attempt": 2, "event": "push", "headBranch": "main"}],
                "waiting",
            ),
            (
                "in-progress",
                [
                    {
                        "attempt": 1,
                        "event": "push",
                        "headBranch": "main",
                        "status": "in_progress",
                        "conclusion": "",
                    }
                ],
                "waiting",
            ),
            (
                "failed",
                [
                    {
                        "attempt": 1,
                        "event": "push",
                        "headBranch": "main",
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ],
                "failure",
            ),
            (
                "successful",
                [
                    {
                        "attempt": 1,
                        "event": "push",
                        "headBranch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
                "success",
            ),
        )
        for label, runs, expected in cases:
            with self.subTest(label=label):
                result = subprocess.run(
                    [sys.executable, "-c", decision_code],
                    input=json.dumps(runs),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(result.stdout.strip(), expected)

    def test_all_actions_are_pinned_to_full_commits(self) -> None:
        pattern = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
        references = [
            step["uses"]
            for job in self.jobs.values()
            for step in job["steps"]
            if "uses" in step
        ]
        self.assertGreaterEqual(len(references), 5)
        for reference in references:
            self.assertRegex(reference, pattern)

    def test_app_token_is_repository_scoped_and_created_after_validation(self) -> None:
        publisher = self.jobs["publish-release"]
        names = [step["name"] for step in publisher["steps"]]
        token = self.step("publish-release", "Generate publication token")

        self.assertEqual(token["with"]["owner"], "${{ github.repository_owner }}")
        self.assertEqual(
            token["with"]["repositories"],
            "${{ github.event.repository.name }}",
        )
        self.assertEqual(token["with"]["permission-administration"], "read")
        self.assertEqual(token["with"]["permission-contents"], "write")
        self.assertLess(
            names.index("Revalidate release candidate"),
            names.index("Generate publication token"),
        )
        self.assertLess(
            names.index("Verify release-note artifact"),
            names.index("Generate publication token"),
        )
        self.assertLess(
            names.index("Verify owner-signed release policy"),
            names.index("Generate publication token"),
        )
        self.assertLess(
            names.index("Require enforced immutable releases"),
            names.index("Reconcile immutable release tag"),
        )
        self.assertLess(
            names.index("Require attested append-only release tag rules"),
            names.index("Reconcile immutable release tag"),
        )

    def test_release_policy_uses_signed_source_not_an_actions_variable(self) -> None:
        signature = self.step("publish-release", "Verify owner-signed release policy")
        policy = self.step(
            "publish-release", "Require attested append-only release tag rules"
        )

        self.assertIn("openssl dgst", signature["run"])
        self.assertIn(".github/release-policy/public-key.pem", signature["run"])
        self.assertIn(".github/release-policy/attestation.json", policy["run"])
        self.assertNotIn("POLICY_ATTESTATION", str(policy))
        self.assertNotIn("RELEASE_POLICY_ATTESTATION", str(self.workflow))

    def test_candidate_and_ci_are_bound_to_exact_commits(self) -> None:
        source_check = self.step("resolve", "Require current main tip")
        wait = self.step("wait-for-ci", "Wait for tooling and release CI")["run"]
        revalidate = self.step("publish-release", "Revalidate release candidate")["run"]

        self.assertEqual(
            source_check["env"]["EXPECTED_SOURCE_SHA"],
            "${{ github.event.inputs.expected_source_sha || github.sha }}",
        )
        self.assertIn("scripts/release.py verify-source", source_check["run"])
        self.assertIn('--expected-source "${EXPECTED_SOURCE_SHA}"', source_check["run"])
        self.assertIn('--commit "${sha}"', wait)
        self.assertIn("A retained ${role} CI run passed", wait)
        self.assertIn('"${current_main_sha}" != "${GITHUB_SHA}"', revalidate)
        self.assertIn("git merge-base --is-ancestor", revalidate)
        self.assertIn("scripts/release.py plan", revalidate)
        self.assertIn("expected_tag_graph_sha256=${EXPECTED_GRAPH}", revalidate)
        publication = self.step("publish-release", "Reconcile immutable release tag")[
            "run"
        ]
        self.assertIn('"${current_main_sha}" != "${GITHUB_SHA}"', publication)
        self.assertLess(
            publication.index("current_main_sha"),
            publication.index("scripts/release.py reconcile-tag"),
        )

    def test_release_artifact_is_digest_checked_at_each_boundary(self) -> None:
        notes = self.step("prepare-release-notes", "Generate and inspect release notes")
        verifier = self.step("publish-release", "Verify release-note artifact")
        uploader = self.step("prepare-release-notes", "Upload release notes")
        downloader = self.step("publish-release", "Download release notes")

        self.assertIn("sha256sum", notes["run"])
        self.assertEqual(
            notes["env"]["EXPECTED_NOTES_SHA256"],
            "${{ needs.resolve.outputs.expected_notes_sha256 }}",
        )
        self.assertIn("Recovered release notes differ", notes["run"])
        self.assertIn("sha256sum", verifier["run"])
        self.assertIn("actions/upload-artifact@", uploader["uses"])
        self.assertIn("actions/download-artifact@", downloader["uses"])
        self.assertEqual(uploader["with"]["retention-days"], "1")

    def test_tag_publication_is_atomic_and_never_mutates_a_release_tag(self) -> None:
        source = "\n".join(
            (ROOT / "scripts" / name).read_text(encoding="utf-8")
            for name in ("release.py", "release_coordination.py")
        )
        reconcile = self.step("publish-release", "Reconcile immutable release tag")[
            "run"
        ]

        self.assertIn('"push",\n        "--atomic"', source)
        self.assertIn("--force-with-lease=", source)
        self.assertIn("refs/heads/release-coordination", source)
        self.assertNotIn('"tag", "--delete"', source)
        self.assertNotIn('"push", "--delete"', source)
        self.assertNotIn('"tag", "--force"', source)
        self.assertNotIn("--force", reconcile)
        self.assertIn('--expected-main-commit "${GITHUB_SHA}"', reconcile)

    def test_release_creation_verifies_exact_immutable_projection(self) -> None:
        creator = self.step("publish-release", "Create GitHub Release")["run"]
        immutable = self.step("publish-release", "Require enforced immutable releases")[
            "run"
        ]

        self.assertIn("verify-immutable-settings", immutable)
        self.assertNotIn("@tsv", immutable)
        self.assertIn("--verify-tag", creator)
        self.assertIn('--target "${RELEASE_SHA}"', creator)
        self.assertIn("scripts/release.py verify-release", creator)
        self.assertIn("A concurrent publisher created", creator)

    def test_orb_publication_is_bound_to_release_source_and_evidence(self) -> None:
        publisher = self.jobs["publish-orb"]
        names = [step["name"] for step in publisher["steps"]]
        tooling = self.step("publish-orb", "Checkout release tooling")
        source = self.step("publish-orb", "Checkout immutable Orb source")
        evidence = self.step(
            "publish-orb", "Require exact CircleCI release evidence"
        )
        pack = self.step("publish-orb", "Pack immutable Orb source")
        publish = self.step("publish-orb", "Publish or verify immutable Orb")

        self.assertEqual(
            publisher["environment"],
            {"name": "circleci-orb-publishing", "deployment": "false"},
        )
        self.assertEqual(
            publisher["needs"], ["resolve", "publish-release"]
        )
        self.assertEqual(tooling["with"]["ref"], "${{ github.sha }}")
        self.assertEqual(
            source["with"]["ref"], "${{ needs.resolve.outputs.sha }}"
        )
        self.assertEqual(
            evidence["env"]["RELEASE_SHA"], "${{ needs.resolve.outputs.sha }}"
        )
        self.assertEqual(
            evidence["env"]["RELEASE_TAG"], "${{ needs.resolve.outputs.tag }}"
        )
        self.assertIn("wait-tag-pipeline", evidence["run"])
        self.assertIn(
            "../release-source/.circleci/test-deploy.yml", evidence["run"]
        )
        self.assertNotIn("v0.1.1", evidence["run"])
        self.assertEqual(
            pack["env"]["ORB_SOURCE_DIRECTORY"], "../release-source/src"
        )
        self.assertEqual(
            publish["env"]["ORB_RELEASE_TAG"],
            "${{ needs.resolve.outputs.tag }}",
        )
        self.assertLess(
            names.index("Require exact CircleCI release evidence"),
            names.index("Pack immutable Orb source"),
        )
        self.assertLess(
            names.index("Pack immutable Orb source"),
            names.index("Publish or verify immutable Orb"),
        )

    def test_orb_credential_is_checked_before_release_metadata(self) -> None:
        verifier = self.jobs["verify-orb-publisher"]
        self.assertEqual(
            verifier["environment"],
            {"name": "circleci-orb-publishing", "deployment": "false"},
        )
        self.assertEqual(verifier["needs"], ["resolve", "wait-for-ci"])
        preflight = self.step(
            "verify-orb-publisher", "Require CircleCI publishing token"
        )
        self.assertEqual(
            preflight["env"]["CIRCLE_TOKEN"],
            "${{ secrets.CIRCLE_TOKEN }}",
        )
        self.assertEqual(preflight["run"], 'test -n "${CIRCLE_TOKEN}"')
        for token, expected_status in (("", 1), ("publisher-token", 0)):
            with self.subTest(token=bool(token)):
                result = subprocess.run(
                    ["bash", "-c", preflight["run"]],
                    env={"CIRCLE_TOKEN": token},
                    check=False,
                )
                self.assertEqual(result.returncode, expected_status)
        self.assertIn(
            "verify-orb-publisher", self.jobs["publish-release"]["needs"]
        )

    def test_orb_secret_is_confined_to_evidence_and_publication(self) -> None:
        secret_steps = [
            (job_name, step["name"])
            for job_name, job in self.jobs.items()
            for step in job["steps"]
            if "secrets.CIRCLE_TOKEN" in str(step)
        ]

        self.assertEqual(
            secret_steps,
            [
                ("verify-orb-publisher", "Require CircleCI publishing token"),
                ("publish-orb", "Publish or verify immutable Orb"),
            ],
        )
        self.assertEqual(str(self.workflow).count("secrets.CIRCLE_TOKEN"), 2)


if __name__ == "__main__":
    unittest.main()
