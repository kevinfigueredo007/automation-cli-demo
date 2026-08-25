"""Pre-flight validation for a release.

All checks that must pass *before* the repository is touched live here. They
return a :class:`ValidationReport` so the CLI can either print a clean summary
or fail with a precise list of problems.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .git import GitRepository
from .models import ReleaseManifest


class ValidationError(Exception):
    """Raised when :func:`validate_release` finds blocking problems."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__("\n".join(report.errors) if report.errors else "validation failed")


@dataclass
class ValidationReport:
    ok: bool
    base_branch: str
    source_branch: str
    release_branch: str
    base_commit: str | None = None
    source_commit: str | None = None
    base_short: str | None = None
    source_short: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_release(
    repo: GitRepository,
    manifest: ReleaseManifest,
    *,
    base_branch: str = "main",
    source_branch: str = "dev",
    create_tag: bool = False,
    push: bool = False,
    remote: str = "origin",
) -> ValidationReport:
    """Run all pre-flight checks and return a :class:`ValidationReport`.

    The checks (in order):
    * base branch exists;
    * source branch exists;
    * release branch does not already exist;
    * working tree is clean;
    * every manifest path is either present in the source branch OR is a
      supported "total removal" (present in the base branch but absent in
      source);
    * when ``create_tag`` is set, the tag must not already exist;
    * when ``push`` is set, ``remote`` must be configured.
    """
    version = manifest.version
    release_branch = f"release/{version}"

    report = ValidationReport(
        ok=True,
        base_branch=base_branch,
        source_branch=source_branch,
        release_branch=release_branch,
    )

    if not repo.branch_exists(base_branch):
        report.add_error(f"base branch does not exist: {base_branch!r}")
        report.ok = False
    else:
        ci = repo.get_commit(base_branch)
        report.base_commit = ci.sha
        report.base_short = ci.short_sha

    if not repo.branch_exists(source_branch):
        report.add_error(f"source branch does not exist: {source_branch!r}")
        report.ok = False
    else:
        ci = repo.get_commit(source_branch)
        report.source_commit = ci.sha
        report.source_short = ci.short_sha

    if repo.branch_exists(release_branch):
        report.add_error(
            f"release branch already exists: {release_branch!r} (remove it or pick a new version)"
        )
        report.ok = False

    if not repo.is_clean():
        porcelain = repo.status_porcelain()
        report.add_error(
            "working tree is not clean; commit or stash your changes before running a release:\n"
            + porcelain
        )
        report.ok = False

    # Path checks only make sense if both refs exist.
    if report.base_commit and report.source_commit:
        for path in manifest.paths:
            in_source = repo.path_exists_in_ref(source_branch, path)
            in_base = repo.path_exists_in_ref(base_branch, path)
            if not in_source and not in_base:
                report.add_error(
                    f"path {path!r} exists neither in {source_branch} nor in {base_branch}"
                )
                report.ok = False
            elif not in_source and in_base:
                # Supported: total removal — the release will delete this path.
                report.add_warning(
                    f"path {path!r} no longer exists in {source_branch}; it will be removed in the release"
                )

    if create_tag and repo.tag_exists(version):
        report.add_error(f"tag already exists: {version!r}")
        report.ok = False

    if push and not repo.has_remote(remote):
        report.add_error(
            f"remote {remote!r} is not configured; cannot push (set it up with: git remote add {remote} <url>)"
        )
        report.ok = False

    if report.errors:
        report.ok = False
    return report


__all__ = ["ValidationError", "ValidationReport", "validate_release"]
