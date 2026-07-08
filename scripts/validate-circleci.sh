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

circleci orb pack src > "$packed_orb"
circleci orb validate "$packed_orb"
circleci config validate .circleci/config.yml

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

circleci config validate "$inline_config"
