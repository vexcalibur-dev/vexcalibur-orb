# Contributing to the Vexcalibur CircleCI orb

Contributions should keep the orb source, its examples, and its reference documentation in agreement. The packed orb is generated from `src/`; don't edit or commit `orb.yml` or files under `dist/`.

Use [GitHub issues](https://github.com/vexcalibur-dev/vexcalibur-orb/issues) to discuss a substantial behavior change before you implement it. Report security defects through the [private security process](SECURITY.md), not through a public issue.

## Prepare a development environment

You need:

- Bash
- Git
- A version manager that reads [`.tool-versions`](.tool-versions), such as mise or asdf

The tool file pins Python, pre-commit, ShellCheck, and the
[CircleCI CLI](https://circleci.com/docs/guides/toolkit/local-cli/). Install
those tools before creating the development environment:

```bash
mise trust
mise install
pre-commit install
```

To use asdf instead, install the repository-pinned plugins, then install the
versions in `.tool-versions`:

```bash
bash scripts/install-asdf-plugins.sh
asdf install
pre-commit install
```

The development requirements install `detect-secrets` and PyYAML. From the
repository root, create an isolated environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Check the CircleCI CLI before running the full validation suite:

```bash
circleci version
```

GitHub CI verifies the same CircleCI CLI release before configuration
validation. If local and CI validation disagree, test with the version in
`.tool-versions` before changing source to accommodate the difference.
The CircleCI pipeline installs the matching ShellCheck release before it lints
the repository. Its base image is pinned by digest too.

## Update development tools

Keep [`.tool-versions`](.tool-versions) and [`mise.toml`](mise.toml) on the
same exact versions. After changing a tool version, refresh the committed Mise
artifact metadata for the supported developer platforms:

```bash
mise trust
mise lock --platform linux-x64 --platform macos-x64 --platform macos-arm64
```

[`scripts/install-asdf-plugins.sh`](scripts/install-asdf-plugins.sh) pins the
asdf plugin commits separately. Update a plugin reference only after reviewing
the intended upstream revision; do not use `asdf plugin update --all`.

## Review Renovate updates

Renovate groups Python requirements and GitHub Actions updates, but every update
stays open for review. It does not update CircleCI dependencies or developer
tool versions because those pins are repeated with checksums, image digests, or
committed Mise metadata. Use [Update development tools](#update-development-tools)
and [Update the CircleCI CLI pins](#update-the-circleci-cli-pins) so each
coordinated change stays complete.

Dependabot owns vulnerability-fix pull requests. Keep the dependency graph,
Dependabot alerts, and Dependabot security updates enabled in the repository
settings. Renovate's vulnerability updates are disabled to avoid duplicate
security pull requests.

## Find the source you need

The orb development kit packs the public interface from `src/`:

| Path | Purpose |
| --- | --- |
| `src/@orb.yml` | Orb metadata |
| `src/commands/run_vexcalibur.yml` | Reusable command and command parameters |
| `src/jobs/run.yml` | Reusable job and job parameters |
| `src/executors/python.yml` | Python Docker executor |
| `src/scripts/run-vexcalibur.sh` | Package validation, isolated installation, and CLI execution |
| `src/examples/` | Examples shown with the published orb |
| `scripts/install-asdf-plugins.sh` | Pinned asdf plugin installer for local development |
| `scripts/install-circleci-cli.sh` | Verified CircleCI CLI download used by GitHub CI |
| `docs/reference/orb.md` | Public interface and runtime reference |

Tests under `tests/` exercise the runner and guard repeated defaults against drift. `.circleci/config.yml` validates and packs the source. It then passes control to `.circleci/test-deploy.yml`, which tests the packed source and controls publication. Both configurations pin production orb imports to full semantic versions. The pack, continuation, and publish jobs also pin the CircleCI CLI container by version and image digest.

## Make a change

Keep each change at the narrowest public layer that can express it.

- When a command, job, executor, default, or validation rule changes, update `docs/reference/orb.md` and the matching YAML descriptions.
- When input handling or installation behavior changes, add runner tests in `tests/test_run_vexcalibur.py`.
- When a workflow changes, update or add a complete example under `src/examples/`.
- When the default Vexcalibur package changes, update the command, job, README, and reference together. `tests/test_repo_consistency.py` checks those copies.
- When a change adds network access, handles credentials, or changes what leaves the job, update the security and trust-boundary documentation.

Each nonempty `args` line is one literal Vexcalibur argument. Examples must put an option and its value on separate lines; a line such as `--output result.json` is one argument and won't behave like two shell words.

## Run the checks

Activate the development environment, then run the same classes of checks used in CI:

```bash
pre-commit run --all-files
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

Every command should exit with status `0`. `scripts/validate-circleci.sh` packs `src/` into a temporary directory and validates the packed orb and setup config. It then injects the orb into the continuation config, processes both configurations, confirms the sensitive jobs use the expected immutable executor, and confirms checksum verification precedes publication. It doesn't publish anything.

GitHub CI also parses every checked-in YAML and JSON file. Run the full workflow in a pull request before merging.

## Update the CircleCI CLI pins

GitHub CI downloads a pinned CircleCI CLI release archive. It verifies the checksum manifest against a pinned SHA-256 digest, verifies the platform archive against that manifest, and checks the extracted binary's reported version before installing it. The workflow never executes CircleCI's remote installer script.

CircleCI publication uses the matching `circleci/circleci-cli` container. The tag is paired with its registry digest in `.circleci/config.yml` and `.circleci/test-deploy.yml`; the tag alone is not accepted. You need Docker with Buildx only when inspecting or updating this image pin.

You need GitHub CLI access to the public [`CircleCI-Public/circleci-cli`](https://github.com/CircleCI-Public/circleci-cli) repository for the release inspection commands below. You don't need a CircleCI token.

1. Choose a stable release and inspect every asset name and digest:

   ```bash
   VERSION=0.1.38646
   gh release view "v${VERSION}" \
     --repo CircleCI-Public/circleci-cli \
     --json tagName,isDraft,isPrerelease,assets \
     --jq '{tagName,isDraft,isPrerelease,assets:[.assets[]|{name,digest}]}'
   ```

   Continue only when the tag matches, `isDraft` and `isPrerelease` are both `false`, every listed asset has a `sha256:` digest, and the release includes the checksum manifest plus Linux archives for `amd64` and `arm64`.

2. Copy the digest for `circleci-cli_${VERSION}_checksums.txt`, without its `sha256:` prefix. Update `CIRCLECI_CLI_VERSION` and `CIRCLECI_CLI_CHECKSUMS_SHA256` together in [`.github/workflows/ci.yml`](.github/workflows/ci.yml), then update the matching `circleci-cli` entry in [`.tool-versions`](.tool-versions).

3. Resolve, inspect, and validate the container manifest for the same version. Run this block in Bash from any directory:

   ```bash
   IMAGE="circleci/circleci-cli:${VERSION}"
   docker buildx imagetools inspect "$IMAGE"
   IMAGE_DIGEST="$(
     docker buildx imagetools inspect "$IMAGE" |
       awk '$1 == "Digest:" {print $2; exit}'
   )"
   [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
   docker pull "${IMAGE}@${IMAGE_DIGEST}"
   docker run --rm "${IMAGE}@${IMAGE_DIGEST}" circleci version
   printf 'Pinned image: %s@%s\n' "$IMAGE" "$IMAGE_DIGEST"
   ```

   The command must report the chosen CircleCI CLI version. Copy the final `sha256:` digest only after that check passes. Update the complete tag-and-digest image reference under `pinned-circleci-cli` in `.circleci/config.yml` and `.circleci/test-deploy.yml`. Confirm both files use the same value.

4. Pin any updated CircleCI orbs to a full `MAJOR.MINOR.PATCH` version. Partial versions such as `orb-tools@12.3` and floating tags such as `latest` fail the repository tests.

5. Run the installer tests and the repository validation suite:

   ```bash
   python -m unittest tests.test_install_circleci_cli
   bash -n scripts/*.sh
   shellcheck scripts/*.sh
   python -m unittest discover -s tests
   scripts/validate-circleci.sh
   ```

   The installer tests use local fixtures. Configuration validation resolves the public orbs and inspects the processed sensitive jobs. The full GitHub workflow performs the real release download and must pass before the version update merges.

If manifest verification, archive verification, image inspection, platform selection, archive layout, or the reported CLI version differs from the pins, stop the update. Investigate the upstream release instead of weakening a check.

## Open a pull request

Include:

- The user-visible behavior that changed and why
- Tests for new validation, argument, or runner behavior
- Updated reference text and examples for interface changes
- Any new public network destination or data sent to it
- Any effect on tokens, package installation, or release permissions
- The local checks you ran

Maintainers use [the publishing guide](docs/how-to/publish-orb.md) after a change is merged. A pull request should never publish an orb or include a CircleCI token.
