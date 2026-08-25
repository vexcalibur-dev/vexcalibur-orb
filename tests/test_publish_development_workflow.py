from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PublishDevelopmentWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.load(
            (ROOT / ".github/workflows/publish-development.yml").read_text(
                encoding="utf-8"
            ),
            Loader=yaml.BaseLoader,
        )
        cls.job = cls.workflow["jobs"]["publish"]

    def step(self, name: str) -> dict[str, object]:
        return next(step for step in self.job["steps"] if step.get("name") == name)

    def test_trigger_and_job_admit_only_successful_current_main_ci(self) -> None:
        self.assertEqual(
            self.workflow["on"],
            {"workflow_run": {"workflows": ["CI"], "types": ["completed"]}},
        )
        self.assertEqual(
            self.workflow["concurrency"],
            {
                "group": "development-orb-publishing",
                "cancel-in-progress": "false",
            },
        )
        condition = self.job["if"]
        for requirement in (
            "github.run_attempt == 1",
            "workflow_run.run_attempt == 1",
            "workflow_run.conclusion == 'success'",
            "workflow_run.event == 'push'",
            "workflow_run.path == '.github/workflows/ci.yml'",
            "workflow_run.head_branch == 'main'",
            "workflow_run.head_repository.full_name == github.repository",
        ):
            self.assertIn(requirement, condition)

        source_check = self.step("Require current main source")
        self.assertIn(
            'test "${current_main_sha}" = "${EXPECTED_SHA}"',
            source_check["run"],
        )
        self.assertIn('test "${GITHUB_SHA}" = "${EXPECTED_SHA}"', source_check["run"])

    def test_workflow_is_read_only_and_uses_a_restricted_environment(self) -> None:
        self.assertEqual(
            self.workflow["permissions"], {"actions": "read", "contents": "read"}
        )
        self.assertEqual(
            self.job["environment"],
            {"name": "circleci-orb-publishing", "deployment": "false"},
        )
        self.assertNotIn("permissions", self.job)

    def test_source_evidence_and_references_are_bound_to_the_ci_commit(self) -> None:
        checkout = self.step("Checkout validated main source")
        source_check = self.step("Require current main source")
        evidence = self.step("Require exact CircleCI main evidence")
        commit_publish = self.step("Publish commit development reference")
        alpha = self.step("Publish alpha from current main")
        names = [step["name"] for step in self.job["steps"]]

        self.assertEqual(
            checkout["with"]["ref"], "${{ github.event.workflow_run.head_sha }}"
        )
        self.assertIn("wait-main-pipeline", evidence["run"])
        self.assertEqual(
            evidence["env"]["RELEASE_SHA"],
            "${{ github.event.workflow_run.head_sha }}",
        )
        self.assertEqual(
            commit_publish["env"]["ORB_DEVELOPMENT_REFERENCE"],
            "dev:${{ github.event.workflow_run.head_sha }}",
        )
        self.assertIn("current_main_sha", alpha["run"])
        self.assertIn("scripts/publish-development-orb.sh", alpha["run"])
        self.assertNotIn("|| true", source_check["run"])
        self.assertNotIn("|| true", alpha["run"])
        self.assertLess(
            names.index("Require exact CircleCI main evidence"),
            names.index("Pack validated Orb source"),
        )
        self.assertLess(
            names.index("Publish commit development reference"),
            names.index("Publish alpha from current main"),
        )

    def test_secret_is_confined_to_evidence_and_publish_steps(self) -> None:
        secret_steps = [
            step["name"]
            for step in self.job["steps"]
            if "secrets.CIRCLE_TOKEN" in str(step)
        ]
        self.assertEqual(
            secret_steps,
            [
                "Publish commit development reference",
                "Publish alpha from current main",
            ],
        )
        self.assertEqual(str(self.workflow).count("secrets.CIRCLE_TOKEN"), 2)

    def test_all_actions_are_pinned_to_full_commits(self) -> None:
        references = [step["uses"] for step in self.job["steps"] if "uses" in step]
        for reference in references:
            self.assertRegex(reference, re.compile(r"^[^@\s]+@[0-9a-f]{40}$"))


if __name__ == "__main__":
    unittest.main()
