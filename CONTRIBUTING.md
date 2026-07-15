# Contributing to the Vexcalibur CircleCI orb

Contributions should keep the orb source, its examples, and its reference documentation in agreement. The packed orb is generated from `src/`; don't edit or commit `orb.yml` or files under `dist/`.

Use [GitHub issues](https://github.com/vexcalibur-dev/vexcalibur-orb/issues) to discuss a substantial behavior change before you implement it. Report security defects through the [private security process](SECURITY.md), not through a public issue.

## Prepare a development environment

You need:

- Bash
- Git
- Python 3.10 or newer with `pip` and `venv`
- The [CircleCI CLI](https://circleci.com/docs/guides/toolkit/local-cli/) for orb and configuration validation

The development requirements install ShellCheck, `detect-secrets`, and PyYAML. From the repository root, create an isolated environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Check the CircleCI CLI before running the full validation suite:

```bash
circleci version
```

GitHub CI pins its CircleCI CLI version in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). If local and CI validation disagree, test with that version before changing source to accommodate the difference.

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
| `docs/reference/orb.md` | Public interface and runtime reference |

Tests under `tests/` exercise the runner and guard repeated defaults against drift. `.circleci/config.yml` validates and packs the source. It then passes control to `.circleci/test-deploy.yml`, which tests the packed source and controls publication.

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
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

Every command should exit with status `0`. `scripts/validate-circleci.sh` packs `src/` into a temporary directory and validates the packed orb and setup config. It then injects the orb into the continuation config and validates the result. It doesn't publish anything.

GitHub CI also parses every checked-in YAML and JSON file. Run the full workflow in a pull request before merging.

## Open a pull request

Include:

- The user-visible behavior that changed and why
- Tests for new validation, argument, or runner behavior
- Updated reference text and examples for interface changes
- Any new public network destination or data sent to it
- Any effect on tokens, package installation, or release permissions
- The local checks you ran

Maintainers use [the publishing guide](docs/how-to/publish-orb.md) after a change is merged. A pull request should never publish an orb or include a CircleCI token.
