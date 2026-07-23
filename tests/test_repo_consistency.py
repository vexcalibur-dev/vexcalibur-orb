from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SPEC_PATTERN = re.compile(r"vexcalibur==\d+(?:\.\d+){1,2}(?:\.post\d+)?")
PACKAGE_SPEC_FILES = [
    "README.md",
    "docs/reference/orb.md",
    "src/commands/run_vexcalibur.yml",
    "src/jobs/run.yml",
    "src/scripts/run-vexcalibur.sh",
]
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GITHUB_ACTION_SHA_PATTERN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
CIRCLECI_CLI_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
EXACT_ORB_REFERENCE_PATTERN = re.compile(
    r"^[a-z0-9_.-]+/[a-z0-9_.-]+@[0-9]+\.[0-9]+\.[0-9]+$"
)
PINNED_CIRCLECI_CLI_IMAGE = (
    "circleci/circleci-cli:0.1.38646@sha256:"
    "2a2081377367e051fb247752ac17f753f7675f5d36e334c24da73034848f0926"  # pragma: allowlist secret
)
PINNED_CIMG_PYTHON_TAG = (
    "3.14.5@sha256:"
    "724637b8722b6f7f7199dfae94ba95bbd2cd14978a99d02ae6bd5c7b12c44805"  # pragma: allowlist secret
)
PINNED_SCORECARD_ACTIONS = [
    "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a",
    "github/codeql-action/upload-sarif@99df26d4f13ea111d4ec1a7dddef6063f76b97e9",
]


