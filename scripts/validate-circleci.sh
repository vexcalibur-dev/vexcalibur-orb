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

"$python_bin" - "$processed_setup_config" "$processed_inline_config" <<'PY'
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

for job_name in ("publish-dev", "publish-release"):
    job = deployment_jobs[job_name]
    require_pinned_executor(job_name, job)
    names = run_step_names(job)
    try:
        verify_index = names.index("Verify packed orb SHA-256")
        publish_index = names.index("Publishing Orb Release")
    except ValueError as error:
        raise SystemExit(f"{job_name} is missing an integrity or publish step") from error
    if verify_index >= publish_index:
        raise SystemExit(f"{job_name} publishes before verifying the packed orb")
PY
