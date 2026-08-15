#!/usr/bin/env python3
"""Verify the live CircleCI release-context restrictions without mutation."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from circleci_api import CircleCIError, Client, require_uuid


ORGANIZATION_NAME = "vexcalibur-dev"
ORGANIZATION_SLUG = "gh/vexcalibur-dev"
ORGANIZATION_VCS = "github"
CONTEXT_NAME = "orb-publishing"
PROJECT_NAME = "vexcalibur-orb"
PROJECT_SLUG = f"{ORGANIZATION_SLUG}/{PROJECT_NAME}"
EXPECTED_EXPRESSION = (
    'pipeline.project.type == "github" and pipeline.project.git_url == '
    '"https://github.com/vexcalibur-dev/vexcalibur-orb" and '
    '(pipeline.git.branch == "main" or pipeline.git.tag matches '
    r"/^v[0-9]+\.[0-9]+\.[0-9]+$/) and not job.ssh.enabled and not "
    '(pipeline.config_source starts-with "api")'
)


class VerificationError(RuntimeError):
    """The live CircleCI release context does not match its contract."""


def fail(message: str) -> NoReturn:
    raise VerificationError(message)


def one_named(items: list[dict[str, Any]], *, name: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        fail(f"expected exactly one {label} named {name!r}")
    return matches[0]


def select_organization(items: list[dict[str, Any]]) -> dict[str, Any]:
    organization = one_named(
        items,
        name=ORGANIZATION_NAME,
        label="organization membership",
    )
    vcs_values = [
        organization[field]
        for field in ("vcs_type", "vcs-type")
        if field in organization
    ]
    if (
        organization.get("slug") != ORGANIZATION_SLUG
        or not vcs_values
        or any(value != ORGANIZATION_VCS for value in vcs_values)
    ):
        fail("CircleCI organization membership has the wrong VCS identity")
    return organization


def verify_restrictions(
    items: list[dict[str, Any]], *, context_id: str, project_id: str
) -> None:
    require_uuid(project_id, label="expected project ID")
    for item in items:
        if item.get("context_id") != context_id:
            fail("CircleCI returned a restriction for another context")

    expressions = [
        item for item in items if item.get("restriction_type") == "expression"
    ]
    if (
        len(expressions) != 1
        or expressions[0].get("restriction_value") != EXPECTED_EXPRESSION
    ):
        fail("orb-publishing does not have the exact release expression restriction")

    projects = [item for item in items if item.get("restriction_type") == "project"]
    if len(projects) != 1:
        fail("orb-publishing must have exactly one project restriction")
    project = one_named(projects, name=PROJECT_NAME, label="project restriction")
    restricted_project_id = require_uuid(
        project.get("project_id"), label="restricted project ID"
    )
    if (
        restricted_project_id != project_id
        or project.get("restriction_value") != project_id
    ):
        fail("orb-publishing has conflicting project restriction IDs")

    groups = [item for item in items if item.get("restriction_type") == "group"]
    if groups:
        fail("orb-publishing must not have security-group authorization grants")

    allowed_types = {"expression", "project"}
    unknown = sorted(
        {
            str(item.get("restriction_type"))
            for item in items
            if item.get("restriction_type") not in allowed_types
        }
    )
    if unknown:
        fail(f"orb-publishing has unsupported restriction types: {', '.join(unknown)}")


def verify(token: str) -> tuple[str, str]:
    client = Client(token)
    collaborations = client.fetch_json("me/collaborations")
    if not isinstance(collaborations, list) or any(
        not isinstance(item, dict) for item in collaborations
    ):
        fail("CircleCI returned malformed organization memberships")
    organization = select_organization(collaborations)
    organization_id = require_uuid(organization.get("id"), label="organization ID")
    project = client.fetch_json(f"project/{PROJECT_SLUG}")
    if not isinstance(project, dict) or project.get("slug") != PROJECT_SLUG:
        fail("CircleCI returned the wrong project identity")
    project_id = require_uuid(project.get("id"), label="project ID")
    contexts = client.fetch_pages(f"context?owner-id={organization_id}")
    context = one_named(contexts, name=CONTEXT_NAME, label="context")
    context_id = require_uuid(context.get("id"), label="orb-publishing context ID")
    restrictions = client.fetch_pages(f"context/{context_id}/restrictions")
    verify_restrictions(
        restrictions,
        context_id=context_id,
        project_id=project_id,
    )
    return organization_id, context_id


def main() -> None:
    token = os.environ.get("CIRCLECI_OPERATOR_TOKEN", "")
    if not token:
        raise SystemExit("CIRCLECI_OPERATOR_TOKEN is required")
    try:
        organization_id, context_id = verify(token)
    except (CircleCIError, VerificationError) as error:
        raise SystemExit(
            f"CircleCI release-context verification failed: {error}"
        ) from error
    print(
        "verified orb-publishing project and expression restrictions "
        f"for organization {organization_id}, context {context_id}"
    )


if __name__ == "__main__":
    main()
