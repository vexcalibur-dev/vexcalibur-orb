#!/usr/bin/env python3
"""Verify the exact CircleCI pipeline and workflow for an orb release."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from circleci_api import CircleCIError, Client, PROJECT_SLUG, fail, require_uuid


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
REQUIRED_RELEASE_JOBS = {
    "command-help-test",
    "format-output-test",
    "job-help-test",
    "pack-release",
    "publish-release",
    "release-source-check",
}


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
    matches: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        vcs = item.get("vcs")
        if not isinstance(vcs, dict) or vcs.get("tag") != tag:
            continue
        if item.get("project_slug") != PROJECT_SLUG:
            fail("matching CircleCI pipeline has the wrong project identity")
        if vcs.get("revision") != revision:
            fail("matching CircleCI pipeline has the wrong release revision")
        number = item.get("number")
        if type(number) is not int or number < 1:
            fail("matching CircleCI pipeline has a malformed number")
        require_uuid(item.get("id"), label="matching CircleCI pipeline ID")
        matches.append((number, item))
    if not matches:
        fail(f"no CircleCI pipeline found for {tag}")
    highest = max(number for number, _item in matches)
    newest = [item for number, item in matches if number == highest]
    if len(newest) != 1:
        fail(f"CircleCI returned duplicate pipeline number {highest}")
    return newest[0]


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


def original_workflow_attempt(
    items: Sequence[dict[str, Any]], *, name: str
) -> dict[str, Any]:
    """Return the first workflow attempt, before any SSH-derived reruns."""
    attempts: list[tuple[datetime, str, dict[str, Any]]] = []
    for item in items:
        if item.get("name") != name:
            continue
        workflow_id = require_uuid(
            item.get("id"), label=f"CircleCI workflow {name!r} ID"
        )
        created_at = parse_timestamp(
            item.get("created_at"), label=f"CircleCI workflow {name!r} creation time"
        )
        attempts.append((created_at, workflow_id, item))
    if not attempts:
        fail(f"CircleCI pipeline has no {name!r} workflow")
    return min(attempts, key=lambda attempt: attempt[:2])[2]


def verify_workflow_projection(items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    lines: list[str] = []
    failures: list[str] = []
    workflows = latest_workflow_attempts(items)
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
        if status != "success":
            failures.append(f"{name}={status} ({workflow_id})")
    if failures:
        fail(
            "latest CircleCI workflow attempts are not successful: "
            + ", ".join(failures)
        )
    return tuple(lines)


def verify_release_jobs(items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    observed: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            fail("CircleCI release job has a malformed name")
        if name in observed:
            fail(f"CircleCI release workflow has duplicate job {name!r}")
        observed[name] = item
    missing = sorted(REQUIRED_RELEASE_JOBS - observed.keys())
    if missing:
        fail("CircleCI release workflow is missing jobs: " + ", ".join(missing))
    lines: list[str] = []
    failures: list[str] = []
    for name in sorted(REQUIRED_RELEASE_JOBS):
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
    return tuple(lines)


def wait_for_workflow(
    client: Client,
    *,
    workflow_id: str,
    timeout: int,
    poll_interval: int,
) -> str:
    workflow_id = require_uuid(workflow_id, label="CircleCI workflow ID")
    if timeout < 1 or poll_interval < 1 or poll_interval > timeout:
        fail("workflow timeout and poll interval must be positive and ordered")
    deadline = time.monotonic() + timeout
    while True:
        document = client.fetch_json(f"workflow/{workflow_id}")
        if not isinstance(document, dict) or document.get("id") != workflow_id:
            fail("CircleCI returned a workflow with the wrong identity")
        status = document.get("status")
        if not isinstance(status, str) or not status:
            fail("CircleCI returned a malformed workflow status")
        if status in TERMINAL_STATUSES:
            if status != "success":
                fail(f"CircleCI workflow {workflow_id} finished with status {status}")
            return status
        if time.monotonic() >= deadline:
            fail(f"timed out waiting for CircleCI workflow {workflow_id}")
        time.sleep(poll_interval)


def rerun_workflow(client: Client, *, workflow_id: str) -> str:
    workflow_id = require_uuid(workflow_id, label="CircleCI workflow ID")
    document = client.post_json(
        f"workflow/{workflow_id}/rerun",
        {"from_failed": False},
    )
    if not isinstance(document, dict):
        fail("CircleCI returned a malformed workflow rerun response")
    return require_uuid(document.get("workflow_id"), label="rerun CircleCI workflow ID")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    pipeline = subparsers.add_parser("pipeline-id")
    pipeline.add_argument("tag")
    pipeline.add_argument("revision")
    verify = subparsers.add_parser("verify-pipeline")
    verify.add_argument("pipeline_id")
    wait = subparsers.add_parser("wait-workflow")
    wait.add_argument("workflow_id")
    wait.add_argument("--timeout", type=int, default=1800)
    wait.add_argument("--poll-interval", type=int, default=10)
    rerun = subparsers.add_parser("rerun-workflow")
    rerun.add_argument("workflow_id")
    original = subparsers.add_parser("original-release-workflow-id")
    original.add_argument("pipeline_id")
    return result


def main() -> None:
    arguments = parser().parse_args()
    token = os.environ.get("CIRCLECI_OPERATOR_TOKEN", "")
    try:
        client = Client(token)
        if arguments.command == "pipeline-id":
            pipelines = client.fetch_pages(f"project/{PROJECT_SLUG}/pipeline")
            print(
                pipeline_for_tag(
                    pipelines,
                    tag=arguments.tag,
                    revision=arguments.revision,
                )["id"]
            )
        elif arguments.command == "verify-pipeline":
            pipeline_id = require_uuid(
                arguments.pipeline_id, label="CircleCI pipeline ID"
            )
            workflows = client.fetch_pages(f"pipeline/{pipeline_id}/workflow")
            workflow_lines = verify_workflow_projection(workflows)
            latest = latest_workflow_attempts(workflows)
            release_workflow = next(
                workflow for workflow in latest if workflow["name"] == "test-deploy"
            )
            jobs = client.fetch_pages(f"workflow/{release_workflow['id']}/job")
            job_lines = verify_release_jobs(jobs)
            print("\n".join((*workflow_lines, *job_lines)))
        elif arguments.command == "wait-workflow":
            status = wait_for_workflow(
                client,
                workflow_id=arguments.workflow_id,
                timeout=arguments.timeout,
                poll_interval=arguments.poll_interval,
            )
            print(f"{arguments.workflow_id}\t{status}")
        elif arguments.command == "rerun-workflow":
            print(rerun_workflow(client, workflow_id=arguments.workflow_id))
        elif arguments.command == "original-release-workflow-id":
            pipeline_id = require_uuid(
                arguments.pipeline_id, label="CircleCI pipeline ID"
            )
            workflows = client.fetch_pages(f"pipeline/{pipeline_id}/workflow")
            print(original_workflow_attempt(workflows, name="test-deploy")["id"])
        else:
            raise AssertionError("argparse accepted an unknown command")
    except CircleCIError as error:
        raise SystemExit(f"CircleCI release verification failed: {error}") from error


if __name__ == "__main__":
    main()
