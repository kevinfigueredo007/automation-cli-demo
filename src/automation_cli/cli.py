"""Typer-based CLI for the release tool.

Commands
--------
* ``automation release <manifest> [--dry-run] [--tag]``  — create a release.
* ``automation validate <manifest>``                      — validate a manifest only.
"""

from __future__ import annotations

import typer

from .git import GitError, GitRepository
from .manifest import ManifestError, load_manifest
from .models import ReleaseManifest
from .release import ChangeSet, DryRunResult, ReleaseError, create_release
from .release import dry_run as run_dry_run

app = typer.Typer(
    name="automation",
    help="Selective release CLI for an Ansible/AAP automation monorepo.",
    no_args_is_help=True,
    add_completion=False,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _open_repo() -> GitRepository:
    try:
        return GitRepository(".")
    except GitError as exc:
        typer.secho(f"not a git repository: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _load(path: str) -> ReleaseManifest:  # returns ReleaseManifest; keep loose for typing
    try:
        return load_manifest(path)
    except ManifestError as exc:
        typer.secho(f"{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _print_dry_run(result: DryRunResult) -> None:
    m = result.manifest
    r = result.report
    cs: ChangeSet = result.changes

    typer.echo(f"Release: {m.version}")
    typer.echo("")
    typer.echo("Base:")
    typer.echo(f"  branch: {r.base_branch}")
    typer.echo(f"  commit: {r.base_short}")
    typer.echo("")
    typer.echo("Source:")
    typer.echo(f"  branch: {r.source_branch}")
    typer.echo(f"  commit: {r.source_short}")
    typer.echo("")
    typer.echo("Selected paths:")
    for p in result.kept_paths:
        typer.echo(f"  + {p}/")
    if result.removed_redundant:
        typer.echo("")
        typer.echo("Redundant paths ignored (subsumed by a parent path):")
        for p in result.removed_redundant:
            typer.echo(f"  ~ {p}/")
    typer.echo("")
    typer.echo("The following paths will remain from base:")
    for p in result.remaining_top_level:
        typer.echo(f"  {p}")
    typer.echo("")
    typer.echo("Target:")
    typer.echo(f"  {r.release_branch}")
    typer.echo("")
    typer.echo("Changes:")
    typer.echo(f"  {len(cs.added)} files added")
    typer.echo(f"  {len(cs.modified)} files modified")
    typer.echo(f"  {len(cs.deleted)} files deleted")
    typer.echo("")
    if cs.is_empty:
        typer.secho("No changes were made.", fg=typer.colors.YELLOW)
    else:
        typer.secho("No changes were made.", fg=typer.colors.YELLOW)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

@app.command()
def release(
    manifest: str = typer.Argument(..., help="Path to the release manifest YAML."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute and show what would happen; change nothing."
    ),
    tag: bool = typer.Option(False, "--tag", help="Also create a tag <version>."),
    push: bool = typer.Option(
        False,
        "--push",
        help="Push the release branch (and tag, if --tag) to origin and switch back to --source.",
    ),
    base: str = typer.Option("main", "--base", help="Base branch (default: main)."),
    source: str = typer.Option("dev", "--source", help="Source branch (default: dev)."),
    remote: str = typer.Option("origin", "--remote", help="Remote to push to (default: origin)."),
) -> None:
    """Create a release branch from <base> + selected paths from <source>."""
    m = _load(manifest)
    repo = _open_repo()

    if dry_run:
        try:
            result = run_dry_run(repo, m, base_branch=base, source_branch=source)
        except (ReleaseError, GitError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        _print_dry_run(result)
        return

    try:
        release_branch, changes, kept, redundant = create_release(
            repo,
            m,
            base_branch=base,
            source_branch=source,
            create_tag=tag,
            push=push,
            remote=remote,
        )
    except (ReleaseError, GitError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"Created release branch: {release_branch}", fg=typer.colors.GREEN)
    typer.echo(f"  base:    {base}")
    typer.echo(f"  source:  {source}")
    typer.echo(f"  paths:   {len(kept)} selected")
    if redundant:
        typer.echo(f"  ignored: {len(redundant)} redundant path(s)")
    typer.echo("Changes:")
    typer.echo(f"  {len(changes.added)} files added")
    typer.echo(f"  {len(changes.modified)} files modified")
    typer.echo(f"  {len(changes.deleted)} files deleted")
    if tag:
        typer.secho(f"Created tag: {m.version}", fg=typer.colors.GREEN)
    if push:
        typer.secho(f"Pushed {release_branch} to {remote}", fg=typer.colors.GREEN)
        if tag:
            typer.secho(f"Pushed tag {m.version} to {remote}", fg=typer.colors.GREEN)
        typer.echo(f"Switched back to {source}")


@app.command()
def validate(
    manifest: str = typer.Argument(..., help="Path to the release manifest YAML."),
    base: str = typer.Option("main", "--base", help="Base branch (default: main)."),
    source: str = typer.Option("dev", "--source", help="Source branch (default: dev)."),
) -> None:
    """Validate a manifest and the pre-flight checks without changing anything."""
    m = _load(manifest)
    repo = _open_repo()
    from .validator import validate_release
    report = validate_release(repo, m, base_branch=base, source_branch=source, create_tag=False)
    if report.ok:
        typer.secho(f"manifest valid: release/{m.version}", fg=typer.colors.GREEN)
        typer.echo(f"  base:   {base} @ {report.base_short}")
        typer.echo(f"  source: {source} @ {report.source_short}")
        typer.echo(f"  paths:  {len(m.paths)}")
        for warn in report.warnings:
            typer.secho(f"  warning: {warn}", fg=typer.colors.YELLOW)
    else:
        typer.secho("manifest invalid:", fg=typer.colors.RED, err=True)
        for err in report.errors:
            typer.secho(f"  - {err}", fg=typer.colors.RED, err=True)
        for warn in report.warnings:
            typer.secho(f"  ! {warn}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
