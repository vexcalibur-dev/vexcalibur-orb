# Vexcalibur CircleCI orb

![Vexcalibur wordmark with a sword forming the letter V](docs/assets/vexcalibur-banner.png)

[![CI](https://github.com/vexcalibur-dev/vexcalibur-orb/actions/workflows/ci.yml/badge.svg)](https://github.com/vexcalibur-dev/vexcalibur-orb/actions/workflows/ci.yml)

The Vexcalibur orb runs [Vexcalibur](https://github.com/vexcalibur-dev/vexcalibur) in a CircleCI pipeline. It gives CircleCI users a reusable job, command, and Python executor for Vulnerability Exploitability eXchange (VEX) workflows.

The orb installs an exact Vexcalibur release into a temporary virtual environment for each invocation. It can run any Vexcalibur command. Common uses include generating a VEX document from a software bill of materials (SBOM) and querying an Open Source Vulnerabilities (OSV) service.

## Project status

The orb has not been published to the CircleCI registry. The configuration examples in this repository preview the intended first release. `vexcalibur-dev/vexcalibur@0.1.0` cannot be resolved until that release exists.

[Issue #1](https://github.com/vexcalibur-dev/vexcalibur-orb/issues/1) tracks the remaining account and publishing setup.

The public release will be a community orb. A CircleCI organization administrator must [allow uncertified orb use](https://circleci.com/docs/orbs/use/orb-intro/#orb-designation) before that organization can import it.

The source currently has these defaults:

| Surface | Default |
| --- | --- |
| Vexcalibur package | `vexcalibur==0.3.0` |
| Python image | `cimg/python:3.14` |
| Repository checkout in the reusable job | Enabled |
| Public OSV access | Disabled unless the caller passes `--allow-public-osv` |

For a Vexcalibur integration that is available today, see the [Vexcalibur GitHub Action](https://github.com/vexcalibur-dev/vexcalibur-action).

## Preview the orb interface

After the first registry release, this workflow will install the default Vexcalibur package and print its command help:

```yaml
version: 2.1

orbs:
  vexcalibur: vexcalibur-dev/vexcalibur@0.1.0

workflows:
  inspect-vexcalibur:
    jobs:
      - vexcalibur/run:
          checkout: false
          args: --help
```

The job succeeds when Vexcalibur installs and exits with status `0`.

The orb also includes four workflow templates:

- [Generate and preserve CycloneDX VEX from an SBOM](src/examples/generate_vex_from_sbom.yml)
- [Generate and preserve OpenVEX from local findings](src/examples/generate_openvex.yml)
- [Generate and preserve CSAF 2.0 VEX from local findings](src/examples/generate_csaf.yml)
- [Query public OSV with an approved package inventory](src/examples/query_public_osv.yml)

Every nonempty line in `args` becomes one command-line argument. Write flags and their values on separate lines. The orb does not split a line on spaces or evaluate it as shell code.

Vexcalibur refuses to send package URLs, versions, or SBOM inventory to public OSV unless the command includes `--allow-public-osv`. Use that flag only when the workflow is allowed to share those values with `https://api.osv.dev`.

All three generation examples pass `--offline`, so Vexcalibur reads a checked-out SBOM and local findings instead of querying OSV. The orb still installs Vexcalibur from pip at run time. Each example stores the generated document as an artifact. The OpenVEX findings must produce at least one valid statement.

When `--output` names a CSAF file, Vexcalibur ties its basename to the document tracking ID. The example uses `EXAMPLE-CSAF-VEX-2026-001`, so it writes `artifacts/example-csaf-vex-2026-001.json`. CSAF support is output-only; the orb does not add CSAF input, conversion, signing, or publication.

## Develop the orb

You need Bash, Git, Python 3.10 or newer with `pip` and `venv`, ShellCheck, and the [CircleCI CLI](https://circleci.com/docs/guides/toolkit/local-cli/). Run these commands from the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

The checks should exit with status `0`. The last command packs the orb and validates the packed orb, the setup configuration, and the continuation configuration.

## Documentation

- [Orb interface reference](docs/reference/orb.md) describes every public parameter and the runner's failure behavior.
- [Runtime and trust boundaries](docs/explanation/runtime-and-trust.md) explains installation isolation, network access, and credential handling.
- [Publish the orb](docs/how-to/publish-orb.md) covers maintainer setup, development publication, release, and recovery.
- [Contributing](CONTRIBUTING.md) explains the source layout and pull request checks.
- [Security policy](SECURITY.md) explains private vulnerability reporting and secret handling.
- [Code of conduct](https://github.com/vexcalibur-dev/.github/blob/main/CODE_OF_CONDUCT.md) sets participation expectations.
- [Apache-2.0 license](LICENSE) contains the project license.

Use [GitHub issues](https://github.com/vexcalibur-dev/vexcalibur-orb/issues) for questions and non-sensitive defects. Report vulnerabilities through the private channel in the security policy.
