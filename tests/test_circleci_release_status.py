from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import circleci_api  # noqa: E402
from circleci_api import (  # noqa: E402
    CircleCIError,
    CircleCIRequestError,
    Client,
    decode_json,
)
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
        "trigger": {"type": "webhook"},
        "vcs": {
            "origin_repository_url": status.REPOSITORY_URL,
            "provider_name": "GitHub",
            "revision": revision,
            "tag": tag,
            "target_repository_url": status.REPOSITORY_URL,
        },
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
                query: dict[str, str] | None = None,
                paginated: bool = False,
            ) -> dict[str, object]:
                del endpoint, query, paginated
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
                        query: dict[str, str] | None = None,
                        paginated: bool = False,
                    ) -> dict[str, object]:
                        del endpoint, query, paginated
                        return {"items": [], "next_page_token": token}

                with self.assertRaisesRegex(CircleCIError, "pagination token"):
                    MalformedClient().fetch_pages(
                        f"project/{circleci_api.PROJECT_SLUG}/pipeline"
                    )

    def test_client_rejects_redirects_without_contacting_the_target(self) -> None:
        target_requests = 0

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                nonlocal target_requests
                target_requests += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

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
                client = Client()
                with self.assertRaisesRegex(CircleCIError, "redirected"):
                    client.fetch_pages(
                        f"project/{circleci_api.PROJECT_SLUG}/pipeline"
                    )
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(target_requests, 0)

    def test_endpoint_policy_is_a_complete_authorization_matrix(self) -> None:
        cases = (
            (
                f"project/{circleci_api.PROJECT_SLUG}/pipeline",
                True,
                frozenset({"branch", "page-token"}),
            ),
            (
                f"pipeline/{PIPELINE_1}/workflow",
                True,
                frozenset({"page-token"}),
            ),
            (
                f"workflow/{WORKFLOW_1}/job",
                True,
                frozenset({"page-token"}),
            ),
        )
        self.assertEqual(len(cases), len(circleci_api.ENDPOINT_POLICIES))
        for endpoint, paginated, allowed_query in cases:
            with self.subTest(endpoint=endpoint):
                matches = [
                    policy
                    for policy in circleci_api.ENDPOINT_POLICIES
                    if policy.endpoint.fullmatch(endpoint)
                ]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].paginated, paginated)
                self.assertEqual(matches[0].allowed_query, allowed_query)

    def test_client_rejects_unapproved_operations_and_queries(self) -> None:
        client = Client()
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
                    client.fetch_pages(endpoint)

        with self.assertRaisesRegex(CircleCIError, "query is not authorized"):
            client.fetch_pages(
                f"project/{circleci_api.PROJECT_SLUG}/pipeline",
                query={"target": "example.com"},
            )
        with self.assertRaisesRegex(CircleCIError, "endpoint is not authorized"):
            client.fetch_pages(f"workflow/{WORKFLOW_1}/rerun")
        with self.assertRaisesRegex(CircleCIError, "must select main"):
            client.fetch_pages(
                f"project/{circleci_api.PROJECT_SLUG}/pipeline",
                query={"branch": "feature"},
            )

    def test_http_failures_preserve_status_for_retry_policy(self) -> None:
        class ApiHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(429)
                self.end_headers()

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
                with self.assertRaises(CircleCIRequestError) as raised:
                    Client().fetch_pages(
                        f"project/{circleci_api.PROJECT_SLUG}/pipeline"
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(raised.exception.status, 429)

    def test_truncated_response_is_a_retryable_request_error(self) -> None:
        class TruncatedOpener:
            def open(self, request: object, timeout: int) -> object:
                del request, timeout
                raise circleci_api.IncompleteRead(b'{"items":', 20)

        client = Client()
        client._opener = TruncatedOpener()  # type: ignore[assignment]
        with self.assertRaises(CircleCIRequestError) as raised:
            client.fetch_pages(f"project/{circleci_api.PROJECT_SLUG}/pipeline")

        self.assertIsNone(raised.exception.status)
        self.assertTrue(status.is_transient_request_error(raised.exception))

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
                client = Client()
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
    def test_multiple_matching_webhook_pipelines_are_rejected(self) -> None:
        pipelines = [
            pipeline(pipeline_id=PIPELINE_1, number=12, tag="v0.1.0"),
            pipeline(pipeline_id=PIPELINE_2, number=14, tag="v0.1.0"),
            pipeline(pipeline_id=WORKFLOW_1, number=15, tag="v0.2.0"),
        ]

        with self.assertRaisesRegex(CircleCIError, "multiple CircleCI"):
            status.pipeline_for_tag(
                pipelines,
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

    def test_missing_pipeline_is_rejected(self) -> None:
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_tag([], tag="v0.1.0", revision=RELEASE_REVISION)

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
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_tag(
                [wrong_project],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

        wrong_trigger = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="v0.1.0",
        )
        wrong_trigger["trigger"] = {"type": "api"}
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_tag(
                [wrong_trigger],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

        wrong_origin = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="v0.1.0",
        )
        wrong_origin["vcs"]["origin_repository_url"] = (
            "https://github.com/example/other"
        )
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_tag(
                [wrong_origin],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            )

        valid = pipeline(
            pipeline_id=PIPELINE_2,
            number=12,
            tag="v0.1.0",
        )
        api_lookalike = pipeline(
            pipeline_id=PIPELINE_1,
            number=13,
            tag="v0.1.0",
        )
        api_lookalike["trigger"] = {"type": "api"}
        self.assertIs(
            status.pipeline_for_tag(
                [api_lookalike, valid],
                tag="v0.1.0",
                revision=RELEASE_REVISION,
            ),
            valid,
        )

    def test_matching_main_pipeline_requires_exact_revision(self) -> None:
        item = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="",
        )
        item["vcs"].update({"branch": "main", "revision": RELEASE_REVISION})

        self.assertIs(
            status.pipeline_for_branch(
                [item], branch="main", revision=RELEASE_REVISION
            ),
            item,
        )
        with self.assertRaisesRegex(CircleCIError, "no CircleCI pipeline"):
            status.pipeline_for_branch(
                [item], branch="main", revision="b" * 40
            )
        with self.assertRaisesRegex(CircleCIError, "main branch"):
            status.pipeline_for_branch(
                [item], branch="feature", revision=RELEASE_REVISION
            )

    def test_rerun_attempt_is_rejected_by_projection_verification(self) -> None:
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

        with self.assertRaisesRegex(CircleCIError, "rerun workflow attempts"):
            status.verify_workflow_projection(workflows)

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

    def test_all_required_setup_jobs_must_succeed(self) -> None:
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_SETUP_JOBS), start=1
            )
        ]
        self.assertEqual(
            len(status.verify_setup_jobs(jobs)), len(status.REQUIRED_SETUP_JOBS)
        )

        legacy_jobs = [
            {
                "name": name,
                "id": f"10000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.LEGACY_SETUP_JOBS), start=1
            )
        ]
        self.assertEqual(
            len(status.verify_setup_jobs(legacy_jobs, allow_legacy_setup=True)),
            len(status.LEGACY_SETUP_JOBS),
        )
        with self.assertRaisesRegex(CircleCIError, "missing jobs"):
            status.verify_setup_jobs(legacy_jobs)

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

    def test_legacy_denied_publisher_is_allowed_only_for_recovery(self) -> None:
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_RELEASE_JOBS), start=1
            )
        ]
        jobs.append(
            {
                "name": status.LEGACY_PUBLISH_JOB,
                "id": "00000000-0000-4000-8000-999999999999",
                "status": "unauthorized",
            }
        )

        with self.assertRaisesRegex(CircleCIError, "unexpected jobs"):
            status.verify_release_jobs(jobs)
        lines = status.verify_release_jobs(
            jobs, allow_legacy_publisher_denial=True
        )
        self.assertIn(
            "legacy:publish-release\tunauthorized\t"
            "00000000-0000-4000-8000-999999999999",
            lines,
        )

        with self.assertRaisesRegex(CircleCIError, "missing jobs"):
            status.verify_release_jobs(
                jobs[:-1], allow_legacy_publisher_denial=True
            )

        jobs[-1]["status"] = "success"
        with self.assertRaisesRegex(CircleCIError, "unauthorized"):
            status.verify_release_jobs(
                jobs, allow_legacy_publisher_denial=True
            )

    def test_development_jobs_have_an_exact_allowlist(self) -> None:
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_DEVELOPMENT_JOBS), start=1
            )
        ]
        self.assertEqual(
            len(status.verify_development_jobs(jobs)),
            len(status.REQUIRED_DEVELOPMENT_JOBS),
        )
        jobs.append(
            {
                "name": "unexpected",
                "id": "00000000-0000-4000-8000-999999999999",
                "status": "success",
            }
        )
        with self.assertRaisesRegex(CircleCIError, "unexpected jobs"):
            status.verify_development_jobs(jobs)

    def test_waits_for_exact_main_pipeline_and_verifies_its_jobs(self) -> None:
        branch_pipeline = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="",
        )
        branch_pipeline["vcs"].update(
            {"branch": "main", "revision": RELEASE_REVISION}
        )
        workflows = [
            workflow(
                name="lint-pack",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-25T12:00:00Z",
                workflow_status="success",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-25T12:01:00Z",
                workflow_status="success",
            ),
        ]
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_DEVELOPMENT_JOBS), start=1
            )
        ]
        setup_jobs = [
            {
                "name": name,
                "id": f"10000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_SETUP_JOBS), start=1
            )
        ]

        class FakeClient:
            pipeline_requests = 0
            workflow_requests = 0
            queries: list[dict[str, str] | None] = []

            def fetch_pages(
                self, path: str, *, query: dict[str, str] | None = None
            ) -> list[dict[str, object]]:
                if path == f"project/{status.PROJECT_SLUG}/pipeline":
                    self.pipeline_requests += 1
                    self.queries.append(query)
                    if self.pipeline_requests == 1:
                        raise CircleCIRequestError(
                            "temporary CircleCI failure", status=503
                        )
                    return [branch_pipeline]
                if path == f"pipeline/{PIPELINE_1}/workflow":
                    self.workflow_requests += 1
                    if self.workflow_requests == 1:
                        return [
                            workflow(
                                name="lint-pack",
                                workflow_id=WORKFLOW_1,
                                created_at="2026-08-25T12:00:00Z",
                                workflow_status="running",
                            )
                        ]
                    return workflows
                if path == f"workflow/{WORKFLOW_1}/job":
                    return setup_jobs
                if path == f"workflow/{WORKFLOW_2}/job":
                    return jobs
                raise AssertionError(path)

        client = FakeClient()
        with patch.object(status.time, "sleep", return_value=None):
            lines = status.wait_for_pipeline(
                client,  # type: ignore[arg-type]
                revision=RELEASE_REVISION,
                tag=None,
                branch="main",
                timeout=30,
                poll_interval=1,
            )

        self.assertEqual(lines[0], f"pipeline\t{PIPELINE_1}")
        self.assertEqual(client.pipeline_requests, 3)
        self.assertEqual(client.workflow_requests, 2)
        self.assertEqual(
            client.queries,
            [{"branch": "main"}, {"branch": "main"}, {"branch": "main"}],
        )
        self.assertEqual(
            len(lines),
            3
            + len(status.REQUIRED_SETUP_JOBS)
            + len(status.REQUIRED_DEVELOPMENT_JOBS),
        )

    def test_waits_for_exact_tag_pipeline_with_legacy_recovery(self) -> None:
        workflows = [
            workflow(
                name="lint-pack",
                workflow_id=WORKFLOW_1,
                created_at="2026-08-25T12:00:00Z",
                workflow_status="success",
            ),
            workflow(
                name="test-deploy",
                workflow_id=WORKFLOW_2,
                created_at="2026-08-25T12:01:00Z",
                workflow_status="unauthorized",
            ),
        ]
        jobs = [
            {
                "name": name,
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.REQUIRED_RELEASE_JOBS), start=1
            )
        ]
        jobs.append(
            {
                "name": status.LEGACY_PUBLISH_JOB,
                "id": "00000000-0000-4000-8000-999999999999",
                "status": "unauthorized",
            }
        )
        setup_jobs = [
            {
                "name": name,
                "id": f"10000000-0000-4000-8000-{index:012d}",
                "status": "success",
            }
            for index, name in enumerate(
                sorted(status.LEGACY_SETUP_JOBS), start=1
            )
        ]

        class FakeClient:
            def fetch_pages(
                self, path: str, *, query: dict[str, str] | None = None
            ) -> list[dict[str, object]]:
                if path == f"project/{status.PROJECT_SLUG}/pipeline":
                    self.assert_query = query
                    return [
                        pipeline(
                            pipeline_id=PIPELINE_1,
                            number=12,
                            tag="v0.1.1",
                        )
                    ]
                if path == f"pipeline/{PIPELINE_1}/workflow":
                    return workflows
                if path == f"workflow/{WORKFLOW_1}/job":
                    return setup_jobs
                if path == f"workflow/{WORKFLOW_2}/job":
                    return jobs
                raise AssertionError(path)

        lines = status.wait_for_pipeline(
            FakeClient(),  # type: ignore[arg-type]
            revision=RELEASE_REVISION,
            tag="v0.1.1",
            branch=None,
            timeout=30,
            poll_interval=1,
            allow_legacy_publisher_denial=True,
        )

        self.assertEqual(lines[0], f"pipeline\t{PIPELINE_1}")
        self.assertTrue(lines[-1].startswith("legacy:publish-release"))

        arguments = status.parser().parse_args(
            [
                "wait-tag-pipeline",
                "v0.1.1",
                RELEASE_REVISION,
                "--allow-legacy-publisher-denial",
            ]
        )
        self.assertEqual(arguments.command, "wait-tag-pipeline")
        self.assertTrue(arguments.allow_legacy_publisher_denial)

    def test_pipeline_wait_rejects_terminal_incomplete_and_rerun_workflows(
        self,
    ) -> None:
        branch_pipeline = pipeline(
            pipeline_id=PIPELINE_1,
            number=12,
            tag="",
        )
        branch_pipeline["vcs"].update(
            {"branch": "main", "revision": RELEASE_REVISION}
        )
        failed = workflow(
            name="lint-pack",
            workflow_id=WORKFLOW_1,
            created_at="2026-08-25T12:00:00Z",
            workflow_status="failed",
        )

        class FakeClient:
            def __init__(self, workflows: list[dict[str, object]]) -> None:
                self.workflows = workflows

            def fetch_pages(
                self, path: str, *, query: dict[str, str] | None = None
            ) -> list[dict[str, object]]:
                if path == f"project/{status.PROJECT_SLUG}/pipeline":
                    self.query = query
                    return [branch_pipeline]
                if path == f"pipeline/{PIPELINE_1}/workflow":
                    return self.workflows
                raise AssertionError(path)

        with self.assertRaisesRegex(CircleCIError, "failed before"):
            status.wait_for_pipeline(
                FakeClient([failed]),  # type: ignore[arg-type]
                revision=RELEASE_REVISION,
                tag=None,
                branch="main",
                timeout=30,
                poll_interval=1,
            )

        rerun = dict(failed)
        rerun.update(
            {
                "id": WORKFLOW_2,
                "created_at": "2026-08-25T12:01:00Z",
                "status": "success",
            }
        )
        original = dict(failed)
        original["status"] = "success"
        with self.assertRaisesRegex(CircleCIError, "rerun workflow attempts"):
            status.wait_for_pipeline(
                FakeClient([original, rerun]),  # type: ignore[arg-type]
                revision=RELEASE_REVISION,
                tag=None,
                branch="main",
                timeout=30,
                poll_interval=1,
            )

if __name__ == "__main__":
    unittest.main()
