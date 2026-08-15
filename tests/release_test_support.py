"""Shared Git repository fixtures for release automation tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

BOT_NAME = "vexcalibur-dev-automation[bot]"
BOT_EMAIL = "301474224+vexcalibur-dev-automation[bot]@users.noreply.github.com"


def git(
    repo: Path,
    *arguments: str,
    check: bool = True,
    input_text: str | None = None,
) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def configure_git(repo: Path) -> None:
    git(repo, "config", "user.name", BOT_NAME)
    git(repo, "config", "user.email", BOT_EMAIL)


def commit(repo: Path, message: str, filename: str) -> str:
    (repo / filename).write_text(message, encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "commit", "--message", message)
    return git(repo, "rev-parse", "HEAD")


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class RepositoryTestCase(unittest.TestCase):
    """Create one Git repository with an automation-authored source commit."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--initial-branch=main")
        configure_git(self.repo)
        self.initial_commit = commit(
            self.repo,
            "chore: initial source",
            "source.txt",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()
