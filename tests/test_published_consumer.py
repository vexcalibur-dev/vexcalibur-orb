from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "published_consumer", ROOT / "scripts/check-published-consumer.py"
)
assert SPEC is not None and SPEC.loader is not None
consumer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consumer)


class PublishedConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.package = "vexcalibur==1.2.3"
        self.orb = "vexcalibur-dev/vexcalibur@4.5.6"
        self.documents = {
            "cyclonedx": {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [{"bom-ref": "component", "purl": consumer.PURL}],
                "vulnerabilities": [
                    {
                        "id": consumer.VULNERABILITY,
                        "analysis": {"state": "in_triage"},
                        "affects": [{"ref": "component"}],
                    }
                ],
            },
            "openvex": {
                "@context": "https://openvex.dev/ns/v0.2.0",
                "statements": [
                    {
                        "status": "under_investigation",
                        "vulnerability": {"name": consumer.VULNERABILITY},
                        "products": [{"@id": consumer.PURL}],
                    }
                ],
            },
            "csaf": {
                "document": {"category": "csaf_vex", "csaf_version": "2.0"},
                "product_tree": {
                    "full_product_names": [
                        {
                            "product_id": "component",
                            "product_identification_helper": {"purl": consumer.PURL},
                        }
                    ]
                },
                "vulnerabilities": [
                    {
                        "cve": consumer.VULNERABILITY,
                        "product_status": {"under_investigation": ["component"]},
                    }
                ],
            },
            "spdx3": {
                "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
                "@graph": [
                    {
                        "type": "software_Package",
                        "spdxId": "package",
                        "software_packageUrl": consumer.PURL,
                    },
                    {
                        "type": "security_Vulnerability",
                        "spdxId": "finding",
                        "name": consumer.VULNERABILITY,
                    },
                    {
                        "type": "security_VexUnderInvestigationVulnAssessmentRelationship",
                        "from": "finding",
                        "to": ["package"],
                    },
                ],
            },
        }
        for output_format, document in self.documents.items():
            data = json.dumps(document).encode("utf-8")
            (self.directory / f"{output_format}.json").write_bytes(data)
            report = {
                "analysis_state_counts": {"in_triage": 1},
                "command": "generate",
                "component_count": 1,
                "document": {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
                "finding_count": 1,
                "finding_source": "local_file",
                "inventory_source": "sbom_file",
                "output_format": output_format,
                "schema_version": 1,
                "vexcalibur_version": "1.2.3",
            }
            self.write_report(output_format, report)

    def write_report(self, output_format: str, report: dict) -> None:
        (self.directory / f"{output_format}-report.json").write_text(
            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def verify(self) -> None:
        consumer.verify(self.directory, self.package, self.orb)

    def test_records_dependency_versions_after_all_formats_pass(self) -> None:
        self.verify()
        evidence = json.loads((self.directory / "consumer-versions.json").read_text())
        self.assertEqual(
            evidence,
            {
                "orb": self.orb,
                "package_spec": self.package,
                "formats": list(consumer.FORMATS),
            },
        )

    def test_rejects_changed_document_for_each_format(self) -> None:
        for output_format in consumer.FORMATS:
            with self.subTest(output_format=output_format):
                path = self.directory / f"{output_format}.json"
                original = path.read_bytes()
                # Whitespace leaves JSON valid but changes the published bytes.
                path.write_bytes(original + b" ")
                with self.assertRaisesRegex(ValueError, "report mismatch"):
                    self.verify()
                path.write_bytes(original)
        self.assertFalse((self.directory / "consumer-versions.json").exists())

    def test_rejects_wrong_report_contract(self) -> None:
        path = self.directory / "cyclonedx-report.json"
        original = json.loads(path.read_text())
        mutations = {
            "schema_version": True,
            "component_count": 1.0,
            "finding_count": 0,
            "analysis_state_counts": {"resolved": 1},
            "finding_source": "public_osv",
            "inventory_source": "custom",
            "output_format": "csaf",
            "vexcalibur_version": "9.9.9",
            "command": "query-osv",
            "unknown": "field",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = value
                self.write_report("cyclonedx", changed)
                with self.assertRaisesRegex(ValueError, "report mismatch"):
                    self.verify()
        self.assertFalse((self.directory / "consumer-versions.json").exists())

    def test_rejects_wrong_cyclonedx_components_with_matching_digest(self) -> None:
        original = self.documents["cyclonedx"]
        extra = copy.deepcopy(original)
        extra["components"].append({"bom-ref": "extra", "purl": consumer.PURL})
        wrong_purl = copy.deepcopy(original)
        wrong_purl["components"][0]["purl"] = "pkg:pypi/different@1.0.0"
        wrong_target = copy.deepcopy(original)
        wrong_target["vulnerabilities"][0]["affects"] = [{"ref": "other"}]
        for document in (extra, wrong_purl, wrong_target):
            with self.subTest(document=document):
                data = json.dumps(document).encode("utf-8")
                (self.directory / "cyclonedx.json").write_bytes(data)
                report = json.loads(
                    (self.directory / "cyclonedx-report.json").read_text()
                )
                report["document"] = {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                self.write_report("cyclonedx", report)
                with self.assertRaisesRegex(ValueError, "CycloneDX"):
                    self.verify()

    def test_rejects_missing_report_and_flexible_dependency_versions(self) -> None:
        with self.assertRaises(ValueError):
            consumer.verify(self.directory, "vexcalibur>=1", self.orb)
        with self.assertRaises(ValueError):
            consumer.verify(
                self.directory, self.package, "vexcalibur-dev/vexcalibur@dev:alpha"
            )
        (self.directory / "spdx3-report.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_rejects_wrong_analysis_in_each_format(self) -> None:
        for output_format, document in self.documents.items():
            with self.subTest(output_format=output_format):
                changed = json.loads(
                    json.dumps(document)
                    .replace("in_triage", "resolved")
                    .replace("under_investigation", "fixed")
                    .replace("UnderInvestigation", "Fixed")
                )
                with self.assertRaises(ValueError):
                    consumer.check_document(changed, output_format)

    def test_hosted_job_uses_registry_command_and_offline_reports(self) -> None:
        config = yaml.safe_load((ROOT / ".circleci/test-deploy.yml").read_text())
        self.assertIn(
            "registry-consumer", config["jobs"]["format-output-test"]["steps"]
        )
        job = config["commands"]["registry-consumer"]
        verification = next(
            step["run"]
            for step in job["steps"]
            if isinstance(step, dict)
            and "run" in step
            and step["run"]["name"] == "Verify published consumer outputs"
        )
        self.assertEqual(
            verification["environment"]["CONSUMER_ORB"], config["orbs"]["released"]
        )
        self.assertEqual(
            verification["environment"]["CONSUMER_PACKAGE_SPEC"],
            "<< parameters.package_spec >>",
        )
        self.assertRegex(
            job["parameters"]["package_spec"]["default"],
            r"^vexcalibur==[0-9]+\.[0-9]+\.[0-9]+$",
        )
        invocations = [
            step["released/run_vexcalibur"]
            for step in job["steps"]
            if isinstance(step, dict) and "released/run_vexcalibur" in step
        ]
        self.assertEqual(len(invocations), len(consumer.FORMATS))
        formats = set()
        for invocation in invocations:
            self.assertEqual(
                invocation["package_spec"], "<< parameters.package_spec >>"
            )
            self.assertFalse(invocation.get("allow_development_package_spec", False))
            args = invocation["args"].splitlines()
            self.assertIn("--offline", args)
            self.assertNotIn("--allow-public-osv", args)
            output_format = args[args.index("--format") + 1]
            formats.add(output_format)
            self.assertEqual(
                args[args.index("--output") + 1],
                f"artifacts/published-consumer/{output_format}.json",
            )
            self.assertEqual(
                args[args.index("--execution-report") + 1],
                f"artifacts/published-consumer/{output_format}-report.json",
            )
        self.assertEqual(formats, set(consumer.FORMATS))


if __name__ == "__main__":
    unittest.main()
