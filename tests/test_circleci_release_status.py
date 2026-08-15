from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import circleci_api  # noqa: E402
from circleci_api import CircleCIError, Client, decode_json  # noqa: E402
import circleci_release_status as status  # noqa: E402


PIPELINE_1 = "ec2bf675-4d47-4b54-b468-9e3336ea7116"
PIPELINE_2 = "d502aa7a-079a-4276-8a1f-e0163eeef12e"
WORKFLOW_1 = "48c9ebc2-8535-40d8-b0cb-b2e39cbd84e5"
WORKFLOW_2 = "6d82786b-a2b3-4329-a6e2-a78e5e5dc34a"
WORKFLOW_3 = "64eefeb5-45eb-4882-b635-f9905c1b3f5d"
RELEASE_REVISION = "a" * 40


def pipeline(
    *, pipeline_id: str, number: int, tag: str, revision: str = RELEASE_REVISION
) -> dict[str, object]:
    return {
        "id": pipeline_id,
        "number": number,
        "project_slug": status.PROJECT_SLUG,
        "vcs": {"tag": tag, "revision": revision},
    }


def workflow(
    *, name: str, workflow_id: str, created_at: str, workflow_status: str
) -> dict[str, object]:
    return {
        "name": name,
        "id": workflow_id,
        "created_at": created_at,
        "status": workflow_status,
    }


