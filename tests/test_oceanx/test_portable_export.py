"""Portable export verification for immutable artifacts and path/disclosure auditing."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from oceanx.artifacts.files import ArtifactFileStore
from oceanx.artifacts.models import ArtifactRef
from oceanx.backend.events import BackendClient
from oceanx.backend.host import OceanBackendHost
from oceanx.exports import PortableExportError, PortableExportService
from oceanx.storage import OceanPaths


class _Recorder:
    def __init__(self) -> None:
        self.events = []

    async def send(self, event) -> None:
        self.events.append(event)


async def _open_workspace(host: OceanBackendHost, *, workspace_path: Path) -> BackendClient:
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=_Recorder().send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_export_handshake",
            "type": "system.handshake",
            "payload": {
                "client_kind": "desktop",
                "client_version": "0.1.0",
                "supported_protocol_versions": [2],
            },
        },
    )
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_export_workspace",
            "type": "workspace.open",
            "payload": {"path": str(workspace_path)},
            "context": {
                "session_id": client.session_id,
                "client_id": client.client_id,
                "workspace_id": "ws_export",
            },
            "expected_workspace_revision": 0,
        },
    )
    return client


def _context(client: BackendClient) -> dict[str, str]:
    assert client.client_id is not None
    assert client.session_id is not None
    return {
        "client_id": client.client_id,
        "session_id": client.session_id,
        "workspace_id": "ws_export",
    }


@pytest.mark.asyncio
async def test_portable_export_copies_verified_versions_and_redacts_workspace_metadata(tmp_path: Path):
    workspace = tmp_path / "research"
    workspace.mkdir()
    paths = OceanPaths.for_project(workspace)
    host = OceanBackendHost(paths.root, write_frame=lambda _frame: None)
    client = await _open_workspace(host, workspace_path=workspace)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_export_artifact",
            "type": "artifact.create",
            "payload": {
                "artifact_id": "observation_export_fixture",
                "artifact_type": "observation",
                "title": "Exportable observation",
                "content": {"statement": "A portable fixture keeps its immutable manifest."},
            },
            "context": _context(client),
            "expected_workspace_revision": 1,
        },
    )
    result = PortableExportService(
        paths=host.paths,
        store=host.store,
        files=ArtifactFileStore(host.paths),
    ).export(
        workspace_id="ws_export",
        workspace_root=workspace,
        export_id="export_fixture",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    copied = result.bundle_directory / "artifacts" / "observation" / "observation_export_fixture" / "v0001"
    assert manifest["artifacts"][0]["ref"] == {
        "artifact_id": "observation_export_fixture",
        "version": 1,
    }
    assert copied.joinpath("manifest.json").is_file()
    assert result.metadata_path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(result.bundle_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(result.metadata_path.stat().st_mode) == 0o600

    connection = sqlite3.connect(result.metadata_path)
    try:
        workspace_payload = connection.execute("SELECT payload_json FROM export_workspace").fetchone()[0]
    finally:
        connection.close()
    assert "<workspace>" in workspace_payload
    assert str(workspace) not in workspace_payload
    await host.close()


@pytest.mark.asyncio
async def test_protocol_portable_export_is_exact_ref_only_and_does_not_return_paths(tmp_path: Path):
    workspace = tmp_path / "research"
    workspace.mkdir()
    paths = OceanPaths.for_project(workspace)
    host = OceanBackendHost(paths.root, write_frame=lambda _frame: None)
    recorder = _Recorder()
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=recorder.send)
    await host.event_bus.register(client)
    try:
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_protocol_export_handshake",
                "type": "system.handshake",
                "payload": {
                    "client_kind": "desktop",
                    "client_version": "0.1.0",
                    "supported_protocol_versions": [2],
                },
            },
        )
        context = {
            "session_id": client.session_id,
            "client_id": client.client_id,
            "workspace_id": "ws_protocol_export",
        }
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_protocol_export_workspace",
                "type": "workspace.open",
                "payload": {"path": str(workspace)},
                "context": context,
                "expected_workspace_revision": 0,
            },
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_protocol_export_artifact",
                "type": "artifact.create",
                "payload": {
                    "artifact_id": "observation_protocol_export",
                    "artifact_type": "observation",
                    "title": "Protocol exportable observation",
                    "content": {"statement": "Exact artifact evidence."},
                },
                "context": context,
                "expected_workspace_revision": 1,
            },
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_protocol_export_create",
                "type": "portable.export.create",
                "payload": {
                    "artifact_refs": [{"artifact_id": "observation_protocol_export", "version": 1}],
                },
                "context": context,
            },
        )
        result = [
            event.payload.result
            for event in recorder.events
            if event.request_id == "req_protocol_export_create" and event.type == "request.completed"
        ][0]
        assert result["format"] == "ocean-portable-export/v1"
        assert result["artifact_refs"] == [
            {"artifact_id": "observation_protocol_export", "version": 1}
        ]
        assert result["raw_logs_included"] is False
        assert result["local_paper_pdfs_included"] is False
        assert "path" not in result
        assert "uri" not in result
        assert (host.paths.exports / result["export_id"] / "export-manifest.json").is_file()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_portable_export_fails_closed_for_immutable_credential_content(tmp_path: Path):
    workspace = tmp_path / "research"
    workspace.mkdir()
    paths = OceanPaths.for_project(workspace)
    host = OceanBackendHost(paths.root, write_frame=lambda _frame: None)
    client = await _open_workspace(host, workspace_path=workspace)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_export_secret_artifact",
            "type": "artifact.create",
            "payload": {
                    "artifact_id": "project_context_secret_fixture",
                    "artifact_type": "project_context",
                "title": "Unsafe to export",
                "content": {"api_token": "must-never-enter-an-export"},
            },
            "context": _context(client),
            "expected_workspace_revision": 1,
        },
    )
    exporter = PortableExportService(
        paths=host.paths,
        store=host.store,
        files=ArtifactFileStore(host.paths),
    )

    with pytest.raises(PortableExportError, match="credential-like"):
        exporter.export(
            workspace_id="ws_export",
                refs=[ArtifactRef(artifact_id="project_context_secret_fixture", version=1)],
            workspace_root=workspace,
            export_id="export_secret",
        )
    assert not (host.paths.exports / "export_secret").exists()
    assert not (host.paths.exports / ".export_secret.staging").exists()
    await host.close()


@pytest.mark.asyncio
async def test_portable_export_keeps_local_paper_pdfs_private_by_default(tmp_path: Path):
    workspace = tmp_path / "research"
    source = workspace / "papers" / "local-paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4\nlocal paper bytes\n")
    paths = OceanPaths.for_project(workspace)
    host = OceanBackendHost(paths.root, write_frame=lambda _frame: None)
    try:
        client = await _open_workspace(host, workspace_path=workspace)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_export_paper_import",
                "type": "paper.import",
                "payload": {
                    "artifact_id": "paper_export_fixture",
                    "relative_path": "papers/local-paper.pdf",
                    "citation": {"title": "Private local paper"},
                    "materialization_acknowledged": True,
                },
                "context": _context(client),
                "expected_workspace_revision": 1,
            },
        )
        exporter = PortableExportService(
            paths=host.paths,
            store=host.store,
            files=ArtifactFileStore(host.paths),
        )

        with pytest.raises(PortableExportError, match="not portable"):
            exporter.export(
                workspace_id="ws_export",
                refs=[ArtifactRef(artifact_id="paper_export_fixture", version=1)],
                workspace_root=workspace,
                export_id="export_local_paper",
            )
        assert not (host.paths.exports / "export_local_paper").exists()
        assert not (host.paths.exports / ".export_local_paper.staging").exists()
    finally:
        await host.close()
