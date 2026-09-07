"""Project-local Ocean storage layout, permissions, URI resolution, and redaction."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


class StoragePolicyError(RuntimeError):
    """A filesystem path, URI, or portable-export value violates local policy."""


_POSIX_PRIVATE_DIRECTORY = 0o700
_POSIX_PRIVATE_FILE = 0o600
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+"
)
_WINDOWS_DEVICE_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


def _posix_permissions_supported() -> bool:
    return os.name == "posix"


def ensure_private_directory(path: Path) -> Path:
    """Create a local state directory and enforce owner-only POSIX permissions."""

    path.mkdir(parents=True, exist_ok=True, mode=_POSIX_PRIVATE_DIRECTORY)
    if _posix_permissions_supported():
        path.chmod(_POSIX_PRIVATE_DIRECTORY)
    return path


def ensure_private_file(path: Path) -> Path:
    """Restrict an existing local state file to the current OS user on POSIX."""

    ensure_private_directory(path.parent)
    if not path.exists():
        path.touch(mode=_POSIX_PRIVATE_FILE)
    if _posix_permissions_supported():
        path.chmod(_POSIX_PRIVATE_FILE)
    return path


def assert_private_mode(path: Path, *, directory: bool) -> None:
    """Raise if a supported platform did not retain the expected private mode."""

    if not _posix_permissions_supported():
        return
    actual = stat.S_IMODE(path.stat().st_mode)
    expected = _POSIX_PRIVATE_DIRECTORY if directory else _POSIX_PRIVATE_FILE
    if actual != expected:
        raise StoragePolicyError(f"Expected mode {expected:o} for {path}, found {actual:o}")


def is_safe_cross_platform_relative_path(path: str) -> bool:
    """Accept only canonical relative path segments on both POSIX and Windows."""

    try:
        relative = PurePosixPath(path)
    except (TypeError, ValueError):
        return False
    if not path or relative.is_absolute() or "\\" in path or not relative.parts:
        return False
    for component in relative.parts:
        if (
            component in {".", ".."}
            or ":" in component
            or component.endswith((".", " "))
            or any(ord(character) < 32 for character in component)
        ):
            return False
        windows_stem = component.split(".", 1)[0].rstrip(" .")
        if _WINDOWS_DEVICE_NAME.fullmatch(windows_stem):
            return False
    return True


@dataclass(frozen=True)
class OceanPaths:
    """Canonical per-project state layout; files remain relative to this root."""

    project_root: Path
    root: Path
    database: Path
    artifacts: Path
    datasets: Path
    runs: Path
    staging: Path
    quarantine: Path
    exports: Path
    backups: Path
    cache: Path

    @classmethod
    def for_project(cls, project_root: Path) -> "OceanPaths":
        project = project_root.resolve()
        root = project / ".oceanmind"
        return cls(
            project_root=project,
            root=root,
            database=root / "workspace.sqlite3",
            artifacts=root / "artifacts",
            datasets=root / "datasets",
            runs=root / "runs",
            staging=root / "staging",
            quarantine=root / "quarantine",
            exports=root / "exports",
            backups=root / "backups",
            cache=root / "cache",
        )

    @classmethod
    def for_state_root(cls, state_root: Path) -> "OceanPaths":
        """Adapt an explicit state-directory option without guessing a project root."""

        root = state_root.resolve()
        return cls(
            project_root=root.parent,
            root=root,
            database=root / "workspace.sqlite3",
            artifacts=root / "artifacts",
            datasets=root / "datasets",
            runs=root / "runs",
            staging=root / "staging",
            quarantine=root / "quarantine",
            exports=root / "exports",
            backups=root / "backups",
            cache=root / "cache",
        )

    def ensure(self) -> "OceanPaths":
        """Create all state roots before a database or artifact operation runs."""

        for directory in (
            self.root,
            self.artifacts,
            self.datasets,
            self.runs,
            self.staging,
            self.quarantine,
            self.exports,
            self.backups,
            self.cache,
        ):
            ensure_private_directory(directory)
            assert_private_mode(directory, directory=True)
        return self

    def ensure_private_subdirectory(self, path: Path) -> Path:
        """Create every root-relative directory component with owner-only modes."""

        relative = self.relative_path(path)
        current = ensure_private_directory(self.root)
        for component in relative.parts:
            current = ensure_private_directory(current / component)
        return current

    def relative_path(self, path: Path) -> Path:
        """Return a root-relative path or reject an arbitrary local filesystem path."""

        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise StoragePolicyError(f"Path escapes Ocean state root: {path}") from exc

    def uri_for(self, path: Path) -> str:
        """Convert a checked state file into a project-relative ocean:// URI."""

        relative = self.relative_path(path)
        relative_text = relative.as_posix()
        if not relative.parts:
            raise StoragePolicyError("Ocean root itself cannot be an artifact URI")
        if not is_safe_cross_platform_relative_path(relative_text):
            raise StoragePolicyError(f"Ocean state path is not cross-platform safe: {path}")
        return "ocean://" + relative_text

    def resolve_uri(self, uri: str) -> Path:
        """Resolve only canonical, root-contained ocean:// URIs."""

        parsed = urlsplit(uri)
        if parsed.scheme != "ocean" or not parsed.netloc or parsed.query or parsed.fragment:
            raise StoragePolicyError(f"Invalid Ocean URI: {uri}")
        if parsed.path and (
            not parsed.path.startswith("/")
            or not parsed.path[1:]
            or any(not piece for piece in parsed.path[1:].split("/"))
        ):
            raise StoragePolicyError(f"Ocean URI is not canonical: {uri}")
        path_pieces = tuple(parsed.path[1:].split("/")) if parsed.path else ()
        pieces = (parsed.netloc, *path_pieces)
        relative_text = "/".join(pieces)
        if not is_safe_cross_platform_relative_path(relative_text):
            raise StoragePolicyError(f"Ocean URI has an unsafe path: {uri}")
        target = self.root.joinpath(*pieces)
        self.relative_path(target)
        return target


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """Write deterministic private JSON used for manifests and local audit records."""

    ensure_private_directory(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if _posix_permissions_supported():
        temporary.chmod(_POSIX_PRIVATE_FILE)
    os.replace(temporary, path)
    ensure_private_file(path)


def sanitize_portable_value(value: Any, *, workspace_root: Path, home_root: Path | None = None) -> Any:
    """Recursively redact local paths and reject credential-like export content."""

    resolved_workspace = workspace_root.resolve()
    resolved_home = (home_root or Path.home()).resolve()
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(api[_-]?key|token|secret|password|authorization)", key_text):
                raise StoragePolicyError("Portable export contains a credential-like field")
            sanitized[key_text] = sanitize_portable_value(
                item,
                workspace_root=resolved_workspace,
                home_root=resolved_home,
            )
        return sanitized
    if isinstance(value, list):
        return [
            sanitize_portable_value(item, workspace_root=resolved_workspace, home_root=resolved_home)
            for item in value
        ]
    if not isinstance(value, str):
        return value
    if _SECRET_ASSIGNMENT.search(value):
        raise StoragePolicyError("Portable export contains a credential-like value")
    sanitized = value.replace(str(resolved_workspace), "<workspace>")
    sanitized = sanitized.replace(str(resolved_home), "<home>")
    return sanitized


__all__ = [
    "OceanPaths",
    "StoragePolicyError",
    "assert_private_mode",
    "ensure_private_directory",
    "ensure_private_file",
    "sanitize_portable_value",
    "write_private_json",
]
