#!/usr/bin/env python3
"""Check the fixed offline fixture produced by the published Orb and CLI."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


FORMATS = ("cyclonedx", "openvex", "csaf", "spdx3")
VULNERABILITY = "CVE-2099-0001"
PURL = "pkg:pypi/example-library@1.2.3"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_document(document: dict[str, Any], output_format: str) -> None:
    """Check fixture identity and analysis state, not full format conformance."""
    if output_format == "cyclonedx":
        require(document["bomFormat"] == "CycloneDX", "wrong CycloneDX format")
        require(document["specVersion"] == "1.6", "wrong CycloneDX version")
        components = document["components"]
        require(len(components) == 1, "wrong CycloneDX component count")
        require(components[0]["purl"] == PURL, "wrong CycloneDX component")
        findings = document["vulnerabilities"]
        require(len(findings) == 1, "wrong CycloneDX finding count")
        require(findings[0]["id"] == VULNERABILITY, "wrong CycloneDX finding")
        require(
            findings[0]["affects"] == [{"ref": components[0]["bom-ref"]}],
            "wrong CycloneDX finding target",
        )
        require(findings[0]["analysis"]["state"] == "in_triage", "wrong analysis")
    elif output_format == "openvex":
        require(
            document["@context"] == "https://openvex.dev/ns/v0.2.0",
            "wrong OpenVEX version",
        )
        statements = document["statements"]
        require(len(statements) == 1, "wrong OpenVEX statement count")
        statement = statements[0]
        require(statement["status"] == "under_investigation", "wrong analysis")
        require(statement["vulnerability"]["name"] == VULNERABILITY, "wrong finding")
        require([p["@id"] for p in statement["products"]] == [PURL], "wrong product")
    elif output_format == "csaf":
        require(document["document"]["category"] == "csaf_vex", "wrong CSAF type")
        require(document["document"]["csaf_version"] == "2.0", "wrong CSAF version")
        products = document["product_tree"]["full_product_names"]
        require(len(products) == 1, "wrong CSAF product count")
        require(
            products[0]["product_identification_helper"]["purl"] == PURL,
            "wrong CSAF product",
        )
        findings = document["vulnerabilities"]
        require(len(findings) == 1, "wrong CSAF finding count")
        require(findings[0]["cve"] == VULNERABILITY, "wrong CSAF finding")
        require(
            findings[0]["product_status"]
            == {"under_investigation": [products[0]["product_id"]]},
            "wrong CSAF analysis",
        )
    elif output_format == "spdx3":
        require(
            document["@context"] == "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
            "wrong SPDX version",
        )
        graph = document["@graph"]
        packages = [e for e in graph if e["type"] == "software_Package"]
        require(len(packages) == 1, "wrong SPDX package count")
        require(packages[0]["software_packageUrl"] == PURL, "wrong SPDX package")
        findings = [e for e in graph if e["type"] == "security_Vulnerability"]
        require(len(findings) == 1, "wrong SPDX finding count")
        require(findings[0]["name"] == VULNERABILITY, "wrong SPDX finding")
        assessments = [
            e
            for e in graph
            if e["type"] == "security_VexUnderInvestigationVulnAssessmentRelationship"
        ]
        require(len(assessments) == 1, "wrong SPDX analysis")
        require(assessments[0]["from"] == findings[0]["spdxId"], "wrong SPDX source")
        require(assessments[0]["to"] == [packages[0]["spdxId"]], "wrong SPDX target")
    else:
        raise ValueError("unsupported consumer format")


def verify(directory: Path, package_spec: str, orb_reference: str) -> None:
    """Verify all fixtures before recording the tested dependency versions."""
    match = re.fullmatch(r"vexcalibur==([0-9]+\.[0-9]+\.[0-9]+)", package_spec)
    require(match is not None, "consumer requires an exact stable CLI version")
    require(
        re.fullmatch(r"vexcalibur-dev/vexcalibur@[0-9]+\.[0-9]+\.[0-9]+", orb_reference)
        is not None,
        "consumer requires an exact production Orb version",
    )
    version = package_spec.removeprefix("vexcalibur==")
    for output_format in FORMATS:
        data = (directory / f"{output_format}.json").read_bytes()
        report = (directory / f"{output_format}-report.json").read_bytes()
        expected = {
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
            "vexcalibur_version": version,
        }
        canonical = json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
        require(
            report == canonical.encode("utf-8"), f"{output_format}: report mismatch"
        )
        check_document(json.loads(data), output_format)

    evidence = {
        "orb": orb_reference,
        "package_spec": package_spec,
        "formats": list(FORMATS),
    }
    (directory / "consumer-versions.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]),
        os.environ["CONSUMER_PACKAGE_SPEC"],
        os.environ["CONSUMER_ORB"],
    )
    print("Published Orb consumer outputs and execution reports verified.")
