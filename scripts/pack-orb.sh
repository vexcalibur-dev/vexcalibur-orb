#!/bin/bash
set -euo pipefail

source_directory="${ORB_SOURCE_DIRECTORY:-src}"
candidate="${ORB_CANDIDATE_PATH:-dist/orb.yml}"

if [[ ! -d "${source_directory}" || -L "${source_directory}" ]]; then
  echo "Orb source must be a directory, not a symlink: ${source_directory}" >&2
  exit 1
fi
if [[ "${candidate}" != */* ]]; then
  echo "Packed Orb path must include a parent directory." >&2
  exit 1
fi

candidate_directory="${candidate%/*}"
mkdir -p -- "${candidate_directory}"
temporary_candidate="${candidate}.tmp.$$"
cleanup() {
  rm -f -- "${temporary_candidate}"
}
trap cleanup EXIT

circleci orb pack --skip-update-check "${source_directory}" \
  >"${temporary_candidate}"
circleci orb validate --skip-update-check "${temporary_candidate}"

if [[ ! -s "${temporary_candidate}" || -L "${temporary_candidate}" ]]; then
  echo "CircleCI CLI did not produce a regular, nonempty packed Orb." >&2
  exit 1
fi
mv -f -- "${temporary_candidate}" "${candidate}"
trap - EXIT

read -r digest _ < <(sha256sum -- "${candidate}")
if [[ ! "${digest}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "sha256sum returned an invalid packed Orb digest." >&2
  exit 1
fi
printf 'Packed and validated %s: %s\n' "${candidate}" "${digest}"
