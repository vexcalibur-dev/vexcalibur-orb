#!/bin/bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/orb-registry-source.sh
source "${script_directory}/orb-registry-source.sh"

candidate="${ORB_CANDIDATE_PATH:-dist/orb.yml}"
orb_name=vexcalibur-dev/vexcalibur
reference="${ORB_DEVELOPMENT_REFERENCE:-}"

if [[ ! -f "${candidate}" || -L "${candidate}" ]]; then
  echo "Packed orb must be a regular, non-symlink file: ${candidate}" >&2
  exit 1
fi
if [[ ! "${reference}" =~ ^dev:([0-9a-f]{40}|alpha)$ ]]; then
  echo "Development reference must be dev:<full-commit-sha> or dev:alpha." >&2
  exit 1
fi
if [[ -z "${CIRCLE_TOKEN:-}" ]]; then
  echo "CIRCLE_TOKEN is required for development orb publication." >&2
  exit 1
fi

export CIRCLECI_CLI_TOKEN="${CIRCLE_TOKEN}"
orb_reference="${orb_name}@${reference}"
temporary_directory="$(mktemp -d)"
registry_source="${temporary_directory}/registry.yml"
source_error="${temporary_directory}/source.err"
publish_output="${temporary_directory}/publish.out"
publish_error="${temporary_directory}/publish.err"
# shellcheck disable=SC2329  # Invoked by the EXIT trap.
cleanup() {
  rm -rf -- "${temporary_directory}"
}
trap cleanup EXIT

source_registry_orb() {
  circleci orb source --skip-update-check "${orb_reference}" \
    >"${registry_source}" 2>"${source_error}"
}

publish_status=0
circleci orb publish --skip-update-check "${candidate}" "${orb_reference}" \
  >"${publish_output}" 2>"${publish_error}" || publish_status=$?

for attempt in {1..13}; do
  if source_registry_orb \
    && registry_matches_candidate "${candidate}" "${registry_source}"; then
    if (( publish_status == 0 )); then
      cat -- "${publish_output}"
      echo "Published and verified development orb ${orb_reference}."
    else
      echo "The publish response failed, but the exact orb is in the registry."
      echo "Verified development orb ${orb_reference}."
    fi
    exit 0
  fi
  if (( attempt < 13 )); then
    sleep 5
  fi
done

cat -- "${publish_error}" >&2
cat -- "${source_error}" >&2
echo "Could not publish or verify development orb ${orb_reference}." >&2
exit 1
