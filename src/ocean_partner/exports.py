"""Explicit, audited portable exports for selected immutable Ocean artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ocean_partner.artifacts.files import ArtifactFileStore
from ocean_partner.artifacts.models import ArtifactRef, ArtifactVersion
from ocean_partner.backend.store import RequestStore
from ocean_partner.storage import OceanPaths, StoragePolicyError, ensure_private_file, sanitize_portable_value, write_private_json


class PortableExportError(RuntimeError):
    """A requested portable bundle cannot preserve evidence without leaking local state."""


@dataclass(frozen=True)
class PortableExport:
    """Locations and immutable references included in one completed export bundle."""

    bundle_directory: Path
    manifest_path: Path
    metadata_path: Path
    refs: tuple[ArtifactRef, ...]


class PortableExportService:
    """Copy verified immutable versions and filtered metadata into a private export bundle."""

    def __init__(self, *, paths: OceanPaths, store: RequestStore, files: ArtifactFileStore) -> None:
        self.paths = paths.ensure()
        self.store = store
        self.files = files

    def export(
        self,
        *,
        workspace_id: str,
        refs: Iterable[ArtifactRef] | None = None,
        workspace_root: Path | None = None,
        export_id: str | None = None,
    ) -> PortableExport:
        """Write a self-checking bundle or fail before any un-audited payload is published."""

        selected = self._select_refs(workspace_id=workspace_id, refs=refs)
        identifier = export_id or f"export_{uuid4().hex}"
        if not identifier.startswith("export_"):
            raise PortableExportError("Portable export IDs must begin with 'export_'")
        target = self.paths.exports / identifier
        staging = self.paths.exports / f".{identifier}.staging"
        if target.exists() or staging.exists():
            raise PortableExportError(f"Export bundle already exists: {identifier}")
        self.paths.ensure_private_subdirectory(staging)
        root_for_audit = (workspace_root or self._workspace_root(workspace_id)).resolve()
        try:
            exported: list[dict[str, Any]] = []
            metadata_artifacts: list[dict[str, Any]] = []
            metadata_links: list[dict[str, Any]] = []
            for ref in selected:
                artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
                projection = self.store.get_projection(workspace_id=workspace_id, ref=ref)
                if artifact is None or projection is None:
                    raise PortableExportError(f"Artifact no longer exists: {ref.key}")
                artifact_payload = artifact.model_dump(mode="json")
                audited_artifact = self._audit_immutable_payload(
                    artifact_payload,
                    workspace_root=root_for_audit,
                    label=ref.key,
                )
                source = self.paths.resolve_uri(artifact.manifest_uri).parent
                manifest = self.files.verify_directory(source)
                if manifest.manifest_sha256 != artifact.manifest_sha256:
                    raise PortableExportError(f"Database/manifest checksum mismatch for {ref.key}")
                self._reject_disallowed_artifact_files(artifact)
                destination = staging / self.paths.relative_path(source)
                self._copy_version_directory(source, destination)
                self.files.verify_directory(destination, expected_manifest=manifest)
                exported.append(
                    {
                        "ref": ref.model_dump(mode="json"),
                        "artifact_type": artifact.artifact_type,
                        "manifest_sha256": artifact.manifest_sha256,
                        "directory": self.paths.relative_path(source).as_posix(),
                    }
                )
                metadata_artifacts.append(
                    {
                        "ref": ref.model_dump(mode="json"),
                        "artifact": audited_artifact,
                        "projection": self._audit_metadata_payload(
                            projection.model_dump(mode="json"), workspace_root=root_for_audit
                        ),
                    }
                )
                metadata_links.extend(
                    self._audit_metadata_payload(link, workspace_root=root_for_audit)
                    for link in self.store.list_artifact_links(
                        workspace_id=workspace_id,
                        ref=ref,
                        direction="both",
                    )
                )

            snapshot = self.store.workspace_snapshot(workspace_id)
            metadata_path = staging / "metadata.sqlite3"
            self._write_metadata_extract(
                metadata_path,
                workspace=self._audit_metadata_payload(
                    snapshot.as_payload(), workspace_root=root_for_audit
                ),
                artifacts=metadata_artifacts,
                links=self._deduplicate(metadata_links),
            )
            inventory = self._checksum_inventory(staging)
            manifest_path = staging / "export-manifest.json"
            write_private_json(
                manifest_path,
                {
                    "schema_version": "ocean-portable-export/v1",
                    "export_id": identifier,
                    "workspace_id": workspace_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "artifacts": exported,
                    "metadata": "metadata.sqlite3",
                    "checksum_inventory": "checksums.json",
                    "raw_logs_included": False,
                    "credential_content_included": False,
                },
            )
            write_private_json(staging / "checksums.json", {"files": inventory})
            os.replace(staging, target)
            return PortableExport(
                bundle_directory=target,
                manifest_path=target / "export-manifest.json",
                metadata_path=target / "metadata.sqlite3",
                refs=tuple(selected),
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _select_refs(
        self,
        *,
        workspace_id: str,
        refs: Iterable[ArtifactRef] | None,
    ) -> list[ArtifactRef]:
        pending = list(refs) if refs is not None else [
            ArtifactRef.model_validate(item["ref"])
            for item in self.store.list_artifact_summaries(workspace_id, limit=500)
        ]
        selected: dict[tuple[str, int], ArtifactRef] = {}
        while pending:
            ref = pending.pop()
            key = (ref.artifact_id, ref.version)
            if key in selected:
                continue
            artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
            if artifact is None:
                raise PortableExportError(f"Artifact does not exist: {ref.key}")
            selected[key] = ref
            for link in self.store.list_artifact_links(
                workspace_id=workspace_id,
                ref=ref,
                direction="outgoing",
            ):
                if link["intrinsic"]:
                    pending.append(ArtifactRef.model_validate(link["target"]))
        return [selected[key] for key in sorted(selected)]

    def _workspace_root(self, workspace_id: str) -> Path:
        snapshot = self.store.workspace_snapshot(workspace_id)
        return Path(snapshot.path) if snapshot.path else self.paths.project_root

    def _audit_immutable_payload(
        self,
        payload: dict[str, Any],
        *,
        workspace_root: Path,
        label: str,
    ) -> dict[str, Any]:
        audited = self._audit_metadata_payload(payload, workspace_root=workspace_root)
        if audited != payload:
            raise PortableExportError(
                f"Immutable artifact {label} contains a local path that would require redaction"
            )
        return audited

    @staticmethod
    def _reject_disallowed_artifact_files(artifact: ArtifactVersion) -> None:
        for record in artifact.files:
            name = Path(record.uri).name.lower()
            if artifact.artifact_type == "paper" and record.mime_type == "application/pdf":
                raise PortableExportError(
                    "Local PaperArtifact PDF files are not portable by default; export citation metadata instead"
                )
            if name.endswith(".log") or name in {"stdout", "stderr"}:
                raise PortableExportError(
                    f"Artifact {artifact.ref.key} declares raw log content, which is not portable by default"
                )

    @staticmethod
    def _audit_metadata_payload(payload: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
        try:
            audited = sanitize_portable_value(payload, workspace_root=workspace_root)
        except StoragePolicyError as exc:
            raise PortableExportError(str(exc)) from exc
        if not isinstance(audited, dict):  # pragma: no cover - type guard for future callers
            raise PortableExportError("Portable metadata must remain an object")
        return audited

    def _copy_version_directory(self, source: Path, destination: Path) -> None:
        self.paths.ensure_private_subdirectory(destination)
        for item in sorted(source.rglob("*")):
            relative = item.relative_to(source)
            target = destination / relative
            if item.is_symlink():
                raise PortableExportError(f"Artifact version contains a symlink: {item}")
            if item.is_dir():
                self.paths.ensure_private_subdirectory(target)
                continue
            if not item.is_file():
                raise PortableExportError(f"Artifact version contains an unsupported entry: {item}")
            self.paths.ensure_private_subdirectory(target.parent)
            shutil.copyfile(item, target)
            ensure_private_file(target)

    @staticmethod
    def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_value = {json.dumps(item, ensure_ascii=True, sort_keys=True): item for item in items}
        return [by_value[key] for key in sorted(by_value)]

    @staticmethod
    def _write_metadata_extract(
        path: Path,
        *,
        workspace: dict[str, Any],
        artifacts: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> None:
        connection = sqlite3.connect(str(path))
        try:
            connection.executescript(
                """
                CREATE TABLE export_workspace (payload_json TEXT NOT NULL);
                CREATE TABLE export_artifacts (ref_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
                CREATE TABLE export_links (edge_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
                """
            )
            connection.execute(
                "INSERT INTO export_workspace (payload_json) VALUES (?)",
                (_canonical_json(workspace),),
            )
            for item in artifacts:
                ref = item["ref"]
                connection.execute(
                    "INSERT INTO export_artifacts (ref_key, payload_json) VALUES (?, ?)",
                    (f"{ref['artifact_id']}@v{int(ref['version']):04d}", _canonical_json(item)),
                )
            for item in links:
                edge_key = _canonical_json(item)
                connection.execute(
                    "INSERT INTO export_links (edge_key, payload_json) VALUES (?, ?)",
                    (hashlib.sha256(edge_key.encode("utf-8")).hexdigest(), edge_key),
                )
            connection.commit()
        finally:
            connection.close()
        ensure_private_file(path)

    @staticmethod
    def _checksum_inventory(directory: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(directory.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise PortableExportError(f"Export staging contains an unsupported entry: {path}")
            relative = path.relative_to(directory).as_posix()
            entries.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        return entries


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "PortableExport",
    "PortableExportError",
    "PortableExportService",
]
