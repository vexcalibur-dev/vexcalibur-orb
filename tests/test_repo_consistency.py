from __future__ import annotations

import json
import re
import sys
import tomllib
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import circleci_release_status as release_status  # noqa: E402
from circleci_config_policy import (  # noqa: E402
    CircleCIConfigPolicyError,
    reject_publishing_capabilities,
)
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
PLANNED_PRODUCTION_ORB_REFERENCE = "vexcalibur-dev/vexcalibur@0.1.1"
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
                for line in (REPO_ROOT / ".tool-versions")
                .read_text(encoding="utf-8")
                .splitlines()
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
        self.assertEqual(
            setup_python["with"]["python-version"], tool_versions["python"]
        )

        setup_tools = next(
            step
            for step in ci_workflow["jobs"]["quality"]["steps"]
            if step.get("name") == "Install workflow tools"
        )
        self.assertEqual(setup_tools["with"]["install_args"], "shellcheck")
        validate_shell = next(
            step
            for step in ci_workflow["jobs"]["quality"]["steps"]
            if step.get("name") == "Validate shell"
        )
        self.assertEqual(
            validate_shell["run"],
            "bash -n scripts/*.sh\n"
            "bash -n src/scripts/*.sh\n"
            "shellcheck scripts/*.sh\n"
            "shellcheck src/scripts/*.sh\n",
        )

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

    def test_release_coordination_branch_does_not_start_circleci_jobs(self) -> None:
        configuration = yaml.safe_load(
            (REPO_ROOT / ".circleci/config.yml").read_text(encoding="utf-8")
        )

        self.assertEqual(
            configuration["filters"]["branches"]["ignore"],
            "release-coordination",
        )

    def test_examples_use_the_planned_production_orb(self) -> None:
        for example_path in sorted((REPO_ROOT / "src/examples").glob("*.yml")):
            with self.subTest(example=example_path.name):
                example = yaml.safe_load(example_path.read_text(encoding="utf-8"))
                reference = example["usage"]["orbs"]["vexcalibur"]
                self.assertEqual(reference, PLANNED_PRODUCTION_ORB_REFERENCE)

    def test_orb_source_meets_registry_quality_contract(self) -> None:
        metadata = yaml.safe_load(
            (REPO_ROOT / "src/@orb.yml").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(metadata["description"].strip()), 64)
        self.assertEqual(
            metadata["display"],
            {
                "home_url": "https://github.com/vexcalibur-dev/vexcalibur",
                "source_url": "https://github.com/vexcalibur-dev/vexcalibur-orb",
            },
        )

        component_paths = sorted(
            path
            for directory in ("commands", "executors", "examples", "jobs")
            for path in (REPO_ROOT / "src" / directory).glob("*.yml")
        )
        self.assertTrue(component_paths)
        self.assertTrue(any("examples" in path.parts for path in component_paths))

        def verify_component_contract(value: Any) -> None:
            if isinstance(value, dict):
                parameters = value.get("parameters")
                if isinstance(parameters, dict):
                    for name, definition in parameters.items():
                        self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
                        default = definition.get("default")
                        if isinstance(default, str):
                            self.assertNotIn("$", default)
                if "run" in value:
                    self.assertIsInstance(value["run"], dict)
                    self.assertTrue(value["run"].get("name", "").strip())
                    command = value["run"].get("command", "")
                    self.assertIsInstance(command, str)
                    if len(command) > 64:
                        self.assertIn("<<include(", command)
                for child in value.values():
                    verify_component_contract(child)
            elif isinstance(value, list):
                for child in value:
                    verify_component_contract(child)

        for path in component_paths:
            with self.subTest(component=path.relative_to(REPO_ROOT)):
                self.assertNotIn("-", path.stem)
                component = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(component.get("description"), str)
                self.assertTrue(component["description"].strip())
                verify_component_contract(component)

    def test_scorecard_workflow_is_pinned_and_least_privilege(self) -> None:
        workflow = yaml.load(
            (REPO_ROOT / ".github/workflows/scorecard.yml").read_text(encoding="utf-8"),
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
        self.assertIn(
            "--csaf-document-id\n            VEXCALIBUR-ORB-ACCEPTANCE", config
        )
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
        workflow_paths = (
            ".github/workflows/ci.yml",
            ".github/workflows/publish-development.yml",
            ".github/workflows/release.yml",
        )
        workflows = {
            path: (REPO_ROOT / path).read_text(encoding="utf-8")
            for path in workflow_paths
        }
        workflow = workflows[".github/workflows/ci.yml"]
        installer = (REPO_ROOT / "scripts/install-circleci-cli.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "raw.githubusercontent.com/CircleCI-Public/circleci-cli", workflow
        )
        self.assertIn("scripts/install-circleci-cli.sh", workflow)
        self.assertIn("scripts/validate-circleci.sh", workflow)
        self.assertIn(
            "https://github.com/CircleCI-Public/circleci-cli/releases/download",
            installer,
        )
        self.assertNotIn("install.sh", installer)

        version_match = re.search(r'CIRCLECI_CLI_VERSION: "([0-9.]+)"', workflow)
        checksum_match = re.search(
            r'CIRCLECI_CLI_CHECKSUMS_SHA256: "([0-9a-f]+)"', workflow
        )
        if version_match is None or checksum_match is None:
            self.fail("CircleCI CLI version and checksum pins must both be present")
        self.assertTrue(CIRCLECI_CLI_VERSION_PATTERN.fullmatch(version_match.group(1)))
        self.assertTrue(SHA256_PATTERN.fullmatch(checksum_match.group(1)))
        for path, content in workflows.items():
            with self.subTest(workflow=path):
                self.assertIn(
                    f'CIRCLECI_CLI_VERSION: "{version_match.group(1)}"', content
                )
                self.assertIn(
                    "CIRCLECI_CLI_CHECKSUMS_SHA256: "
                    f'"{checksum_match.group(1)}"',
                    content,
                )
                self.assertIn("scripts/install-circleci-cli.sh", content)

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
            for name, reference in document.get("orbs", {}).items():
                if isinstance(reference, str):
                    references[f"{relative_path}:{name}"] = reference
                    self.assertRegex(reference, EXACT_ORB_REFERENCE_PATTERN)
                else:
                    local_orbs[f"{relative_path}:{name}"] = reference

        self.assertEqual(
            references,
            {},
        )
        self.assertEqual(
            local_orbs,
            {".circleci/test-deploy.yml:vexcalibur": {}},
        )

    def test_circleci_jobs_validate_without_a_publishing_credential(
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

        for job_name in ("validate-orb", "continue"):
            self.assertEqual(
                setup["jobs"][job_name]["executor"], "pinned-circleci-cli"
            )

        deployment_jobs = deployment["workflows"]["test-deploy"]["jobs"]

        parameters = next(
            invocation["pack-release"]
            for invocation in deployment_jobs
            if isinstance(invocation, dict) and "pack-release" in invocation
        )
        self.assertNotIn("context", parameters)
        self.assertEqual(
            deployment["jobs"]["pack-release"]["executor"],
            "pinned-circleci-cli",
        )

        reject_publishing_capabilities(setup, path=".circleci/config.yml")
        reject_publishing_capabilities(
            deployment, path=".circleci/test-deploy.yml"
        )
        for invocation in deployment_jobs:
            if isinstance(invocation, str):
                job_name = invocation
                display_name = invocation
            else:
                job_name, job_parameters = next(iter(invocation.items()))
                display_name = job_parameters.get("name", job_name)
            self.assertNotIn("publish", job_name)
            self.assertNotIn("publish", display_name)

    def test_circleci_policy_rejects_any_context_or_orb_publisher(self) -> None:
        forbidden = (
            {"workflows": {"build": {"jobs": [{"test": {"context": "renamed"}}]}}},
            {
                "jobs": {
                    "test": {
                        "steps": [
                            {
                                "run": {
                                    "command": "circleci  orb\n publish orb.yml org/orb@1.0"
                                }
                            }
                        ]
                    }
                }
            },
            {
                "jobs": {
                    "test": {
                        "steps": [
                            {
                                "run": {
                                    "command": (
                                        "cir'cleci' orb publish candidate reference"
                                    )
                                }
                            }
                        ]
                    }
                }
            },
            {
                "jobs": {
                    "test": {
                        "steps": [
                            {
                                "run": {
                                    "command": (
                                        "circleci orb \\\npublish candidate reference"
                                    )
                                }
                            }
                        ]
                    }
                }
            },
        )
        for document in forbidden:
            with self.subTest(document=document):
                with self.assertRaises(CircleCIConfigPolicyError):
                    reject_publishing_capabilities(document, path="test")

    def test_circleci_evidence_allowlists_match_the_continuation_config(
        self,
    ) -> None:
        deployment = yaml.safe_load(
            (REPO_ROOT / ".circleci/test-deploy.yml").read_text(encoding="utf-8")
        )
        invocations = deployment["workflows"]["test-deploy"]["jobs"]
        branch_jobs: set[str] = set()
        tag_jobs: set[str] = set()
        for invocation in invocations:
            if isinstance(invocation, str):
                name = invocation
                filters: dict[str, Any] = {}
            else:
                job_name, parameters = next(iter(invocation.items()))
                name = parameters.get("name", job_name)
                filters = parameters.get("filters", {})
            branches = filters.get("branches", {})
            tags = filters.get("tags", {})
            if branches.get("ignore") != "/.*/":
                branch_jobs.add(name)
            if tags.get("only") in (
                "/.*/",
                "/^v[0-9]+\\.[0-9]+\\.[0-9]+$/",
            ):
                tag_jobs.add(name)

        self.assertEqual(branch_jobs, release_status.REQUIRED_DEVELOPMENT_JOBS)
        self.assertEqual(tag_jobs, release_status.REQUIRED_RELEASE_JOBS)

        setup = yaml.safe_load(
            (REPO_ROOT / ".circleci/config.yml").read_text(encoding="utf-8")
        )
        setup_jobs = {
            invocation
            if isinstance(invocation, str)
            else next(iter(invocation))
            for invocation in setup["workflows"]["lint-pack"]["jobs"]
        }
        self.assertEqual(setup_jobs, release_status.REQUIRED_SETUP_JOBS)

    def test_circleci_evidence_images_are_digest_pinned(self) -> None:
        for path in (".circleci/config.yml", ".circleci/test-deploy.yml"):
            document = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
            images: list[str] = []

            def collect_images(value: Any) -> None:
                if isinstance(value, dict):
                    docker = value.get("docker")
                    if isinstance(docker, list):
                        images.extend(
                            image["image"]
                            for image in docker
                            if isinstance(image, dict)
                            and isinstance(image.get("image"), str)
                        )
                    for child in value.values():
                        collect_images(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_images(child)

            collect_images(document)
            self.assertTrue(images, path)
            for image in images:
                with self.subTest(path=path, image=image):
                    self.assertRegex(image, r"@sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
