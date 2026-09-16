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

filters = {
    "branches": {"ignore": "release-coordination"},
    "tags": {"only": "/.*/"},
}
expected_setup_jobs = [
    {"validate-orb": {"filters": filters}},
    {
        "continue": {
            "requires": ["validate-orb"],
            "filters": filters,
        }
    },
]
if (
    setup_config.get("workflows", {}).get("lint-pack", {}).get("jobs")
    != expected_setup_jobs
):
    raise SystemExit("setup workflow does not have the exact validation handoff")
if test_deploy.get("orbs") != {"vexcalibur": {}}:
    raise SystemExit("continuation config must contain one local vexcalibur Orb")

test_deploy["orbs"]["vexcalibur"] = packed_orb
inline_config.write_text(yaml.safe_dump(test_deploy, sort_keys=False), encoding="utf-8")
PY

circleci config validate --skip-update-check "$inline_config"
circleci config process --skip-update-check "$inline_config" \
  > "$processed_inline_config"
circleci config validate --skip-update-check "$processed_inline_config"

"$python_bin" - \
  "$processed_setup_config" \
  "$processed_inline_config" \
  .circleci/config.yml \
  .circleci/test-deploy.yml <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, "scripts")

from circleci_config_policy import (  # noqa: E402
    CircleCIConfigPolicyError,
    reject_publishing_capabilities,
)

PINNED_CIRCLECI_CLI_IMAGE = (
    "circleci/circleci-cli:0.1.38646@sha256:"
    "2a2081377367e051fb247752ac17f753f7675f5d36e334c24da73034848f0926"  # pragma: allowlist secret
)


def load_document(path: str) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit(f"CircleCI config is not a mapping: {path}")
    return document


def load_jobs(path: str) -> dict[str, dict[str, Any]]:
    document = load_document(path)
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


def require_digest_pinned_images(jobs: dict[str, dict[str, Any]]) -> None:
    for job_name, job in jobs.items():
        docker = job.get("docker")
        if not isinstance(docker, list) or not docker:
            raise SystemExit(f"{job_name} does not use a Docker executor")
        for image in docker:
            reference = image.get("image") if isinstance(image, dict) else None
            if not isinstance(reference, str) or re.search(
                r"@sha256:[0-9a-f]{64}$", reference
            ) is None:
                raise SystemExit(
                    f"{job_name} uses an image without a digest: {reference!r}"
                )


setup_jobs = load_jobs(sys.argv[1])
require_digest_pinned_images(setup_jobs)
for job_name in ("validate-orb", "continue"):
    require_pinned_executor(job_name, setup_jobs[job_name])

deployment_jobs = load_jobs(sys.argv[2])
require_digest_pinned_images(deployment_jobs)
pack_release = deployment_jobs["pack-release"]
require_pinned_executor("pack-release", pack_release)

for path in sys.argv[1:]:
    try:
        reject_publishing_capabilities(load_document(path), path=path)
    except CircleCIConfigPolicyError as error:
        raise SystemExit(str(error)) from error

test_deploy = yaml.safe_load(Path(sys.argv[4]).read_text(encoding="utf-8"))
workflow_jobs = test_deploy.get("workflows", {}).get("test-deploy", {}).get("jobs", [])
if not isinstance(workflow_jobs, list):
    raise SystemExit("test-deploy workflow has no jobs list")
pack_invocation = next(
    (
        job["pack-release"]
        for job in workflow_jobs
        if isinstance(job, dict)
        and isinstance(job.get("pack-release"), dict)
    ),
    None,
)
if not isinstance(pack_invocation, dict):
    raise SystemExit("test-deploy workflow has no release pack job")
release_filters = pack_invocation.get("filters", {})
if release_filters.get("branches", {}).get("ignore") != "/.*/":
    raise SystemExit("pack-release must ignore every branch")
if release_filters.get("tags", {}).get("only") != (
    "/^v[0-9]+\\.[0-9]+\\.[0-9]+$/"
):
    raise SystemExit("pack-release must require an exact production tag")
PY
