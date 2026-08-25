"""Tests for the pre-flight validator."""

from __future__ import annotations

from automation_cli.git import GitRepository
from automation_cli.models import ReleaseManifest
from automation_cli.validator import validate_release


def _m(paths, version="1.4.0"):
    return ReleaseManifest(version=version, paths=paths)


def test_valid(repo: GitRepository):
    r = validate_release(repo, _m(["roles/aws_restart/", "playbooks/aws/restart-instance/"]))
    assert r.ok
    assert r.base_commit
    assert r.source_commit


def test_missing_base_branch(repo: GitRepository):
    r = validate_release(repo, _m(["roles/aws_restart/"]), base_branch="nope")
    assert not r.ok
    assert any("base branch" in e for e in r.errors)


def test_missing_source_branch(repo: GitRepository):
    r = validate_release(repo, _m(["roles/aws_restart/"]), source_branch="nope")
    assert not r.ok
    assert any("source branch" in e for e in r.errors)


def test_release_branch_exists(repo: GitRepository):
    repo.create_branch("release/1.4.0", "main")
    r = validate_release(repo, _m(["roles/aws_restart/"]))
    assert not r.ok
    assert any("already exists" in e for e in r.errors)


def test_dirty_tree(repo: GitRepository, repo_path):
    with open(repo_path / "README.md", "a") as f:
        f.write("dirty\n")
    r = validate_release(repo, _m(["roles/aws_restart/"]))
    assert not r.ok
    assert any("working tree" in e for e in r.errors)


def test_path_not_in_either(repo: GitRepository):
    r = validate_release(repo, _m(["does/not/exist/"]))
    assert not r.ok
    assert any("exists neither" in e for e in r.errors)


def test_total_removal_is_warning(repo: GitRepository, repo_path):
    # A path that exists in main but was never introduced in dev: promoting it
    # is a supported "total removal" (the release will delete it).
    import subprocess
    # We are on main; add a legacy dir only on main and commit.
    (repo_path / "legacy").mkdir(exist_ok=True)
    (repo_path / "legacy/old.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo_path), check=True)
    subprocess.run(["git", "commit", "-m", "add legacy on main"], cwd=str(repo_path), check=True)

    r = validate_release(repo, _m(["legacy/"]))
    assert r.ok
    assert any("will be removed" in w for w in r.warnings)


def test_tag_exists(repo: GitRepository):
    repo.create_tag("1.4.0", "main")
    r = validate_release(repo, _m(["roles/aws_restart/"]), create_tag=True)
    assert not r.ok
    assert any("tag already exists" in e for e in r.errors)