class CircleCIJsonTests(unittest.TestCase):
    def test_strict_json_accepts_a_document(self) -> None:
        self.assertEqual(decode_json(b'{"items":[]}'), {"items": []})

    def test_strict_json_rejects_duplicate_and_nonfinite_values(self) -> None:
        with self.assertRaisesRegex(CircleCIError, "duplicate JSON key"):
            decode_json(b'{"items":[],"items":[]}')
        with self.assertRaisesRegex(CircleCIError, "non-finite"):
            decode_json(b'{"value":NaN}')

    def test_repeated_pagination_token_is_rejected(self) -> None:
        class RepeatingClient(Client):
            def __init__(self) -> None:
                pass

            def _request_json(
                self,
                endpoint: str,
                *,
                method: str,
                payload: dict[str, object] | None = None,
                query: dict[str, str] | None = None,
                paginated: bool = False,
            ) -> dict[str, object]:
                del endpoint, method, payload, query, paginated
                return {"items": [], "next_page_token": "same-token"}

        with self.assertRaisesRegex(CircleCIError, "repeated pagination token"):
            RepeatingClient().fetch_pages(
                f"project/{circleci_api.PROJECT_SLUG}/pipeline"
            )

    def test_falsey_nonstring_pagination_tokens_are_rejected(self) -> None:
        for token in (False, 0, [], {}):
            with self.subTest(token=token):

                class MalformedClient(Client):
                    def __init__(self) -> None:
                        pass

                    def _request_json(
                        self,
                        endpoint: str,
                        *,
                        method: str,
                        payload: dict[str, object] | None = None,
                        query: dict[str, str] | None = None,
                        paginated: bool = False,
                    ) -> dict[str, object]:
                        del endpoint, method, payload, query, paginated
                        return {"items": [], "next_page_token": token}

                with self.assertRaisesRegex(CircleCIError, "pagination token"):
                    MalformedClient().fetch_pages(
                        f"project/{circleci_api.PROJECT_SLUG}/pipeline"
                    )

    def test_client_never_forwards_authentication_across_redirects(self) -> None:
        target_requests: list[str | None] = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                target_requests.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = do_GET

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        target_url = f"http://127.0.0.1:{target.server_port}/capture"

        class RedirectHandler(BaseHTTPRequestHandler):
            def redirect(self) -> None:
                self.send_response(302)
                self.send_header("Location", target_url)
                self.end_headers()

            do_GET = redirect
            do_POST = redirect

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        servers = (target, redirect)
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in servers
        ]
        for thread in threads:
            thread.start()
        try:
            api_root = f"http://127.0.0.1:{redirect.server_port}"
            with patch.object(circleci_api, "API_ROOT", api_root):
                client = Client("operator-token")
                with self.assertRaisesRegex(CircleCIError, "redirected"):
                    client.fetch_json("me/collaborations")
                with self.assertRaisesRegex(CircleCIError, "redirected"):
                    client.post_json(f"workflow/{WORKFLOW_1}/rerun", {"attempt": 1})
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(target_requests, [])

    def test_endpoint_policy_is_a_complete_authorization_matrix(self) -> None:
        cases = (
            ("GET", "me/collaborations", False, frozenset(), frozenset()),
            (
                "GET",
                f"project/{circleci_api.PROJECT_SLUG}",
                False,
                frozenset(),
                frozenset(),
            ),
            (
                "GET",
                f"project/{circleci_api.PROJECT_SLUG}/pipeline",
                True,
                frozenset(),
                frozenset({"page-token"}),
            ),
            (
                "GET",
                "context",
                True,
                frozenset({"owner-id"}),
                frozenset({"owner-id", "page-token"}),
            ),
            (
                "GET",
                f"context/{WORKFLOW_1}/restrictions",
                True,
                frozenset(),
                frozenset({"page-token"}),
            ),
            (
                "GET",
                f"pipeline/{PIPELINE_1}/workflow",
                True,
                frozenset(),
                frozenset({"page-token"}),
            ),
            ("GET", f"workflow/{WORKFLOW_1}", False, frozenset(), frozenset()),
            (
                "GET",
                f"workflow/{WORKFLOW_1}/job",
                True,
                frozenset(),
                frozenset({"page-token"}),
            ),
            (
                "POST",
                f"workflow/{WORKFLOW_1}/rerun",
                False,
                frozenset(),
                frozenset(),
            ),
        )
        self.assertEqual(len(cases), len(circleci_api.ENDPOINT_POLICIES))
        for method, endpoint, paginated, required_query, allowed_query in cases:
            with self.subTest(method=method, endpoint=endpoint):
                matches = [
                    policy
                    for policy in circleci_api.ENDPOINT_POLICIES
                    if policy.method == method and policy.endpoint.fullmatch(endpoint)
                ]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].paginated, paginated)
                self.assertEqual(matches[0].required_query, required_query)
                self.assertEqual(matches[0].allowed_query, allowed_query)

    def test_client_rejects_unapproved_operations_and_queries(self) -> None:
        client = Client("operator-token")
        rejected = (
            "https://example.com/capture",
            "../capture",
            "workflow/not-a-uuid",
            f"workflow/{WORKFLOW_1}/rerun?target=example.com",
            f"workflow/{WORKFLOW_1}/cancel",
        )
        for endpoint in rejected:
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(CircleCIError, "not authorized"):
                    client.fetch_json(endpoint)

        with self.assertRaisesRegex(CircleCIError, "query is not authorized"):
            client.fetch_pages("context", query={"target": "example.com"})

        with self.assertRaisesRegex(CircleCIError, "missing required parameters"):
            client.fetch_pages("context")
        with self.assertRaisesRegex(CircleCIError, "organization ID"):
            client.fetch_pages("context", query={"owner-id": "not-a-uuid"})
        with self.assertRaisesRegex(CircleCIError, "pagination mode"):
            client.fetch_json(f"project/{circleci_api.PROJECT_SLUG}/pipeline")
        with self.assertRaisesRegex(CircleCIError, "pagination mode"):
            client.fetch_pages("me/collaborations")
        with self.assertRaisesRegex(CircleCIError, "endpoint is not authorized"):
            client.post_json(f"workflow/{WORKFLOW_1}", {})
        with self.assertRaisesRegex(CircleCIError, "endpoint is not authorized"):
            client.fetch_json(f"workflow/{WORKFLOW_1}/rerun")

    def test_client_requires_canonical_uuid_values(self) -> None:
        self.assertEqual(
            circleci_api.require_uuid(WORKFLOW_1, label="workflow"), WORKFLOW_1
        )
        for value in (WORKFLOW_1.upper(), f"{{{WORKFLOW_1}}}"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(CircleCIError, "not a canonical UUID"):
                    circleci_api.require_uuid(value, label="workflow")
        with self.assertRaisesRegex(CircleCIError, "not a UUID"):
            circleci_api.require_uuid("not-a-uuid", label="workflow")

    def test_pagination_tokens_cannot_change_the_authorized_endpoint(self) -> None:
        request_paths: list[str] = []
        pagination_token = "../admin?target=example.com&action=delete"

        class ApiHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                request_paths.append(self.path)
                body = (
                    json.dumps(
                        {"items": [], "next_page_token": pagination_token},
                        separators=(",", ":"),
                    ).encode()
                    if len(request_paths) == 1
                    else b'{"items":[]}'
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(
                circleci_api,
                "API_ROOT",
                f"http://127.0.0.1:{server.server_port}",
            ):
                client = Client("operator-token")
                self.assertEqual(
                    client.fetch_pages(f"project/{circleci_api.PROJECT_SLUG}/pipeline"),
                    [],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        endpoint = f"/project/{circleci_api.PROJECT_SLUG}/pipeline"
        self.assertEqual(
            request_paths,
            [
                endpoint,
                endpoint
                + "?page-token=..%2Fadmin%3Ftarget%3Dexample.com%26action%3Ddelete",
            ],
        )


class CircleCIReleaseStatusTests(unittest.TestCase):
    def test_highest_numbered_matching_tag_pipeline_is_selected(self) -> None:
        pipelines = [
            pipeline(pipeline_id=PIPELINE_1, number=12, tag="v0.1.0"),
            pipeline(pipeline_id=PIPELINE_2, number=14, tag="v0.1.0"),
            pipeline(pipeline_id=WORKFLOW_1, number=15, tag="v0.2.0"),
        ]

        selected = status.pipeline_for_tag(
            pipelines,
            tag="v0.1.0",
            revision=RELEASE_REVISION,
        )

        self.assertEqual(selected["id"], PIPELINE_2)

    def test_missing_and_duplicate_pipeline_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_tag([], tag="v0.1.0", revision=RELEASE_REVISION)
        duplicate = [
            pipeline(pipeline_id=PIPELINE_1, number=12, tag="v0.1.0"),
            pipeline(pipeline_id=PIPELINE_2, number=12, tag="v0.1.0"),
        ]
        with self.assertRaisesRegex(CircleCIError, "duplicate pipeline number"):
            status.pipeline_for_tag(
                duplicate,
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

    def test_matching_pipeline_requires_exact_project_and_revision(self) -> None:
        wrong_revision = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="v0.1.0",
            revision="b" * 40,
        )
        with self.assertRaisesRegex(CircleCIError, "release revision"):
            status.pipeline_for_tag(
                [wrong_revision],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

        wrong_project = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="v0.1.0",
        )
        wrong_project["project_slug"] = "gh/example/other"
        with self.assertRaisesRegex(CircleCIError, "project identity"):
            status.pipeline_for_tag(
                [wrong_project],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

    def test_latest_attempt_per_workflow_name_controls_verification(self) -> None:
        workflows = [
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-15T12:00:00Z",
                workflow_status="failed",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-15T12:05:00Z",
                workflow_status="success",
            ),
            workflow(
                name="lint-pack",
                workflow_id=WORKFLOW_3,
                created_at="2026-08-15T12:06:00Z",
                workflow_status="success",
            ),
        ]

        self.assertEqual(
            status.verify_workflow_projection(workflows),
            (
                f"lint-pack\tsuccess\t{WORKFLOW_3}",
                f"test-deploy\tsuccess\t{WORKFLOW_2}",
            ),
        )

    def test_failed_latest_attempt_is_rejected(self) -> None:
        workflows = [
            workflow(
                name="lint-pack",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-15T12:00:00+00:00",
                workflow_status="success",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-15T12:01:00+00:00",
                workflow_status="failed",
            ),
        ]

        with self.assertRaisesRegex(
            CircleCIError,
            f"test-deploy=failed.*{WORKFLOW_2}",
        ):
            status.verify_workflow_projection(workflows)

    def test_original_release_workflow_excludes_later_ssh_derived_attempt(self) -> None:
        workflows = [
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-15T12:01:00+00:00",
                workflow_status="failed",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-15T12:00:00+00:00",
                workflow_status="failed",
            ),
        ]

        self.assertEqual(
            status.original_workflow_attempt(workflows, name="test-deploy")["id"],
            WORKFLOW_1,
        )

    def test_cli_failure_identifies_the_workflow_to_rerun(self) -> None:
        workflows = [
            workflow(
                name="lint-pack",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-15T12:00:00+00:00",
                workflow_status="success",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-15T12:01:00+00:00",
                workflow_status="failed",
            ),
        ]

        class FakeClient:
            def __init__(self, token: str) -> None:
                self.token = token

            def fetch_pages(self, path: str) -> list[dict[str, object]]:
                return workflows

        with (
            patch.dict(os.environ, {"CIRCLECI_OPERATOR_TOKEN": "operator-token"}),
            patch.object(status, "Client", FakeClient),
            patch.object(
                sys,
                "argv",
                ["circleci_release_status.py", "verify-pipeline", PIPELINE_1],
            ),
            self.assertRaises(SystemExit) as raised,
        ):
            status.main()

        self.assertIn(WORKFLOW_2, str(raised.exception))

    def test_workflow_timestamp_must_include_a_time_zone(self) -> None:
        workflows = [
            workflow(
                name="publish-release",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-15T12:00:00",
                workflow_status="success",
            )
        ]

        with self.assertRaisesRegex(CircleCIError, "time zone"):
            status.latest_workflow_attempts(workflows)

    def test_all_required_release_jobs_must_succeed(self) -> None:
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(sorted(status.REQUIRED_RELEASE_JOBS), start=1)
        ]

        lines = status.verify_release_jobs(jobs)

        self.assertEqual(len(lines), len(status.REQUIRED_RELEASE_JOBS))
        self.assertTrue(all(line.startswith("job:") for line in lines))

    def test_missing_or_failed_release_job_is_rejected(self) -> None:
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(sorted(status.REQUIRED_RELEASE_JOBS), start=1)
        ]
        with self.assertRaisesRegex(CircleCIError, "missing jobs"):
            status.verify_release_jobs(jobs[:-1])

        jobs[-1]["status"] = "failed"
        with self.assertRaisesRegex(CircleCIError, "not successful"):
            status.verify_release_jobs(jobs)

    def test_exact_rerun_workflow_is_followed_to_success(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.documents = [
                    {"id": WORKFLOW_1, "status": "running"},
                    {"id": WORKFLOW_1, "status": "success"},
                ]

            def fetch_json(self, path: str) -> dict[str, str]:
                self.assert_path = path
                return self.documents.pop(0)

        client = FakeClient()
        original_sleep = status.time.sleep
        status.time.sleep = lambda _seconds: None
        try:
            result = status.wait_for_workflow(
                client,  # type: ignore[arg-type]
                workflow_id=WORKFLOW_1,
                timeout=30,
                poll_interval=1,
            )
        finally:
            status.time.sleep = original_sleep

        self.assertEqual(result, "success")
        self.assertEqual(client.assert_path, f"workflow/{WORKFLOW_1}")

    def test_rerun_uses_json_client_and_validates_returned_identity(self) -> None:
        class FakeClient:
            def post_json(
                self, path: str, payload: dict[str, object]
            ) -> dict[str, str]:
                self.path = path
                self.payload = payload
                return {"workflow_id": WORKFLOW_2}

        client = FakeClient()
        result = status.rerun_workflow(  # type: ignore[arg-type]
            client,
            workflow_id=WORKFLOW_1,
        )

        self.assertEqual(result, WORKFLOW_2)
        self.assertEqual(client.path, f"workflow/{WORKFLOW_1}/rerun")
        self.assertEqual(client.payload, {"from_failed": False})


if __name__ == "__main__":
    unittest.main()
