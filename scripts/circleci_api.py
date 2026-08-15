"""Small, strict CircleCI API client shared by release checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, NoReturn
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ROOT = "https://circleci.com/api/v2"
MAX_PAGES = 1_000
UUID_FRAGMENT = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
UUID_PATTERN = re.compile(rf"^{UUID_FRAGMENT}$")
PROJECT_SLUG = "gh/vexcalibur-dev/vexcalibur-orb"
PAGE_TOKEN_QUERY = frozenset({"page-token"})


@dataclass(frozen=True)
class EndpointPolicy:
    """One authorized CircleCI API operation."""

    method: str
    endpoint: re.Pattern[str]
    paginated: bool = False
    required_query: frozenset[str] = frozenset()
    allowed_query: frozenset[str] = frozenset()


ENDPOINT_POLICIES = (
    EndpointPolicy("GET", re.compile(r"^me/collaborations$")),
    EndpointPolicy("GET", re.compile(rf"^project/{re.escape(PROJECT_SLUG)}$")),
    EndpointPolicy(
        "GET",
        re.compile(rf"^project/{re.escape(PROJECT_SLUG)}/pipeline$"),
        paginated=True,
        allowed_query=PAGE_TOKEN_QUERY,
    ),
    EndpointPolicy(
        "GET",
        re.compile(r"^context$"),
        paginated=True,
        required_query=frozenset({"owner-id"}),
        allowed_query=frozenset({"owner-id", "page-token"}),
    ),
    EndpointPolicy(
        "GET",
        re.compile(rf"^context/{UUID_FRAGMENT}/restrictions$"),
        paginated=True,
        allowed_query=PAGE_TOKEN_QUERY,
    ),
    EndpointPolicy(
        "GET",
        re.compile(rf"^pipeline/{UUID_FRAGMENT}/workflow$"),
        paginated=True,
        allowed_query=PAGE_TOKEN_QUERY,
    ),
    EndpointPolicy("GET", re.compile(rf"^workflow/{UUID_FRAGMENT}$")),
    EndpointPolicy(
        "GET",
        re.compile(rf"^workflow/{UUID_FRAGMENT}/job$"),
        paginated=True,
        allowed_query=PAGE_TOKEN_QUERY,
    ),
    EndpointPolicy("POST", re.compile(rf"^workflow/{UUID_FRAGMENT}/rerun$")),
)


class CircleCIError(RuntimeError):
    """CircleCI returned malformed data or a release check failed."""


def fail(message: str) -> NoReturn:
    raise CircleCIError(message)


def require_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or UUID_PATTERN.fullmatch(value) is None:
        fail(f"{label} is not a UUID")
    return value


def _reject_constant(value: str) -> NoReturn:
    fail(f"CircleCI returned the non-finite JSON value {value!r}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"CircleCI returned duplicate JSON key {key!r}")
        result[key] = value
    return result


def decode_json(data: bytes) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        fail("CircleCI returned non-UTF-8 JSON")
        raise AssertionError from error
    except json.JSONDecodeError as error:
        fail("CircleCI returned malformed JSON")
        raise AssertionError from error


class RejectRedirects(HTTPRedirectHandler):
    """Prevent an authenticated request from changing origins."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> NoReturn:
        del req, fp, code, msg, headers, newurl
        fail("CircleCI API redirected an authenticated request")


class Client:
    """Authenticated client for the fixed CircleCI API origin."""

    def __init__(self, token: str) -> None:
        if not token or any(character.isspace() for character in token):
            fail("CircleCI operator token is missing or malformed")
        self._token = token
        self._opener = build_opener(RejectRedirects())

    def _request_json(
        self,
        endpoint: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        paginated: bool = False,
    ) -> Any:
        matches = [
            policy
            for policy in ENDPOINT_POLICIES
            if policy.method == method and policy.endpoint.fullmatch(endpoint)
        ]
        if len(matches) != 1:
            fail("CircleCI API endpoint is not authorized")
        policy = matches[0]
        if paginated != policy.paginated:
            fail("CircleCI API pagination mode is not authorized")
        query_keys = frozenset(query or {})
        if not query_keys <= policy.allowed_query:
            fail("CircleCI API query is not authorized")
        if not policy.required_query <= query_keys:
            fail("CircleCI API query is missing required parameters")
        if query is not None and "owner-id" in query:
            require_uuid(query["owner-id"], label="CircleCI organization ID")
        url = f"{API_ROOT}/{endpoint}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                if response.geturl() != url:
                    fail("CircleCI API response came from an unexpected URL")
                return decode_json(response.read())
        except OSError as error:
            fail(f"CircleCI API request failed: {error}")
            raise AssertionError from error

    def fetch_json(self, endpoint: str) -> Any:
        return self._request_json(endpoint, method="GET")

    def post_json(self, endpoint: str, payload: dict[str, Any]) -> Any:
        return self._request_json(endpoint, method="POST", payload=payload)

    def fetch_pages(
        self, endpoint: str, *, query: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _page in range(MAX_PAGES):
            page_query = dict(query or {})
            if page_token:
                page_query["page-token"] = page_token
            document = self._request_json(
                endpoint,
                method="GET",
                query=page_query,
                paginated=True,
            )
            if not isinstance(document, dict) or not isinstance(
                document.get("items"), list
            ):
                fail("CircleCI returned a malformed paginated response")
            for item in document["items"]:
                if not isinstance(item, dict):
                    fail("CircleCI returned a malformed list item")
                items.append(item)
            next_page_token = document.get("next_page_token")
            if next_page_token is None:
                return items
            if not isinstance(next_page_token, str):
                fail("CircleCI returned a malformed pagination token")
            page_token = next_page_token
            if not page_token:
                return items
            if page_token in seen_tokens:
                fail("CircleCI returned a repeated pagination token")
            seen_tokens.add(page_token)
        fail(f"CircleCI response exceeded {MAX_PAGES} pages")
