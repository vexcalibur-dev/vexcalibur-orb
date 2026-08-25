# Publish the orb

This guide is for Vexcalibur maintainers who administer the GitHub repository
and CircleCI namespace. GitHub Actions publishes development and production
Orb versions. CircleCI tests the same commit but never receives the registry
credential.

Production versions are immutable. The release workflow creates the tag, waits
for exact GitHub and CircleCI evidence, packs the tagged source, and publishes
the matching registry version. Don't create, move, replace, or delete a release
tag by hand.

## Gather the required access

You need:

- Admin access to `vexcalibur-dev/vexcalibur-orb` on GitHub
- Owner access to the Vexcalibur organization in CircleCI
- A CircleCI personal API token for an identity that can publish the
  `vexcalibur-dev` namespace
- GitHub CLI and CircleCI CLI for setup and registry verification

CircleCI personal tokens created in the web application have full API read and
write access. They also expire on the date you select, no more than one year
after creation. Use a dedicated token named for Orb automation and record its
expiration in your credential manager.

Create it under **User Settings**, then **Personal API Tokens** in CircleCI.
Name it `vexcalibur-orb-github-publishing`, choose the longest acceptable
expiration, and store the value when CircleCI shows it. You cannot retrieve the
value later.

The publication workflow uses the token only for CircleCI CLI publication.
Pipeline, workflow, and job evidence for this public project is available
without authentication. Store the token only as the GitHub environment secret
described below. Don't add it to a CircleCI context, repository variable,
workflow file, or local shell profile.

## Create the registry entry once

Skip this section when the namespace and Orb already exist under the expected
Vexcalibur organization.

1. Find the CircleCI organization ID under **Organization Settings**.

   ```bash
   CIRCLECI_ORG_ID=00000000-0000-0000-0000-000000000000
   ```

   Replace the example UUID with the Vexcalibur organization ID.

2. Authenticate the local CircleCI CLI with your maintainer account:

   ```bash
   circleci setup
   ```

3. Create the namespace if it does not exist:

   ```bash
   circleci namespace create vexcalibur-dev \
     --org-id "$CIRCLECI_ORG_ID" \
     --no-prompt
   ```

4. Create the public Orb entry:

   ```bash
   circleci orb create vexcalibur-dev/vexcalibur --no-prompt
   ```

The final command creates a world-readable registry entry. It does not publish
a version.

5. Connect `vexcalibur-dev/vexcalibur-orb` through CircleCI's GitHub OAuth
   integration.

