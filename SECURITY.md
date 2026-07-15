# Security policy

## Report a vulnerability

Report a vulnerability through [GitHub private vulnerability reporting](https://github.com/vexcalibur-dev/vexcalibur-orb/security/advisories/new). Do not open a public issue for a suspected vulnerability.

This project also follows the [shared `vexcalibur-dev` security policy](https://github.com/vexcalibur-dev/.github/security/policy).

Include the affected source or workflow, impact, reproduction steps, and any suggested mitigation in the private report. Do not include credentials, private package data, or sensitive SBOM contents unless a maintainer confirms a suitable encrypted transfer method. The shared policy defines response and update targets for active reports.

## Supported versions

The orb has not been published to the CircleCI registry. Security fixes currently target the default branch.

| Surface | Supported |
| --- | --- |
| Source on `main` | Yes |
| CircleCI registry releases | None exist |

This table will change when the first versioned orb is published.

## Keep secrets out of orb parameters

Don't put a token, password, or private package credential in `package_spec`, `constraints_file`, or `args`. Those values live in CircleCI configuration or files, and package installer or command output can expose them in job logs.

The runner rejects a package spec that starts with a pip option or with an obvious credential-bearing URL. That check catches common mistakes; it is not a credential scanner. A development package spec and a constraints file can still carry sensitive data in forms the runner doesn't recognize.

When Vexcalibur needs a GitHub token, store it in a restricted CircleCI context or project environment variable. Pass the environment variable's name to Vexcalibur when the CLI requires one; don't copy the token value into `args`. Grant the token only the permissions needed for the repository and operation.

If a credential appears in configuration, source, an artifact, or a job log, revoke or rotate it at the issuing service immediately. Then follow your organization's incident process and remove the exposed value from future pipeline runs.

## Review outbound data

The command installs Vexcalibur from the package index available to pip. Vexcalibur can also contact GitHub, public OSV, or a private OSV-compatible service when its CLI arguments request that behavior.

The orb never adds `--allow-public-osv`. Without that flag, Vexcalibur refuses to send package URLs, package versions, or SBOM-derived inventory to `https://api.osv.dev`. Approve that data sharing before adding the flag to a workflow.

The [runtime and trust-boundary explanation](docs/explanation/runtime-and-trust.md) describes the installation and network boundaries in more detail.
