# Orb Reference

The Vexcalibur orb wraps the Vexcalibur Python package in CircleCI reusable
configuration.

## Commands

### `run_vexcalibur`

Installs Vexcalibur into an isolated virtual environment and runs the installed
`vexcalibur` executable.

| Parameter | Type | Default | Contract |
| --- | --- | --- | --- |
| `package_spec` | string | `vexcalibur==0.1.1` | Package spec passed to isolated `pip install`. Release workflows should use an exact release. |
| `allow_development_package_spec` | boolean | `false` | Allows Git URLs, local paths, or other non-release package specs. Use only for development workflows. |
| `constraints_file` | string | `""` | Absolute path to a pip constraints file passed to `pip install --constraint`. Empty means no constraints file. |
| `args` | string | `--help` | Newline-separated CLI arguments. Each nonblank line becomes one argument. |

## Jobs

### `run`

Runs `run_vexcalibur` in the orb's Python Docker executor.

| Parameter | Type | Default | Contract |
| --- | --- | --- | --- |
| `python_version` | string | `3.14` | Tag for the `cimg/python` Docker image. |
| `checkout` | boolean | `true` | Whether to checkout the repository before running Vexcalibur. |
| `package_spec` | string | `vexcalibur==0.1.1` | Passed to `run_vexcalibur`. |
| `allow_development_package_spec` | boolean | `false` | Passed to `run_vexcalibur`. |
| `constraints_file` | string | `""` | Passed to `run_vexcalibur`. Empty means no constraints file. |
| `args` | string | `--help` | Passed to `run_vexcalibur`. |

## Package Installation

By default, `package_spec` must be an exact stable Vexcalibur release such as
`vexcalibur==0.1.1`. Development package specs require
`allow_development_package_spec: true`.

The orb rejects package specs that begin with a pip option, and it rejects URLs
with embedded credentials. Do not put tokens, passwords, or private package
credentials in `package_spec`, `constraints_file`, or `args`.

`constraints_file` should point at a checked-in constraints file in the caller's
workspace, for example
`/home/circleci/project/.github/vexcalibur-constraints.txt`. The path must be
absolute because Vexcalibur is installed from an isolated temporary virtual
environment. Other CLI arguments keep the CircleCI step working directory, so
repository-relative SBOM and findings paths still resolve after checkout.

## Public OSV Boundary

The orb does not add `--allow-public-osv` for callers. Vexcalibur commands that
query public OSV can send package URLs, versions, or SBOM-derived inventory to
`https://api.osv.dev`. Include `--allow-public-osv` in `args` only when public
OSV data sharing is approved for the workflow.

## Runtime Model

The runner script:

1. Validates `package_spec`.
2. Validates `constraints_file` when one is set.
3. Splits `args` by line.
4. Removes inherited `PYTHON*`, `PIP_*`, and `PIPX_*` environment variables.
5. Creates a temporary virtual environment.
6. Installs Vexcalibur with `python -I -m pip --isolated --no-cache-dir`. When
   `constraints_file` is set, the script passes it with `--constraint`.
7. Runs the installed `vexcalibur` executable with the newline-split arguments.

The temporary virtual environment is removed after the command exits.

## Validation Failures

| Condition | Exit Code | Message |
| --- | --- | --- |
| `package_spec` is missing | `2` | `package_spec is required`. |
| `package_spec` starts with a pip option | `2` | `package_spec must not start with a pip option...`. |
| `package_spec` contains embedded URL credentials | `2` | `package_spec must not contain embedded credentials`. |
| `package_spec` is not an exact stable release and development specs are not allowed | `2` | `package_spec must be an exact Vexcalibur release...`. |
| `constraints_file` is relative | `2` | `constraints_file must be an absolute path...`. |
| `constraints_file` is missing or unreadable | `2` | `constraints_file does not exist or is not readable...`. |
| Runner Python is missing or not executable | `2` | The message names the invalid Python value. |
