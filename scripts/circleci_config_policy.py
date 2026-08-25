"""Structural security policy for Vexcalibur CircleCI configurations."""

from __future__ import annotations

import shlex
from typing import Any


class CircleCIConfigPolicyError(ValueError):
    """A CircleCI configuration grants a forbidden publishing capability."""


def _invokes_orb_publisher(command: str) -> bool:
    command = command.replace("\\\r\n", "").replace("\\\n", "")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = [token.rsplit("/", 1)[-1] for token in lexer]
    except ValueError as error:
        raise CircleCIConfigPolicyError(
            "CircleCI run command is not valid shell syntax"
        ) from error

    for index, token in enumerate(tokens):
        if token != "circleci":
            continue
        arguments = tokens[index + 1 :]
        try:
            orb_index = arguments.index("orb")
            publish_index = arguments.index("publish", orb_index + 1)
        except ValueError:
            continue
        if not any(token in {";", "&&", "||", "|", "&"} for token in arguments[:publish_index]):
            return True
    return False


def reject_publishing_capabilities(value: Any, *, path: str) -> None:
    """Reject CircleCI contexts and resolved Orb publication commands."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "context":
                raise CircleCIConfigPolicyError(
                    f"CircleCI config must not attach a context: {child_path}"
                )
            if (
                key == "command"
                and isinstance(child, str)
                and _invokes_orb_publisher(child)
            ):
                raise CircleCIConfigPolicyError(
                    f"CircleCI config must not publish an Orb: {child_path}"
                )
            reject_publishing_capabilities(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_publishing_capabilities(child, path=f"{path}[{index}]")
