#!/bin/bash
set -euo pipefail

package_spec="${VEXCALIBUR_ORB_PACKAGE_SPEC:-}"
allow_development_package_spec="${VEXCALIBUR_ORB_ALLOW_DEVELOPMENT_PACKAGE_SPEC:-false}"
constraints_file="${VEXCALIBUR_ORB_CONSTRAINTS_FILE:-}"
if [[ -v VEXCALIBUR_ORB_ARGS ]]; then
  raw_cli_args="$VEXCALIBUR_ORB_ARGS"
else
  raw_cli_args="--help"
fi
python_bin="${VEXCALIBUR_ORB_PYTHON:-python3}"
cli_args=()
work_dir=""
pip_cache_dir=""
venv_dir=""
venv_python=""
vexcalibur_bin=""

export -n package_spec allow_development_package_spec constraints_file raw_cli_args python_bin
export -n work_dir pip_cache_dir venv_dir venv_python vexcalibur_bin
unset VEXCALIBUR_ORB_ARGS
unset VEXCALIBUR_ORB_ALLOW_DEVELOPMENT_PACKAGE_SPEC
unset VEXCALIBUR_ORB_CONSTRAINTS_FILE
unset VEXCALIBUR_ORB_PACKAGE_SPEC
unset VEXCALIBUR_ORB_PYTHON

cleanup() {
  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf "$work_dir"
  fi
}
trap cleanup EXIT

is_true() {
  [[ "$1" == "true" ]]
}

unset_python_tool_env() {
  local env_name
  while IFS= read -r env_name; do
    case "$env_name" in
      PYTHON*|PIP_*|PIPX_*)
        unset "$env_name"
        ;;
    esac
  done < <(compgen -e)
}

validate_package_spec() {
  if [[ -z "$package_spec" ]]; then
    echo "package_spec is required" >&2
    exit 2
  fi

  if [[ "$package_spec" == -* ]]; then
    echo "package_spec must not start with a pip option" >&2
    exit 2
  fi

  if [[ "$package_spec" =~ ^[^:/?#]+://[^/[:space:]]+@ ]]; then
    echo "package_spec must not contain embedded credentials" >&2
    exit 2
  fi

  if is_true "$allow_development_package_spec"; then
    return
  fi

  if [[ "$package_spec" =~ ^vexcalibur==[0-9]+(\.[0-9]+){1,2}(\.post[0-9]+)?$ ]]; then
    return
  fi

  echo "package_spec must be an exact Vexcalibur release such as vexcalibur==0.3.1" >&2
  echo "set allow_development_package_spec to true only for development workflows" >&2
  exit 2
}

validate_constraints_file() {
  if [[ -z "$constraints_file" ]]; then
    return
  fi

  if [[ "$constraints_file" != /* ]]; then
    echo "constraints_file must be an absolute path: $constraints_file" >&2
    exit 2
  fi

  if [[ ! -f "$constraints_file" || ! -r "$constraints_file" ]]; then
    echo "constraints_file does not exist or is not readable: $constraints_file" >&2
    exit 2
  fi
}

read_cli_args() {
  local arg_line
  cli_args=()
  while IFS= read -r arg_line || [[ -n "$arg_line" ]]; do
    arg_line="${arg_line%$'\r'}"
    if [[ -n "$arg_line" ]]; then
      cli_args+=("$arg_line")
    fi
  done <<<"$raw_cli_args"
}

configure_venv_paths() {
  local temp_root
  temp_root="${TMPDIR:-/tmp}"

  if [[ "$python_bin" == */* ]]; then
    if [[ ! -x "$python_bin" ]]; then
      echo "VEXCALIBUR_ORB_PYTHON is not executable: $python_bin" >&2
      exit 2
    fi
  elif ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "VEXCALIBUR_ORB_PYTHON is not executable: $python_bin" >&2
    exit 2
  fi

  work_dir="$(mktemp -d "${temp_root%/}/vexcalibur-orb.XXXXXX")"
  pip_cache_dir="$work_dir/pip-cache"
  venv_dir="$work_dir/venv"
  mkdir -p "$pip_cache_dir"
}

resolve_vexcalibur_bin() {
  if [[ -x "$venv_dir/bin/vexcalibur" ]]; then
    printf '%s\n' "$venv_dir/bin/vexcalibur"
    return
  fi

  echo "vexcalibur executable was not found after installation" >&2
  exit 127
}

validate_package_spec
validate_constraints_file
read_cli_args

unset_python_tool_env
configure_venv_paths
"$python_bin" -I -m venv "$venv_dir"
venv_python="$venv_dir/bin/python"
pip_install_args=(--isolated --no-cache-dir install)
if [[ -n "$constraints_file" ]]; then
  pip_install_args+=(--constraint "$constraints_file")
fi
pip_install_args+=("$package_spec")
PIP_CONFIG_FILE=/dev/null PIP_CACHE_DIR="$pip_cache_dir" "$venv_python" -I -m pip "${pip_install_args[@]}"

vexcalibur_bin="$(resolve_vexcalibur_bin)"
"$vexcalibur_bin" "${cli_args[@]}"