6. Under **Project Settings**, then **Advanced**, confirm **Enable dynamic
   config using setup workflows** is on. The checked-in setup configuration
   uses a local, digest-pinned continuation job to load
   `.circleci/test-deploy.yml`. CircleCI's
   [dynamic configuration guide](https://circleci.com/docs/guides/orchestrate/dynamic-config/)
   describes that project setting.

## Configure the GitHub publishing environment

Create a repository environment named `circleci-orb-publishing`. Select only
the `main` branch under **Deployment branches and tags**. Do not add a wait
timer, required reviewer, or custom protection rule. The workflows already
require successful checks and exact source identities, so a manual approval
would add delay without adding independent evidence.

Under the environment's administrator-bypass setting, clear **Allow
administrators to bypass configured protection rules**. There is one
maintainer, so this does not replace review; it prevents an accidental manual
bypass from broadening the selected-branch rule.

Use the GitHub web interface, or create the environment with the API:

```bash
REPOSITORY=vexcalibur-dev/vexcalibur-orb
ENVIRONMENT=circleci-orb-publishing

gh api \
  --method PUT \
  "repos/${REPOSITORY}/environments/${ENVIRONMENT}" \
  -F wait_timer=0 \
  -F prevent_self_review=false \
  -F deployment_branch_policy[protected_branches]=false \
  -F deployment_branch_policy[custom_branch_policies]=true

gh api \
  --method POST \
  "repos/${REPOSITORY}/environments/${ENVIRONMENT}/deployment-branch-policies" \
  -f name=main \
  -f type=branch
```

The REST API does not expose the administrator-bypass control. After the API
commands finish, open the environment under **Settings**, then **Environments**,
and clear **Allow administrators to bypass configured protection rules**.

Before adding the token, inspect the environment and its branch rule:

```bash
gh api "repos/${REPOSITORY}/environments/${ENVIRONMENT}"
gh api \
  "repos/${REPOSITORY}/environments/${ENVIRONMENT}/deployment-branch-policies"
```

The environment must report `can_admins_bypass: false` and a custom branch
policy with one `branch` rule named `main`. Stop if another ref can use the
environment.

Store the CircleCI personal token through the GitHub CLI prompt:

```bash
gh secret set CIRCLE_TOKEN \
  --repo "${REPOSITORY}" \
  --env "${ENVIRONMENT}"
```

The command reads the value without putting it on the command line. Confirm
that GitHub recorded the secret name:

```bash
gh secret list --repo "${REPOSITORY}" --env "${ENVIRONMENT}"
```

GitHub does not return the value. The list should contain `CIRCLE_TOKEN`.

## Decommission the legacy CircleCI credential

Complete this migration step after GitHub lists the new environment secret and
before the publishing-workflow pull request merges. The existing
`orb-publishing` CircleCI context can still authorize the publisher embedded in
an older immutable configuration. Leaving it available would let a legacy
workflow rerun publish outside the new GitHub evidence gate.

Authenticate the CircleCI CLI with your maintainer account, then copy the
Vexcalibur organization ID from **Organization Settings**, then **Overview**:

```bash
circleci setup
CIRCLECI_ORG_ID=00000000-0000-0000-0000-000000000000
```

Replace the example UUID before continuing. Confirm the context and variable:

```bash
circleci context show \
  --org-id "${CIRCLECI_ORG_ID}" \
  orb-publishing
```

The result must list the `orb-publishing` context and its `CIRCLE_TOKEN`
variable.

Delete the old context:

```bash
circleci context delete \
  --org-id "${CIRCLECI_ORG_ID}" \
  orb-publishing
```

Confirm the prompt only after the CLI names the Vexcalibur organization and
`orb-publishing` context. If another project still uses that context, stop and
remove only its `CIRCLE_TOKEN` instead:

```bash
circleci context remove-secret \
  --org-id "${CIRCLECI_ORG_ID}" \
  orb-publishing \
  CIRCLE_TOKEN
```

Then revoke the old personal token in CircleCI. Do not revoke the new token
stored in GitHub. The migration is ready to merge only after the legacy context
no longer exposes `CIRCLE_TOKEN` and the old token cannot authenticate.

## Validate a change locally

Follow [the contributor setup](../../CONTRIBUTING.md#prepare-a-development-environment),
then run:

```bash
pre-commit run --all-files
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

The commands should exit with status `0`. The CircleCI validator packs the Orb,
validates both source and processed configurations, and confirms that neither
CircleCI configuration contains the publishing context or a publication job.

## Publish a development version

A successful GitHub `CI` run for a push to the current `main` commit triggers
`Publish development Orb`. The workflow then:

1. Confirms that the successful run came from this repository's `main` branch.
2. Confirms that its commit is still the live `main` commit.
3. Waits for the CircleCI branch pipeline for that exact commit.
4. Requires every expected CircleCI workflow and acceptance job to pass.
5. Packs and validates the source from that commit.
6. Publishes `dev:<full-commit-sha>` and verifies the registry source.
7. Confirms `main` again before publishing and verifying `dev:alpha`.

The concurrency group serializes development publication, and the workflow
checks `main` immediately before it writes `dev:alpha`. The registry does not
offer compare-and-set publication, so the alias can briefly lag if `main`
advances during that network call. The next successful `main` publication
corrects it.

Inspect the result without executing it:

```bash
MAIN_SHA="$(
  gh api repos/vexcalibur-dev/vexcalibur-orb/git/ref/heads/main \
    --jq '.object.sha'
)"
circleci orb info "vexcalibur-dev/vexcalibur@dev:${MAIN_SHA}"
circleci orb info vexcalibur-dev/vexcalibur@dev:alpha
```

Both commands should identify the development Orb. Development versions are
mutable and are not supported production releases.

## Release a production version

Every push to `main` runs the GitHub `Release` workflow. It calculates the next
semantic version from Conventional Commits after the latest production tag.
The highest matching change controls the bump:

| Bump | Conventional Commit input |
| --- | --- |
| Major | A subject with `!`, such as `feat!:`, or a `BREAKING CHANGE:` footer |
| Minor | A `feat:` subject, including a scoped subject such as `feat(orb):` |
| Patch | `fix:`, `perf:`, `refactor:`, `deps:`, `revert:`, `build(deps):`, `chore(deps):`, or a Git-generated revert subject |
| None | Other commit types, or a current commit containing `[skip release]` or `[release skip]` |

The workflow does not write a version into repository files. It derives
`vMAJOR.MINOR.PATCH` from immutable tags, so release versions remain metadata.

The release workflow creates the annotated tag and GitHub Release first. Its
final job then waits for the CircleCI tag pipeline at the exact tag and commit.
After every required job passes, GitHub packs the immutable tag source and
publishes the matching `MAJOR.MINOR.PATCH` Orb version. It reads the registry
source afterward and fails if the bytes differ from the candidate.

Watch the automatic run:

```bash
MAIN_SHA="$(
  gh api repos/vexcalibur-dev/vexcalibur-orb/git/ref/heads/main \
    --jq '.object.sha'
)"
RUN_IDS="$(
  gh run list \
    --repo vexcalibur-dev/vexcalibur-orb \
    --workflow release.yml \
    --event push \
    --branch main \
    --commit "${MAIN_SHA}" \
    --limit 100 \
    --json attempt,databaseId,event,headBranch \
    --jq \
      '.[] | select(
        .attempt == 1 and
        .event == "push" and
        .headBranch == "main"
      ) | .databaseId'
)"
test "$(printf '%s\n' "${RUN_IDS}" | sed '/^$/d' | wc -l)" -eq 1
gh run watch "${RUN_IDS}" \
  --repo vexcalibur-dev/vexcalibur-orb \
  --exit-status
