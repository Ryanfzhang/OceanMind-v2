"""Resolve immutable DatasetArtifact sources without assuming they are single files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oceanx.artifacts.models import ArtifactRef
from oceanx.backend.store import RequestStore
from oceanx.storage import OceanPaths, is_safe_cross_platform_relative_path


class DatasetSourceError(RuntimeError):
    """A DatasetArtifact has no safe, unambiguous local materialization."""


@dataclass(frozen=True)
class ResolvedDatasetSource:
    path: Path
    uri: str
    format: str


def resolve_dataset_source(
    *,
    store: RequestStore,
    paths: OceanPaths,
    workspace_id: str,
    ref: ArtifactRef,
) -> ResolvedDatasetSource:
    """Resolve either one immutable file or one immutable directory-backed store."""

    artifact = store.get_artifact(workspace_id=workspace_id, ref=ref)
    if artifact is None or artifact.artifact_type != "dataset":
        raise DatasetSourceError("dataset_ref must identify an available DatasetArtifact")

    declared_format = str(artifact.content.get("format", "")).strip().lower()
    materialization = str(artifact.content.get("materialization_level", "")).strip()
    if materialization == "local_reference":
        raw_path = artifact.content.get("source_path")
        if not isinstance(raw_path, str):
            relative_path = artifact.content.get("source_relative_path")
            workspace = store.workspace_snapshot(workspace_id)
            if not isinstance(relative_path, str) or not workspace.path:
                raise DatasetSourceError("Local DatasetArtifact has no resolvable source path")
            if not is_safe_cross_platform_relative_path(relative_path):
                raise DatasetSourceError("Local DatasetArtifact workspace path is unsafe")
            raw_path = str(Path(workspace.path).joinpath(*PurePosixPath(relative_path).parts))
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink():
            raise DatasetSourceError("Local DatasetArtifact path is not an absolute non-link path")
        try:
            source = candidate.resolve(strict=True)
        except OSError as exc:
            raise DatasetSourceError("Local DatasetArtifact source is unavailable") from exc
        source_kind = str(artifact.content.get("source_kind", "")).strip().lower()
        if source_kind == "file" and (not source.is_file() or source.is_symlink()):
            raise DatasetSourceError("Local DatasetArtifact source is not a regular file")
        if source_kind == "directory" and (not source.is_dir() or source.is_symlink()):
            raise DatasetSourceError("Local DatasetArtifact source is not a directory")
        if source_kind not in {"file", "directory"}:
            raise DatasetSourceError("Local DatasetArtifact has an invalid source kind")
        inferred_format = declared_format or (
            source.suffix.lower().lstrip(".") if source_kind == "file" else "directory"
        )
        if inferred_format in {"nc", "nc4", "cdf"}:
            inferred_format = "netcdf"
        return ResolvedDatasetSource(
            path=source,
            uri=f"local-reference://{ref.key}",
            format=inferred_format,
        )

    store_root = artifact.content.get("store_root")
    source_kind = str(artifact.content.get("source_kind", "")).strip().lower()
    if source_kind == "directory" or isinstance(store_root, str):
        if not isinstance(store_root, str):
            raise DatasetSourceError("Directory DatasetArtifact must declare store_root")
        if not is_safe_cross_platform_relative_path(store_root):
            raise DatasetSourceError("Directory DatasetArtifact store_root is unsafe")
        version_root = paths.resolve_uri(artifact.manifest_uri).parent
        relative = PurePosixPath(store_root)
        source = version_root.joinpath(*relative.parts)
        try:
            source.resolve(strict=True).relative_to(version_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise DatasetSourceError("Zarr store is unavailable or escapes its artifact") from exc
        if not source.is_dir() or source.is_symlink():
            raise DatasetSourceError("Dataset source is not a local immutable directory")
        source_root = source.resolve(strict=True)
        contained_files = 0
        for record in artifact.files:
            candidate = paths.resolve_uri(record.uri)
            try:
                candidate.resolve(strict=True).relative_to(source_root)
            except (OSError, ValueError):
                continue
            if not candidate.is_file() or candidate.is_symlink():
                raise DatasetSourceError("Dataset directory contains an unavailable store member")
            contained_files += 1
        if contained_files == 0:
            raise DatasetSourceError("Directory DatasetArtifact contains no immutable files")
        return ResolvedDatasetSource(
            path=source,
            uri=paths.uri_for(source),
            format=declared_format or "directory",
        )

    candidates = {item.uri for item in artifact.files}
    declared_sources: set[str] = set()
    for key in ("source_uri", "data_uri", "materialized_uri"):
        value = artifact.content.get(key)
        if isinstance(value, str):
            candidates.add(value)
            declared_sources.add(value)
    if len(declared_sources) == 1:
        selected = next(iter(declared_sources))
    elif len(candidates) == 1:
        selected = next(iter(candidates))
    else:
        raise DatasetSourceError("DatasetArtifact has no unique materialized local source URI")
    path = paths.resolve_uri(selected)
    if not path.is_file() or path.is_symlink():
        raise DatasetSourceError("Dataset source is not a regular local materialized file")
    inferred_format = declared_format or path.suffix.lower().lstrip(".") or "unknown"
    if inferred_format in {"nc", "nc4", "cdf"}:
        inferred_format = "netcdf"
    return ResolvedDatasetSource(path=path, uri=selected, format=inferred_format)


__all__ = [
    "DatasetSourceError",
    "ResolvedDatasetSource",
    "resolve_dataset_source",
]
