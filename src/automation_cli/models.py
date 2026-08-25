"""Pydantic models for the release manifest and related value objects.

The manifest is the single source of truth for a release. It declares a SemVer
version and a list of repository-relative paths that must be promoted from the
source branch (``dev``) onto the release branch (built on top of ``main``).

Design notes
------------
* Paths are always stored normalized: POSIX separators, no leading ``./``,
  no trailing slash (the directory-ness is inferred from the tree).
* Path safety is enforced here so that any caller holding a ``ReleaseManifest``
  can trust the paths are within the repo and not absolute / traversal.
* ``version`` is validated against a strict SemVer regex.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

# Strict SemVer (2.0.0). Numeric identifiers, optional pre-release and build.
# Examples: 1.4.0, 1.4.0-rc1, 2.0.0+build.5
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def normalize_path(path: str) -> str:
    """Normalize a manifest path to a canonical POSIX-relative form.

    Rules:
    * Strip surrounding whitespace.
    * Drop a single leading ``./``.
    * Drop a trailing ``/``.
    * Convert backslashes to forward slashes.
    * Collapse ``.`` segments and refuse empty / traversal / absolute.

    Raises ``ValueError`` on any unsafe or empty path.
    """
    if path is None:
        raise ValueError("path must be a string")
    p = path.strip()
    if not p:
        raise ValueError("path is empty")
    if "\\" in p:
        p = p.replace("\\", "/")
    if "\\" in p:
        raise ValueError("path contains backslashes after normalization")
    # Reject absolute (drive letters too) and Windows UNC.
    if p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", p):
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    if p.startswith("//"):
        raise ValueError(f"UNC paths are not allowed: {path!r}")

    # Drop a single leading "./".
    while p.startswith("./"):
        p = p[2:]
    # Drop trailing slash (but keep a single "."? we reject that below).
    p = p.rstrip("/")

    if p == "" or p == ".":
        raise ValueError("path resolves to the repository root")

    parts = PurePosixPath(p).parts
    for part in parts:
        if part == "..":
            raise ValueError(f"path traversal (..) is not allowed: {path!r}")
        if part == "":
            raise ValueError(f"empty path segment in: {path!r}")
    # Reconstruct to drop any residual '.' segments (PurePosixPath keeps them).
    cleaned = "/".join(part for part in parts if part not in (".", ""))
    if not cleaned:
        raise ValueError("path resolves to nothing after normalization")
    return cleaned


class ReleaseManifest(BaseModel):
    """Typed release manifest.

    Example::

        version: "1.4.0"
        paths:
          - playbooks/aws/restart-instance/
          - roles/aws_restart/
          - roles/aws_common/
    """

    version: Annotated[str, Field(description="Semantic version of the release.")]
    paths: Annotated[list[str], Field(min_length=1, description="Repository-relative paths to promote.")]

    model_config = {"extra": "forbid"}

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not isinstance(v, str):
            raise TypeError("version must be a string")
        v = v.strip()
        if not v:
            raise ValueError("version is empty")
        if not _SEMVER_RE.match(v):
            raise ValueError(f"version is not valid SemVer: {v!r} (expected MAJOR.MINOR.PATCH)")
        return v

    @field_validator("paths")
    @classmethod
    def _validate_paths(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            raise TypeError("paths must be a list")
        if len(v) == 0:
            raise ValueError("paths must contain at least one entry")
        normalized: list[str] = []
        for raw in v:
            normalized.append(normalize_path(raw))
        # Uniqueness on the normalized form.
        seen: set[str] = set()
        dups: list[str] = []
        for p in normalized:
            if p in seen:
                dups.append(p)
            seen.add(p)
        if dups:
            raise ValueError(f"duplicate paths in manifest: {sorted(set(dups))}")
        return normalized

    @model_validator(mode="after")
    def _model_after(self) -> ReleaseManifest:
        # Keep a copy without overlaps; the manifest itself stores what the user
        # wrote (overlaps are reported/normalized by the release service).
        return self

    def normalized_paths(self) -> list[str]:
        """Return the already-normalized paths (kept in order, unique)."""
        return list(self.paths)

    def deduplicate_overlaps(self) -> tuple[list[str], list[str]]:
        """Return (kept_paths, removed_redundant_paths).

        A path ``B`` is redundant when another path ``A`` in the manifest is a
        parent directory of ``B`` (i.e. ``B`` starts with ``A + '/'``). In that
        case processing ``A`` already covers ``B``, so ``B`` is dropped.
        Equal paths are already prevented by uniqueness validation.
        """
        kept: list[str] = []
        removed: list[str] = []
        paths = self.paths
        for candidate in paths:
            redundant = False
            for other in paths:
                if other == candidate:
                    continue
                # other is a strict parent of candidate?
                if candidate.startswith(other + "/"):
                    redundant = True
                    break
            if redundant:
                removed.append(candidate)
            else:
                kept.append(candidate)
        # Preserve original order in `kept` and avoid duplicate entries.
        return kept, removed


__all__ = ["ReleaseManifest", "normalize_path"]
