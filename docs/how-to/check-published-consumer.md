# Check a published CLI through CircleCI

Use this check to test the production Orb with a selected published CLI before
a release decision. You need permission to open a pull request in this
repository and view its CircleCI pipeline. The CircleCI project must be
connected, with dynamic configuration and uncertified Orbs enabled as described
in the [publishing setup](publish-orb.md).

The `format-output-test` job first tests the local Orb source with its default
CLI. It then invokes the `registry-consumer` command, which downloads the
production Orb from the registry and installs the selected CLI from PyPI. This
second path exercises the components a downstream project would install.

## Select the dependencies

Edit [`.circleci/test-deploy.yml`](../../.circleci/test-deploy.yml) on a branch:

1. Set `orbs.released` to an exact production Orb reference in the form
   `vexcalibur-dev/vexcalibur@MAJOR.MINOR.PATCH`.
2. Set `commands.registry-consumer.parameters.package_spec.default` to
   `vexcalibur==MAJOR.MINOR.PATCH` for the published CLI being evaluated.
3. Run the [contributor checks](../../CONTRIBUTING.md), then open a pull request.

These are test dependency pins, not the Orb repository's release version. The
Orb reference also supplies the recorded dependency identity through a YAML
anchor. The CLI parameter supplies all four invocations and the verifier's
expected version, so neither value needs a second edit in the test code.

This path does not accept a development Orb, a CLI prerelease, or an unpublished
wheel. Don't enable development package requirements to work around that limit.
Testing an unpublished candidate needs a separate, reviewed installation path.

## Verify the run

In CircleCI, open the pull request's pipeline, then the `test-deploy` workflow
and `format-output-test` job. Confirm that **Verify published consumer outputs**
prints `Published Orb consumer outputs and execution reports verified.` and the
job succeeds.

The job's artifacts include `artifacts/published-consumer` with four VEX files,
their four execution reports, and `consumer-versions.json`. The version file
records the selected Orb reference, CLI package requirement, and output formats.
For the initial stable release, record the pipeline URL, tested commit, and
these dependency identities in
[Vexcalibur 1.0 readiness](https://github.com/vexcalibur-dev/vexcalibur/issues/136)
before claiming consumer coverage.

The verifier checks the fixed fixture's analysis state and component or finding
identity in CycloneDX, OpenVEX, CSAF, and SPDX 3. Each canonical schema-v1 report
must contain the selected CLI version, local source categories, and expected
counts. Its document size and SHA-256 must match the generated file's bytes.
These are integration smoke tests; full format conformance remains in the CLI
repository's test suite.

## Handle a failure

An installation or registry error can stop the job before generation. Confirm
that both selected versions exist and inspect the failed step before retrying.
A report mismatch means the emitted bytes, metadata, or fixture contract differ
from the expectation. Keep the failure visible and investigate the artifacts;
don't adjust the verifier merely to accept the new output.

All generation uses checked-in fixtures, local findings, and `--offline`.
Package and Orb downloads still require network access. The test adds no
publishing credentials or CircleCI contexts and does not publish a version.
It remains part of the existing acceptance job, so the release verifier's
expected workflow and job names do not change.

A passing run proves this pinned combination worked for the fixture. It does
not establish that a later CLI or Orb release passed. It does not replace the
[release workflow's immutable-tag and CircleCI evidence checks](publish-orb.md),
or the final reviews tracked in the 1.0 readiness issue.
