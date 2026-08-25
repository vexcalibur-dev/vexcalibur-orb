#!/usr/bin/env python3
"""Plan and verify append-only Vexcalibur Orb releases."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_common import (  # noqa: E402
    AnnotatedTag,
    CURRENT_NOTES_FORMAT,
    MAX_VERSION_COMPONENT,
    REPOSITORY,
    REPOSITORY_URL,
    SHA256_PATTERN,
    SUPPORTED_NOTES_FORMATS,
    ReleaseError,
    ReleasePlan,
    TagMetadata,
    TagState,
    Version,
    emit_github_outputs,
    fail,
    read_json,
    require_sha,
    strict_json,
)
from release_policy import (  # noqa: E402
    verify_attested_rulesets,
    verify_immutable_release_settings,
    verify_release,
    verify_release_document,
)
from release_coordination import (  # noqa: E402
    RELEASE_COORDINATION_REF,
    atomic_publish_release,
    create_coordination_commit,
    verify_remote_coordination,
)
from release_git import (  # noqa: E402
    fail_git,
    fail_git_result,
    git,
    git_bytes,
    is_ancestor,
    local_reference_exists,
    remote_reference,
)


TAGGER_PATTERN = re.compile(
    r"^tagger (?P<name>.+) <(?P<email>[^>]+)> [0-9]+ [+-][0-9]{4}$"
)
BREAKING_SUBJECT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\([^)]+\))?!:", re.IGNORECASE
)
BREAKING_FOOTER_PATTERN = re.compile(
    r"^(?:BREAKING CHANGE|BREAKING-CHANGE):[ \t]+\S.*$",
    re.IGNORECASE | re.MULTILINE,
)
FOOTER_ENTRY_PATTERN = re.compile(
    r"^(?:BREAKING CHANGE|[A-Za-z0-9-]+)(?::[ \t]+|[ \t]+#)\S.*$",
    re.IGNORECASE,
)
FEATURE_PATTERN = re.compile(r"^feat(?:\([^)]+\))?:", re.IGNORECASE)
PATCH_PATTERN = re.compile(
    r"^(?:fix|perf|refactor|deps|revert)(?:\([^)]+\))?:"
    r"|^(?:build|chore)\(deps\):|^Revert \"",
    re.IGNORECASE,
)
SKIP_PATTERN = re.compile(r"\[(?:skip release|release skip)\]", re.IGNORECASE)


def verify_source_commit(
    *, expected_source: str, workflow_source: str, current_main: str
) -> None:
    require_sha(expected_source, label="expected source commit")
    require_sha(workflow_source, label="workflow source commit")
    require_sha(current_main, label="current main commit")
    if expected_source != workflow_source:
        fail("workflow source does not match the requested release commit")
    if current_main != workflow_source:
        fail("workflow source is no longer the current main commit")


def read_tag(reference: str) -> tuple[str, str, str, str, bytes]:
    if git("cat-file", "-t", reference).stdout.strip() != "tag":
        fail(f"{reference} is not an annotated tag")
    document = git_bytes("cat-file", "-p", reference)
    headers_bytes, separator, message = document.partition(b"\n\n")
    if not separator:
        fail(f"{reference} has no annotated message")
    try:
        headers = headers_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(f"{reference} has non-UTF-8 annotated-tag headers")
    lines = headers.splitlines()
    if (
        len(lines) != 4
        or not lines[0].startswith("object ")
        or lines[1] != "type commit"
        or not lines[2].startswith("tag ")
    ):
        fail(f"{reference} has conflicting annotated-tag headers")
    match = TAGGER_PATTERN.fullmatch(lines[3])
    if match is None:
        fail(f"{reference} has malformed tagger metadata")
    commit = require_sha(lines[0].removeprefix("object "), label=f"{reference} target")
    return (
        commit,
        lines[2].removeprefix("tag "),
        match.group("name"),
        match.group("email"),
        message,
    )


def tag_target(tag: str) -> str:
    reference = f"refs/tags/{tag}"
    commit, embedded_tag, _tagger_name, _tagger_email, _message = read_tag(reference)
    if embedded_tag != tag:
        fail(f"release tag {tag} must directly annotate its named commit")
    return commit


def release_tags(*, head: str | None = None) -> tuple[TagState, ...]:
    if head is None:
        head = require_sha(git("rev-parse", "HEAD").stdout.strip(), label="HEAD")
    else:
        require_sha(head, label="release graph head")
    names = tuple(filter(None, git("tag", "--list", "v*").stdout.splitlines()))
    states: list[TagState] = []
    commits: dict[str, str] = {}
    for name in names:
        if "/" in name:
            continue
        version = Version.from_tag(name)
        object_id = require_sha(
            git("rev-parse", "--verify", f"refs/tags/{name}").stdout.strip(),
            label=f"release tag {name} object",
        )
        commit = tag_target(name)
        if not is_ancestor(commit, head):
            fail(f"release tag {name} is not reachable from current main")
        if commit in commits:
            fail(f"release tags {commits[commit]} and {name} target the same commit")
        commits[commit] = name
        states.append(TagState(version, object_id, commit))

    states.sort(key=lambda state: state.version)
    for index, state in enumerate(states):
        metadata = read_tag_metadata(f"refs/tags/{state.tag}")
        expected_previous = states[index - 1].tag if index else ""
        if (
            metadata.tag != state.tag
            or metadata.commit != state.commit
            or metadata.previous_tag != expected_previous
        ):
            fail(f"release tag {state.tag} has conflicting canonical metadata")
    for older, newer in zip(states, states[1:]):
        if not is_ancestor(older.commit, newer.commit):
            fail(f"release tag {newer.tag} is not descended from {older.tag}")
    return tuple(states)


def tag_graph_sha256(tags: Sequence[TagState]) -> str:
    payload = "".join(
        f"{state.tag}\t{state.object_id}\t{state.commit}\n" for state in tags
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def final_footer_paragraph(message: str) -> str:
    paragraphs = re.split(r"\n[ \t]*\n", message.strip())
    if len(paragraphs) <= 1:
        return ""
    footer = paragraphs[-1]
    first_line = footer.splitlines()[0] if footer.splitlines() else ""
    return footer if FOOTER_ENTRY_PATTERN.fullmatch(first_line) else ""


def classify_commit(message: str) -> str:
    subject = message.splitlines()[0] if message.splitlines() else ""
    footer = final_footer_paragraph(message)
    if BREAKING_SUBJECT_PATTERN.match(subject) or BREAKING_FOOTER_PATTERN.search(
        footer
    ):
        return "major"
    if FEATURE_PATTERN.match(subject):
        return "minor"
    if PATCH_PATTERN.match(subject):
        return "patch"
    return "skip"


def classify_range(revision_range: str) -> str:
    ranks = {"skip": 0, "patch": 1, "minor": 2, "major": 3}
    required = "skip"
    commits = git("rev-list", "--reverse", revision_range).stdout.splitlines()
    for commit in commits:
        message = git("show", "-s", "--format=%B", commit).stdout
        classification = classify_commit(message)
        if ranks[classification] > ranks[required]:
            required = classification
        if required == "major":
            break
    return required


def plan_release(requested_tag: str) -> ReleasePlan:
    head = require_sha(git("rev-parse", "HEAD").stdout.strip(), label="HEAD")
    tags = release_tags()
    graph_sha256 = tag_graph_sha256(tags)
    latest = tags[-1] if tags else None

    if requested_tag:
        requested = Version.from_tag(requested_tag)
        existing_index = next(
            (index for index, state in enumerate(tags) if state.version == requested),
            None,
        )
        if existing_index is not None:
            existing = tags[existing_index]
            previous = tags[existing_index - 1].tag if existing_index else ""
            metadata = read_tag_metadata(f"refs/tags/{existing.tag}")
            if (
                metadata.tag != existing.tag
                or metadata.commit != existing.commit
                or metadata.previous_tag != previous
            ):
                fail(f"release tag {existing.tag} has conflicting canonical metadata")
            return ReleasePlan(
                "recover",
                existing.tag,
                previous,
                "existing",
                existing.commit,
                metadata.notes_format,
                metadata.notes_sha256,
                graph_sha256,
                existing == latest,
                "recover existing immutable release",
            )
        if tags:
            fail("an explicit new tag is allowed only for the first production release")
        return ReleasePlan(
            "publish",
            requested.tag,
            "",
            "initial",
            head,
            CURRENT_NOTES_FORMAT,
            "",
            graph_sha256,
            True,
            "explicit first production release",
        )

    if latest is None:
        return ReleasePlan(
            "skip",
            "",
            "",
            "skip",
            head,
            CURRENT_NOTES_FORMAT,
            "",
            graph_sha256,
            False,
            "the first production release requires an explicit dispatch tag",
        )

    if latest.commit == head:
        previous = tags[-2].tag if len(tags) > 1 else ""
        metadata = read_tag_metadata(f"refs/tags/{latest.tag}")
        if (
            metadata.tag != latest.tag
            or metadata.commit != latest.commit
            or metadata.previous_tag != previous
        ):
            fail(f"release tag {latest.tag} has conflicting canonical metadata")
        return ReleasePlan(
            "recover",
            latest.tag,
            previous,
            "existing",
            latest.commit,
            metadata.notes_format,
            metadata.notes_sha256,
            graph_sha256,
            True,
            "recover release on the current main commit",
        )

    head_message = git("log", "-1", "--format=%B").stdout
    if SKIP_PATTERN.search(head_message):
        return ReleasePlan(
            "skip",
            "",
            latest.tag,
            "skip",
            head,
            CURRENT_NOTES_FORMAT,
            "",
            graph_sha256,
            False,
            "current commit requests release suppression",
        )

    bump = classify_range(f"{latest.tag}..HEAD")
    if bump == "skip":
        return ReleasePlan(
            "skip",
            "",
            latest.tag,
            "skip",
            head,
            CURRENT_NOTES_FORMAT,
            "",
            graph_sha256,
            False,
            "no releasable Conventional Commit exists after the latest tag",
        )
    next_version = latest.version.bump(bump)
    if any(
        component > MAX_VERSION_COMPONENT
        for component in (next_version.major, next_version.minor, next_version.patch)
    ):
        fail("the next release version exceeds the supported component bound")
    return ReleasePlan(
        "publish",
        next_version.tag,
        latest.tag,
        bump,
        head,
        CURRENT_NOTES_FORMAT,
        "",
        graph_sha256,
        True,
        f"{bump} Conventional Commit release",
    )


def _release_notes_format_one(
    tag: str,
    commit: str,
    previous_tag: str,
    version: Version,
) -> str:
    if previous_tag:
        changes = (
            f"[Compare {previous_tag}...{tag}]"
            f"({REPOSITORY_URL}/compare/{previous_tag}...{tag})"
        )
    else:
        changes = f"[Release commit]({REPOSITORY_URL}/commit/{commit})"
    return (
        f"# Vexcalibur Orb {tag}\n\n"
        f"This release publishes `{REPOSITORY.removesuffix('-orb')}@"
        f"{version.major}.{version.minor}.{version.patch}` "
        f"from commit [`{commit[:12]}`]({REPOSITORY_URL}/commit/{commit}).\n\n"
        f"## Changes\n\n{changes}\n\n"
        "CircleCI starts the production registry pipeline from this immutable tag. "
        "The GitHub Release records the source identity; the CircleCI registry entry "
        "is the consumer artifact.\n"
    )


def _release_notes_format_two(
    tag: str,
    commit: str,
    previous_tag: str,
    version: Version,
) -> str:
    if previous_tag:
        changes = (
            f"[Compare {previous_tag}...{tag}]"
            f"({REPOSITORY_URL}/compare/{previous_tag}...{tag})"
        )
    else:
        changes = f"[Release commit]({REPOSITORY_URL}/commit/{commit})"
    return (
        f"# Vexcalibur Orb {tag}\n\n"
        f"This release publishes `{REPOSITORY.removesuffix('-orb')}@"
        f"{version.major}.{version.minor}.{version.patch}` "
        f"from commit [`{commit[:12]}`]({REPOSITORY_URL}/commit/{commit}).\n\n"
        f"## Changes\n\n{changes}\n\n"
        "GitHub Actions publishes the registry entry from this immutable tag "
        "after the exact CircleCI pipeline passes. The GitHub Release records "
        "the source identity; the CircleCI registry entry is the consumer "
        "artifact.\n"
    )


NotesRenderer = Callable[[str, str, str, Version], str]
NOTES_RENDERERS: dict[str, NotesRenderer] = {
    "1": _release_notes_format_one,
    "2": _release_notes_format_two,
}


def release_notes(
    *, tag: str, commit: str, previous_tag: str, notes_format: str
) -> str:
    renderer = NOTES_RENDERERS.get(notes_format)
    if renderer is None or notes_format not in SUPPORTED_NOTES_FORMATS:
        fail(f"unsupported release-note format {notes_format!r}")
    version = Version.from_tag(tag)
    require_sha(commit, label="release commit")
    if previous_tag and Version.from_tag(previous_tag) >= version:
        fail("previous release tag must be older than the new release")
    return renderer(tag, commit, previous_tag, version)


def tag_message(
    *,
    tag: str,
    commit: str,
    previous_tag: str,
    notes_format: str,
    notes_sha256: str,
) -> str:
    Version.from_tag(tag)
    require_sha(commit, label="release commit")
    if previous_tag:
        Version.from_tag(previous_tag)
    if notes_format not in SUPPORTED_NOTES_FORMATS:
        fail(f"unsupported release-note format {notes_format!r}")
    if SHA256_PATTERN.fullmatch(notes_sha256) is None:
        fail("release-note digest must be lowercase SHA-256")
    return json.dumps(
        {
            "commit": commit,
            "notes_sha256": notes_sha256,
            "notes_format": notes_format,
            "previous_tag": previous_tag,
            "schema_version": 1,
            "tag": tag,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def read_annotated_tag(reference: str) -> AnnotatedTag:
    commit, embedded_tag, tagger_name, tagger_email, message = read_tag(reference)
    document = strict_json(message, source=f"{reference} annotation")
    expected_keys = {
        "commit",
        "notes_format",
        "notes_sha256",
        "previous_tag",
        "schema_version",
        "tag",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        fail(f"{reference} has malformed canonical metadata")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        fail(f"{reference} has an unsupported metadata schema")
    tag = document.get("tag")
    metadata_commit = document.get("commit")
    previous_tag = document.get("previous_tag")
    notes_format = document.get("notes_format")
    notes_sha256 = document.get("notes_sha256")
    if not all(
        isinstance(value, str)
        for value in (tag, metadata_commit, previous_tag, notes_format, notes_sha256)
    ):
        fail(f"{reference} has malformed canonical metadata")
    assert isinstance(tag, str)
    assert isinstance(metadata_commit, str)
    assert isinstance(previous_tag, str)
    assert isinstance(notes_format, str)
    assert isinstance(notes_sha256, str)
    Version.from_tag(tag)
    require_sha(metadata_commit, label=f"{reference} metadata commit")
    if previous_tag:
        Version.from_tag(previous_tag)
    if notes_format not in SUPPORTED_NOTES_FORMATS:
        fail(f"{reference} uses unsupported release-note format {notes_format!r}")
    if SHA256_PATTERN.fullmatch(notes_sha256) is None:
        fail(f"{reference} has malformed release-note digest")
    if tag != embedded_tag or metadata_commit != commit:
        fail(f"{reference} has conflicting canonical metadata")
    metadata = TagMetadata(
        tag,
        metadata_commit,
        previous_tag,
        notes_format,
        notes_sha256,
    )
    canonical_message = tag_message(
        tag=metadata.tag,
        commit=metadata.commit,
        previous_tag=metadata.previous_tag,
        notes_format=metadata.notes_format,
        notes_sha256=metadata.notes_sha256,
    )
    if message != canonical_message.encode() + b"\n":
        fail(f"{reference} has conflicting canonical metadata")
    return AnnotatedTag(metadata, tagger_name, tagger_email)


def read_tag_metadata(reference: str) -> TagMetadata:
    return read_annotated_tag(reference).metadata


def verify_published_release(
    *,
    release_json: Path,
    tag: str,
    commit: str,
    main_commit: str,
    expected_author: str,
    expected_tagger_name: str,
    expected_tagger_email: str,
) -> None:
    require_sha(main_commit, label="expected main commit")
    tags = release_tags(head=main_commit)
    matching = [state for state in tags if state.tag == tag]
    if len(matching) != 1:
        fail(f"release tag {tag} is not present in the verified tag graph")
    if matching[0].commit != commit:
        fail(f"release tag {tag} does not target the expected commit")
    annotated = read_annotated_tag(f"refs/tags/{tag}")
    metadata = annotated.metadata
    require_sha(commit, label="release commit")
    if metadata.tag != tag:
        fail(f"release tag reference {tag} contains metadata for {metadata.tag}")
    if (
        annotated.tagger_name != expected_tagger_name
        or annotated.tagger_email != expected_tagger_email
    ):
        fail(f"release tag {tag} was not authored by the expected automation identity")
    notes = release_notes(
        tag=tag,
        commit=commit,
        previous_tag=metadata.previous_tag,
        notes_format=metadata.notes_format,
    )
    if hashlib.sha256(notes.encode()).hexdigest() != metadata.notes_sha256:
        fail(f"release tag {tag} has conflicting release-note metadata")
    verify_release_document(
        release_json=release_json,
        notes=notes,
        tag=tag,
        commit=commit,
        expected_author=expected_author,
    )


def verify_tag(
    *,
    reference: str,
    tag: str,
    commit: str,
    previous_tag: str,
    notes_format: str,
    notes_sha256: str,
    expected_tagger_name: str,
    expected_tagger_email: str,
) -> None:
    annotated = read_annotated_tag(reference)
    expected_metadata = TagMetadata(
        tag,
        commit,
        previous_tag,
        notes_format,
        notes_sha256,
    )
    if annotated.metadata != expected_metadata:
        fail(f"{reference} does not contain the expected release metadata")
    if (
        annotated.tagger_name != expected_tagger_name
        or annotated.tagger_email != expected_tagger_email
    ):
        fail(f"{reference} was not authored by the expected automation identity")


def remote_release_tags(remote: str) -> tuple[TagState, ...]:
    arguments = (
        "ls-remote",
        "--tags",
        remote,
        "refs/tags/v*",
    )
    result = git(*arguments, check=False)
    if result.returncode != 0:
        fail_git(arguments, result)

    objects: dict[str, str] = {}
    commits: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
            fail("remote repository returned malformed release-tag state")
        object_id = require_sha(fields[0], label=f"remote {fields[1]} object")
        name = fields[1].removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        if peeled:
            name = name.removesuffix("^{}")
        if "/" in name:
            continue
        Version.from_tag(name)
        destination = commits if peeled else objects
        if name in destination:
            fail(f"remote repository returned duplicate state for release tag {name}")
        destination[name] = object_id

    if objects.keys() != commits.keys():
        fail("remote repository contains a release tag that is not annotated")
    states = [
        TagState(Version.from_tag(name), object_id, commits[name])
        for name, object_id in objects.items()
    ]
    states.sort(key=lambda state: state.version)
    seen_commits: dict[str, str] = {}
    for older, newer in zip(states, states[1:]):
        if not is_ancestor(older.commit, newer.commit):
            fail(f"remote release tag {newer.tag} is not descended from {older.tag}")
    for state in states:
        if state.commit in seen_commits:
            fail(
                f"remote release tags {seen_commits[state.commit]} and {state.tag} "
                "target the same commit"
            )
        seen_commits[state.commit] = state.tag
    return tuple(states)


def verify_remote_tag(
    *,
    remote: str,
    tag: str,
    commit: str,
    previous_tag: str,
    notes_format: str,
    notes_sha256: str,
    expected_tagger_name: str,
    expected_tagger_email: str,
) -> None:
    verification_ref = f"refs/vexcalibur-release/verify/{tag}"
    git("update-ref", "-d", verification_ref)
    try:
        git(
            "fetch",
            "--no-tags",
            remote,
            f"refs/tags/{tag}:{verification_ref}",
        )
        verify_tag(
            reference=verification_ref,
            tag=tag,
            commit=commit,
            previous_tag=previous_tag,
            notes_format=notes_format,
            notes_sha256=notes_sha256,
            expected_tagger_name=expected_tagger_name,
            expected_tagger_email=expected_tagger_email,
        )
    finally:
        git("update-ref", "-d", verification_ref)


def reconcile_tag(
    *,
    remote: str,
    expected_main_commit: str,
    expected_tag_graph_sha256: str,
    tag: str,
    commit: str,
    previous_tag: str,
    notes_format: str,
    notes_sha256: str,
    expected_tagger_name: str,
    expected_tagger_email: str,
) -> str:
    require_sha(expected_main_commit, label="expected main commit")
    if SHA256_PATTERN.fullmatch(expected_tag_graph_sha256) is None:
        fail("expected tag-graph digest must be lowercase SHA-256")
    main_ref = "refs/heads/main"
    # This observation orders the release before any later main update. The
    # tag and coordination ref below serialize release operations themselves.
    if remote_reference(remote, main_ref) != expected_main_commit:
        fail("remote main changed after release planning")
    coordination_before = remote_reference(remote, RELEASE_COORDINATION_REF)
    tags = remote_release_tags(remote)
    if coordination_before:
        if not tags:
            fail("release coordination branch exists without a release tag")
        verify_remote_coordination(
            remote=remote,
            object_id=coordination_before,
            latest=tags[-1],
            expected_author_name=expected_tagger_name,
            expected_author_email=expected_tagger_email,
        )
    matching = next((state for state in tags if state.tag == tag), None)
    if matching is not None:
        if matching.commit != commit:
            fail(f"existing release tag {tag} targets a different commit")
        current_graph = tag_graph_sha256(tags)
        graph_without_target = tag_graph_sha256(
            tuple(state for state in tags if state.tag != tag)
        )
        if expected_tag_graph_sha256 not in {current_graph, graph_without_target}:
            fail("remote release-tag graph changed before existing-tag verification")
        matching_index = tags.index(matching)
        expected_previous = tags[matching_index - 1].tag if matching_index else ""
        if previous_tag != expected_previous:
            fail(f"release tag {tag} has conflicting predecessor metadata")
        verify_remote_tag(
            remote=remote,
            tag=tag,
            commit=commit,
            previous_tag=previous_tag,
            notes_format=notes_format,
            notes_sha256=notes_sha256,
            expected_tagger_name=expected_tagger_name,
            expected_tagger_email=expected_tagger_email,
        )
        if not coordination_before:
            repair_ref = "refs/vexcalibur-release/repair/coordination"
            git("update-ref", "-d", repair_ref)
            repair_object = create_coordination_commit(tags[-1])
            git("update-ref", repair_ref, repair_object)
            try:
                repair = git(
                    "push",
                    f"--force-with-lease={RELEASE_COORDINATION_REF}:",
                    remote,
                    f"{repair_ref}:{RELEASE_COORDINATION_REF}",
                    check=False,
                )
            finally:
                git("update-ref", "-d", repair_ref)
            repaired = remote_reference(remote, RELEASE_COORDINATION_REF)
            if not repaired:
                if repair.returncode != 0:
                    fail_git_result("release coordination repair push", repair)
                fail("release coordination branch is missing after repair")
            verify_remote_coordination(
                remote=remote,
                object_id=repaired,
                latest=tags[-1],
                expected_author_name=expected_tagger_name,
                expected_author_email=expected_tagger_email,
            )
        return "verified"

    if git("rev-parse", "--verify", "HEAD").stdout.strip() != commit:
        fail("a new release tag must target the checked-out candidate commit")

    if tag_graph_sha256(tags) != expected_tag_graph_sha256:
        fail("remote release-tag graph changed after planning")
    version = Version.from_tag(tag)
    expected_previous = tags[-1].tag if tags else ""
    if previous_tag != expected_previous:
        fail(f"release tag {tag} has conflicting predecessor metadata")
    if tags:
        latest = tags[-1]
        if version <= latest.version:
            fail(f"new release tag {tag} must be greater than {latest.tag}")
        if not is_ancestor(latest.commit, commit):
            fail(f"new release commit must descend from {latest.tag}")
    duplicate = next((state.tag for state in tags if state.commit == commit), None)
    if duplicate is not None:
        fail(f"release commit already has remote release tag {duplicate}")

    local_ref = f"refs/tags/{tag}"
    if local_reference_exists(local_ref):
        fail(f"local release tag {tag} exists while the remote repository does not")

    message = tag_message(
        tag=tag,
        commit=commit,
        previous_tag=previous_tag,
        notes_format=notes_format,
        notes_sha256=notes_sha256,
    )
    creation_ref = f"refs/vexcalibur-release/create/{tag}"
    git("update-ref", "-d", creation_ref)
    tagger = git("var", "GIT_COMMITTER_IDENT").stdout.strip()
    tag_document = "\n".join(
        (
            f"object {commit}",
            "type commit",
            f"tag {tag}",
            f"tagger {tagger}",
            "",
            message,
            "",
        )
    )
    object_id = require_sha(
        git("mktag", input_text=tag_document).stdout.strip(),
        label=f"release tag {tag} object",
    )
    git("update-ref", creation_ref, object_id)
    new_tag = TagState(version, object_id, commit)
    coordination_ref = f"refs/vexcalibur-release/create-coordination/{tag}"
    git("update-ref", "-d", coordination_ref)
    git("update-ref", coordination_ref, create_coordination_commit(new_tag))
    try:
        verify_tag(
            reference=creation_ref,
            tag=tag,
            commit=commit,
            previous_tag=previous_tag,
            notes_format=notes_format,
            notes_sha256=notes_sha256,
            expected_tagger_name=expected_tagger_name,
            expected_tagger_email=expected_tagger_email,
        )
        push = atomic_publish_release(
            remote=remote,
            tag_source=creation_ref,
            tag_destination=local_ref,
            coordination_source=coordination_ref,
            coordination_before=coordination_before,
        )
    finally:
        git("update-ref", "-d", creation_ref)
        git("update-ref", "-d", coordination_ref)

    final_tags = remote_release_tags(remote)
    matching = next((state for state in final_tags if state.tag == tag), None)
    if matching is None or matching.commit != commit:
        if push.returncode != 0:
            fail_git_result("atomic release push", push)
        fail(f"release tag {tag} was not created at the expected commit")
    verify_remote_tag(
        remote=remote,
        tag=tag,
        commit=commit,
        previous_tag=previous_tag,
        notes_format=notes_format,
        notes_sha256=notes_sha256,
        expected_tagger_name=expected_tagger_name,
        expected_tagger_email=expected_tagger_email,
    )
    final_coordination = remote_reference(remote, RELEASE_COORDINATION_REF)
    if not final_coordination:
        fail("release coordination branch is missing after publication")
    verify_remote_coordination(
        remote=remote,
        object_id=final_coordination,
        latest=matching,
        expected_author_name=expected_tagger_name,
        expected_author_email=expected_tagger_email,
    )
    final_without_target = tuple(state for state in final_tags if state.tag != tag)
    if tag_graph_sha256(final_without_target) != expected_tag_graph_sha256:
        fail("remote release-tag graph changed during publication")
    return "created" if push.returncode == 0 else "verified"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--tag", default="")

    notes = commands.add_parser("render-notes")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--commit", required=True)
    notes.add_argument("--previous-tag", default="")
    notes.add_argument("--notes-format", required=True)
    notes.add_argument("--output", type=Path, required=True)

    reconcile = commands.add_parser("reconcile-tag")
    reconcile.add_argument("--remote", default="origin")
    reconcile.add_argument("--expected-main-commit", required=True)
    reconcile.add_argument("--expected-tag-graph-sha256", required=True)
    reconcile.add_argument("--tag", required=True)
    reconcile.add_argument("--commit", required=True)
    reconcile.add_argument("--previous-tag", default="")
    reconcile.add_argument("--notes-format", required=True)
    reconcile.add_argument("--notes-sha256", required=True)
    reconcile.add_argument("--expected-tagger-name", required=True)
    reconcile.add_argument("--expected-tagger-email", required=True)

    release = commands.add_parser("verify-release")
    release.add_argument("--release-json", type=Path, required=True)
    release.add_argument("--notes-file", type=Path, required=True)
    release.add_argument("--tag", required=True)
    release.add_argument("--commit", required=True)
    release.add_argument("--expected-author", required=True)

    published_release = commands.add_parser("verify-published-release")
    published_release.add_argument("--release-json", type=Path, required=True)
    published_release.add_argument("--tag", required=True)
    published_release.add_argument("--commit", required=True)
    published_release.add_argument("--main-commit", required=True)
    published_release.add_argument("--expected-author", required=True)
    published_release.add_argument("--expected-tagger-name", required=True)
    published_release.add_argument("--expected-tagger-email", required=True)

    source = commands.add_parser("verify-source")
    source.add_argument("--expected-source", required=True)
    source.add_argument("--workflow-source", required=True)
    source.add_argument("--current-main", required=True)

    immutable = commands.add_parser("verify-immutable-settings")
    immutable.add_argument("--settings-json", type=Path, required=True)

    policy = commands.add_parser("verify-attested-rulesets")
    policy.add_argument("--immutable-json", type=Path, required=True)
    policy.add_argument("--creation-json", type=Path, required=True)
    policy.add_argument("--attestation", type=Path, required=True)
    policy.add_argument("--repository", required=True)
    policy.add_argument("--app-id", type=int, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            emit_github_outputs(plan_release(args.tag).github_outputs())
        elif args.command == "render-notes":
            args.output.write_text(
                release_notes(
                    tag=args.tag,
                    commit=args.commit,
                    previous_tag=args.previous_tag,
                    notes_format=args.notes_format,
                ),
                encoding="utf-8",
            )
        elif args.command == "reconcile-tag":
            operation = reconcile_tag(
                remote=args.remote,
                expected_main_commit=args.expected_main_commit,
                expected_tag_graph_sha256=args.expected_tag_graph_sha256,
                tag=args.tag,
                commit=args.commit,
                previous_tag=args.previous_tag,
                notes_format=args.notes_format,
                notes_sha256=args.notes_sha256,
                expected_tagger_name=args.expected_tagger_name,
                expected_tagger_email=args.expected_tagger_email,
            )
            print(f"{operation} immutable release tag {args.tag}")
        elif args.command == "verify-release":
            verify_release(
                release_json=args.release_json,
                notes_file=args.notes_file,
                tag=args.tag,
                commit=args.commit,
                expected_author=args.expected_author,
            )
            print(f"verified immutable GitHub Release {args.tag}")
        elif args.command == "verify-published-release":
            verify_published_release(
                release_json=args.release_json,
                tag=args.tag,
                commit=args.commit,
                main_commit=args.main_commit,
                expected_author=args.expected_author,
                expected_tagger_name=args.expected_tagger_name,
                expected_tagger_email=args.expected_tagger_email,
            )
            print(f"verified published GitHub Release {args.tag}")
        elif args.command == "verify-source":
            verify_source_commit(
                expected_source=args.expected_source,
                workflow_source=args.workflow_source,
                current_main=args.current_main,
            )
            print("verified release workflow source commit")
        elif args.command == "verify-immutable-settings":
            verify_immutable_release_settings(args.settings_json)
            print("verified owner-enforced immutable releases")
        elif args.command == "verify-attested-rulesets":
            verify_attested_rulesets(
                immutable=read_json(args.immutable_json),
                creation=read_json(args.creation_json),
                attestation=read_json(args.attestation),
                repository=args.repository,
                app_id=args.app_id,
            )
            print("verified live release rules against the owner attestation")
        else:
            fail(f"unsupported command {args.command!r}")
    except (OSError, ReleaseError) as error:
        raise SystemExit(f"release state error: {error}") from error


if __name__ == "__main__":
    main()
