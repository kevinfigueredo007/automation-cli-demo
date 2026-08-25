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
    branch, changes, kept, redundant, _sync = create_release(repo, manifest)

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
    _branch, _changes, kept, redundant, _sync = create_release(repo, manifest)
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


# --- push ------------------------------------------------------------------

def test_release_push_pushes_branch_and_returns_to_source(repo_with_remote, tmp_path):
    repo, bare = repo_with_remote
    create_release(repo, _m(["roles/aws_restart/"], version="10.0.0"), push=True)
    # Current branch should be `dev` (the configured source).
    assert repo.current_branch() == "dev"
    # The release branch must exist on the remote.
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/heads/release/10.0.0"],
        cwd=str(bare), capture_output=True, text=True, check=True,
    )
    assert result.returncode == 0


def test_release_push_with_tag_pushes_tag_too(repo_with_remote):
    repo, bare = repo_with_remote
    create_release(
        repo, _m(["roles/aws_restart/"], version="11.0.0"), push=True, create_tag=True,
    )
    assert repo.current_branch() == "dev"
    # Tag must exist on the remote.
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/tags/11.0.0"],
        cwd=str(bare), capture_output=True, text=True, check=True,
    )
    assert result.returncode == 0


def test_release_push_without_origin_fails(repo: GitRepository):
    # No `origin` configured on the plain `repo` fixture.
    from automation_cli.release import ReleaseError
    with pytest.raises(ReleaseError) as excinfo:
        create_release(repo, _m(["roles/aws_restart/"], version="12.0.0"), push=True)
    assert "remote" in str(excinfo.value).lower()


def test_release_push_failed_does_not_switch_to_source(repo_with_remote, monkeypatch):
    """If push fails we stay on the release branch for inspection."""
    repo, _bare = repo_with_remote
    # Sabotage the remote URL so push fails, but keep has_remote() truthy
    # (it only checks get-url, not connectivity).
    subprocess.run(["git", "remote", "set-url", "origin", "/does/not/exist.git"], cwd=str(repo.root), check=True)
    from automation_cli.git import GitError
    with pytest.raises(GitError):
        create_release(repo, _m(["roles/aws_restart/"], version="13.0.0"), push=True)
    # Should still be on the release branch (not switched to dev).
    assert repo.current_branch() == "release/13.0.0"


# --- sync (fetch + ff-only pull) ------------------------------------------

def test_sync_pulls_behind_branch(repo_with_remote):
    """When local main is behind origin/main, sync fast-forwards it."""
    repo, bare = repo_with_remote
    # Add a commit to origin/main via a second clone.
    clone2 = bare.parent / "clone2"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone2)], check=True)
    subprocess.run(["git", "checkout", "main"], cwd=str(clone2), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(clone2), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(clone2), check=True)
    (clone2 / "new.txt").write_text("from origin\n")
    subprocess.run(["git", "add", "-A"], cwd=str(clone2), check=True)
    subprocess.run(["git", "commit", "-m", "new commit on origin main"], cwd=str(clone2), check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(clone2), check=True)

    # Local main is now behind. Sync should fast-forward it.
    from automation_cli.release import sync_branches
    result = sync_branches(repo, ["main"], remote="origin")
    assert len(result.synced) == 1
    assert "main" in result.synced[0]
    # The new file should now exist locally on main.
    assert (repo.root / "new.txt").exists()


def test_sync_skips_when_ahead(repo_with_remote):
    """When local has commits the remote doesn't, sync skips (no merge)."""
    repo, _bare = repo_with_remote
    # Add a local-only commit on main.
    subprocess.run(["git", "checkout", "main"], cwd=str(repo.root), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo.root), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo.root), check=True)
    (repo.root / "local-only.txt").write_text("local\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo.root), check=True)
    subprocess.run(["git", "commit", "-m", "local-only commit"], cwd=str(repo.root), check=True)

    from automation_cli.release import sync_branches
    result = sync_branches(repo, ["main"], remote="origin")
    assert len(result.skipped) == 1
    assert "ahead" in result.skipped[0]


def test_sync_skips_when_up_to_date(repo_with_remote):
    """When local matches remote, sync reports 'already up to date'."""
    repo, _bare = repo_with_remote
    from automation_cli.release import sync_branches
    result = sync_branches(repo, ["main"], remote="origin")
    assert len(result.skipped) == 1
    assert "up to date" in result.skipped[0]


def test_sync_restores_original_branch(repo_with_remote):
    """Sync should leave you on whatever branch you started on."""
    repo, _bare = repo_with_remote
    subprocess.run(["git", "checkout", "main"], cwd=str(repo.root), check=True)
    from automation_cli.release import sync_branches
    sync_branches(repo, ["main", "dev"], remote="origin")
    assert repo.current_branch() == "main"


def test_no_fetch_skips_sync(repo: GitRepository):
    """With no_fetch=True, sync is skipped entirely (sync_result is None)."""
    _branch, _changes, _kept, _redundant, sync_result = create_release(
        repo, _m(["roles/aws_restart/"], version="30.0.0"), no_fetch=True
    )
    assert sync_result is None


def test_no_fetch_with_remote_skips_sync(repo_with_remote):
    """Even with a remote, no_fetch=True skips sync."""
    repo, _bare = repo_with_remote
    _branch, _changes, _kept, _redundant, sync_result = create_release(
        repo, _m(["roles/aws_restart/"], version="31.0.0"), no_fetch=True
    )
    assert sync_result is None


def test_sync_failure_does_not_block_release(repo_with_remote):
    """If fetch fails (e.g. bad remote URL), the release still proceeds."""
    repo, _bare = repo_with_remote
    subprocess.run(["git", "remote", "set-url", "origin", "/does/not/exist.git"], cwd=str(repo.root), check=True)
    _branch, _changes, _kept, _redundant, sync_result = create_release(
        repo, _m(["roles/aws_restart/"], version="32.0.0")
    )
    assert sync_result is not None
    assert any("skipped" in s for s in sync_result.skipped)
