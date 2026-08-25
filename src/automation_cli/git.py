"""Git operations encapsulated in :class:`GitRepository`.

Every git call uses :func:`subprocess.run` with a list of arguments — never
``shell=True`` and never an interpolated command string. Paths are passed as
separate argv elements so that no shell quoting is involved.

The service is intentionally minimal: it exposes what the release flow needs and
nothing more, keeping it easy to audit and test against a temporary repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or the repo is in an unexpected state."""


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short_sha: str
    subject: str


@dataclass(frozen=True)
class TreeEntry:
    """A file as it exists in a given ref/tree: relative path + blob SHA."""
    path: str
    blob_sha: str


class GitRepository:
    """A thin, safe wrapper around git for the release workflow.

    All commands are run with ``cwd`` set to the repository root passed to the
    constructor. A non-zero exit code raises :class:`GitError` with the captured
    stderr so callers never silently continue.
    """

    def __init__(self, path: str | Path, *, git_binary: str = "git") -> None:
        self.root = Path(path).resolve()
        self._git = git_binary
        # Fail fast: ensure this is actually a git repository.
        self._run(["rev-parse", "--is-inside-work-tree"])

    # ---- low level ---------------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = [self._git] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=capture,
                text=text,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError(f"git executable not found: {self._git!r}") from exc
        if check and result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise GitError(
                f"git {' '.join(args[:1])} failed (exit {result.returncode}): {stderr or 'no stderr'}"
            )
        return result

    @staticmethod
    def _clean(stdout: str) -> str:
        return stdout.strip()

    # ---- queries -----------------------------------------------------------

    def current_branch(self) -> str:
        out = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        name = self._clean(out.stdout)
        if name == "HEAD":
            # Detached HEAD: return the sha for clarity.
            return self.head_sha()
        return name

    def head_sha(self) -> str:
        return self._clean(self._run(["rev-parse", "HEAD"]).stdout)

    def is_clean(self) -> bool:
        """True when the working tree has no staged/unstaged/untracked changes."""
        out = self._run(["status", "--porcelain"]).stdout
        return self._clean(out) == ""

    def status_porcelain(self) -> str:
        return self._run(["status", "--porcelain"]).stdout.strip()

    def rev_exists(self, ref: str) -> bool:
        """True when ``ref`` resolves to a commit (branch/tag/sha)."""
        result = self._run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False)
        return result.returncode == 0

    def branch_exists(self, name: str) -> bool:
        result = self._run(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], check=False
        )
        return result.returncode == 0

    def tag_exists(self, name: str) -> bool:
        result = self._run(
            ["show-ref", "--verify", "--quiet", f"refs/tags/{name}"], check=False
        )
        return result.returncode == 0

    def get_commit(self, ref: str) -> CommitInfo:
        sha = self._clean(self._run(["rev-parse", ref]).stdout)
        short = self._clean(self._run(["rev-parse", "--short", ref]).stdout)
        subject = self._clean(self._run(["log", "-1", "--format=%s", ref]).stdout)
        return CommitInfo(sha=sha, short_sha=short, subject=subject)

    def top_level_entries(self, ref: str) -> list[str]:
        """Top-level files/dirs at the root of ``ref`` (non-recursive)."""
        out = self._run(["ls-tree", "--name-only", ref]).stdout
        return [self._clean(line) for line in out.splitlines() if line.strip()]

    def list_dir(self, ref: str, path: str) -> list[str]:
        """Immediate children of directory ``path`` at ``ref`` (non-recursive).

        Returns full repo-relative paths. Empty when ``path`` is not a directory
        at ``ref``. Uses the ``<ref>:<path>`` tree-ish syntax so that the
        children (not the directory entry itself) are listed.
        """
        norm = path.strip().strip("/")
        result = self._run(["ls-tree", "--name-only", f"{ref}:{norm}"], check=False)
        if result.returncode != 0:
            return []
        entries: list[str] = []
        for line in result.stdout.splitlines():
            name = self._clean(line)
            if not name:
                continue
            entries.append(f"{norm}/{name}")
        return entries

    # ---- tree listing (with blob SHAs) ------------------------------------

    def ls_tree_files(self, ref: str, path: str) -> list[TreeEntry]:
        """Recursively list file blobs under ``path`` at ``ref``.

        Returns one :class:`TreeEntry` per file (relative to repo root).
        Returns an empty list when ``path`` does not exist at ``ref``.
        Works whether ``path`` is a file or a directory.
        """
        norm = path.strip().strip("/")
        # Try recursive listing with pathspec filter.
        result = self._run(
            ["ls-tree", "-r", "--name-only", ref, "--", norm], check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [self._clean(l) for l in result.stdout.splitlines() if l.strip()]
            entries: list[TreeEntry] = []
            for relpath in lines:
                blob = self.blob_sha(ref, relpath)
                entries.append(TreeEntry(path=relpath, blob_sha=blob))
            return entries
        # Maybe it is a single file that ls-tree -r didn't enumerate (edge cases).
        if self.path_exists_in_ref(ref, norm):
            blob = self.blob_sha(ref, norm)
            return [TreeEntry(path=norm, blob_sha=blob)]
        return []

    def blob_sha(self, ref: str, path: str) -> str:
        """Return the blob SHA for ``path`` at ``ref`` or raise :class:`GitError`."""
        out = self._run(["rev-parse", f"{ref}:{path}"])
        return self._clean(out.stdout)

    def path_exists_in_ref(self, ref: str, path: str) -> bool:
        """True when ``path`` exists (file or dir) at ``ref``."""
        # A directory exists if ls-tree --name-only <ref> -- <path> returns something.
        result = self._run(["ls-tree", "--name-only", ref, "--", path], check=False)
        if result.returncode == 0 and result.stdout.strip():
            return True
        # A file? Check with rev-parse existence.
        result = self._run(["rev-parse", "--verify", "--quiet", f"{ref}:{path}"], check=False)
        return result.returncode == 0

    def is_tree_in_ref(self, ref: str, path: str) -> bool:
        """True when ``path`` is a directory (tree object) at ``ref``."""
        result = self._run(["cat-file", "-t", f"{ref}:{path}"], check=False)
        return result.returncode == 0 and result.stdout.strip() == "tree"

    # ---- mutations ---------------------------------------------------------

    def fetch(self, remote: str = "origin") -> None:
        self._run(["fetch", "--", remote])

    def fetch_branches(self, remote: str, branches: list[str]) -> None:
        """Fetch specific branches from ``remote`` without merging."""
        if not branches:
            return
        self._run(["fetch", "--", remote, *branches])

    def pull_ff_only(self, branch: str, *, remote: str = "origin") -> None:
        """Fast-forward-only pull of ``branch`` from ``remote``.

        Equivalent to ``git pull --ff-only <remote> <branch>``. Fails with
        :class:`GitError` if the branch has diverged (local has commits the
        remote does not), so no merge commit is ever created silently.
        """
        self._run(["pull", "--ff-only", remote, branch])

    def local_ahead_or_behind(self, branch: str, remote_ref: str) -> tuple[int, int]:
        """Return ``(ahead, behind)`` commit counts of local ``branch`` vs
        ``remote_ref`` (e.g. ``origin/main``).

        ``ahead``  = commits local has that remote doesn't.
        ``behind`` = commits remote has that local doesn't.
        """
        result = self._run(
            ["rev-list", "--left-right", "--count", f"{branch}...{remote_ref}"], check=False
        )
        if result.returncode != 0:
            return (0, 0)
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            return (0, 0)
        return int(parts[0]), int(parts[1])

    def create_branch(self, name: str, from_ref: str) -> None:
        self._run(["branch", name, from_ref])

    def checkout(self, ref: str) -> None:
        self._run(["checkout", ref])

    def checkout_path_from_ref(self, ref: str, path: str) -> None:
        """Restore ``path`` (file or directory) at the working tree from ``ref``.

        Equivalent to ``git checkout <ref> -- <path>``. Updates both the index
        and the working tree for the matching entries.
        """
        self._run(["checkout", ref, "--", path])

    def add(self, paths: Iterable[str]) -> None:
        paths = list(paths)
        if not paths:
            return
        # Only stage pathspecs that still exist in the working tree, so that a
        # fully-removed path does not cause `git add` to abort with
        # "pathspec did not match any files".
        existing = [p for p in paths if (self.root / p).exists()]
        if not existing:
            return
        self._run(["add", "--", *existing])

    def rm(self, paths: Iterable[str], *, force: bool = False) -> None:
        paths = list(paths)
        if not paths:
            return
        args = ["rm"]
        if force:
            args.append("-f")
        args.append("--")
        args.extend(paths)
        self._run(args)

    def commit(self, message: str, *, allow_empty: bool = False) -> str:
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self._run(args)
        return self.head_sha()

    def create_tag(self, name: str, ref: str, *, message: str | None = None) -> None:
        args = ["tag"]
        if message is not None:
            args += ["-a", name, "-m", message, ref]
        else:
            args += [name, ref]
        self._run(args)

    def push_ref(self, ref: str, *, remote: str = "origin", set_upstream: bool = False) -> None:
        """Push ``ref`` to ``remote``.

        With ``set_upstream=True`` (recommended for branches) configures tracking.
        """
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args += [remote, ref]
        self._run(args)

    def push_tag(self, name: str, *, remote: str = "origin") -> None:
        """Push a single tag to ``remote``."""
        self._run(["push", remote, name])

    def has_remote(self, name: str = "origin") -> bool:
        """True when ``name`` is a configured remote."""
        result = self._run(["remote", "get-url", name], check=False)
        return result.returncode == 0 and result.stdout.strip() != ""

    # ---- convenience -------------------------------------------------------

    def repo_root(self) -> Path:
        return self.root


__all__ = ["CommitInfo", "GitError", "GitRepository", "TreeEntry"]
