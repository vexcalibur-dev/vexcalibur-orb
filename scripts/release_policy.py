"""GitHub Release projection and live release-policy verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from release_common import Version, fail, read_json, require_sha


STRICT_TAG_PATTERN = "refs/tags/v*"
IMMUTABLE_RULESET_NAME = "immutable release tags"
CREATION_RULESET_NAME = "restricted release tag creation"
RULESET_REVISION_PATTERN = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})"
)


def verify_release_document(
    *,
    release_json: Path,
    notes: str,
    tag: str,
    commit: str,
    expected_author: str,
) -> None:
    Version.from_tag(tag)
    require_sha(commit, label="release commit")
    release = read_json(release_json)
    author = release.get("author")
    author_login = author.get("login") if isinstance(author, dict) else None
    expected_fields: dict[str, Any] = {
        "tag_name": tag,
        "target_commitish": commit,
        "name": tag,
        "body": notes,
        "assets": [],
    }
    for field, value in expected_fields.items():
        if release.get(field) != value:
            fail(f"GitHub Release field {field!r} does not match protected metadata")
    for field, expected_value in (
        ("draft", False),
        ("prerelease", False),
        ("immutable", True),
    ):
        if type(release.get(field)) is not bool or release[field] is not expected_value:
            fail(f"GitHub Release field {field!r} does not match protected metadata")
    if author_login != expected_author:
        fail("GitHub Release was not authored by the expected automation identity")


def verify_release(
    *,
    release_json: Path,
    notes_file: Path,
    tag: str,
    commit: str,
    expected_author: str,
) -> None:
    verify_release_document(
        release_json=release_json,
        notes=notes_file.read_text(encoding="utf-8"),
        tag=tag,
        commit=commit,
        expected_author=expected_author,
    )


def verify_immutable_release_settings(settings_json: Path) -> None:
    settings = read_json(settings_json)
    for field in ("enabled", "enforced_by_owner"):
        if type(settings.get(field)) is not bool or settings[field] is not True:
            fail(f"immutable-release setting {field!r} must be JSON true")


def parse_revision(value: Any, *, label: str) -> datetime:
    match = (
        RULESET_REVISION_PATTERN.fullmatch(value) if isinstance(value, str) else None
    )
    if match is None:
        fail(f"{label} ruleset has an invalid revision timestamp")

    zone = match.group("zone")
    if zone == "Z":
        offset = timedelta()
    else:
        offset_hours = int(zone[1:3])
        offset_minutes = int(zone[4:6])
        if offset_hours > 23 or offset_minutes > 59 or zone == "-00:00":
            fail(f"{label} ruleset has an invalid revision timestamp")
        offset = timedelta(hours=offset_hours, minutes=offset_minutes)
        if zone.startswith("-"):
            offset = -offset

    fraction = (match.group("fraction") or "").ljust(6, "0")
    try:
        revision = datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            microsecond=int(fraction or "0"),
            tzinfo=timezone(offset),
        )
        return revision.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        fail(f"{label} ruleset has an invalid revision timestamp")


def require_tag_scope(ruleset: dict[str, Any], *, label: str) -> None:
    conditions = ruleset.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if (
        ruleset.get("target") != "tag"
        or ruleset.get("enforcement") != "active"
        or not isinstance(ref_name, dict)
        or ref_name.get("include") != [STRICT_TAG_PATTERN]
        or ref_name.get("exclude") != []
    ):
        fail(
            f"{label} ruleset must actively and exclusively cover {STRICT_TAG_PATTERN}"
        )


def rule_types(ruleset: dict[str, Any], *, label: str) -> set[str]:
    rules = ruleset.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        fail(f"{label} ruleset has malformed rules")
    types: set[str] = set()
    for rule in rules:
        rule_type = rule.get("type")
        if not isinstance(rule_type, str):
            fail(f"{label} ruleset has a malformed rule type")
        types.add(rule_type)
    return types


def ruleset_evidence(
    ruleset: dict[str, Any], *, repository: str, expected_name: str
) -> dict[str, Any]:
    if (
        type(ruleset.get("id")) is not int
        or ruleset["id"] <= 0
        or ruleset.get("name") != expected_name
        or ruleset.get("source_type") != "Repository"
        or ruleset.get("source") != repository
        or "bypass_actors" not in ruleset
    ):
        fail(f"ruleset {expected_name!r} has invalid repository identity evidence")
    parse_revision(ruleset.get("updated_at"), label=expected_name)
    return {
        "id": ruleset["id"],
        "name": ruleset["name"],
        "source_type": ruleset["source_type"],
        "source": ruleset["source"],
        "updated_at": ruleset["updated_at"],
        "bypass_actors": ruleset["bypass_actors"],
    }


def verify_attested_rulesets(
    *,
    immutable: dict[str, Any],
    creation: dict[str, Any],
    attestation: dict[str, Any],
    repository: str,
    app_id: int,
) -> None:
    if not repository or repository.count("/") != 1:
        fail("repository must use the OWNER/NAME form")
    if type(app_id) is not int or app_id <= 0:
        fail("automation App ID must be a positive integer")
    if set(attestation) != {"schema", "repository", "app_id", "immutable", "creation"}:
        fail("release policy attestation has unexpected fields")
    if (
        type(attestation.get("schema")) is not int
        or attestation["schema"] != 1
        or attestation.get("repository") != repository
        or type(attestation.get("app_id")) is not int
        or attestation["app_id"] != app_id
    ):
        fail("release policy attestation has conflicting identity")

    complete: dict[str, dict[str, Any]] = {}
    evidence_keys = {
        "id",
        "name",
        "source_type",
        "source",
        "updated_at",
        "bypass_actors",
    }
    for label, live, expected_name in (
        ("immutable", immutable, IMMUTABLE_RULESET_NAME),
        ("creation", creation, CREATION_RULESET_NAME),
    ):
        evidence = attestation.get(label)
        if not isinstance(evidence, dict) or set(evidence) != evidence_keys:
            fail(f"release policy attestation has malformed {label} evidence")
        merged = {**live, "bypass_actors": evidence["bypass_actors"]}
        current = ruleset_evidence(
            merged, repository=repository, expected_name=expected_name
        )
        for field in evidence_keys - {"bypass_actors", "updated_at"}:
            if current[field] != evidence[field]:
                fail(f"live {label} ruleset differs from the owner attestation")
        if parse_revision(current["updated_at"], label=expected_name) != parse_revision(
            evidence["updated_at"], label=expected_name
        ):
            fail(f"live {label} ruleset differs from the owner attestation")
        complete[label] = merged

    require_tag_scope(complete["immutable"], label="immutable release tag")
    if rule_types(complete["immutable"], label="immutable release tag") != {
        "update",
        "deletion",
    }:
        fail("immutable release tag ruleset must block only updates and deletion")
    if complete["immutable"].get("bypass_actors") != []:
        fail("immutable release tag ruleset must have no bypass actors")

    require_tag_scope(complete["creation"], label="release tag creation")
    if rule_types(complete["creation"], label="release tag creation") != {"creation"}:
        fail("release tag creation ruleset must restrict only creation")
    expected_actor = {
        "actor_id": app_id,
        "actor_type": "Integration",
        "bypass_mode": "always",
    }
    if complete["creation"].get("bypass_actors") != [expected_actor]:
        fail("release tag creation ruleset must allow only the automation App")
