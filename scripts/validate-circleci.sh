#!/bin/bash
set -euo pipefail

if ! command -v circleci >/dev/null 2>&1; then
  echo "circleci CLI is required" >&2
  exit 127
fi

python_bin="${PYTHON:-python}"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vexcalibur-orb-circleci.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

packed_orb="$tmp_dir/vexcalibur-orb.yml"
inline_config="$tmp_dir/test-deploy-inline.yml"
processed_setup_config="$tmp_dir/setup-processed.yml"
processed_inline_config="$tmp_dir/test-deploy-processed.yml"

circleci orb pack --skip-update-check src > "$packed_orb"
circleci orb validate --skip-update-check "$packed_orb"
circleci config validate --skip-update-check .circleci/config.yml
circleci config process --skip-update-check .circleci/config.yml \
  > "$processed_setup_config"
circleci config validate --skip-update-check "$processed_setup_config"

"$python_bin" - "$packed_orb" .circleci/config.yml .circleci/test-deploy.yml "$inline_config" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml

packed_orb = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
setup_config = yaml.safe_load(Path(sys.argv[2]).read_text(encoding="utf-8"))
test_deploy = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
inline_config = Path(sys.argv[4])

continue_orb_names = [
    job["orb-tools/continue"]["orb_name"]
    for workflow in setup_config.get("workflows", {}).values()
    for job in workflow.get("jobs", [])
    if isinstance(job, dict) and "orb-tools/continue" in job
]
if len(continue_orb_names) != 1:
    raise SystemExit(f"expected exactly one orb-tools/continue job, found {len(continue_orb_names)}")

test_deploy["orbs"][continue_orb_names[0]] = packed_orb
inline_config.write_text(yaml.safe_dump(test_deploy, sort_keys=False), encoding="utf-8")
PY

circleci config validate --skip-update-check "$inline_config"
circleci config process --skip-update-check "$inline_config" \
  > "$processed_inline_config"
circleci config validate --skip-update-check "$processed_inline_config"

"$python_bin" - "$processed_setup_config" "$processed_inline_config" .circleci/test-deploy.yml <<'PY'
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

PINNED_CIRCLECI_CLI_IMAGE = (
    "circleci/circleci-cli:0.1.38646@sha256:"
    "2a2081377367e051fb247752ac17f753f7675f5d36e334c24da73034848f0926"  # pragma: allowlist secret
)


def load_jobs(path: str) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise SystemExit(f"processed config has no jobs mapping: {path}")
    return jobs


def require_pinned_executor(job_name: str, job: dict[str, Any]) -> None:
    docker = job.get("docker")
    if docker != [{"image": PINNED_CIRCLECI_CLI_IMAGE}]:
        raise SystemExit(
            f"{job_name} did not resolve to the pinned CircleCI CLI image: {docker!r}"
        )


def run_step_names(job: dict[str, Any]) -> list[str]:
    return [
        step["run"].get("name", "")
        for step in job.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("run"), dict)
    ]


setup_jobs = load_jobs(sys.argv[1])
for job_name in ("orb-tools/pack", "orb-tools/continue"):
    require_pinned_executor(job_name, setup_jobs[job_name])

deployment_jobs = load_jobs(sys.argv[2])
for job_name in ("pack-dev", "pack-release"):
    job = deployment_jobs[job_name]
    require_pinned_executor(job_name, job)
    names = run_step_names(job)
    if "Record packed orb SHA-256" not in names:
        raise SystemExit(f"{job_name} did not retain the checksum-recording step")

publish_dev_job = deployment_jobs["publish-dev"]
require_pinned_executor("publish-dev", publish_dev_job)
publish_dev_names = run_step_names(publish_dev_job)
try:
    verify_index = publish_dev_names.index("Verify packed orb SHA-256")
    publish_index = publish_dev_names.index("Publishing Orb Release")
except ValueError as error:
    raise SystemExit("publish-dev is missing an integrity or publish step") from error
if verify_index >= publish_index:
    raise SystemExit("publish-dev publishes before verifying the packed orb")

publish_release_job = deployment_jobs["publish-release"]
require_pinned_executor("publish-release", publish_release_job)
publish_release_names = run_step_names(publish_release_job)
try:
    verify_index = publish_release_names.index("Verify packed orb SHA-256")
    publish_index = publish_release_names.index("Publish or verify production orb")
except ValueError as error:
    raise SystemExit(
        "publish-release is missing an integrity or idempotent publish step"
    ) from error
if verify_index >= publish_index:
    raise SystemExit("publish-release publishes before verifying the packed orb")

test_deploy = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
workflow_jobs = test_deploy.get("workflows", {}).get("test-deploy", {}).get("jobs", [])
if not isinstance(workflow_jobs, list):
    raise SystemExit("test-deploy workflow has no jobs list")

if any("approve-dev-publish" in job for job in workflow_jobs if isinstance(job, dict)):
    raise SystemExit("development publication must not require a manual approval job")

publish_dev = next(
    (
        job["orb-tools/publish"]
        for job in workflow_jobs
        if isinstance(job, dict)
        and isinstance(job.get("orb-tools/publish"), dict)
        and job["orb-tools/publish"].get("name") == "publish-dev"
    ),
    None,
)
if not isinstance(publish_dev, dict):
    raise SystemExit("test-deploy workflow has no publish-dev job")

expected_dependencies = {
    "pack-dev",
    "command-help-test",
    "format-output-test",
    "job-help-test",
}
if set(publish_dev.get("requires", [])) != expected_dependencies:
    raise SystemExit("publish-dev must require every credentialless development check")

if publish_dev.get("context") != "orb-publishing":
    raise SystemExit("publish-dev must use the restricted orb-publishing context")

filters = publish_dev.get("filters", {})
if filters.get("branches", {}).get("only") != "main":
    raise SystemExit("publish-dev must run only from main")
if filters.get("tags", {}).get("ignore") != "/.*/":
    raise SystemExit("publish-dev must not run from tags")

publish_release = next(
    (
        job["publish-production-orb"]
        for job in workflow_jobs
        if isinstance(job, dict)
        and isinstance(job.get("publish-production-orb"), dict)
        and job["publish-production-orb"].get("name") == "publish-release"
    ),
    None,
)
if not isinstance(publish_release, dict):
    raise SystemExit("test-deploy workflow has no idempotent publish-release job")
if publish_release.get("context") != "orb-publishing":
    raise SystemExit("publish-release must use the restricted orb-publishing context")
if set(publish_release.get("requires", [])) != {
    "pack-release",
    "command-help-test",
    "format-output-test",
    "job-help-test",
    "release-source-check",
}:
    raise SystemExit("publish-release must require every credentialless release check")
release_filters = publish_release.get("filters", {})
if release_filters.get("branches", {}).get("ignore") != "/.*/":
    raise SystemExit("publish-release must ignore every branch")
if release_filters.get("tags", {}).get("only") != (
    "/^v[0-9]+\\.[0-9]+\\.[0-9]+$/"
):
    raise SystemExit("publish-release must require an exact production tag")
PY
