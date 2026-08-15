"""Sanitized Git transport helpers for release automation."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import NoReturn

from release_common import fail, require_sha


CREDENTIAL_URL_PATTERN = re.compile(r"(?P<scheme>https?://)[^/@\s]+@")


def fail_git_result(
    operation: str, result: subprocess.CompletedProcess[str]
) -> NoReturn:
    """Report a Git failure without exposing URL credentials."""
    detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
    diagnostic = f"{operation} exited {result.returncode}: {detail}"
    fail(CREDENTIAL_URL_PATTERN.sub(r"\g<scheme><redacted>@", diagnostic))


def fail_git(
    arguments: tuple[str, ...], result: subprocess.CompletedProcess[str]
) -> NoReturn:
    fail_git_result(shlex.join(("git", *arguments)), result)


def git(
    *arguments: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git without a shell and return its captured text output."""
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if check and result.returncode != 0:
        fail_git(arguments, result)
    return result


def git_bytes(*arguments: str) -> bytes:
    """Run Git and preserve stdout bytes for canonical object verification."""
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        decoded = subprocess.CompletedProcess(
            result.args,
            result.returncode,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
        )
        fail_git(arguments, decoded)
    return result.stdout


def is_ancestor(ancestor: str, descendant: str) -> bool:
    arguments = ("merge-base", "--is-ancestor", ancestor, descendant)
    result = git(*arguments, check=False)
    if result.returncode not in (0, 1):
        fail_git(arguments, result)
    return result.returncode == 0


def remote_reference(remote: str, reference: str) -> str:
    arguments = ("ls-remote", "--refs", remote, reference)
    result = git(*arguments, check=False)
    if result.returncode != 0:
        fail_git(arguments, result)
    lines = result.stdout.splitlines()
    if not lines:
        return ""
    if len(lines) != 1:
        fail(f"remote repository returned ambiguous state for {reference}")
    fields = lines[0].split("\t", 1)
    if len(fields) != 2 or fields[1] != reference:
        fail(f"remote repository returned malformed state for {reference}")
    return require_sha(fields[0], label=f"remote {reference} object")


def local_reference_exists(reference: str) -> bool:
    arguments = ("show-ref", "--verify", "--quiet", reference)
    result = git(*arguments, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    fail_git(arguments, result)
