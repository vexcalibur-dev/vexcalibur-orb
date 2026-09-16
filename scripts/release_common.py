"""Shared models and strict input validation for orb releases."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, NoReturn


REPOSITORY = "vexcalibur-dev/vexcalibur-orb"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
TAG_PATTERN = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)
SHA_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_VERSION_COMPONENT = 999_999
CURRENT_NOTES_FORMAT = "2"
SUPPORTED_NOTES_FORMATS = frozenset({"1", CURRENT_NOTES_FORMAT})


class ReleaseError(RuntimeError):
    """Release state is malformed, stale, or inconsistent."""


def fail(message: str) -> NoReturn:
    """Raise one consistently typed release-state error."""
    raise ReleaseError(message)


@dataclass(frozen=True, order=True)
class Version:
    """A bounded semantic version used only as release metadata."""

    major: int
    minor: int
    patch: int

    @classmethod
    def from_tag(cls, tag: str) -> Version:
        match = TAG_PATTERN.fullmatch(tag)
        if match is None:
            fail(
                f"release tag {tag!r} must be vMAJOR.MINOR.PATCH without leading zeros"
            )
        components = tuple(
            int(match.group(name)) for name in ("major", "minor", "patch")
        )
        if any(component > MAX_VERSION_COMPONENT for component in components):
            fail(
                f"release tag {tag!r} has a component larger than "
                f"{MAX_VERSION_COMPONENT}"
            )
        return cls(*components)

    @property
    def tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def bump(self, kind: str) -> Version:
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        fail(f"unknown release bump {kind!r}")


@dataclass(frozen=True)
class TagState:
    """One existing, direct annotated release tag."""

    version: Version
    object_id: str
    commit: str

    @property
    def tag(self) -> str:
        return self.version.tag


@dataclass(frozen=True)
class TagMetadata:
    """Canonical metadata stored in an automation-authored tag."""

    tag: str
    commit: str
    previous_tag: str
    notes_format: str
    notes_sha256: str


@dataclass(frozen=True)
class AnnotatedTag:
    """Canonical release metadata and the identity that authored its tag."""

    metadata: TagMetadata
    tagger_name: str
    tagger_email: str


@dataclass(frozen=True)
class ReleasePlan:
    """A new release, recovery operation, or intentional skip."""

    operation: str
    tag: str
    previous_tag: str
    bump: str
    commit: str
    notes_format: str
    expected_notes_sha256: str
    expected_tag_graph_sha256: str
    make_latest: bool
    reason: str

    def github_outputs(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "tag": self.tag,
            "previous_tag": self.previous_tag,
            "bump": self.bump,
            "sha": self.commit,
            "notes_format": self.notes_format,
            "expected_notes_sha256": self.expected_notes_sha256,
            "expected_tag_graph_sha256": self.expected_tag_graph_sha256,
            "make_latest": str(self.make_latest).lower(),
            "reason": self.reason,
        }


def require_sha(value: str, *, label: str) -> str:
    if SHA_PATTERN.fullmatch(value) is None:
        fail(f"{label} must be a full lowercase Git object ID")
    return value


def strict_json(data: bytes, *, source: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"{source} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"{source} is not valid UTF-8: {error}")
    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: fail(
                f"{source} contains unsupported JSON constant {value!r}"
            ),
        )
    except json.JSONDecodeError as error:
        fail(f"{source} is not valid JSON: {error}")


def read_json(path: Path) -> dict[str, Any]:
    document = strict_json(path.read_bytes(), source=str(path))
    if not isinstance(document, dict):
        fail(f"{path} must contain a JSON object")
    return document


def emit_github_outputs(values: dict[str, str]) -> None:
    for key, value in values.items():
        if any(character in key or character in value for character in ("\n", "\r")):
            fail(f"GitHub Actions output {key!r} must fit on one line")
        print(f"{key}={value}")