```

The workflow must finish with `success`. Verify the public registry version:

```bash
circleci orb info vexcalibur-dev/vexcalibur@MAJOR.MINOR.PATCH
circleci orb source vexcalibur-dev/vexcalibur@MAJOR.MINOR.PATCH \
  > /tmp/vexcalibur-orb-registry.yml
```

Replace `MAJOR.MINOR.PATCH` with the release version. The metadata should name
that version, and the source command should write a nonempty YAML file.

## Dispatch or recover a release

Use a manual dispatch when an automatic workflow stopped after creating release
metadata, or when you need to publish an existing immutable tag. Capture the
current tooling commit first:

For this one-time migration, squash-merge the publishing-workflow pull request
with this exact subject:

```text
ci: publish Orbs from GitHub Actions [skip release]
```

The marker suppresses the release run triggered by the merge. The non-releasing
`ci:` type also prevents this commit from creating a delayed version bump after
recovery. Do not use `fix:`, `feat:`, or another releasing type for the squash
commit. Recover `v0.1.1` before merging any later releasing commit.

```bash
TOOLING_SHA="$(
  gh api repos/vexcalibur-dev/vexcalibur-orb/git/ref/heads/main \
    --jq '.object.sha'
)"

RUN_URL="$(
  gh workflow run release.yml \
    --repo vexcalibur-dev/vexcalibur-orb \
    --ref main \
    --field expected_source_sha="${TOOLING_SHA}" \
    --field tag=v0.1.1
)"
if [[ ! "${RUN_URL}" =~ ^https://github\.com/vexcalibur-dev/vexcalibur-orb/actions/runs/([0-9]+)$ ]]; then
  printf 'GitHub CLI did not return the created run URL: %s\n' "${RUN_URL}" >&2
  exit 1
fi
RUN_ID="${BASH_REMATCH[1]}"
```

The GitHub CLI returns the URL for the dispatch it created. Stop when it does
not return that URL; do not substitute the latest run from the repository.
Watch the captured run:

```bash
gh run watch "${RUN_ID}" \
  --repo vexcalibur-dev/vexcalibur-orb \
  --exit-status

test "$(
  gh run view "${RUN_ID}" \
    --repo vexcalibur-dev/vexcalibur-orb \
    --json jobs \
    --jq '.jobs[] | select(.name == "Publish CircleCI Orb") | .conclusion'
)" = success
```

The final command must exit with status `0`. That job packs the immutable tag,
reads the published registry source back, and compares its bytes with the
candidate. Its success is the exact-source proof required before you remove the
legacy release compatibility path. The legacy CircleCI credential was already
removed before merge; do not recreate it during recovery.

Omit `tag` to recalculate a release from current `main`. Supply a tag only when
it already identifies the immutable source you intend to recover. The workflow
uses current release tooling but checks out, packs, and publishes the tag's own
source commit.

The `v0.1.1` recovery path recognizes the legacy CircleCI pipeline whose
publisher was denied. It still requires every credentialless test, pack, and
source job to pass. This exception comes from the tagged configuration, not a
hard-coded release number, and does not apply to current releases.

Current publication accepts only the original webhook workflow attempts. It
rejects API-triggered pipelines and every rerun, including SSH-derived reruns.
If an exact CircleCI workflow fails for a current release, fix the problem on
`main` and publish a new version. A source or configuration defect cannot be
repaired within an immutable tag.

GitHub workflow reruns also stop before any publication job. Start a new manual
dispatch when you recover an existing release; don't rerun a failed publisher.

### Finish the first production recovery

The first successful registry publication changes the project's public status.
Complete these documentation updates in a follow-up pull request after the
registry source has been verified:

1. Replace the pending-release notice in `README.md` with the published Orb
   version and remove the warning that the checked-in examples cannot run.
2. Add the published version to the supported-version table in `SECURITY.md`.
3. Search `README.md`, `SECURITY.md`, and `docs/` for `pending`, `no production`,
   and the recovered version. Remove any statement that became false when the
   registry accepted the Orb.

Do not merge that status update before the registry commands under
[Release a production version](#release-a-production-version) succeed. GitHub
release metadata alone is not proof that the Orb exists.

## Rotate the CircleCI token

CircleCI sends expiration reminders for personal tokens. Rotate before the
deadline:

1. Create a replacement personal token for the same CircleCI identity. Keep the
   old value in your credential manager during verification.
2. From any directory, replace the GitHub environment secret:

   ```bash
   REPOSITORY=vexcalibur-dev/vexcalibur-orb
   ENVIRONMENT=circleci-orb-publishing

   gh secret set CIRCLE_TOKEN \
     --repo "${REPOSITORY}" \
     --env "${ENVIRONMENT}"
   ```

   Enter the replacement token at the prompt. The command should finish without
   printing the secret.
3. Merge a tested change and watch that exact `Publish development Orb` run.
4. Verify its commit-specific registry reference and `dev:alpha` with the
   commands in [Publish a development version](#publish-a-development-version).
5. Delete the old token in CircleCI after both checks pass.

If publication fails because of authentication, run the command from step 2
again and enter the old value. Inspect the failed run before trying another
replacement. Never delete the old token before the new token passes registry
verification.

## Troubleshoot publication

| Symptom | Action |
| --- | --- |
| GitHub reports that `CIRCLE_TOKEN` is missing | Confirm that the secret exists in the `circleci-orb-publishing` environment and that the job names that environment. |
| The environment rejects the job's ref | Keep the single `main` branch policy. Dispatch release recovery with `--ref main`; don't broaden the environment. |
| No exact CircleCI pipeline appears | Check the CircleCI GitHub integration and webhook delivery for the tag or `main` push. Don't start a substitute pipeline with unreviewed API configuration. |
| CircleCI evidence reports unexpected jobs | Review the CircleCI configuration and the evidence allowlist together. Publication fails closed when the tested job set changes. |
| Registry source differs from the candidate | Stop. Production versions cannot be replaced. For development aliases, inspect the concurrent publication history before retrying. |
| A production version already exists | The workflow succeeds only when the existing registry source matches the immutable release source. Publish a new patch version for corrections. |

If no CircleCI tag pipeline exists, confirm that the exact tag has no pipeline
before you redeliver anything. In the GitHub repository, open **Settings**, then
**Webhooks**, and select the CircleCI webhook. Find the delivery whose payload
names `refs/tags/TAG`, confirm that its `after` value is the immutable tag's
commit, and use **Redeliver**. GitHub retains webhook deliveries for three days;
after that window, stop and investigate with CircleCI rather than recreating the
tag or starting an API pipeline. The verifier rejects both replacement tags and
API-triggered substitutes.

Wait for the redelivered pipeline to finish, then start a new manual release
dispatch. Do not rerun the failed GitHub workflow. GitHub's
[webhook redelivery guide](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)
shows the repository controls and retention limit.

The [production release policy](../reference/release-policy.md) defines the
controls that the workflow enforces. The [runtime and trust explanation](../explanation/runtime-and-trust.md)
describes why GitHub owns the credential while CircleCI remains independent
test evidence.
