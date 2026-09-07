"""Immutable artifact version directories with staging and checksum verification."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping
from uuid import uuid4

from oceanx.artifacts.models import ArtifactFile, ArtifactManifest, ArtifactVersionDraft
from oceanx.storage import (
    OceanPaths,
    ensure_private_file,
    is_safe_cross_platform_relative_path,
    write_private_json,
)


class ArtifactFileError(RuntimeError):
    """An immutable artifact version cannot be staged, finalized, or verified."""


@dataclass(frozen=True)
class StagedArtifact:
    operation_id: str
    manifest: ArtifactManifest
    staging_directory: Path
    target_directory: Path

    @property
    def manifest_path(self) -> Path:
        return self.staging_directory / "manifest.json"


class ArtifactFileStore:
    """Own private immutable artifact paths; metadata is committed by RequestStore."""

    def __init__(self, paths: OceanPaths) -> None:
        self.paths = paths.ensure()

    def next_version_directory(self, draft: ArtifactVersionDraft, version: int) -> Path:
        return self.paths.artifacts / draft.artifact_type / draft.artifact_id / f"v{version:04d}"

    def stage(
        self,
        draft: ArtifactVersionDraft,
        *,
        version: int,
        files: Mapping[str, bytes | Path] | None = None,
        operation_id: str | None = None,
    ) -> StagedArtifact:
        """Create a complete private staging directory before any metadata is committed."""

        operation = operation_id or f"artifact_{uuid4().hex}"
        staging = self.paths.staging / operation
        if staging.exists():
            raise ArtifactFileError(f"Artifact staging operation already exists: {operation}")
        self.paths.ensure_private_subdirectory(staging)
        target = self.next_version_directory(draft, version)
        self.paths.ensure_private_subdirectory(target.parent)
        try:
            file_records = self._write_files(staging, target, files or {})
            manifest = ArtifactManifest.from_draft(draft, version=version, files=tuple(file_records))
            write_private_json(manifest_path := staging / "manifest.json", manifest.model_dump(mode="json"))
            ensure_private_file(manifest_path)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return StagedArtifact(
            operation_id=operation,
            manifest=manifest,
            staging_directory=staging,
            target_directory=target,
        )

    def finalize(self, staged: StagedArtifact) -> Path:
        """Atomically move a fully checked staging directory into its immutable target."""

        self.verify_directory(staged.staging_directory, expected_manifest=staged.manifest)
        target = staged.target_directory
        self.paths.ensure_private_subdirectory(target.parent)
        if target.exists():
            self.verify_directory(target, expected_manifest=staged.manifest)
            if staged.staging_directory.exists():
                shutil.rmtree(staged.staging_directory)
            return target
        try:
            os.replace(staged.staging_directory, target)
        except OSError as exc:
            raise ArtifactFileError(f"Could not atomically finalize artifact version: {target}") from exc
        self.verify_directory(target, expected_manifest=staged.manifest)
        return target

    def verify_directory(
        self,
        directory: Path,
        *,
        expected_manifest: ArtifactManifest | None = None,
    ) -> ArtifactManifest:
        """Verify immutable manifest and every declared artifact-file checksum."""

        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ArtifactFileError(f"Artifact manifest is unavailable: {manifest_path}")
        try:
            manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ArtifactFileError(f"Artifact manifest is invalid: {manifest_path}") from exc
        rebuilt = ArtifactManifest.from_draft(
            ArtifactVersionDraft(
                workspace_id=manifest.workspace_id,
                artifact_id=manifest.artifact_id,
                artifact_type=manifest.artifact_type,
                schema_version=manifest.artifact_schema_version,
                title=manifest.title,
                summary=manifest.summary,
                created_by=manifest.created_by,
                content=manifest.content,
                intrinsic_links=manifest.intrinsic_links,
                provenance=manifest.provenance,
                supersedes_version=manifest.supersedes_version,
            ),
            version=manifest.version,
            files=manifest.files,
            created_at=manifest.created_at,
        )
        if rebuilt.manifest_sha256 != manifest.manifest_sha256:
            raise ArtifactFileError("Artifact manifest checksum does not match immutable content")
        if expected_manifest is not None and manifest.manifest_sha256 != expected_manifest.manifest_sha256:
            raise ArtifactFileError("Existing target belongs to a different artifact commit")
        canonical_target = self.next_version_directory(
            ArtifactVersionDraft(
                workspace_id=manifest.workspace_id,
                artifact_id=manifest.artifact_id,
                artifact_type=manifest.artifact_type,
                schema_version=manifest.artifact_schema_version,
                title=manifest.title,
                summary=manifest.summary,
                created_by=manifest.created_by,
                content=manifest.content,
                intrinsic_links=manifest.intrinsic_links,
                provenance=manifest.provenance,
                supersedes_version=manifest.supersedes_version,
            ),
            manifest.version,
        )
        for record in manifest.files:
            canonical_file = self.paths.resolve_uri(record.uri)
            try:
                relative = canonical_file.relative_to(canonical_target)
            except ValueError as exc:
                raise ArtifactFileError("Artifact manifest references a file outside its version directory") from exc
            file_path = directory / relative
            if not relative.parts or not file_path.is_file() or file_path.is_symlink():
                raise ArtifactFileError(f"Artifact file is unavailable: {record.uri}")
            if file_path.stat().st_size != record.size_bytes:
                raise ArtifactFileError(f"Artifact file size differs from manifest: {record.uri}")
            if _sha256_file(file_path) != record.sha256:
                raise ArtifactFileError(f"Artifact file checksum differs from manifest: {record.uri}")
        return manifest

    def quarantine(self, staged: StagedArtifact, *, reason: str) -> Path:
        """Preserve a mismatched staged payload for audit without making it publishable."""

        return self.quarantine_directory(
            staged.staging_directory,
            operation_id=staged.operation_id,
            reason=reason,
        )

    def quarantine_directory(self, directory: Path, *, operation_id: str, reason: str) -> Path:
        """Move an uncommitted state-root directory aside without overwriting evidence."""

        self.paths.relative_path(directory)
        safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "-", reason).strip("-") or "unknown"
        base = self.paths.quarantine / f"{operation_id}-{safe_reason}"
        destination = base
        suffix = 1
        while destination.exists():
            destination = self.paths.quarantine / f"{base.name}-{suffix}"
            suffix += 1
        self.paths.ensure_private_subdirectory(destination.parent)
        if directory.exists():
            os.replace(directory, destination)
        return destination

    def _write_files(
        self,
        staging: Path,
        target: Path,
        files: Mapping[str, bytes | Path],
    ) -> list[ArtifactFile]:
        records: list[ArtifactFile] = []
        for relative_name, source in sorted(files.items()):
            relative = _safe_relative_name(relative_name)
            destination = staging / relative
            self.paths.ensure_private_subdirectory(destination.parent)
            if isinstance(source, Path):
                if not source.is_file() or source.is_symlink():
                    raise ArtifactFileError(f"Artifact source must be a regular non-symlink file: {source}")
                shutil.copyfile(source, destination)
            else:
                destination.write_bytes(source)
            ensure_private_file(destination)
            uri = self.paths.uri_for(target / relative)
            records.append(
                ArtifactFile(
                    uri=uri,
                    mime_type=_mime_type_for(relative),
                    size_bytes=destination.stat().st_size,
                    sha256=_sha256_file(destination),
                )
            )
        return records


def _safe_relative_name(name: str) -> Path:
    candidate = PurePosixPath(name)
    if not is_safe_cross_platform_relative_path(name):
        raise ArtifactFileError(f"Artifact file name must be a relative safe path: {name}")
    return Path(*candidate.parts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mime_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".json": "application/json",
        ".geojson": "application/geo+json",
        ".png": "image/png",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".ipynb": "application/x-ipynb+json",
        ".csv": "text/csv",
        ".nc": "application/x-netcdf",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")


__all__ = ["ArtifactFileError", "ArtifactFileStore", "StagedArtifact"]
