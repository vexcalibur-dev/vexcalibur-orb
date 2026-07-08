# Security Policy

## Reporting Vulnerabilities

Vexcalibur CircleCI Orb follows the shared `vexcalibur-dev` security policy:

<https://github.com/vexcalibur-dev/.github/security/policy>

Use GitHub private vulnerability reporting for Vexcalibur CircleCI Orb
vulnerabilities:

<https://github.com/vexcalibur-dev/vexcalibur-orb/security/advisories/new>

Do not open public issues with vulnerabilities, exploit details, secrets,
tokens, private package data, affected package names, logs, stack traces,
screenshots, reproduction steps, or other sensitive evidence.

Do not put tokens, passwords, or private package credentials in orb
`package_spec`, `constraints_file`, or `args` values. The runner rejects common
credentialed URL package specs, but workflow logs and package installer output
can still expose sensitive values supplied by callers.

## Supported Versions

The orb has not been published to the CircleCI registry yet. Security fixes
target the default branch until versioned orb releases are published. After the
first orb release, this policy must list supported release lines.
