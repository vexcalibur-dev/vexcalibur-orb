"""Small, strict CircleCI API client shared by release checks."""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ROOT = "https://circleci.com/api/v2"
MAX_PAGES = 1_000
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
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
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if (
            not path
            or path.startswith(("/", "//"))
            or "://" in path
            or any(ord(character) < 0x20 for character in path)
        ):
            fail("CircleCI API path is malformed")
        url = f"{API_ROOT}/{path}"
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

    def fetch_json(self, path: str) -> Any:
        return self._request_json(path, method="GET")

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json(path, method="POST", payload=payload)

    def fetch_pages(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _page in range(MAX_PAGES):
            separator = "&" if "?" in path else "?"
            page_path = (
                f"{path}{separator}page-token={quote(page_token, safe='')}"
                if page_token
                else path
            )
            document = self.fetch_json(page_path)
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
