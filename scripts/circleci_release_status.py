#!/usr/bin/env python3
"""Verify the exact CircleCI pipeline and workflow for an orb release."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import sys
import time
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from circleci_api import (
    CircleCIError,
    CircleCIRequestError,
    Client,
    PROJECT_SLUG,
    fail,
    require_uuid,
)


TAG_PATTERN = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
TERMINAL_STATUSES = {
    "canceled",
    "error",
    "failed",
    "not_run",
    "success",
    "unauthorized",
}
EXPECTED_WORKFLOWS = {"lint-pack", "test-deploy"}
REQUIRED_SETUP_JOBS = {
    "continue",
    "validate-orb",
}
LEGACY_SETUP_JOBS = {
    "orb-tools/continue",
    "orb-tools/lint",
    "orb-tools/pack",
    "orb-tools/review",
    "shellcheck",
}
REQUIRED_RELEASE_JOBS = {
    "command-help-test",
    "format-output-test",
    "job-help-test",
    "pack-release",
    "release-source-check",
}
REQUIRED_DEVELOPMENT_JOBS = {
    "command-help-test",
    "format-output-test",
    "job-help-test",
}
LEGACY_PUBLISH_JOB = "publish-release"
REPOSITORY_URL = "https://github.com/vexcalibur-dev/vexcalibur-orb"


def parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        fail(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(f"{label} is not an ISO 8601 timestamp")
    if parsed.tzinfo is None:
        fail(f"{label} does not include a time zone")
    return parsed


def pipeline_for_tag(
    items: Sequence[dict[str, Any]], *, tag: str, revision: str
) -> dict[str, Any]:
    if TAG_PATTERN.fullmatch(tag) is None:
        fail("release tag must be vMAJOR.MINOR.PATCH without leading zeros")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        fail("release revision must be a full lowercase Git object ID")
    matches: list[dict[str, Any]] = []
    for item in items:
        vcs = item.get("vcs")
        if not isinstance(vcs, dict) or vcs.get("tag") != tag:
            continue
        if item.get("project_slug") != PROJECT_SLUG:
            continue
        try:
            verify_webhook_provenance(item)
        except CircleCIError:
            continue
        if vcs.get("revision") != revision:
            fail("matching CircleCI pipeline has the wrong release revision")
        number = item.get("number")
        if type(number) is not int or number < 1:
            fail("matching CircleCI pipeline has a malformed number")
        require_uuid(item.get("id"), label="matching CircleCI pipeline ID")
        matches.append(item)
    if not matches:
        fail(f"no CircleCI pipeline found for {tag}")
    if len(matches) != 1:
        fail(f"multiple CircleCI webhook pipelines found for {tag} at {revision}")
    return matches[0]


def pipeline_for_branch(
    items: Sequence[dict[str, Any]], *, branch: str, revision: str
) -> dict[str, Any]:
    if branch != "main":
        fail("development publication requires the main branch")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        fail("development revision must be a full lowercase Git object ID")
    matches: list[dict[str, Any]] = []
    for item in items:
        vcs = item.get("vcs")
        if not isinstance(vcs, dict) or vcs.get("branch") != branch:
            continue
        if item.get("project_slug") != PROJECT_SLUG:
            continue
        try:
            verify_webhook_provenance(item)
        except CircleCIError:
            continue
        if vcs.get("revision") != revision:
            continue
        number = item.get("number")
        if type(number) is not int or number < 1:
            fail("matching CircleCI pipeline has a malformed number")
        require_uuid(item.get("id"), label="matching CircleCI pipeline ID")
        matches.append(item)
    if not matches:
        fail(f"no CircleCI pipeline found for {branch} at {revision}")
    if len(matches) != 1:
        fail(f"multiple CircleCI webhook pipelines found for {branch} at {revision}")
    return matches[0]


def verify_webhook_provenance(item: dict[str, Any]) -> None:
    trigger = item.get("trigger")
    vcs = item.get("vcs")
    if not isinstance(trigger, dict) or trigger.get("type") != "webhook":
        fail("matching CircleCI pipeline was not triggered by a VCS webhook")
    if not isinstance(vcs, dict):
        fail("matching CircleCI pipeline has malformed VCS provenance")
    if (
        vcs.get("provider_name") != "GitHub"
        or vcs.get("origin_repository_url") != REPOSITORY_URL
        or vcs.get("target_repository_url") != REPOSITORY_URL
    ):
        fail("matching CircleCI pipeline has the wrong repository provenance")


def latest_workflow_attempts(
    items: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    latest: dict[str, tuple[datetime, str, dict[str, Any]]] = {}
    for item in items:
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not name:
            fail("CircleCI workflow has a malformed name")
        if not isinstance(status, str) or not status:
            fail(f"CircleCI workflow {name!r} has a malformed status")
        workflow_id = require_uuid(
            item.get("id"), label=f"CircleCI workflow {name!r} ID"
        )
        created_at = parse_timestamp(
            item.get("created_at"), label=f"CircleCI workflow {name!r} creation time"
        )
        candidate = (created_at, workflow_id, item)
        if name not in latest or candidate[:2] > latest[name][:2]:
            latest[name] = candidate
    if not latest:
        fail("CircleCI pipeline has no workflows")
    return tuple(latest[name][2] for name in sorted(latest))


def verify_workflow_projection(
    items: Sequence[dict[str, Any]], *, allow_legacy_publisher_denial: bool = False
) -> tuple[str, ...]:
    lines: list[str] = []
    failures: list[str] = []
    workflows = latest_workflow_attempts(items)
    if len(workflows) != len(items):
        fail("CircleCI pipeline contains rerun workflow attempts")
    names = {workflow["name"] for workflow in workflows}
    if names != EXPECTED_WORKFLOWS:
        fail(
            "CircleCI pipeline has unexpected workflow names: "
            + ", ".join(sorted(names))
        )
    for workflow in workflows:
        name = workflow["name"]
        status = workflow["status"]
        workflow_id = workflow["id"]
        lines.append(f"{name}\t{status}\t{workflow_id}")
        legacy_denial = (
            allow_legacy_publisher_denial
            and name == "test-deploy"
            and status == "unauthorized"
        )
        if status != "success" and not legacy_denial:
            failures.append(f"{name}={status} ({workflow_id})")
    if failures:
        fail(
            "latest CircleCI workflow attempts are not successful: "
            + ", ".join(failures)
        )
    return tuple(lines)


def verify_required_jobs(
    items: Sequence[dict[str, Any]],
    *,
    required_jobs: set[str],
    allow_legacy_publisher_denial: bool = False,
) -> tuple[str, ...]:
    observed: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            fail("CircleCI workflow job has a malformed name")
        if name in observed:
            fail(f"CircleCI workflow has duplicate job {name!r}")
        observed[name] = item
    allowed_jobs = set(required_jobs)
    if allow_legacy_publisher_denial:
        allowed_jobs.add(LEGACY_PUBLISH_JOB)
    missing = sorted(allowed_jobs - observed.keys())
    if missing:
        fail("CircleCI workflow is missing jobs: " + ", ".join(missing))
    unexpected = sorted(observed.keys() - allowed_jobs)
    if unexpected:
        fail("CircleCI workflow has unexpected jobs: " + ", ".join(unexpected))
    lines: list[str] = []
    failures: list[str] = []
    for name in sorted(required_jobs):
        item = observed[name]
        job_id = require_uuid(item.get("id"), label=f"CircleCI job {name!r} ID")
        job_status = item.get("status")
        if not isinstance(job_status, str) or not job_status:
            fail(f"CircleCI job {name!r} has a malformed status")
        lines.append(f"job:{name}\t{job_status}\t{job_id}")
        if job_status != "success":
            failures.append(f"{name}={job_status}")
    if failures:
        fail(
            "required CircleCI release jobs are not successful: " + ", ".join(failures)
        )
    if LEGACY_PUBLISH_JOB in observed:
        legacy = observed[LEGACY_PUBLISH_JOB]
        legacy_id = require_uuid(
            legacy.get("id"), label=f"CircleCI job {LEGACY_PUBLISH_JOB!r} ID"
        )
        if not allow_legacy_publisher_denial or legacy.get("status") != "unauthorized":
            fail("legacy CircleCI publisher did not fail with unauthorized status")
        lines.append(f"legacy:{LEGACY_PUBLISH_JOB}\tunauthorized\t{legacy_id}")
    return tuple(lines)


def verify_release_jobs(
    items: Sequence[dict[str, Any]], *, allow_legacy_publisher_denial: bool = False
) -> tuple[str, ...]:
    return verify_required_jobs(
        items,
        required_jobs=REQUIRED_RELEASE_JOBS,
        allow_legacy_publisher_denial=allow_legacy_publisher_denial,
    )


def verify_setup_jobs(
    items: Sequence[dict[str, Any]], *, allow_legacy_setup: bool = False
) -> tuple[str, ...]:
    required_jobs = LEGACY_SETUP_JOBS if allow_legacy_setup else REQUIRED_SETUP_JOBS
    return verify_required_jobs(items, required_jobs=required_jobs)


def verify_development_jobs(items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    return verify_required_jobs(items, required_jobs=REQUIRED_DEVELOPMENT_JOBS)


def is_transient_request_error(error: CircleCIRequestError) -> bool:
    """Return whether a failed CircleCI request is safe to retry."""
    return error.status is None or error.status == 429 or 500 <= error.status <= 599


def wait_for_pipeline(
    client: Client,
    *,
    revision: str,
    tag: str | None,
    branch: str | None,
    timeout: int,
    poll_interval: int,
    allow_legacy_publisher_denial: bool = False,
) -> tuple[str, ...]:
    if (tag is None) == (branch is None):
        fail("exactly one CircleCI tag or branch selector is required")
    if timeout < 1 or poll_interval < 1 or poll_interval > timeout:
        fail("pipeline timeout and poll interval must be positive and ordered")
    deadline = time.monotonic() + timeout
    pipeline_id: str | None = None

    while True:
        try:
            if pipeline_id is None:
                query = {"branch": branch} if branch is not None else None
                pipelines = client.fetch_pages(
                    f"project/{PROJECT_SLUG}/pipeline", query=query
                )
                try:
                    if tag is not None:
                        selected = pipeline_for_tag(
                            pipelines, tag=tag, revision=revision
                        )
                    else:
                        selected = pipeline_for_branch(
                            pipelines,
                            branch=branch or "",
                            revision=revision,
                        )
                except CircleCIError as error:
                    if not str(error).startswith("no CircleCI pipeline found"):
                        raise
                else:
                    pipeline_id = require_uuid(
                        selected.get("id"), label="matching CircleCI pipeline ID"
                    )

            if pipeline_id is not None:
                workflows = client.fetch_pages(f"pipeline/{pipeline_id}/workflow")
                latest = latest_workflow_attempts(workflows) if workflows else ()
                names = {workflow["name"] for workflow in latest}
                if len(latest) != len(workflows):
                    fail("CircleCI pipeline contains rerun workflow attempts")
                unexpected = sorted(names - EXPECTED_WORKFLOWS)
                if unexpected:
                    fail(
                        "CircleCI pipeline has unexpected workflow names: "
                        + ", ".join(unexpected)
                    )
                failures = []
                for workflow in latest:
                    name = workflow["name"]
                    workflow_status = workflow["status"]
                    legacy_denial = (
                        allow_legacy_publisher_denial
                        and name == "test-deploy"
                        and workflow_status == "unauthorized"
                    )
                    if (
                        workflow_status in TERMINAL_STATUSES
                        and workflow_status != "success"
                        and not legacy_denial
                    ):
                        failures.append(f"{name}={workflow_status}")
                if failures:
                    fail(
                        "CircleCI workflow failed before the pipeline projection "
                        "completed: " + ", ".join(failures)
                    )
                if names == EXPECTED_WORKFLOWS and all(
                    workflow["status"] in TERMINAL_STATUSES for workflow in latest
                ):
                    workflow_lines = verify_workflow_projection(
                        workflows,
                        allow_legacy_publisher_denial=(
                            allow_legacy_publisher_denial
                        ),
                    )
                    release_workflow = next(
                        workflow
                        for workflow in latest
                        if workflow["name"] == "test-deploy"
                    )
                    setup_workflow = next(
                        workflow
                        for workflow in latest
                        if workflow["name"] == "lint-pack"
                    )
                    setup_jobs = client.fetch_pages(
                        f"workflow/{setup_workflow['id']}/job"
                    )
                    setup_job_lines = verify_setup_jobs(
                        setup_jobs,
                        allow_legacy_setup=allow_legacy_publisher_denial,
                    )
                    jobs = client.fetch_pages(
                        f"workflow/{release_workflow['id']}/job"
                    )
                    if tag is not None:
                        job_lines = verify_release_jobs(
                            jobs,
                            allow_legacy_publisher_denial=(
                                allow_legacy_publisher_denial
                            ),
                        )
                    else:
                        job_lines = verify_development_jobs(jobs)
                    pipeline_query = {"branch": branch} if branch is not None else None
                    current_pipelines = client.fetch_pages(
                        f"project/{PROJECT_SLUG}/pipeline", query=pipeline_query
                    )
                    if tag is not None:
                        current_pipeline = pipeline_for_tag(
                            current_pipelines, tag=tag, revision=revision
                        )
                    else:
                        current_pipeline = pipeline_for_branch(
                            current_pipelines,
                            branch=branch or "",
                            revision=revision,
                        )
                    if current_pipeline.get("id") != pipeline_id:
                        fail("CircleCI pipeline identity changed during verification")
                    return (
                        f"pipeline\t{pipeline_id}",
                        *workflow_lines,
                        *setup_job_lines,
                        *job_lines,
                    )
        except CircleCIRequestError as error:
            if not is_transient_request_error(error) or time.monotonic() >= deadline:
                raise

        if time.monotonic() >= deadline:
            selector = tag if tag is not None else branch
            fail(f"timed out waiting for CircleCI pipeline {selector} at {revision}")
        time.sleep(poll_interval)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    wait_tag = subparsers.add_parser("wait-tag-pipeline")
    wait_tag.add_argument("tag")
    wait_tag.add_argument("revision")
    wait_tag.add_argument("--timeout", type=int, default=1800)
    wait_tag.add_argument("--poll-interval", type=int, default=10)
    wait_tag.add_argument("--allow-legacy-publisher-denial", action="store_true")
    wait_branch = subparsers.add_parser("wait-main-pipeline")
    wait_branch.add_argument("revision")
    wait_branch.add_argument("--timeout", type=int, default=1800)
    wait_branch.add_argument("--poll-interval", type=int, default=10)
    return result


def main() -> None:
    arguments = parser().parse_args()
    try:
        client = Client()
        if arguments.command == "wait-tag-pipeline":
            print(
                "\n".join(
                    wait_for_pipeline(
                        client,
                        revision=arguments.revision,
                        tag=arguments.tag,
                        branch=None,
                        timeout=arguments.timeout,
                        poll_interval=arguments.poll_interval,
                        allow_legacy_publisher_denial=(
                            arguments.allow_legacy_publisher_denial
                        ),
                    )
                )
            )
        elif arguments.command == "wait-main-pipeline":
            print(
                "\n".join(
                    wait_for_pipeline(
                        client,
                        revision=arguments.revision,
                        tag=None,
                        branch="main",
                        timeout=arguments.timeout,
                        poll_interval=arguments.poll_interval,
                    )
                )
            )
        else:
            raise AssertionError("argparse accepted an unknown command")
    except CircleCIError as error:
        raise SystemExit(f"CircleCI release verification failed: {error}") from error


if __name__ == "__main__":
    main()
