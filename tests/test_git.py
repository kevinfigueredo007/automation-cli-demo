"""Tests for the GitRepository wrapper using a temporary repo."""

from __future__ import annotations

from pathlib import Path

from automation_cli.git import GitRepository


def test_repo_root_detected(repo: GitRepository):
    assert repo.repo_root().exists()
    assert repo.current_branch() == "main"


def test_branch_existence(repo: GitRepository):
    assert repo.branch_exists("main")
    assert repo.branch_exists("dev")
    assert repo.branch_exists("stage")
    assert not repo.branch_exists("release/9.9.9")


def test_is_clean_and_dirty(repo: GitRepository, repo_path: Path):
    assert repo.is_clean()
    with open(repo_path / "playbooks/aws/restart-instance/main.yml", "a") as f:
        f.write("dirty\n")
    assert not repo.is_clean()
    assert repo.status_porcelain().strip() != ""


def test_get_commit(repo: GitRepository):
    c = repo.get_commit("main")
    assert len(c.sha) == 40
    assert len(c.short_sha) >= 7
    assert c.subject.startswith("main:")


def test_ls_tree_files_directory(repo: GitRepository):
    files = repo.ls_tree_files("dev", "roles/aws_restart")
    names = sorted(e.path for e in files)
    # old.yml was removed in dev; newtask.yml added.
    assert "roles/aws_restart/tasks/main.yml" in names
    assert "roles/aws_restart/tasks/newtask.yml" in names
    assert "roles/aws_restart/tasks/old.yml" not in names


def test_ls_tree_files_file(repo: GitRepository):
    files = repo.ls_tree_files("dev", "collections/requirements.yml")
    assert len(files) == 1
    assert files[0].path == "collections/requirements.yml"


def test_ls_tree_files_missing(repo: GitRepository):
    assert repo.ls_tree_files("dev", "nope/missing/") == []


def test_blob_sha_differ_between_refs(repo: GitRepository):
    main_sha = repo.blob_sha("main", "roles/aws_restart/tasks/main.yml")
    dev_sha = repo.blob_sha("dev", "roles/aws_restart/tasks/main.yml")
    assert main_sha != dev_sha


def test_path_exists_in_ref(repo: GitRepository):
    assert repo.path_exists_in_ref("dev", "roles/aws_restart")
    assert not repo.path_exists_in_ref("dev", "roles/aws_restart/tasks/old.yml")
    assert repo.path_exists_in_ref("main", "roles/aws_restart/tasks/old.yml")


def test_top_level_entries(repo: GitRepository):
    tops = set(repo.top_level_entries("main"))
    assert {"playbooks", "roles", "collections", "README.md"} <= tops


def test_checkout_path_from_ref(repo: GitRepository, repo_path: Path):
    before = (repo_path / "roles/aws_restart/tasks/main.yml").read_text()
    assert "old" in before
    repo.checkout_path_from_ref("dev", "roles/aws_restart/tasks/main.yml")
    after = (repo_path / "roles/aws_restart/tasks/main.yml").read_text()
    assert "NEW" in after


def test_create_branch_and_tag(repo: GitRepository):
    repo.create_branch("release/1.2.3", "main")
    assert repo.branch_exists("release/1.2.3")
    repo.checkout("release/1.2.3")
    repo.create_tag("1.2.3", "release/1.2.3", message="release: 1.2.3")
    assert repo.tag_exists("1.2.3")
