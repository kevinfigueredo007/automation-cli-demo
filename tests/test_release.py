"""Integration tests for the release flow against a temporary git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation_cli.git import GitRepository
from automation_cli.models import ReleaseManifest
from automation_cli.release import ChangeSet, create_release, dry_run


def _m(paths, version="1.4.0"):
    return ReleaseManifest(version=version, paths=paths)


def _read(repo_path: Path, relpath: str) -> str:
    return (repo_path / relpath).read_text()


# --- core invariant: release = main + selected paths from dev -------------

def test_release_creates_branch_with_correct_state(repo: GitRepository, repo_path: Path):
    manifest = _m(["roles/aws_restart/", "playbooks/aws/restart-instance/", "collections/requirements.yml"])
    branch, changes, kept, redundant = create_release(repo, manifest)

    assert branch == "release/1.4.0"
    assert kept == ["roles/aws_restart", "playbooks/aws/restart-instance", "collections/requirements.yml"]
    assert redundant == []
    assert repo.branch_exists("release/1.4.0")

    # The branch should be checked out and contain the promoted state.
    assert _read(repo_path, "roles/aws_restart/tasks/main.yml") == "dev: aws_restart NEW\n"
    assert _read(repo_path, "playbooks/aws/restart-instance/main.yml") == "dev: restart NEW\n"
    assert _read(repo_path, "collections/requirements.yml") == "dev: requirements NEW\n"
    # Added file from dev under a selected path.
    assert _read(repo_path, "roles/aws_restart/tasks/newtask.yml") == "dev: new task added\n"
    # Deleted in dev -> removed in the release.
    assert not (repo_path / "roles/aws_restart/tasks/old.yml").exists()

    # Untouched paths must remain exactly as in main.
    assert _read(repo_path, "playbooks/aws/snapshot/main.yml") == "main: snapshot old\n"
    assert _read(repo_path, "playbooks/telecom/bgp/main.yml") == "main: bgp old\n"
    assert _read(repo_path, "roles/aws_common/tasks/main.yml") == "main: aws_common old\n"
    assert _read(repo_path, "README.md") == "main: readme\n"

    # Change counts.
    assert len(changes.added) == 1        # newtask.yml
    assert len(changes.modified) >= 3    # restart main.yml, aws_restart main.yml, requirements.yml
    assert len(changes.deleted) == 1     # old.yml


def test_release_preserves_unselected_modified_paths(repo: GitRepository, repo_path: Path):
    # Select ONLY aws_restart; aws_common and telecom/bgp were modified in dev
    # but must stay as in main because they are not in the manifest.
    manifest = _m(["roles/aws_restart/"])
    create_release(repo, manifest)
    assert _read(repo_path, "roles/aws_common/tasks/main.yml") == "main: aws_common old\n"
    assert _read(repo_path, "playbooks/telecom/bgp/main.yml") == "main: bgp old\n"


def test_release_creates_tag(repo: GitRepository):
    manifest = _m(["roles/aws_restart/"], version="2.0.0")
    create_release(repo, manifest, create_tag=True)
    assert repo.tag_exists("2.0.0")
    assert repo.branch_exists("release/2.0.0")


def test_release_without_tag(repo: GitRepository):
    manifest = _m(["roles/aws_restart/"], version="3.0.0")
    create_release(repo, manifest, create_tag=False)
    assert not repo.tag_exists("3.0.0")
    assert repo.branch_exists("release/3.0.0")


def test_release_commit_message(repo: GitRepository):
    manifest = _m(["roles/aws_restart/"], version="5.0.0")
    create_release(repo, manifest)
    c = repo.get_commit("release/5.0.0")
    assert c.subject == "release: 5.0.0"


def test_release_fails_when_branch_exists(repo: GitRepository):
    repo.create_branch("release/1.4.0", "main")
    from automation_cli.release import ReleaseError
    with pytest.raises(ReleaseError):
        create_release(repo, _m(["roles/aws_restart/"]))


def test_release_fails_on_dirty_tree(repo: GitRepository, repo_path: Path):
    with open(repo_path / "README.md", "a") as f:
        f.write("dirty\n")
    from automation_cli.release import ReleaseError
    with pytest.raises(ReleaseError):
        create_release(repo, _m(["roles/aws_restart/"]))


# --- deletions ------------------------------------------------------------

def test_release_handles_total_removal(repo: GitRepository, repo_path: Path):
    # A path present in main but absent in dev must be deleted in the release.
    (repo_path / "legacy").mkdir(exist_ok=True)
    (repo_path / "legacy/old.txt").write_text("legacy on main\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo_path), check=True)
    subprocess.run(["git", "commit", "-m", "add legacy on main"], cwd=str(repo_path), check=True)

    # Promoting legacy/ (which does not exist in dev) deletes it in the release.
    create_release(repo, _m(["legacy/"], version="7.0.0"))
    assert not (repo_path / "legacy").exists()


# --- overlaps --------------------------------------------------------------

def test_release_normalizes_overlapping_paths(repo: GitRepository, repo_path: Path):
    # roles/aws_restart is a sub-path of roles (not in manifest). Use overlapping:
    # 'roles/aws_restart' and 'roles/aws_restart/tasks' — the latter is redundant.
    manifest = _m(["roles/aws_restart/", "roles/aws_restart/tasks/"], version="8.0.0")
    _branch, _changes, kept, redundant = create_release(repo, manifest)
    assert "roles/aws_restart/tasks" in redundant
    assert kept == ["roles/aws_restart"]
    assert _read(repo_path, "roles/aws_restart/tasks/main.yml") == "dev: aws_restart NEW\n"


# --- dry run --------------------------------------------------------------

def test_dry_run_does_not_modify(repo: GitRepository):
    before = repo.head_sha()
    manifest = _m(["roles/aws_restart/", "playbooks/aws/restart-instance/"])
    result = dry_run(repo, manifest)
    after = repo.head_sha()
    assert before == after
    assert not repo.branch_exists("release/1.4.0")
    assert isinstance(result.changes, ChangeSet)
    assert len(result.changes.added) >= 1
    assert len(result.changes.modified) >= 2
    assert len(result.changes.deleted) == 1


def test_dry_run_reports_remaining_top_level(repo: GitRepository):
    manifest = _m(["roles/aws_restart/"])
    result = dry_run(repo, manifest)
    # 'playbooks' is fully untouched -> shown as remaining.
    assert any(p.startswith("playbooks") for p in result.remaining_top_level)
    # 'roles' is partially selected -> only the untouched sibling surfaces.
    assert "roles/aws_common/" in result.remaining_top_level
    assert not any(p.startswith("roles/aws_restart") for p in result.remaining_top_level)


# --- file (not directory) path --------------------------------------------

def test_release_single_file_path(repo: GitRepository, repo_path: Path):
    manifest = _m(["collections/requirements.yml"], version="9.0.0")
    create_release(repo, manifest)
    assert _read(repo_path, "collections/requirements.yml") == "dev: requirements NEW\n"
    # Unrelated modified path stays from main.
    assert _read(repo_path, "roles/aws_restart/tasks/main.yml") == "main: aws_restart old\n"
