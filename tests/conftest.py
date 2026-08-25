"""Shared pytest fixtures: temporary git repositories for integration tests."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from automation_cli.git import GitRepository


def _run(args: list[str], cwd: Path) -> None:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com",
        }
    )
    subprocess.run(args, cwd=str(cwd), check=True, env=env)


def _write(repo: Path, relpath: str, content: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _commit(repo: Path, msg: str, *, allow_empty: bool = False) -> None:
    _run(["git", "add", "-A"], repo)
    commit_args = ["git", "commit", "-m", msg]
    if allow_empty:
        commit_args.append("--allow-empty")
    _run(commit_args, repo)


def _build_repo(repo: Path) -> None:
    """Create a representative monorepo with main/dev/stage branches."""
    _run(["git", "init", "-b", "main"], repo)
    # Ensure default branch name even on older git.
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], repo)
    _run(["git", "config", "user.name", "Tester"], repo)
    _run(["git", "config", "user.email", "tester@example.com"], repo)

    # main: initial "old" state for several paths.
    _write(repo, "playbooks/aws/restart-instance/main.yml", "main: restart old\n")
    _write(repo, "playbooks/aws/snapshot/main.yml", "main: snapshot old\n")
    _write(repo, "playbooks/telecom/bgp/main.yml", "main: bgp old\n")
    _write(repo, "roles/aws_restart/tasks/main.yml", "main: aws_restart old\n")
    _write(repo, "roles/aws_restart/tasks/old.yml", "main: to be deleted\n")
    _write(repo, "roles/aws_common/tasks/main.yml", "main: aws_common old\n")
    _write(repo, "collections/requirements.yml", "main: requirements old\n")
    _write(repo, "README.md", "main: readme\n")
    _commit(repo, "main: initial state")

    # dev: several independent changes.
    _run(["git", "checkout", "-b", "dev"], repo)
    _write(repo, "playbooks/aws/restart-instance/main.yml", "dev: restart NEW\n")
    _write(repo, "playbooks/aws/snapshot/main.yml", "dev: snapshot NEW (should NOT promote)\n")
    _write(repo, "playbooks/telecom/bgp/main.yml", "dev: bgp NEW (should NOT promote)\n")
    _write(repo, "roles/aws_restart/tasks/main.yml", "dev: aws_restart NEW\n")
    (repo / "roles/aws_restart/tasks/old.yml").unlink()
    _write(repo, "roles/aws_restart/tasks/newtask.yml", "dev: new task added\n")
    _write(repo, "roles/aws_common/tasks/main.yml", "dev: aws_common NEW\n")
    _write(repo, "collections/requirements.yml", "dev: requirements NEW\n")
    # New top-level path only in dev.
    _write(repo, "roles/brand_new/tasks/main.yml", "dev: brand new role\n")
    _commit(repo, "dev: independent changes")

    # stage: a third branch mirroring AAP stage.
    _run(["git", "checkout", "main"], repo)
    _run(["git", "checkout", "-b", "stage"], repo)
    _commit(repo, "stage: empty stage placeholder", allow_empty=True)  # nothing changed yet
    _run(["git", "checkout", "main"], repo)


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    _build_repo(tmp_path)
    return tmp_path


@pytest.fixture
def repo(repo_path: Path) -> Iterator[GitRepository]:
    """A temp git repo with main/dev/stage branches and independent changes."""
    yield GitRepository(repo_path)
