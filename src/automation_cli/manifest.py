"""Manifest loading and parsing.

This module turns a YAML file (or string) into a validated :class:`ReleaseManifest`.
All format/schema/SemVer/path-safety problems surface here as
:class:`ManifestError`, so callers do not have to deal with Pydantic exceptions
directly.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import ReleaseManifest


class ManifestError(ValueError):
    """Raised when the manifest cannot be loaded or fails validation."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


def parse_manifest(content: str, *, source: str = "<string>") -> ReleaseManifest:
    """Parse a YAML string into a validated :class:`ReleaseManifest`.

    Raises :class:`ManifestError` on YAML parse errors, schema mismatch,
    SemVer or path-safety violations.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {source}: {exc}", path=source) from exc

    if data is None:
        raise ManifestError(f"manifest is empty in {source}", path=source)
    if not isinstance(data, dict):
        raise ManifestError(
            f"manifest must be a YAML mapping with 'version' and 'paths', got {type(data).__name__}",
            path=source,
        )

    try:
        return ReleaseManifest.model_validate(data)
    except ValidationError as exc:
        # Flatten the pydantic error to a readable message.
        problems = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            msg = err.get("msg", "")
            problems.append(f"{loc}: {msg}" if loc else msg)
        detail = "; ".join(problems) if problems else str(exc)
        raise ManifestError(f"invalid manifest in {source}: {detail}", path=source) from exc


def load_manifest(path: str | Path) -> ReleaseManifest:
    """Load a manifest from ``path`` on disk."""
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest file does not exist: {p}", path=str(p))
    if not p.is_file():
        raise ManifestError(f"manifest path is not a file: {p}", path=str(p))
    try:
        content = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {p}: {exc}", path=str(p)) from exc
    return parse_manifest(content, source=str(p))


__all__ = ["ManifestError", "ReleaseManifest", "load_manifest", "parse_manifest"]