class RepositoryConsistencyTests(unittest.TestCase):
    def test_version_manager_manifests_are_consistent(self) -> None:
        tool_versions = {
            name: version
            for name, version in (
                line.split(maxsplit=1)
                for line in (REPO_ROOT / ".tool-versions").read_text(
                    encoding="utf-8"
                ).splitlines()
            )
        }
        with (REPO_ROOT / "mise.toml").open("rb") as stream:
            mise_configuration = tomllib.load(stream)
        with (REPO_ROOT / "mise.lock").open("rb") as stream:
            mise_lock = tomllib.load(stream)

        self.assertTrue(mise_configuration["settings"]["lockfile"])
        self.assertEqual(mise_configuration["tools"], tool_versions)
        self.assertEqual(set(mise_lock["tools"]), set(tool_versions))
        for name, version in tool_versions.items():
            with self.subTest(tool=name):
                lock_entries = mise_lock["tools"][name]
                self.assertEqual(len(lock_entries), 1)
                lock_entry = lock_entries[0]
                self.assertEqual(lock_entry["version"], version)
                for platform in ("linux-x64", "macos-arm64", "macos-x64"):
                    asset = lock_entry[f"platforms.{platform}"]
                    self.assertRegex(asset["checksum"], r"^sha256:[0-9a-f]{64}$")
                    self.assertTrue(asset["url"].startswith("https://"))

        ci_workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        )
        setup_python = next(
            step
            for step in ci_workflow["jobs"]["quality"]["steps"]
            if step.get("name") == "Set up Python"
        )
        self.assertEqual(setup_python["with"]["python-version"], tool_versions["python"])

        circleci_configuration = yaml.safe_load(
            (REPO_ROOT / ".circleci/config.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            circleci_configuration["jobs"]["shellcheck"]["docker"],
            [
                {
                        "image": (
                            "cimg/base:2026.07@sha256:"
                            "6b53042171c5eec83d8a9b14206b8483195dde2f3265a85a3d18fe4b778329f3"  # pragma: allowlist secret
                    )
                }
            ],
        )
        shellcheck_install = next(
            step["shellcheck/install"]
            for step in circleci_configuration["jobs"]["shellcheck"]["steps"]
            if isinstance(step, dict) and "shellcheck/install" in step
        )
        self.assertEqual(shellcheck_install["version"], tool_versions["shellcheck"])

        job_source = yaml.safe_load(
            (REPO_ROOT / "src/jobs/run.yml").read_text(encoding="utf-8")
        )
        executor_source = yaml.safe_load(
            (REPO_ROOT / "src/executors/python.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            job_source["parameters"]["python_version"]["default"],
            PINNED_CIMG_PYTHON_TAG,
        )
        self.assertEqual(
            executor_source["parameters"]["tag"]["default"],
            PINNED_CIMG_PYTHON_TAG,
        )

    def test_default_package_spec_is_consistent(self) -> None:
        specs_by_path: dict[str, set[str]] = {}
        for relative_path in PACKAGE_SPEC_FILES:
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            specs_by_path[relative_path] = set(PACKAGE_SPEC_PATTERN.findall(content))

        all_specs = set().union(*specs_by_path.values())

        self.assertEqual(all_specs, {"vexcalibur==0.3.1"}, specs_by_path)

    def test_acceptance_output_uses_default_vexcalibur_version(self) -> None:
        deployment = (REPO_ROOT / ".circleci/test-deploy.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '"engine": {"name": "Vexcalibur", "version": "0.3.1"}',
            deployment,
        )
        self.assertNotIn('"version": "0.3.0"', deployment)

    def test_scorecard_workflow_is_pinned_and_least_privilege(self) -> None:
        workflow = yaml.load(
            (REPO_ROOT / ".github/workflows/scorecard.yml").read_text(
                encoding="utf-8"
            ),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(workflow["permissions"], "read-all")
        scorecard = workflow["jobs"]["scorecard"]
        self.assertEqual(
            scorecard["permissions"],
            {
                "actions": "read",
                "checks": "read",
                "contents": "read",
                "id-token": "write",
                "issues": "read",
                "pull-requests": "read",
                "security-events": "write",
            },
        )

        action_references = [
            step["uses"] for step in scorecard["steps"] if "uses" in step
        ]
        self.assertEqual(action_references, PINNED_SCORECARD_ACTIONS)
        for action_reference in action_references:
            self.assertRegex(action_reference, GITHUB_ACTION_SHA_PATTERN)

        scorecard_step = next(
            step
            for step in scorecard["steps"]
            if step.get("name") == "Run OpenSSF Scorecard"
        )
        self.assertEqual(scorecard_step["with"]["publish_results"], "false")

        upload_step = next(
            step
            for step in scorecard["steps"]
            if step.get("name") == "Upload Scorecard SARIF"
        )
        self.assertEqual(upload_step["if"], "github.event_name != 'pull_request'")

    def test_renovate_update_policy_is_explicit(self) -> None:
        configuration = json.loads(
            (REPO_ROOT / "renovate.json").read_text(encoding="utf-8")
        )

        self.assertEqual(configuration["timezone"], "America/Chicago")
        self.assertEqual(configuration["schedule"], ["* 8-11 * * 1"])
        self.assertEqual(configuration["prHourlyLimit"], 2)
        self.assertEqual(configuration["minimumReleaseAge"], "5 days")
        self.assertEqual(
            configuration["minimumReleaseAgeBehaviour"], "timestamp-required"
        )
        self.assertEqual(configuration["internalChecksFilter"], "strict")
        self.assertEqual(
            configuration["enabledManagers"],
            ["github-actions", "pip_requirements"],
        )
        self.assertEqual(configuration["vulnerabilityAlerts"], {"enabled": False})
        self.assertIn("helpers:pinGitHubActionDigests", configuration["extends"])
        self.assertNotIn("automergeType", configuration)
        self.assertNotIn("platformAutomerge", configuration)
        self.assertEqual(
            configuration["packageRules"],
            [
                {
                    "description": "Group reviewable GitHub Actions updates.",
                    "matchManagers": ["github-actions"],
                    "groupName": "GitHub Actions",
                },
                {
                    "description": "Group reviewable Python updates.",
                    "matchManagers": ["pip_requirements"],
                    "groupName": "Python requirements",
                },
            ],
        )

    def test_security_policy_uses_private_vulnerability_reporting(self) -> None:
        policy = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")

        self.assertIn(
            "https://github.com/vexcalibur-dev/vexcalibur-orb/security/advisories/new",
            policy,
        )
        self.assertNotIn("private vulnerability reporting is not enabled", policy)
        self.assertNotIn("private_disclosure_request.yml", policy)

    def test_acceptance_fixtures_are_local_and_valid_json(self) -> None:
        config = (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
        self.assertNotIn("--allow-public-osv", config)
        self.assertIn("--format\n            cyclonedx", config)
        self.assertIn("--format\n            openvex", config)
        self.assertIn("--format\n            csaf", config)
        self.assertIn("--csaf-document-id\n            VEXCALIBUR-ORB-ACCEPTANCE", config)
        self.assertIn(
            "--output\n            artifacts/acceptance/vexcalibur-orb-acceptance.json",
            config,
        )

        csaf_example = (REPO_ROOT / "src/examples/generate_csaf.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--offline", csaf_example)
        self.assertNotIn("--allow-public-osv", csaf_example)
        self.assertIn("--format\n              csaf", csaf_example)
        self.assertIn(
            "--csaf-document-id\n              EXAMPLE-CSAF-VEX-2026-001",
            csaf_example,
        )
        for option in (
            "--csaf-document-title",
            "--csaf-publisher-name",
            "--csaf-publisher-namespace",
            "--csaf-publisher-category",
            "--csaf-document-status",
        ):
            self.assertIn(option, csaf_example)
        self.assertIn(
            "--output\n              artifacts/example-csaf-vex-2026-001.json",
            csaf_example,
        )

        for relative_path in (
            "tests/fixtures/cyclonedx-sbom.json",
            "tests/fixtures/local-findings.json",
        ):
            with (REPO_ROOT / relative_path).open(encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)

    def test_ci_uses_verified_circleci_cli_archive_installer(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        installer = (REPO_ROOT / "scripts/install-circleci-cli.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("raw.githubusercontent.com/CircleCI-Public/circleci-cli", workflow)
        self.assertIn("scripts/install-circleci-cli.sh", workflow)
        self.assertIn("scripts/validate-circleci.sh", workflow)
        self.assertIn(
            "https://github.com/CircleCI-Public/circleci-cli/releases/download",
            installer,
        )
        self.assertNotIn("install.sh", installer)

        version_match = re.search(r'CIRCLECI_CLI_VERSION: "([0-9.]+)"', workflow)
        checksum_match = re.search(r'CIRCLECI_CLI_CHECKSUMS_SHA256: "([0-9a-f]+)"', workflow)
        if version_match is None or checksum_match is None:
            self.fail("CircleCI CLI version and checksum pins must both be present")
        self.assertTrue(CIRCLECI_CLI_VERSION_PATTERN.fullmatch(version_match.group(1)))
        self.assertTrue(SHA256_PATTERN.fullmatch(checksum_match.group(1)))

        tool_versions = (REPO_ROOT / ".tool-versions").read_text(encoding="utf-8")
        local_version_match = re.search(
            r"^circleci-cli ([0-9.]+)$", tool_versions, re.MULTILINE
        )
        if local_version_match is None:
            self.fail(".tool-versions must pin circleci-cli")
        self.assertEqual(local_version_match.group(1), version_match.group(1))

    def test_circleci_orb_imports_use_exact_versions(self) -> None:
        references: dict[str, str] = {}
        local_orbs: dict[str, object] = {}
        for relative_path in (".circleci/config.yml", ".circleci/test-deploy.yml"):
            document = yaml.safe_load(
                (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            )
            for name, reference in document["orbs"].items():
                if isinstance(reference, str):
                    references[f"{relative_path}:{name}"] = reference
                    self.assertRegex(reference, EXACT_ORB_REFERENCE_PATTERN)
                else:
                    local_orbs[f"{relative_path}:{name}"] = reference

        self.assertEqual(
            references,
            {
                ".circleci/config.yml:orb-tools": "circleci/orb-tools@12.3.3",
                ".circleci/config.yml:shellcheck": "circleci/shellcheck@3.2.0",
                ".circleci/test-deploy.yml:orb-tools": (
                    "circleci/orb-tools@12.3.3"
                ),
            },
        )
        self.assertEqual(
            local_orbs,
            {".circleci/test-deploy.yml:vexcalibur": {}},
        )

    def test_orb_publisher_jobs_use_immutable_executor_and_verified_handoff(
        self,
    ) -> None:
        setup = yaml.safe_load(
            (REPO_ROOT / ".circleci/config.yml").read_text(encoding="utf-8")
        )
        deployment = yaml.safe_load(
            (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
        )

        for document in (setup, deployment):
            self.assertNotIn(":latest", json.dumps(document))
            self.assertEqual(
                document["executors"]["pinned-circleci-cli"],
                {"docker": [{"image": PINNED_CIRCLECI_CLI_IMAGE}]},
            )

        setup_jobs = setup["workflows"]["lint-pack"]["jobs"]
        for orb_job in ("orb-tools/pack", "orb-tools/continue"):
            invocation = next(
                job[orb_job]
                for job in setup_jobs
                if isinstance(job, dict) and orb_job in job
            )
            self.assertEqual(invocation["executor"], "pinned-circleci-cli")

        deployment_jobs = deployment["workflows"]["test-deploy"]["jobs"]

        def named_invocation(name: str) -> tuple[str, dict[str, Any]]:
            for job in deployment_jobs:
                if not isinstance(job, dict):
                    continue
                orb_job, parameters = next(iter(job.items()))
                if parameters.get("name") == name:
                    return orb_job, parameters
            self.fail(f"workflow job not found: {name}")

        for name in ("pack-dev", "pack-release"):
            orb_job, parameters = named_invocation(name)
            self.assertEqual(orb_job, "orb-tools/pack")
            self.assertEqual(parameters["executor"], "pinned-circleci-cli")
            self.assertFalse(parameters["persist_to_workspace"])
            self.assertNotIn("context", parameters)
            self.assertEqual(parameters["post-steps"][0], "record-packed-orb")
            self.assertEqual(
                parameters["post-steps"][-1],
                {
                    "persist_to_workspace": {
                        "root": "dist",
                        "paths": ["orb.yml", "orb.yml.sha256"],
                    }
                },
            )

        for name in ("publish-dev", "publish-release"):
            orb_job, parameters = named_invocation(name)
            self.assertEqual(orb_job, "orb-tools/publish")
            self.assertEqual(parameters["executor"], "pinned-circleci-cli")
            self.assertFalse(parameters["attach_workspace"])
            self.assertFalse(parameters["enable_pr_comment"])
            self.assertEqual(parameters["context"], "orb-publishing")
            self.assertEqual(
                parameters["pre-steps"],
                [
                    {"attach_workspace": {"at": "dist"}},
                    "verify-packed-orb",
                ],
            )


if __name__ == "__main__":
    unittest.main()
