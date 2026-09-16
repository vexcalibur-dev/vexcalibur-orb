#!/bin/bash

registry_matches_candidate() {
  local candidate="$1"
  local registry_source="$2"
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
