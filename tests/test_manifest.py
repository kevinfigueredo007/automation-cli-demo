"""Unit tests for the manifest model and loading."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automation_cli.manifest import ManifestError, load_manifest, parse_manifest
from automation_cli.models import ReleaseManifest, normalize_path

# --- path normalization ----------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("roles/aws_restart/", "roles/aws_restart"),
        ("./roles/aws_restart/", "roles/aws_restart"),
        ("roles//aws_restart/", "roles/aws_restart"),
        (" roles/aws_restart ", "roles/aws_restart"),
    ],
)
def test_normalize_path_basic(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", ".", "./", "/", "/etc", "../x", "a/../b", "C:\\x"])
def test_normalize_path_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        normalize_path(bad)


def test_normalize_path_rejects_traversal_mid():
    with pytest.raises(ValueError):
        normalize_path("roles/../../etc/passwd")


# --- model validation ------------------------------------------------------

def test_valid_manifest():
    m = ReleaseManifest(version="1.4.0", paths=["roles/aws_restart/", "playbooks/aws/"])
    assert m.version == "1.4.0"
    assert m.paths == ["roles/aws_restart", "playbooks/aws"]


@pytest.mark.parametrize("v", ["1", "1.4", "1.4.0.1", "v1.4.0", "1.4.x", ""])
def test_invalid_semver(v):
    with pytest.raises(ValidationError):
        ReleaseManifest(version=v, paths=["roles/x/"])


@pytest.mark.parametrize("v", ["1.4.0", "1.4.0-rc1", "2.0.0+build.5", "10.20.30"])
def test_valid_semver(v):
    ReleaseManifest(version=v, paths=["roles/x/"])


def test_paths_required_and_non_empty():
    with pytest.raises(ValidationError):
        ReleaseManifest(version="1.4.0", paths=[])


def test_duplicate_paths_rejected():
    with pytest.raises(ValidationError):
        ReleaseManifest(version="1.4.0", paths=["roles/x/", "roles/x"])


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate({"version": "1.4.0", "paths": ["x/"], "extra": 1})


# --- loading from YAML -----------------------------------------------------

def test_parse_manifest_ok():
    m = parse_manifest("version: '1.4.0'\npaths:\n  - roles/aws_restart/\n")
    assert m.paths == ["roles/aws_restart"]


def test_parse_manifest_empty_raises():
    with pytest.raises(ManifestError):
        parse_manifest("")


def test_parse_manifest_not_mapping_raises():
    with pytest.raises(ManifestError):
        parse_manifest("- a\n- b\n")


def test_load_manifest_missing_file(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nope.yaml")


def test_load_manifest_ok(tmp_path):
    f = tmp_path / "release.yaml"
    f.write_text("version: '1.4.0'\npaths:\n  - roles/aws_restart/\n")
    m = load_manifest(f)
    assert m.version == "1.4.0"
    assert m.paths == ["roles/aws_restart"]


# --- overlap normalization -------------------------------------------------

def test_deduplicate_overlaps():
    m = ReleaseManifest(version="1.4.0", paths=["roles/aws/", "roles/aws/restart/"])
    kept, removed = m.deduplicate_overlaps()
    assert kept == ["roles/aws"]
    assert removed == ["roles/aws/restart"]


def test_deduplicate_no_overlap():
    m = ReleaseManifest(version="1.4.0", paths=["roles/aws/", "roles/telecom/"])
    kept, removed = m.deduplicate_overlaps()
    assert sorted(kept) == ["roles/aws", "roles/telecom"]
    assert removed == []
