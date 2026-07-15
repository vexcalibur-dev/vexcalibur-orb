# Publish the orb

This guide is for Vexcalibur maintainers who can administer the CircleCI organization, its contexts, and GitHub release tags. The orb is not in the CircleCI registry yet.

[Issue #1](https://github.com/vexcalibur-dev/vexcalibur-orb/issues/1) tracks the one-time setup described here.

A production orb version is immutable. Complete the development publication and verification before creating a release tag.

## Gather the required access

You need:

- Owner access to the Vexcalibur CircleCI organization
- Maintainer access to `vexcalibur-dev/vexcalibur-orb` on GitHub
- A CircleCI personal API token with the production publishing scope required by `circleci/orb-tools`
- Permission to create a restricted CircleCI context
- The [CircleCI CLI](https://circleci.com/docs/guides/toolkit/local-cli/) for local checks and registry verification

The token used by the publishing jobs grants broad organization access. Store it only in the CircleCI context described below. Don't reuse it for local CLI work or add it to another secret store for this workflow. Never export it in a shared shell or paste it into configuration.

Run `circleci setup` to authenticate the local CLI through its prompt with your maintainer credentials. Confirm the CircleCI host, then verify the organization UUID separately before creating registry resources.

## Create the registry resources once

Skip a step when the named resource already exists and is owned by the expected Vexcalibur organization. The namespace and public orb are global registry names; don't create substitutes from a personal organization.

1. Find the CircleCI organization UUID and assign it locally:

   ```bash
   CIRCLECI_ORG_ID=00000000-0000-0000-0000-000000000000
   ```

   Replace the reserved example UUID with the Vexcalibur CircleCI organization UUID.

2. Create the public namespace:

   ```bash
   circleci namespace create vexcalibur-dev --org-id "$CIRCLECI_ORG_ID" --no-prompt
   ```

3. Create the public orb:

   ```bash
   circleci orb create vexcalibur-dev/vexcalibur --no-prompt
   ```

   The command creates a world-readable registry entry. It doesn't publish a version.

4. Connect `vexcalibur-dev/vexcalibur-orb` as a CircleCI project. Use the GitHub OAuth integration if the release policy requires a security group to limit publishing to maintainers.

5. In **Project Settings** > **Advanced**, confirm **Enable dynamic config using setup workflows** is on. CircleCI enables it by default for newer projects, but the setting still needs verification. The checked-in `.circleci/config.yml` has `setup: true` and uses `orb-tools/continue` to load `.circleci/test-deploy.yml`. See CircleCI's [dynamic configuration setup](https://circleci.com/docs/guides/orchestrate/dynamic-config/#enable-dynamic-config).

6. Create a CircleCI context named `orb-publishing`, then store the personal API token under the exact variable name `CIRCLE_TOKEN`:

   ```bash
   circleci context create --org-id "$CIRCLECI_ORG_ID" orb-publishing
   circleci context store-secret --org-id "$CIRCLECI_ORG_ID" orb-publishing CIRCLE_TOKEN
   ```

   Skip the create command when the context already exists. `store-secret` prompts for the value so it doesn't become a command-line argument.

7. Add a project restriction that limits `orb-publishing` to `vexcalibur-dev/vexcalibur-orb`. Don't leave a publishing context available to every project in the organization.

8. Restrict the context to the security group for release maintainers. CircleCI supports context security groups only with its GitHub OAuth integration.

   If the project uses another integration, stop here and resolve the release authorization design in issue #1. A project restriction alone doesn't limit which project member can approve a job. With the GitHub OAuth integration, a user who approves the development job must belong to the permitted group.

9. Add a GitHub tag ruleset for `v*`. Limit tag creation and deletion to release maintainers. The CircleCI release workflow narrows production tags further to `vMAJOR.MINOR.PATCH`.

CircleCI documents both [project and security-group context restrictions](https://circleci.com/docs/guides/security/contexts/#security-group-restrictions). Verify the restrictions from an account that is not in the release group before storing the production token.

Rotate the token when a maintainer with access leaves the release group. Update the context after rotation and confirm the former token no longer works.

## Validate the source locally

Follow [the contributor setup](../../CONTRIBUTING.md#prepare-a-development-environment), activate `.venv`, and run these commands from the repository root:

```bash
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

Both commands should exit with status `0`. The validation script packs the source and validates the orb and setup configuration. It then injects the packed orb into `.circleci/test-deploy.yml` and validates that continuation configuration. Generated files stay in a temporary directory.

## Publish and test a development version

Push the release candidate to `main`. CircleCI should run the setup workflow, then continue into `test-deploy` with the packed local orb inserted under the `vexcalibur` name.

Before approval, confirm all three prerequisites for `approve-dev-publish` succeeded:

- `pack-dev`
- `command-help-test`
- `job-help-test`

Approve `approve-dev-publish`. The following `publish-dev` job uses the restricted context to publish two development aliases: `dev:<commit-sha>` and `dev:alpha`. Development versions expire after 90 days; `dev:alpha` can move, so verify the commit-specific alias.

From a checkout of the published commit, run:

```bash
DEV_VERSION="dev:$(git rev-parse HEAD)"
circleci orb info "vexcalibur-dev/vexcalibur@${DEV_VERSION}"
```

The command should show metadata for the Vexcalibur orb at that exact development version. Confirm the production `publish-release` job did not run on the branch pipeline.

The continuation tests exercise the packed command and job with `--help`. They don't yet generate a fixture-backed VEX document; [issue #3](https://github.com/vexcalibur-dev/vexcalibur-orb/issues/3) tracks that acceptance coverage.

## Publish a production version

Use a full three-part semantic version. The first intended release is `v0.1.0`; change the value below for a later release.

1. Fetch the current release state:

   ```bash
   git fetch origin main --tags
   RELEASE_TAG=v0.1.0
   RELEASE_VERSION="${RELEASE_TAG#v}"
   RELEASE_COMMIT="$(git rev-parse origin/main)"
   ```

2. Confirm the release commit is the development version you tested. Confirm GitHub CI and the CircleCI `test-deploy` workflow succeeded for that commit.

3. Confirm `src/@orb.yml`, `README.md`, `docs/reference/orb.md`, and the release notes agree on the default Vexcalibur package and public interface.

4. Create a GitHub release with tag `$RELEASE_TAG`, target `$RELEASE_COMMIT`, and release notes that describe user-visible changes. Publish the GitHub release; don't create the tag from another branch.

5. Watch the CircleCI tag pipeline. It must complete these release jobs:

   - `pack-release`
   - `release-source-check`
   - `command-help-test`
   - `job-help-test`
   - `publish-release`

   `release-source-check` stops publication unless the tag commit is reachable from `origin/main`.

6. Verify the immutable registry version:

   ```bash
   circleci orb info "vexcalibur-dev/vexcalibur@${RELEASE_VERSION}"
   ```

7. Open the registry page reported by the publish job and confirm the command, job, executor, examples, and parameter descriptions render as expected.

8. After the first release exists, update the README and orb reference to replace the unpublished status with the verified registry version. Update the supported-version table in `SECURITY.md` in the same pull request.

Don't move or reuse a production release tag.

## Recover from a failed release

First determine whether the production version exists:

```bash
circleci orb info "vexcalibur-dev/vexcalibur@${RELEASE_VERSION}"
```

If no registry version exists, fix the failed prerequisite and rerun the CircleCI workflow for the same unchanged tag. Don't bypass `release-source-check`.

If the tag points outside `main`, merge the desired change and choose a new version instead of moving the tag.

If the version exists but is defective, it can't be replaced. Leave the Git tag and registry version in place. Document the affected version, then publish a corrected patch version from `main`. Tell consumers to pin the last known-good version until the patch is available.

If a publishing credential may have been exposed, revoke it before retrying anything. Create a replacement token, update `CIRCLE_TOKEN` in the restricted context, and review context access.

## Diagnose common failures

| Symptom | Check |
| --- | --- |
| Setup workflow doesn't continue | Confirm the project is connected and dynamic config is enabled. Then inspect `orb-tools/continue`. |
| `No Orb Publishing Token detected` | Confirm the `orb-publishing` context is attached, its variable is named `CIRCLE_TOKEN`, and the approving user can access the context. |
| Registry says the namespace or orb doesn't exist | Complete the one-time namespace and orb creation with the Vexcalibur CircleCI organization. |
| `release-source-check` fails | Confirm the tag commit is reachable from `origin/main`. Don't weaken or bypass the check. |
| Production publish says the version exists | Treat the registry version as immutable and publish a new patch version if a correction is needed. |
