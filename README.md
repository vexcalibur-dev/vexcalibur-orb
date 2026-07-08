# Vexcalibur CircleCI Orb

![Vexcalibur wordmark and sword logo](docs/assets/vexcalibur-banner.png)

[![CI](https://github.com/vexcalibur-dev/vexcalibur-orb/actions/workflows/ci.yml/badge.svg)](https://github.com/vexcalibur-dev/vexcalibur-orb/actions/workflows/ci.yml)

CircleCI orb for running [Vexcalibur](https://github.com/vexcalibur-dev/vexcalibur)
in SBOM and VEX workflows.

This repository is ready for orb source review and GitHub-side validation. The
CircleCI registry publication path still needs a `vexcalibur-dev` CircleCI
namespace, a project connected to this repository, and an `orb-publishing`
context with a CircleCI token.

## Usage After First Registry Release

These examples work after the orb has been published as
`vexcalibur-dev/vexcalibur@0.1.0` in the CircleCI registry.

The default package spec is the current Vexcalibur release used by the GitHub
Action compatibility table: `vexcalibur==0.1.1`.

Supply-chain-sensitive workflows can pass `constraints_file` with an absolute
path to a checked-in pip constraints file. The orb rejects credentialed package
specs and pip option-looking package specs.

```yaml
version: 2.1

orbs:
  vexcalibur: vexcalibur-dev/vexcalibur@0.1.0

workflows:
  vexcalibur:
    jobs:
      - vexcalibur/run:
          checkout: false
          constraints_file: /home/circleci/project/.github/vexcalibur-constraints.txt
          args: --help
```

OSV-backed commands can send package URLs, versions, or SBOM-derived inventory
to public OSV. Pass `--allow-public-osv` only when that data sharing is approved
for the workflow.

```yaml
version: 2.1

orbs:
  vexcalibur: vexcalibur-dev/vexcalibur@0.1.0

workflows:
  query-public-osv:
    jobs:
      - vexcalibur/run:
          checkout: false
          args: |
            query-osv
            --allow-public-osv
            --
            pkg:pypi/django@1.2
```

## Development

Prerequisites:

- Python with `pip`
- ShellCheck
- detect-secrets
- CircleCI CLI for `scripts/validate-circleci.sh`

Run local checks:

```bash
python -m pip install -r requirements-dev.txt
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

GitHub CI runs syntax, YAML/JSON, CircleCI config, shell, secret, and unit-test
checks. CircleCI publishing requires the project setup described in
[Orb publishing setup](docs/how-to/publish-orb.md).

## Project Links

- [Orb reference](docs/reference/orb.md)
- [Publish the orb](docs/how-to/publish-orb.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [License](LICENSE)
