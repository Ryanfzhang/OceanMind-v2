"""Tests for Phase 2 project-local path and portable-export policy."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ocean_partner.artifacts.files import ArtifactFileError, ArtifactFileStore
from ocean_partner.artifacts.models import ArtifactVersionDraft
from ocean_partner.backend.store import RequestStore
from ocean_partner.storage import (
    OceanPaths,
    StoragePolicyError,
    is_safe_cross_platform_relative_path,
    sanitize_portable_value,
    write_private_json,
)


def test_project_layout_is_private_and_uses_only_ocean_uris(tmp_path: Path):
    paths = OceanPaths.for_project(tmp_path).ensure()
    manifest = paths.artifacts / "report" / "report_fixture" / "v0001" / "manifest.json"
    write_private_json(manifest, {"artifact_id": "report_fixture"})

    assert paths.database == tmp_path / ".oceanmind" / "workspace.sqlite3"
    assert paths.uri_for(manifest) == "ocean://artifacts/report/report_fixture/v0001/manifest.json"
    assert paths.resolve_uri(paths.uri_for(manifest)) == manifest
    if os.name == "posix":
        assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


def test_uri_resolution_rejects_filesystem_and_parent_escapes(tmp_path: Path):
    paths = OceanPaths.for_project(tmp_path).ensure()

    with pytest.raises(StoragePolicyError):
        paths.resolve_uri("file:///etc/passwd")
    with pytest.raises(StoragePolicyError):
        paths.resolve_uri("ocean://artifacts/../private/secret.log")
    with pytest.raises(StoragePolicyError):
        paths.uri_for(tmp_path / "outside.txt")


@pytest.mark.parametrize(
    "path",
    (
        "artifacts/report/example/v0001/manifest.json:alternate-stream",
        "artifacts/report/example/v0001/NUL",
        "artifacts/report/example/v0001/aux.json",
        "artifacts/report/example/v0001/line. ",
        "artifacts/report/example/v0001/line.\x00json",
        "artifacts\\report\\example\\v0001\\manifest.json",
    ),
)
def test_cross_platform_relative_path_policy_rejects_windows_aliases(path: str):
    assert not is_safe_cross_platform_relative_path(path)


def test_uri_resolution_and_artifact_writes_reject_cross_platform_aliases(tmp_path: Path):
    paths = OceanPaths.for_project(tmp_path).ensure()
    files = ArtifactFileStore(paths)
    draft = ArtifactVersionDraft(
        workspace_id="ws_alias",
        artifact_id="alias_fixture",
        artifact_type="dataset",
        title="Alias fixture",
        created_by="tool",
        content={"schema_version": "ocean-dataset/v1", "materialization_level": "materialized_snapshot"},
    )

    with pytest.raises(StoragePolicyError):
        paths.resolve_uri("ocean://artifacts/dataset/alias_fixture/v0001/source.nc:alternate")
    with pytest.raises(StoragePolicyError):
        paths.resolve_uri("ocean://artifacts//dataset/alias_fixture/v0001/source.nc")
    with pytest.raises(ArtifactFileError, match="relative safe path"):
        files.stage(draft, version=1, files={"source.nc:alternate": b"CDF\x01"})


def test_portable_export_sanitizes_paths_and_rejects_credential_values(tmp_path: Path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    nested = home / "researcher" / "data.nc"

    sanitized = sanitize_portable_value(
        {"workspace_path": str(workspace / "inputs"), "home_path": str(nested)},
        workspace_root=workspace,
        home_root=home,
    )

    assert sanitized == {
        "workspace_path": "<workspace>/inputs",
        "home_path": "<home>/researcher/data.nc",
    }
    with pytest.raises(StoragePolicyError):
        sanitize_portable_value(
            {"diagnostic": "API_TOKEN=do-not-export"},
            workspace_root=workspace,
            home_root=home,
        )


def test_private_modes_cover_sqlite_sidecars_and_nested_artifact_directories(tmp_path: Path):
    paths = OceanPaths.for_project(tmp_path).ensure()
    store = RequestStore(paths.database)
    files = ArtifactFileStore(paths)
    staged = files.stage(
        ArtifactVersionDraft(
            workspace_id="ws_modes",
            artifact_id="dataset_modes",
            artifact_type="dataset",
            title="Nested private files",
            created_by="tool",
            content={"materialization_level": "materialized_snapshot", "format": "json"},
        ),
        version=1,
        files={"nested/data.json": b"{}"},
    )

    if os.name == "posix":
        assert stat.S_IMODE(paths.database.stat().st_mode) == 0o600
        assert stat.S_IMODE(staged.staging_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((staged.staging_directory / "nested").stat().st_mode) == 0o700
        assert stat.S_IMODE((staged.staging_directory / "nested" / "data.json").stat().st_mode) == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{paths.database}{suffix}")
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    store.close()
