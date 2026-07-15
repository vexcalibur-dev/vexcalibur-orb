#!/usr/bin/env bash
set -euo pipefail

version="${CIRCLECI_CLI_VERSION:-}"
checksums_sha256="${CIRCLECI_CLI_CHECKSUMS_SHA256:-}"
install_dir="${CIRCLECI_CLI_INSTALL_DIR:-}"
release_base_url="https://github.com/CircleCI-Public/circleci-cli/releases/download"
work_dir=""

fail() {
  printf 'CircleCI CLI installation failed: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf "$work_dir"
  fi
}
trap cleanup EXIT

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  fail "CIRCLECI_CLI_VERSION must be a three-part numeric version"
fi

if [[ ! "$checksums_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "CIRCLECI_CLI_CHECKSUMS_SHA256 must be a lowercase SHA-256 digest"
fi

if [[ -z "$install_dir" || "$install_dir" != /* ]]; then
  fail "CIRCLECI_CLI_INSTALL_DIR must be an absolute path"
fi

for required_command in curl install sha256sum tar uname; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    fail "required command is unavailable: $required_command"
  fi
done

case "$(uname -s)" in
  Linux)
    os="linux"
    ;;
  *)
    fail "unsupported operating system: $(uname -s)"
    ;;
esac

case "$(uname -m)" in
  x86_64)
    arch="amd64"
    ;;
  aarch64 | arm64)
    arch="arm64"
    ;;
  *)
    fail "unsupported architecture: $(uname -m)"
    ;;
esac

temp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
if [[ ! -d "$temp_root" || ! -w "$temp_root" ]]; then
  fail "temporary directory is unavailable"
fi
work_dir="$(mktemp -d "${temp_root%/}/circleci-cli-install.XXXXXX")"

checksums_name="circleci-cli_${version}_checksums.txt"
archive_name="circleci-cli_${version}_${os}_${arch}.tar.gz"
release_url="${release_base_url}/v${version}"
checksums_path="$work_dir/$checksums_name"
archive_path="$work_dir/$archive_name"

download() {
  local url="$1"
  local destination="$2"

  curl \
    --fail \
    --location \
    --silent \
    --show-error \
    --retry 3 \
    --retry-all-errors \
    --proto '=https' \
    --proto-redir '=https' \
    --output "$destination" \
    "$url"
}

download "${release_url}/${checksums_name}" "$checksums_path"
if ! printf '%s  %s\n' "$checksums_sha256" "$checksums_path" |
  sha256sum --check --strict --status; then
  fail "checksum manifest did not match its pinned SHA-256 digest"
fi

mapfile -t matching_archive_hashes < <(
  awk -v archive="$archive_name" '$2 == archive { print $1 }' "$checksums_path"
)
if ((${#matching_archive_hashes[@]} != 1)); then
  fail "checksum manifest must contain exactly one entry for $archive_name"
fi
archive_sha256="${matching_archive_hashes[0]}"
if [[ ! "$archive_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "checksum manifest contains an invalid SHA-256 digest for $archive_name"
fi

download "${release_url}/${archive_name}" "$archive_path"
if ! (
  cd "$work_dir"
  printf '%s  %s\n' "$archive_sha256" "$archive_name" |
    sha256sum --check --strict --status
); then
  fail "release archive did not match the upstream checksum manifest"
fi

archive_root="${archive_name%.tar.gz}"
binary_member="${archive_root}/circleci"
if ! archive_members="$(tar -tzf "$archive_path")"; then
  fail "verified release archive could not be listed"
fi
binary_member_count="$(
  awk -v member="$binary_member" '$0 == member { count++ } END { print count + 0 }' \
    <<<"$archive_members"
)"
if [[ "$binary_member_count" != "1" ]]; then
  fail "release archive must contain exactly one $binary_member"
fi

extract_dir="$work_dir/extracted"
mkdir -p "$extract_dir"
tar -xzf "$archive_path" --directory "$extract_dir" "$binary_member"
verified_binary="$extract_dir/$binary_member"
if [[ ! -f "$verified_binary" || ! -x "$verified_binary" ]]; then
  fail "verified release archive did not contain an executable CircleCI CLI"
fi

reported_version="$($verified_binary version)"
if [[ "$reported_version" != "$version" && "$reported_version" != "$version"+* ]]; then
  fail "verified CircleCI CLI did not report version $version"
fi

mkdir -p "$install_dir"
install -m 0755 "$verified_binary" "$install_dir/circleci"
printf 'Installed CircleCI CLI %s at %s\n' "$version" "$install_dir/circleci"
