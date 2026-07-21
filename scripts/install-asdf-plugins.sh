#!/usr/bin/env bash
set -euo pipefail

asdf_data_dir="${ASDF_DATA_DIR:-${HOME}/.asdf}"

install_plugin() {
  local name="$1"
  local url="$2"
  local revision="$3"
  local plugin_dir="${asdf_data_dir}/plugins/${name}"

  if [[ -d "${plugin_dir}" ]]; then
    if [[ "$(git -C "${plugin_dir}" remote get-url origin)" != "${url}" ]]; then
      printf 'Plugin %s has an unexpected origin.\n' "${name}" >&2
      exit 1
    fi
  else
    asdf plugin add "${name}" "${url}"
  fi

  git -C "${plugin_dir}" fetch --depth=1 origin "${revision}"
  git -C "${plugin_dir}" checkout --detach "${revision}"
}

install_plugin python https://github.com/danhper/asdf-python.git \
  abc2a03863e4d569b4f9de0d0efc1a88d96c2c12
install_plugin pre-commit https://github.com/jonathanmorley/asdf-pre-commit.git \
  26bfc420d6009f700514d6218cc28947d1c3aad9
install_plugin shellcheck https://github.com/luizm/asdf-shellcheck.git \
  8b954a95d44b8d8a6c6cd5a5a52ed643b7bb52e3
install_plugin circleci-cli https://github.com/ucpr/asdf-circleci-cli.git \
  3a52b5e6ab0e986e1938eef16c06f073d7405862
