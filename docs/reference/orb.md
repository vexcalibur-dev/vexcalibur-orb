# Orb interface reference

The Vexcalibur orb provides one command, one job, one executor, and three examples. It installs the selected Vexcalibur Python package at run time and invokes the package's `vexcalibur` executable.

The orb has not been published. References to `vexcalibur-dev/vexcalibur@0.1.0` in source examples describe the intended first registry release, not an available version.

The release will be a community orb. CircleCI organizations must [allow uncertified orb use](https://circleci.com/docs/orbs/use/orb-intro/#orb-designation) before importing it.

## Compatibility and defaults

| Item | Current value |
| --- | --- |
| CircleCI configuration version | `2.1` |
| Default Vexcalibur package | `vexcalibur==0.2.0` |
| Default executor image | `cimg/python:3.14` |
| Intended first orb version | `0.1.0` |
| VEX formats in generation examples | CycloneDX 1.6 VEX JSON and OpenVEX 0.2.0 JSON |
| Registry home | `https://github.com/vexcalibur-dev/vexcalibur` |
| Orb source | `https://github.com/vexcalibur-dev/vexcalibur-orb` |

The Python image uses a minor-version tag, so CircleCI can resolve a newer `3.14.x` image over time. Set `python_version` on the job or `tag` on the executor when a workflow needs a specific image tag.

## Command: `run_vexcalibur`

`run_vexcalibur` installs Vexcalibur in a temporary virtual environment and runs its CLI in the current CircleCI working directory.

### Parameters

| Parameter | Type | Default | Contract |
| --- | --- | --- | --- |
| `package_spec` | string | `vexcalibur==0.2.0` | Package requirement passed as one operand to `pip install`. Without the development opt-in, it must name an exact stable Vexcalibur release. |
| `allow_development_package_spec` | boolean | `false` | Skips the exact-release check when `true`. The leading-option and credentialed-URL checks still apply. |
| `constraints_file` | string | `""` | Readable absolute path to a pip constraints file. An empty value applies no constraints. |
| `args` | string | `--help` | Vexcalibur arguments. Each nonempty line becomes one literal argument. |

### Argument encoding

The command does not parse `args` as a shell command. It removes empty lines, removes a trailing carriage return from each line, and passes every remaining line as one argument. A line that contains only spaces is still an argument.

This value passes four arguments:

```yaml
args: |
  generate
  sbom/cyclonedx.json
  --output
  artifacts/vex.json
```

This value passes one argument and is not equivalent:

```yaml
args: --output artifacts/vex.json
```

Spaces within a line remain part of that argument. This makes a path containing spaces representable without shell quoting.

### Package requirements

Without `allow_development_package_spec`, `package_spec` must match this form:

```text
vexcalibur==MAJOR.MINOR[.PATCH][.postNUMBER]
```

Pre-releases, ranges, local paths, Git references, and direct URLs fail the default check. Set `allow_development_package_spec: true` only in a development workflow that deliberately needs one of those forms.

The command always rejects:

- An empty package spec
- A package spec whose first character is `-`
- A package spec that starts with a URL containing user information before the authority's `@` separator

These checks prevent common unsafe inputs. They don't recognize every way a requirement or constraints file could refer to credentials. Secrets remain prohibited in all orb parameters.

When set, `constraints_file` must be absolute, readable, and a regular file. A checked-in file commonly resolves under `/home/circleci/project`; for example:

```yaml
constraints_file: /home/circleci/project/.github/vexcalibur-constraints.txt
```

The constraints file controls dependency resolution but doesn't replace `package_spec`.

### Execution sequence

The command performs these operations in order:

1. Validate `package_spec` and `constraints_file`.
2. Convert `args` into an argument array.
3. Remove inherited environment variables whose names start with `PYTHON`, `PIP_`, or `PIPX_`.
4. Create a temporary working directory and Python virtual environment.
5. Run the virtual environment's pip as `python -I -m pip --isolated --no-cache-dir install`, with the constraints file when one is set.
6. Locate the installed `vexcalibur` executable.
7. Run Vexcalibur with the argument array.
8. Remove the temporary working directory when the command exits.

The virtual environment isolates Python packages; it is not a network sandbox. Pip uses its default package index unless an allowed development input or constraints directive names another source. Vexcalibur can reach services selected by its CLI arguments.

The command keeps the CircleCI working directory. Repository-relative input and output paths still resolve after checkout.

`VEXCALIBUR_ORB_PYTHON` selects the Python executable inside the runner, but it is an internal test hook rather than a supported orb parameter. Public workflows should select Python with the job's `python_version` or the executor's `tag`.

### Exit behavior

Input validation failures exit with status `2` before package installation.

| Condition | Standard error |
| --- | --- |
| Missing package spec | `package_spec is required` |
| Leading pip option | `package_spec must not start with a pip option` |
| Credential-bearing URL at the start of the spec | `package_spec must not contain embedded credentials` |
| Non-release spec without the development opt-in | `package_spec must be an exact Vexcalibur release such as vexcalibur==0.2.0` followed by opt-in guidance |
| Relative constraints path | `constraints_file must be an absolute path: PATH` |
| Missing, non-file, or unreadable constraints path | `constraints_file does not exist or is not readable: PATH` |
| Missing or non-executable Python | `VEXCALIBUR_ORB_PYTHON is not executable: VALUE` |

If installation fails, the command returns pip's nonzero status. If Vexcalibur runs, the command returns Vexcalibur's status. Failure to find the executable after installation returns `127`. Cleanup runs for both success and failure.

## Job: `run`

`run` optionally checks out the project, then calls `run_vexcalibur` in the orb's Python executor.

| Parameter | Type | Default | Contract |
| --- | --- | --- | --- |
| `python_version` | string | `3.14` | Image tag passed to the `python` executor. |
| `checkout` | boolean | `true` | Runs the CircleCI `checkout` step before Vexcalibur when `true`. |
| `package_spec` | string | `vexcalibur==0.2.0` | Passed to the command unchanged. |
| `allow_development_package_spec` | boolean | `false` | Passed to the command unchanged. |
| `constraints_file` | string | `""` | Passed to the command unchanged. |
| `args` | string | `--help` | Passed to the command unchanged. |

Use the job for a workflow that needs only checkout and one Vexcalibur invocation. Use the command inside a custom job when the workflow must create directories, attach a workspace, store the VEX file, or run later steps.

## Executor: `python`

The `python` executor runs one Docker image.

| Parameter | Type | Default | Contract |
| --- | --- | --- | --- |
| `tag` | string | `3.14` | Appended to `cimg/python:` as the Docker image tag. |

For example, `tag: 3.14.1` selects `cimg/python:3.14.1`.

## Examples

The orb source contains examples that CircleCI can display with a registry release:

| Example | Source | Result |
| --- | --- | --- |
| `generate_vex_from_sbom` | [`src/examples/generate_vex_from_sbom.yml`](../../src/examples/generate_vex_from_sbom.yml) | Requires the caller to provide the shown local SBOM and findings paths. It writes deterministic CycloneDX VEX, stores it as a CircleCI artifact, and doesn't query OSV. |
| `generate_openvex` | [`src/examples/generate_openvex.yml`](../../src/examples/generate_openvex.yml) | Requires the caller to provide the shown local SBOM and findings paths. It writes OpenVEX 0.2.0 JSON to `artifacts/openvex.json`, stores it as a CircleCI artifact, and doesn't query OSV. |
| `query_public_osv` | [`src/examples/query_public_osv.yml`](../../src/examples/query_public_osv.yml) | Queries public OSV for two intentionally public package URLs. It sends those URLs and versions to `https://api.osv.dev`. |

All examples reference the intended `0.1.0` orb release and won't resolve before publication.

The OpenVEX example requires an author and at least one local finding. This minimal `security/openvex-findings.json` produces one `under_investigation` statement for the matching SBOM component:

```json
{
  "findings": [
    {
      "id": "CVE-2099-0001",
      "component_ref": "component:example-library",
      "source_name": "Example security review",
      "source_url": "https://security.example.test/vulnerabilities/CVE-2099-0001",
      "modified": "2099-01-01T00:00:00Z",
      "analysis_state": "in_triage",
      "analysis_detail": "The product security team is reviewing impact."
    }
  ]
}
```

The SBOM must contain a component whose `bom-ref` is `component:example-library`. That component also needs a versioned package URL. Other OpenVEX states require the evidence fields documented in the [Vexcalibur local findings reference](https://vexcalibur-dev.github.io/vexcalibur/reference/local-findings.html).

## Public OSV boundary

The orb never adds `--allow-public-osv`. Vexcalibur commands that use public OSV can send package URLs, versions, or SBOM-derived inventory to `https://api.osv.dev`. A workflow must include that flag only after its owner approves sharing the submitted inventory.

Private OSV-compatible endpoints and GitHub SBOM input are Vexcalibur interfaces, not extra orb parameters. Pass their CLI options through `args` and follow the [Vexcalibur CLI documentation](https://vexcalibur-dev.github.io/vexcalibur/reference/cli.html).

The [runtime and trust-boundary explanation](../explanation/runtime-and-trust.md) covers package installation, environment handling, and outbound connections.
