"""CLI smoke tests using Typer's CliRunner against a temporary repo."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from automation_cli.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_validate_command_ok(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text("version: '1.4.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["validate", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "manifest valid" in result.output
    assert "release/1.4.0" in result.output


def test_validate_command_invalid_manifest(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text("version: 'not-semver'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["validate", str(manifest)])
    assert result.exit_code == 1
    assert "invalid" in result.output.lower()


def test_validate_command_missing_file(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    result = runner.invoke(app, ["validate", str(repo_path / "nope.yaml")])
    assert result.exit_code == 1


def test_release_dry_run_command(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text(
        "version: '1.4.0'\npaths:\n  - roles/aws_restart/\n  - playbooks/aws/restart-instance/\n"
    )
    result = runner.invoke(app, ["release", str(manifest), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Release: 1.4.0" in result.output
    assert "No changes were made." in result.output
    # The release branch must NOT exist after a dry run.
    import subprocess
    branches = subprocess.run(
        ["git", "branch", "--list", "release/1.4.0"], cwd=str(repo_path), capture_output=True, text=True, check=True
    ).stdout
    assert branches.strip() == ""


def test_release_command_creates_branch_and_tag(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text("version: '2.1.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest), "--tag"])
    assert result.exit_code == 0, result.output
    assert "Created release branch: release/2.1.0" in result.output
    assert "Created tag: 2.1.0" in result.output

    import subprocess
    tags = subprocess.run(["git", "tag", "-l", "2.1.0"], cwd=str(repo_path), capture_output=True, text=True, check=True).stdout
    assert tags.strip() == "2.1.0"


def test_release_command_fails_on_existing_branch(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    import subprocess
    subprocess.run(["git", "branch", "release/3.0.0", "main"], cwd=str(repo_path), check=True)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text("version: '3.0.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest)])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_release_command_not_a_git_repo(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "release.yaml"
    manifest.write_text("version: '1.0.0'\npaths:\n  - x/\n")
    result = runner.invoke(app, ["release", str(manifest), "--dry-run"])
    assert result.exit_code == 2


def test_release_command_push(runner: CliRunner, repo_with_remote, monkeypatch: pytest.MonkeyPatch):
    repo, bare = repo_with_remote
    monkeypatch.chdir(repo.root)
    manifest = repo.root.parent / "release.yaml"
    manifest.write_text("version: '20.0.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest), "--push"])
    assert result.exit_code == 0, result.output
    assert "Pushed release/20.0.0 to origin" in result.output
    assert "Switched back to dev" in result.output
    # Remote should have the release branch.
    import subprocess
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/heads/release/20.0.0"],
        cwd=str(bare), capture_output=True, text=True, check=True,
    )
    assert out.returncode == 0


def test_release_command_push_and_tag(runner: CliRunner, repo_with_remote, monkeypatch: pytest.MonkeyPatch):
    repo, _bare = repo_with_remote
    monkeypatch.chdir(repo.root)
    manifest = repo.root.parent / "release.yaml"
    manifest.write_text("version: '21.0.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest), "--push", "--tag"])
    assert result.exit_code == 0, result.output
    assert "Pushed tag 21.0.0 to origin" in result.output


def test_release_command_push_without_origin_fails(runner: CliRunner, repo_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo_path)
    manifest = repo_path.parent / "release.yaml"
    manifest.write_text("version: '23.0.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest), "--push"])
    assert result.exit_code == 1
    assert "remote" in result.output.lower()


def test_release_command_dry_run_with_push_does_not_push(runner: CliRunner, repo_with_remote, monkeypatch: pytest.MonkeyPatch):
    repo, bare = repo_with_remote
    monkeypatch.chdir(repo.root)
    manifest = repo.root.parent / "release.yaml"
    manifest.write_text("version: '22.0.0'\npaths:\n  - roles/aws_restart/\n")
    result = runner.invoke(app, ["release", str(manifest), "--dry-run", "--push"])
    assert result.exit_code == 0, result.output
    # Dry run must not have created/pushed the branch.
    import subprocess
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/heads/release/22.0.0"],
        cwd=str(bare), capture_output=True, text=True, check=False,
    )
    assert out.returncode != 0
