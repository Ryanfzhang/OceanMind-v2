"""Artifact version-directory staging, atomic finalization, and checksum tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ocean_partner.artifacts.files import ArtifactFileError, ArtifactFileStore
from ocean_partner.artifacts.models import ArtifactVersionDraft
from ocean_partner.storage import OceanPaths


def _draft() -> ArtifactVersionDraft:
    return ArtifactVersionDraft(
        workspace_id="ws_files",
        artifact_id="dataset_fixture",
        artifact_type="dataset",
        title="Fixture dataset",
        created_by="tool",
        content={"materialization_level": "materialized_snapshot", "format": "json"},
    )


def test_stage_then_finalize_creates_an_immutable_checksum_verified_version(tmp_path: Path):
    paths = OceanPaths.for_project(tmp_path)
    files = ArtifactFileStore(paths)
    staged = files.stage(_draft(), version=1, files={"data.json": b'{"values":[1,2]}\n'})

    assert staged.staging_directory.is_dir()
    assert not staged.target_directory.exists()
    finalized = files.finalize(staged)

    manifest = files.verify_directory(finalized)
    assert finalized == staged.target_directory
    assert manifest.version == 1
    assert manifest.files[0].uri.endswith("/data.json")
    assert not staged.staging_directory.exists()


def test_target_collision_requires_the_same_immutable_manifest(tmp_path: Path):
    files = ArtifactFileStore(OceanPaths.for_project(tmp_path))
    first = files.stage(_draft(), version=1, files={"data.json": b"one"})
    files.finalize(first)
    second = files.stage(_draft(), version=1, files={"data.json": b"two"})

    with pytest.raises(ArtifactFileError, match="different artifact commit"):
        files.finalize(second)


def test_staging_rejects_path_traversal_and_symlink_sources(tmp_path: Path):
    files = ArtifactFileStore(OceanPaths.for_project(tmp_path))
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    link = tmp_path / "source-link.txt"
    link.symlink_to(source)

    with pytest.raises(ArtifactFileError, match="relative safe"):
        files.stage(_draft(), version=1, files={"../escape.txt": b"no"})
    with pytest.raises(ArtifactFileError, match="non-symlink"):
        files.stage(_draft(), version=1, files={"source.txt": link})
