# Publish the Orb

Use this guide when preparing the Vexcalibur orb for the CircleCI registry.

## Required Setup

CircleCI registry publication is not fully configured in this repository yet.
The remaining setup requires a CircleCI account and organization owner access.

1. Install and authenticate the CircleCI CLI.
2. Claim or confirm the CircleCI namespace for the `vexcalibur-dev` organization.
3. Create the registry orb as `vexcalibur-dev/vexcalibur`.
4. Connect this repository as a CircleCI project.
5. Enable CircleCI setup workflows, also called dynamic config, for the project.
6. Create an `orb-publishing` context with a CircleCI personal API token stored
   as `CIRCLE_TOKEN`.
7. Restrict that context to the maintainers who are allowed to publish the orb.
8. Protect `v*` tags in GitHub so only maintainers can create production tags.

The checked-in `.circleci/config.yml` and `.circleci/test-deploy.yml` are based
on CircleCI's orb development kit layout. Production publish is filtered to
semantic version tags matching `vX.Y.Z`, and the release workflow checks that
the tag commit is reachable from `origin/main`.

If publish fails with a message like `No Orb Publishing Token detected`, confirm
the `orb-publishing` context is attached to the job, the token variable is named
`CIRCLE_TOKEN`, and the approving user is allowed to access the context. Rotate
or revoke the token from CircleCI after suspected exposure or maintainer access
changes.

## Local Validation

Run the GitHub-side validation before enabling CircleCI publication:

```bash
python -m pip install -r requirements-dev.txt
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
```

After installing the CircleCI CLI, also validate the orb source with the
CircleCI tooling:

```bash
scripts/validate-circleci.sh
```

Expected success signal: local checks pass, the packed orb validates, the setup
config validates, and the continuation config validates with the local packed
orb inlined.

## Dry Run

The `test-deploy` workflow includes an approval-gated `publish-dev` job on
`main`. Use that job to confirm the namespace, project, context, and token work
before creating the first production tag.

Expected success signal: after approval, CircleCI publishes a development orb
version for `vexcalibur-dev/vexcalibur`, and the production publish job remains
skipped because no semantic version tag was pushed.

## Release

Orb versions are immutable once published. Before tagging a release:

1. Confirm `src/@orb.yml`, `README.md`, and `docs/reference/orb.md` agree on
   the Vexcalibur package version.
2. Confirm the CircleCI `test-deploy` workflow has passed on the release commit.
3. Confirm `publish-dev` has succeeded for the current `main` branch.
4. Create a GitHub Release with a semantic version tag such as `v0.1.0` from a
   commit on `main`.
5. Confirm the CircleCI publish workflow publishes the matching registry orb
   version.

Do not move published release tags.

## Bad Release Recovery

Published orb versions cannot be changed or deleted. If a production release is
bad:

1. Do not move the Git tag.
2. Publish a corrected patch version from `main`.
3. Update README and release notes to point users at the corrected version.
4. Open or update an issue that describes the affected version and workaround.
5. Rotate the publishing token if the release exposed credentials or publishing
   context details.
