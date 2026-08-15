# Production release policy

This reference defines the controls that authorize a Vexcalibur orb production release. It is for maintainers auditing GitHub and CircleCI before a release or rotating the automation App key.

The production registry accepts only an immutable `MAJOR.MINOR.PATCH` version published by the CircleCI tag pipeline. GitHub creates the source tag and Release; CircleCI publishes the consumer artifact.

## Trust chain

| Boundary | Required control |
| --- | --- |
| Source branch | `main` accepts changes through pull requests and required checks. |
| GitHub release | Immutable releases are enabled and enforced by the organization owner. |
| Tag creation | Only the Vexcalibur automation App can create a tag covered by `refs/tags/v*`. |
| Tag lifetime | No actor can update or delete a tag covered by `refs/tags/v*`. |
| Policy evidence | The ruleset identity, revision, and bypass actors are signed by the automation App key and reviewed in source. |
| Registry publication | Only the `vexcalibur-orb` project can use the `orb-publishing` context, and only from `main` or an exact version tag without SSH or API-supplied configuration. |

The ruleset pattern does not cover a tag name containing `/`. Release planning ignores those names and accepts only `vMAJOR.MINOR.PATCH` without leading zeros.

## Automation App

The GitHub App ID is `4250150`. The installation covers the Vexcalibur repositories, while the release workflow narrows each installation token to `vexcalibur-dev/vexcalibur-orb` and requests these repository permissions:

| Permission | Access | Use |
| --- | --- | --- |
| Administration | Read | Read immutable-release and ruleset state. GitHub omits ruleset bypass actors at this access level. |
| Contents | Write | Create the annotated tag and immutable GitHub Release. |

The organization Actions variable `AUTOMATION_CLIENT_ID` identifies the App. The organization Actions secret `AUTOMATION_SECRET` contains its private key. Both are limited to the Vexcalibur repositories. The private key must not be committed, printed, or copied into a repository variable.

## GitHub tag rulesets

The repository has two active tag rulesets. Their signed IDs and revisions are recorded in [the policy attestation](../../.github/release-policy/attestation.json).

| Ruleset | Ref include | Rules | Bypass |
| --- | --- | --- | --- |
| `immutable release tags` | `refs/tags/v*` | Update and deletion | None |
| `restricted release tag creation` | `refs/tags/v*` | Creation | Vexcalibur automation App, always |

Both rulesets target tags, have no excluded refs, and are enforced. Do not add a user, team, deploy key, repository role, or organization administrator as a bypass actor.

The release workflow reads each live ruleset with the App token. Because that read-only token cannot see bypass actors, the workflow adds the bypass evidence from the signed attestation. It then requires every visible identity and revision field to match live state before it evaluates the complete policy.

## Signed attestation

Three source files preserve the owner-reviewed evidence:

- `.github/release-policy/attestation.json` contains canonical JSON with the repository, App ID, ruleset IDs, ruleset revisions, and bypass actors.
- `.github/release-policy/attestation.sig` contains the base64-encoded RSA SHA-256 signature of the exact attestation bytes.
- `.github/release-policy/public-key.pem` contains the signing public key. Its DER-encoded SHA-256 fingerprint is `e288ab156d6bc667f5f97e860e1dbb2ad46cedb17d6c602be8136d3d1705dc3e`.

Verify the checked-in signature from the repository root:

```bash
signature_file="$(mktemp)"
trap 'rm -f "${signature_file}"' EXIT
base64 --decode .github/release-policy/attestation.sig >"${signature_file}"
openssl dgst \
  -sha256 \
  -verify .github/release-policy/public-key.pem \
  -signature "${signature_file}" \
  .github/release-policy/attestation.json
```

The final command prints `Verified OK` and exits with status `0`.

Verify the documented public-key fingerprint separately:

```bash
openssl pkey \
  -pubin \
  -in .github/release-policy/public-key.pem \
  -outform DER |
  sha256sum
```

The digest must match the fingerprint above.

## Policy changes and key rotation

A ruleset edit changes its live revision and stops releases until an owner signs new evidence. This failure is intentional.

To change the policy or rotate the App key:

1. Make the intended ruleset or App-key change in GitHub.
2. Read both complete rulesets through an owner-authenticated GitHub session and verify their names, tag scope, rules, and bypass actors.
3. Update the canonical attestation with the live IDs, revisions, and reviewed bypass actors.
4. Sign the exact attestation file with the current App private key. For key rotation, use the new key and replace the checked-in public key in the same pull request.
5. Recompute the DER-encoded fingerprint with the command above and replace the documented value when the public key changes.
6. Verify the signature and fingerprint locally, review the three policy files together, and run the repository test suite.
7. Merge the policy pull request before dispatching another release. After a key rotation succeeds, revoke the superseded App key.

Never generate the signature in GitHub Actions. Signing remains an owner action so a workflow cannot approve a ruleset change that it is supposed to verify.

## Coordination branch

`release-coordination` is mutable serialization state. A new tag transaction advances it to a synthetic commit with a force-with-lease guard, and CircleCI ignores the branch. The commit has the released source as its sole parent and reuses that source tree. Its canonical JSON message records the release tag, tag object, and source commit.

The synthetic target matters when two attempted tags point to the same source commit. Each tag object produces different coordination metadata, so the second push cannot appear up to date and bypass its stale lease. The tag and coordination branch update remain one atomic Git transaction.

If the branch is absent during recovery, the release tool reconstructs it from the latest verified immutable tag. A conflicting branch value stops the release instead of overwriting unexplained state. Do not edit the branch manually.
