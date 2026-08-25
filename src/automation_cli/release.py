"""Release orchestration: manifest -> release/<version> branch + optional tag.

The core invariant is::

    release/<version> = main + (selected paths from dev)

This module computes the set of changes implied by the manifest, can describe
them for a dry run, and can apply them on top of a freshly-created release
branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .git import GitRepository
from .models import ReleaseManifest
from .validator import ValidationReport, validate_release


@dataclass
class ChangeSet:
    """The diff that a release would apply to the base branch."""

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


@dataclass
class DryRunResult:
    manifest: ReleaseManifest
    report: ValidationReport
    changes: ChangeSet
    kept_paths: list[str]
    removed_redundant: list[str]
    remaining_top_level: list[str]


class ReleaseError(RuntimeError):
    """Raised when the release cannot proceed (validation or git failure)."""


def _compute_remaining(repo: GitRepository, base_ref: str, kept: list[str]) -> list[str]:
    """List paths that will stay exactly as in ``base_ref``.

    Walks the base tree, recursing into directories. A node is reported as
    "remaining" when no selected path covers it (as an ancestor/equal) and no
    selected path lives underneath it. When a node is fully covered by a
    selected ancestor it is skipped entirely; when it is only partially covered
    we recurse into its children to surface the untouched siblings.
    """

    def _is_ancestor_or_eq(a: str, b: str) -> bool:
        """True when ``a`` is an ancestor of (or equal to) ``b``."""
        return a == b or b.startswith(a + "/")

    remaining: list[str] = []

    def _walk(path: str) -> None:
        # Fully replaced by a selected ancestor (or selected itself)?
        if any(_is_ancestor_or_eq(p, path) for p in kept):
            return
        # Any selected descendant under `path`? -> partially covered, recurse.
        if not any(p.startswith(path + "/") for p in kept):
            # Nothing selected here or below: the whole node remains.
            remaining.append(path + "/" if repo.is_tree_in_ref(base_ref, path) else path)
            return
        for child in repo.list_dir(base_ref, path):
            _walk(child)

    for top in repo.top_level_entries(base_ref):
        _walk(top)
    return sorted(remaining)


def _compare_trees(
    repo: GitRepository,
    base_ref: str,
    source_ref: str,
    paths: list[str],
) -> ChangeSet:
    """Compute add/modify/delete for the selected ``paths`` between base and source.

    A file is:
    * added   : present in source under a selected path, absent in base;
    * modified: present in both but with a different blob SHA;
    * deleted : present in base under a selected path, absent in source.
    """
    cs = ChangeSet()
    for path in paths:
        base_entries = {e.path: e.blob_sha for e in repo.ls_tree_files(base_ref, path)}
        source_entries = {e.path: e.blob_sha for e in repo.ls_tree_files(source_ref, path)}

        for f, sha in source_entries.items():
            if f not in base_entries:
                cs.added.append(f)
            elif base_entries[f] != sha:
                cs.modified.append(f)
        for f in base_entries:
            if f not in source_entries:
                cs.deleted.append(f)
    # Stable, human-friendly ordering.
    cs.added.sort()
    cs.modified.sort()
    cs.deleted.sort()
    return cs


def dry_run(
    repo: GitRepository,
    manifest: ReleaseManifest,
    *,
    base_branch: str = "main",
    source_branch: str = "dev",
) -> DryRunResult:
    """Compute what would happen without touching the repository."""
    report = validate_release(
        repo, manifest, base_branch=base_branch, source_branch=source_branch, create_tag=False
    )
    if not report.ok:
        raise ReleaseError(_format_report(report))

    kept, removed_redundant = manifest.deduplicate_overlaps()
    changes = _compare_trees(repo, base_branch, source_branch, kept)

    remaining_top_level = _compute_remaining(repo, base_branch, kept)

    return DryRunResult(
        manifest=manifest,
        report=report,
        changes=changes,
        kept_paths=kept,
        removed_redundant=removed_redundant,
        remaining_top_level=remaining_top_level,
    )


def create_release(
    repo: GitRepository,
    manifest: ReleaseManifest,
    *,
    base_branch: str = "main",
    source_branch: str = "dev",
    create_tag: bool = False,
    commit_message: str | None = None,
    push: bool = False,
    remote: str = "origin",
) -> tuple[str, ChangeSet, list[str], list[str]]:
    """Apply the release: create ``release/<version>`` from base + selected paths.

    Returns ``(release_branch, changes, kept_paths, removed_redundant)``.

    Steps:
    1. Pre-flight validation (incl. remote presence when ``push``).
    2. Normalize overlapping paths.
    3. Create the release branch from the base branch and check it out.
    4. For each selected path, restore its state from the source branch and
       remove any files that were deleted there.
    5. Stage and commit. Optionally create a tag.
    6. When ``push``: push the release branch (and the tag when ``create_tag``)
       to ``remote`` with ``-u``, then ``checkout`` the source branch.
    """
    report = validate_release(
        repo,
        manifest,
        base_branch=base_branch,
        source_branch=source_branch,
        create_tag=create_tag,
        push=push,
        remote=remote,
    )
    if not report.ok:
        raise ReleaseError(_format_report(report))

    kept, removed_redundant = manifest.deduplicate_overlaps()
    version = manifest.version
    release_branch = f"release/{version}"

    # Build the branch on top of base.
    repo.create_branch(release_branch, base_branch)
    repo.checkout(release_branch)

    changes = ChangeSet()

    for path in kept:
        source_entries = {e.path: e.blob_sha for e in repo.ls_tree_files(source_branch, path)}
        base_entries = {e.path: e.blob_sha for e in repo.ls_tree_files(base_branch, path)}

        if source_entries:
            # Restore everything under `path` from the source branch.
            repo.checkout_path_from_ref(source_branch, path)
        # Remove files that existed in base under `path` but no longer in source.
        to_delete = [f for f in base_entries if f not in source_entries]
        if to_delete:
            repo.rm(to_delete)

        for f, sha in source_entries.items():
            if f not in base_entries:
                changes.added.append(f)
            elif base_entries[f] != sha:
                changes.modified.append(f)
        for f in base_entries:
            if f not in source_entries:
                changes.deleted.append(f)

    # Stage anything under the selected paths (covers additions & modifications;
    # deletions are already staged by `git rm`).
    repo.add(kept + changes.added + changes.modified)

    changes.added.sort()
    changes.modified.sort()
    changes.deleted.sort()

    message = commit_message if commit_message is not None else f"release: {version}"
    repo.commit(message)

    if create_tag:
        repo.create_tag(version, release_branch, message=message)

    if push:
        # Push the branch first; if it fails, leave the user on the release
        # branch so they can inspect and retry — do NOT switch to source.
        repo.push_ref(release_branch, remote=remote, set_upstream=True)
        if create_tag:
            repo.push_tag(version, remote=remote)
        # Only switch back to the source branch after a successful push.
        repo.checkout(source_branch)

    return release_branch, changes, kept, removed_redundant


def _format_report(report: ValidationReport) -> str:
    parts = ["Validation failed:"]
    for err in report.errors:
        parts.append(f"  - {err}")
    for warn in report.warnings:
        parts.append(f"  ! {warn}")
    return "\n".join(parts)


__all__ = [
    "ChangeSet",
    "DryRunResult",
    "ReleaseError",
    "create_release",
    "dry_run",
]
