#!/bin/bash
set -euo pipefail

if [[ -z "${CIRCLE_CONTINUATION_KEY:-}" ]]; then
  echo "CIRCLE_CONTINUATION_KEY is required for dynamic configuration." >&2
  exit 1
fi

temporary_directory="$(mktemp -d)"
# shellcheck disable=SC2329  # Invoked by the EXIT trap.
cleanup() {
  rm -rf -- "${temporary_directory}"
}
trap cleanup EXIT

candidate="${temporary_directory}/orb.yml"
configuration="${temporary_directory}/continue.yml"
payload="${temporary_directory}/continue.json"
response_body="${temporary_directory}/response.json"

ORB_CANDIDATE_PATH="${candidate}" scripts/pack-orb.sh >/dev/null
export ORB_SOURCE="${candidate}"
yq '.orbs.vexcalibur = load(strenv(ORB_SOURCE))' \
  .circleci/test-deploy.yml >"${configuration}"
circleci config validate --skip-update-check "${configuration}"

jq -n \
  --rawfile configuration "${configuration}" \
  '{
    "continuation-key": env.CIRCLE_CONTINUATION_KEY,
    "configuration": $configuration
  }' >"${payload}"

status="$(
  curl \
    --silent \
    --show-error \
    --output "${response_body}" \
    --write-out '%{http_code}' \
    --request POST \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data-binary "@${payload}" \
    --proto '=https' \
    --tlsv1.2 \
    https://circleci.com/api/v2/pipeline/continue
)"
if [[ "${status}" != "200" ]]; then
  echo "CircleCI continuation failed with HTTP ${status}." >&2
  exit 1
fi

echo "CircleCI accepted the validated continuation configuration."
