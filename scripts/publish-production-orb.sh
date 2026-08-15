#!/bin/bash
set -euo pipefail

candidate=dist/orb.yml
orb_name=vexcalibur-dev/vexcalibur

if [[ ! -f "${candidate}" || -L "${candidate}" ]]; then
  echo "Packed orb must be a regular, non-symlink file: ${candidate}" >&2
  exit 1
fi
if [[ ! "${CIRCLE_TAG:-}" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "Production publication requires an exact vMAJOR.MINOR.PATCH tag." >&2
  exit 1
fi
if [[ -z "${CIRCLE_TOKEN:-}" ]]; then
  echo "CIRCLE_TOKEN is required for production orb publication." >&2
  exit 1
fi

export CIRCLECI_CLI_TOKEN="${CIRCLE_TOKEN}"
version="${CIRCLE_TAG#v}"
orb_reference="${orb_name}@${version}"
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

registry_matches_candidate() {
  local candidate_digest
  local candidate_with_newline_digest
  local registry_digest

  read -r candidate_digest _ < <(sha256sum -- "${candidate}")
  read -r registry_digest _ < <(sha256sum -- "${registry_source}")
  read -r candidate_with_newline_digest _ < <(
    {
      cat -- "${candidate}"
      printf '\n'
    } | sha256sum
  )
  [[ "${registry_digest}" == "${candidate_digest}" \
    || "${registry_digest}" == "${candidate_with_newline_digest}" ]]
}

require_matching_registry_source() {
  if ! registry_matches_candidate; then
    echo "Registry source for ${orb_reference} differs from the packed release." >&2
    exit 1
  fi
}

if source_registry_orb; then
  require_matching_registry_source
  echo "Verified existing immutable orb ${orb_reference}."
  exit 0
fi

publish_status=0
circleci orb publish --skip-update-check "${candidate}" "${orb_reference}" \
  >"${publish_output}" 2>"${publish_error}" || publish_status=$?

for _attempt in {1..12}; do
  if source_registry_orb; then
    require_matching_registry_source
    if (( publish_status == 0 )); then
      cat -- "${publish_output}"
      echo "Published and verified immutable orb ${orb_reference}."
    else
      echo "The publish response failed, but the exact orb is in the registry."
      echo "Verified immutable orb ${orb_reference}."
    fi
    exit 0
  fi
  sleep 5
done

cat -- "${publish_error}" >&2
cat -- "${source_error}" >&2
echo "Could not publish or verify immutable orb ${orb_reference}." >&2
exit 1
