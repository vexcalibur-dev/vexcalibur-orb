from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_circleci_release_context as verifier  # noqa: E402


ORGANIZATION_ID = "238006c1-b8b3-475d-98c9-fab5d1b03ec7"
CONTEXT_ID = "1f03eff7-48d3-47c7-9eac-db985c59c3a8"
PROJECT_ID = "238297f5-b2d1-47f5-9074-16d5e5dcafce"


def restrictions() -> list[dict[str, object]]:
    return [
        {
            "context_id": CONTEXT_ID,
            "id": "5455ea6c-45c9-4577-bef5-e4f99c91e91a",
            "name": "",
            "restriction_type": "expression",
            "restriction_value": verifier.EXPECTED_EXPRESSION,
        },
        {
            "context_id": CONTEXT_ID,
            "id": "2aba0d9e-f2d5-4cfb-9982-6a062028a1c9",
            "name": "vexcalibur-orb",
            "restriction_type": "project",
            "restriction_value": PROJECT_ID,
            "project_id": PROJECT_ID,
        },
    ]


class CircleCiReleaseContextTests(unittest.TestCase):
    def test_main_reports_every_verified_restriction_type(self) -> None:
        output = StringIO()
        with (
            patch.dict(os.environ, {"CIRCLECI_OPERATOR_TOKEN": "operator-token"}),
            patch.object(
                verifier,
                "verify",
                return_value=(ORGANIZATION_ID, CONTEXT_ID),
            ),
            redirect_stdout(output),
        ):
            verifier.main()

        self.assertIn("project and expression restrictions", output.getvalue())

    def test_verify_accepts_the_live_circleci_response_contract(self) -> None:
        class FakeClient:
            def __init__(self, token: str) -> None:
                self.token = token

            def fetch_json(self, path: str) -> object:
                if path == "me/collaborations":
                    return [
                        {
                            "id": ORGANIZATION_ID,
                            "name": "vexcalibur-dev",
                            "slug": "gh/vexcalibur-dev",
                            "vcs_type": "github",
                        }
                    ]
                if path == "project/gh/vexcalibur-dev/vexcalibur-orb":
                    return {
                        "id": PROJECT_ID,
                        "slug": "gh/vexcalibur-dev/vexcalibur-orb",
                    }
                raise AssertionError(path)

            def fetch_pages(self, path: str) -> list[dict[str, object]]:
                if path == f"context?owner-id={ORGANIZATION_ID}":
                    return [{"id": CONTEXT_ID, "name": "orb-publishing"}]
                if path == f"context/{CONTEXT_ID}/restrictions":
                    return restrictions()
                raise AssertionError(path)

        with patch.object(verifier, "Client", FakeClient):
            self.assertEqual(
                verifier.verify("operator-token"),
                (ORGANIZATION_ID, CONTEXT_ID),
            )

    def test_exact_github_organization_identity_is_required(self) -> None:
        organization = {
            "id": ORGANIZATION_ID,
            "name": "vexcalibur-dev",
            "slug": "gh/vexcalibur-dev",
            "vcs_type": "github",
        }

        self.assertEqual(verifier.select_organization([organization]), organization)
        for field, value in (("slug", "bb/vexcalibur-dev"), ("vcs_type", "bitbucket")):
            with self.subTest(field=field):
                changed = dict(organization)
                changed[field] = value
                with self.assertRaisesRegex(verifier.VerificationError, "VCS identity"):
                    verifier.select_organization([changed])

        documented = dict(organization)
        documented["vcs-type"] = documented.pop("vcs_type")
        self.assertEqual(verifier.select_organization([documented]), documented)

        conflicting = dict(organization)
        conflicting["vcs-type"] = "bitbucket"
        with self.assertRaisesRegex(verifier.VerificationError, "VCS identity"):
            verifier.select_organization([conflicting])

    def test_expected_live_restrictions_are_accepted(self) -> None:
        verifier.verify_restrictions(
            restrictions(), context_id=CONTEXT_ID, project_id=PROJECT_ID
        )

    def test_expression_drift_is_rejected(self) -> None:
        items = restrictions()
        items[0]["restriction_value"] = 'pipeline.git.branch == "main"'

        with self.assertRaisesRegex(verifier.VerificationError, "exact release"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )

    def test_wrong_project_is_rejected(self) -> None:
        items = restrictions()
        items[1]["name"] = "other-project"

        with self.assertRaisesRegex(verifier.VerificationError, "project restriction"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )

    def test_restriction_for_another_project_id_is_rejected(self) -> None:
        items = restrictions()
        items[1]["project_id"] = "93d90e39-f576-4773-8c33-f6de351f8822"
        items[1]["restriction_value"] = items[1]["project_id"]

        with self.assertRaisesRegex(verifier.VerificationError, "conflicting"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )

    def test_additional_project_is_rejected(self) -> None:
        items = restrictions()
        extra = dict(items[1])
        extra["id"] = "b09fa2ae-fc8c-4ad9-840f-2c1621e13357"
        extra["name"] = "other-project"
        extra["project_id"] = "93d90e39-f576-4773-8c33-f6de351f8822"
        extra["restriction_value"] = extra["project_id"]
        items.append(extra)

        with self.assertRaisesRegex(verifier.VerificationError, "exactly one"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )

    def test_security_group_authorization_grant_is_rejected(self) -> None:
        items = restrictions()
        items.append(
            {
                "context_id": CONTEXT_ID,
                "id": ORGANIZATION_ID,
                "name": "All members",
                "restriction_type": "group",
                "restriction_value": ORGANIZATION_ID,
            }
        )

        with self.assertRaisesRegex(verifier.VerificationError, "authorization grants"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )

    def test_restriction_from_another_context_is_rejected(self) -> None:
        items = restrictions()
        items[0]["context_id"] = ORGANIZATION_ID

        with self.assertRaisesRegex(verifier.VerificationError, "another context"):
            verifier.verify_restrictions(
                items, context_id=CONTEXT_ID, project_id=PROJECT_ID
            )


if __name__ == "__main__":
    unittest.main()
