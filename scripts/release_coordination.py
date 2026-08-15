"""Uniquely changing coordination state for create-only release tags."""

from __future__ import annotations

import json
import subprocess

from release_common import TagState, fail, require_sha, strict_json
from release_git import git


RELEASE_COORDINATION_REF = "refs/heads/release-coordination"
COORDINATION_SCHEMA_VERSION = 1


def coordination_message(tag: TagState) -> str:
    """Render the canonical state stored by the coordination branch."""
    return json.dumps(
        {
            "commit": tag.commit,
            "schema_version": COORDINATION_SCHEMA_VERSION,
            "tag": tag.tag,
            "tag_object": tag.object_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def create_coordination_commit(tag: TagState) -> str:
    """Create a synthetic commit whose identity is unique to a release tag."""
    tree = require_sha(
        git("rev-parse", f"{tag.commit}^{{tree}}").stdout.strip(),
        label=f"release commit {tag.commit} tree",
    )
    return require_sha(
        git(
            "commit-tree",
            tree,
            "-p",
            tag.commit,
            input_text=coordination_message(tag) + "\n",
        ).stdout.strip(),
        label="release coordination commit",
    )


def verify_coordination_reference(
    reference: str,
    *,
    latest: TagState,
    expected_author_name: str,
    expected_author_email: str,
) -> None:
    """Require a coordination commit to identify the latest release exactly."""
    if git("cat-file", "-t", reference).stdout.strip() != "commit":
        fail("release coordination ref does not point to a commit")
    parents = git("show", "-s", "--format=%P", reference).stdout.split()
    if parents != [latest.commit]:
        fail("release coordination commit has a conflicting parent")
    coordination_tree = git("rev-parse", f"{reference}^{{tree}}").stdout.strip()
    release_tree = git("rev-parse", f"{latest.commit}^{{tree}}").stdout.strip()
    if coordination_tree != release_tree:
        fail("release coordination commit has a conflicting tree")

    projection = git(
        "show",
        "-s",
        "--format=%an%x00%ae%x00%cn%x00%ce%x00%B",
        reference,
    ).stdout.split("\0", 4)
    if len(projection) != 5:
        fail("release coordination commit has malformed identity metadata")
    author_name, author_email, committer_name, committer_email, message = projection
    if (
        author_name != expected_author_name
        or committer_name != expected_author_name
        or author_email != expected_author_email
        or committer_email != expected_author_email
    ):
        fail("release coordination commit has an unexpected automation identity")
    document = strict_json(
        message.rstrip("\n").encode(),
        source=f"{reference} message",
    )
    expected = json.loads(coordination_message(latest))
    if document != expected or type(document.get("schema_version")) is not int:
        fail("release coordination commit has conflicting protected metadata")


def verify_remote_coordination(
    *,
    remote: str,
    object_id: str,
    latest: TagState,
    expected_author_name: str,
    expected_author_email: str,
) -> None:
    """Fetch and verify the remote coordination branch under a private ref."""
    verification_ref = "refs/vexcalibur-release/verify/coordination"
    git("update-ref", "-d", verification_ref)
    try:
        git(
            "fetch",
            "--no-tags",
            remote,
            f"{RELEASE_COORDINATION_REF}:{verification_ref}",
        )
        actual = require_sha(
            git("rev-parse", "--verify", verification_ref).stdout.strip(),
            label="release coordination object",
        )
        if actual != object_id:
            fail("release coordination branch changed while it was inspected")
        verify_coordination_reference(
            verification_ref,
            latest=latest,
            expected_author_name=expected_author_name,
            expected_author_email=expected_author_email,
        )
    finally:
        git("update-ref", "-d", verification_ref)


def atomic_publish_release(
    *,
    remote: str,
    tag_source: str,
    tag_destination: str,
    coordination_source: str,
    coordination_before: str,
) -> subprocess.CompletedProcess[str]:
    """Push one create-only tag and its uniquely changing coordination commit."""
    return git(
        "push",
        "--atomic",
        f"--force-with-lease={RELEASE_COORDINATION_REF}:{coordination_before}",
        remote,
        f"{tag_source}:{tag_destination}",
        f"{coordination_source}:{RELEASE_COORDINATION_REF}",
        check=False,
    )
