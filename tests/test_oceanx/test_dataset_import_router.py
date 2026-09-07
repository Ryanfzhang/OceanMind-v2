"""Protocol v2 imports safe local files and directory-backed datasets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oceanx.backend.events import BackendClient
from oceanx.backend.host import OceanBackendHost
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.datasets import resolve_dataset_source


class _Recorder:
    def __init__(self) -> None:
        self.events = []

    async def send(self, event) -> None:
        self.events.append(event)

    def latest(self, event_type: str):
        return [event for event in self.events if event.type == event_type][-1]


async def _open_workspace(
    host: OceanBackendHost,
    workspace_path: Path,
    *,
    client_kind: str = "desktop",
) -> tuple[BackendClient, _Recorder, dict[str, str]]:
    recorder = _Recorder()
    client = BackendClient(
        transport="stdio",
        expected_client_kind=client_kind,
        sender=recorder.send,
    )
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_dataset_import_handshake",
            "type": "system.handshake",
            "payload": {
                "client_kind": client_kind,
                "client_version": "test",
                "supported_protocol_versions": [2],
            },
        },
    )
    context = {
        "client_id": str(client.client_id),
        "session_id": str(client.session_id),
        "workspace_id": "ws_dataset_import",
    }
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_dataset_import_workspace",
            "type": "workspace.open",
            "payload": {"path": str(workspace_path)},
            "context": context,
            "expected_workspace_revision": 0,
        },
    )
    return client, recorder, context


@pytest.mark.asyncio
async def test_dataset_import_registers_workspace_local_netcdf_as_read_only_reference(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "data" / "station.nc"
    source.parent.mkdir(parents=True)
    source_bytes = b"CDF\x01small trusted fixture"
    source.write_bytes(source_bytes)
    outside = tmp_path / "outside.nc"
    outside.write_bytes(b"CDF\x01outside")

    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task = host.store.create_research_task(
            workspace_id="ws_dataset_import", title="Import station NetCDF"
        )
        task_context = {**context, "task_id": task.task_id}
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_ok",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "data/station.nc",
                    "materialization_acknowledged": True,
                    "title": "Station NetCDF",
                },
                "context": task_context,
                "expected_workspace_revision": 1,
            },
        )

        created = recorder.latest("artifact.created")
        ref = created.payload.artifact.ref
        artifact = host.store.get_artifact(workspace_id="ws_dataset_import", ref=ref)
        assert artifact is not None
        assert artifact.artifact_type == "dataset"
        assert artifact.content == {
            "schema_version": "ocean-dataset/v2",
            "materialization_level": "local_reference",
            "source_relative_path": "data/station.nc",
            "source_scope": "workspace",
            "format": "netcdf",
            "source_kind": "file",
            "registered_fingerprint": {
                "size_bytes": len(source_bytes),
                "modified_ns": source.stat().st_mtime_ns,
            },
        }
        assert artifact.provenance == {
            "schema_version": "ocean-local-dataset-import/v2",
            "source_scope": "workspace",
            "source_relative_path": "data/station.nc",
            "materialization_acknowledged": True,
        }
        assert artifact.files == ()
        resolved = resolve_dataset_source(
            store=host.store,
            paths=host.paths,
            workspace_id="ws_dataset_import",
            ref=ref,
        )
        assert resolved.path == source.resolve()
        assert resolved.format == "netcdf"
        snapshot = host.store.task_snapshot(task_id=task.task_id)
        assert snapshot.outputs == ()
        assert len(snapshot.sources) == 1
        assert snapshot.sources[0]["artifact"]["ref"] == {
            "artifact_id": ref.artifact_id,
            "version": ref.version,
        }
        assert snapshot.sources[0]["relation"] == "source"
        assert snapshot.sources[0]["origin_request_id"] == "req_dataset_import_ok"

        submitted = host.router._submitted_text_with_context_refs(
            workspace_id="ws_dataset_import",
            task_id=task.task_id,
            text="What data do I have?",
            context_refs=(),
        )
        assert "What data do I have?" in submitted
        assert f"source_1: {ref.artifact_id}@v{ref.version}" in submitted
        assert "Station NetCDF" in submitted
        assert host.router._task_source_refs(
            workspace_id="ws_dataset_import",
            task_id=task.task_id,
        )[0].ref == ref.key
        task_roots = list((workspace / "OceanMind Tasks").iterdir())
        assert len(task_roots) == 1
        assert list((task_roots[0] / "data").glob("*.nc")) == []
        sources = (task_roots[0] / "data" / "sources.json").read_text(encoding="utf-8")
        assert '"materialization_level": "local_reference"' in sources
        assert '"source_relative_path": "data/station.nc"' in sources

        unrelated_task = host.store.create_research_task(
            workspace_id="ws_dataset_import", title="Unrelated task"
        )
        assert host.router._submitted_text_with_context_refs(
            workspace_id="ws_dataset_import",
            task_id=unrelated_task.task_id,
            text="What data do I have?",
            context_refs=(),
        ) == "What data do I have?"
        assert host.router._task_source_refs(
            workspace_id="ws_dataset_import",
            task_id=unrelated_task.task_id,
        ) == ()

        for request_id, relative_path in (
            ("req_dataset_import_escape", "../outside.nc"),
            ("req_dataset_import_absolute", str(outside)),
        ):
            await host.router.handle_payload(
                client,
                {
                    "protocol_version": 2,
                    "request_id": request_id,
                    "type": "dataset.import",
                    "payload": {
                        "relative_path": relative_path,
                        "materialization_acknowledged": True,
                    },
                    "context": context,
                    "expected_workspace_revision": 2,
                },
            )
            failed = recorder.latest("request.failed")
            assert failed.request_id == request_id
            assert failed.payload.error.code == "store_error"
            assert str(workspace) not in failed.payload.error.message

        symlink = workspace / "data" / "linked.nc"
        symlink.symlink_to(source)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_symlink",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "data/linked.nc",
                    "materialization_acknowledged": True,
                },
                "context": context,
                "expected_workspace_revision": 2,
            },
        )
        failed = recorder.latest("request.failed")
        assert failed.request_id == "req_dataset_import_symlink"
        assert failed.payload.error.code == "store_error"
        assert host.store.workspace_snapshot("ws_dataset_import").revision == 2
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_repeated_local_reference_import_reuses_one_artifact_across_tasks(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    source = workspace / "data" / "shared.nc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"CDF\x01immutable local fixture")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        first_task = host.store.create_research_task(
            workspace_id="ws_dataset_import", title="First task"
        )
        first_payload = {
            "protocol_version": 2,
            "request_id": "req_dataset_import_reuse_first",
            "type": "dataset.import",
            "payload": {"relative_path": "data/shared.nc", "title": "Shared data"},
            "context": {**context, "task_id": first_task.task_id},
            "expected_workspace_revision": 1,
        }
        await host.router.handle_payload(client, first_payload)
        first_ref = recorder.latest("artifact.created").payload.artifact.ref
        assert host.store.workspace_snapshot("ws_dataset_import").revision == 2

        await host.router.handle_payload(
            client,
            {
                **first_payload,
                "request_id": "req_dataset_import_reuse_same_task",
                "expected_workspace_revision": 2,
            },
        )
        same_task_result = recorder.latest("request.completed").payload.result
        assert same_task_result["reused"] is True
        assert same_task_result["artifact"]["ref"] == first_ref.model_dump(mode="json")
        assert host.store.workspace_snapshot("ws_dataset_import").revision == 2
        assert len(host.store.list_task_artifacts(task_id=first_task.task_id)) == 1

        second_task = host.store.create_research_task(
            workspace_id="ws_dataset_import", title="Second task"
        )
        await host.router.handle_payload(
            client,
            {
                **first_payload,
                "request_id": "req_dataset_import_reuse_second_task",
                "context": {**context, "task_id": second_task.task_id},
                "expected_workspace_revision": 2,
            },
        )
        second_task_result = recorder.latest("request.completed").payload.result
        assert second_task_result["reused"] is True
        assert second_task_result["artifact"]["ref"] == first_ref.model_dump(mode="json")
        assert [
            record.artifact.ref
            for record in host.store.list_task_artifacts(task_id=second_task.task_id)
        ] == [first_ref]
        assert len(
            host.store.list_artifact_summaries(
                "ws_dataset_import", artifact_type="dataset", limit=100
            )
        ) == 1
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_desktop_can_attach_an_external_dataset_without_copying_it(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "large-external.zarr"
    external.mkdir()
    (external / ".zgroup").write_text('{"zarr_format":2}', encoding="utf-8")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(
            host,
            workspace,
            client_kind="desktop",
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_external",
                "type": "dataset.import",
                "payload": {
                    "local_path": str(external),
                    "title": "External Zarr",
                },
                "context": context,
                "expected_workspace_revision": 1,
            },
        )
        ref = recorder.latest("artifact.created").payload.artifact.ref
        artifact = host.store.get_artifact(workspace_id="ws_dataset_import", ref=ref)
        assert artifact is not None
        assert artifact.files == ()
        assert artifact.content["source_path"] == str(external.resolve())
        assert artifact.content["source_scope"] == "desktop_authorized"
        assert resolve_dataset_source(
            store=host.store,
            paths=host.paths,
            workspace_id="ws_dataset_import",
            ref=ref,
        ).path == external.resolve()
        assert not any(path.name == ".zgroup" for path in host.paths.artifacts.rglob("*"))

        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_external_reuse",
                "type": "dataset.import",
                "payload": {
                    "local_path": str(external),
                    "title": "External Zarr",
                },
                "context": context,
                "expected_workspace_revision": 2,
            },
        )
        reused = recorder.latest("request.completed").payload.result
        assert reused["reused"] is True
        assert reused["artifact"]["ref"] == ref.model_dump(mode="json")
        assert host.store.workspace_snapshot("ws_dataset_import").revision == 2
        assert len(
            host.store.list_artifact_summaries(
                "ws_dataset_import", artifact_type="dataset", limit=100
            )
        ) == 1
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_dataset_import_references_a_zarr_store_without_enumerating_chunks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = workspace / "data" / "ocean.zarr"
    (store / "sst").mkdir(parents=True)
    (store / ".zgroup").write_text('{"zarr_format":2}', encoding="utf-8")
    (store / "sst" / ".zarray").write_text(
        '{"chunks":[2],"compressor":null,"dtype":"<f4","fill_value":null,'
        '"filters":null,"order":"C","shape":[2],"zarr_format":2}',
        encoding="utf-8",
    )
    (store / "sst" / "0").write_bytes(b"chunk")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_zarr",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "data/ocean.zarr",
                    "materialization_acknowledged": True,
                },
                "context": context,
                "expected_workspace_revision": 1,
            },
        )
        ref = recorder.latest("artifact.created").payload.artifact.ref
        artifact = host.store.get_artifact(workspace_id="ws_dataset_import", ref=ref)
        assert artifact is not None
        assert artifact.content["format"] == "zarr"
        assert artifact.content["source_kind"] == "directory"
        assert artifact.content["materialization_level"] == "local_reference"
        assert artifact.files == ()
        resolved = resolve_dataset_source(
            store=host.store,
            paths=host.paths,
            workspace_id="ws_dataset_import",
            ref=ref,
        )
        assert resolved.path == store.resolve()
        assert resolved.format == "zarr"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_dataset_import_accepts_an_unknown_local_file_without_format_whitelisting(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    source = workspace / "data" / "instrument.custom"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"vendor-specific scientific bytes")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_unknown",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "data/instrument.custom",
                    "materialization_acknowledged": True,
                },
                "context": context,
                "expected_workspace_revision": 1,
            },
        )
        ref = recorder.latest("artifact.created").payload.artifact.ref
        artifact = host.store.get_artifact(workspace_id="ws_dataset_import", ref=ref)
        assert artifact is not None
        assert artifact.content["format"] == "custom"
        assert artifact.content["source_kind"] == "file"
        assert artifact.files == ()
        assert resolve_dataset_source(
            store=host.store,
            paths=host.paths,
            workspace_id="ws_dataset_import",
            ref=ref,
        ).path == source.resolve()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_restart_reconciles_only_provable_legacy_task_source_ownership(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "data" / "legacy.nc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"CDF\x01legacy fixture")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    database = host.paths.database
    task_id: str | None = None
    ref = None
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task = host.store.create_research_task(
            workspace_id="ws_dataset_import", title="Legacy import ownership"
        )
        task_id = task.task_id
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_dataset_import_legacy",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "data/legacy.nc",
                    "materialization_acknowledged": True,
                },
                "context": {**context, "task_id": task.task_id},
                "expected_workspace_revision": 1,
            },
        )
        ref = recorder.latest("artifact.created").payload.artifact.ref
    finally:
        await host.close()

    assert task_id is not None and ref is not None
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "DELETE FROM task_artifact_links WHERE task_id = ? AND artifact_id = ?",
            (task_id, ref.artifact_id),
        )
        connection.execute(
            "UPDATE artifact_commit_intents SET task_id = NULL, task_relation = NULL "
            "WHERE origin_request_id = ?",
            ("req_dataset_import_legacy",),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 35")
        connection.commit()
    finally:
        connection.close()

    restarted = RequestStore(database)
    try:
        snapshot = restarted.task_snapshot(task_id=task_id)
        assert snapshot.outputs == ()
        assert len(snapshot.sources) == 1
        assert snapshot.sources[0]["artifact"]["ref"] == {
            "artifact_id": ref.artifact_id,
            "version": ref.version,
        }
        assert snapshot.sources[0]["origin_request_id"] == "req_dataset_import_legacy"
    finally:
        restarted.close()


def test_desktop_staged_dataset_paths_are_narrowly_scoped() -> None:
    OceanRequestRouter._assert_desktop_staged_dataset_path(
        ".oceanmind/staging/desktop-imports/"
        "desktop_import_0123456789abcdef0123456789abcdef/source/data.tif"
    )
    with pytest.raises(RequestStoreError, match="Desktop-staged"):
        OceanRequestRouter._assert_desktop_staged_dataset_path("data/not-staged.tif")


@pytest.mark.parametrize(
    ("resolver", "relative_path"),
    (
        ("_workspace_dataset_source", "data/station.nc:alternate"),
        ("_workspace_dataset_source", "data/NUL.nc"),
        ("_workspace_dataset_source", "data/station.nc "),
        ("_workspace_pdf_source", "papers/example.pdf:alternate"),
        ("_workspace_pdf_source", "papers/CON.pdf"),
        ("_workspace_pdf_source", "papers/example.pdf."),
    ),
)
def test_workspace_import_paths_reject_windows_aliases_before_resolution(
    tmp_path: Path,
    resolver: str,
    relative_path: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolve_source = getattr(OceanRequestRouter, resolver)

    with pytest.raises(RequestStoreError, match="safe workspace-relative path"):
        resolve_source(workspace_path=str(workspace), requested_path=relative_path)


def test_zarr_snapshot_rejects_symlinked_store_members(tmp_path: Path):
    store = tmp_path / "unsafe.zarr"
    store.mkdir()
    (store / ".zgroup").write_text('{"zarr_format":2}', encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (store / "linked-chunk").symlink_to(outside)

    with pytest.raises(RequestStoreError, match="link-like entry"):
        OceanRequestRouter._dataset_snapshot_files(source=store, source_kind="directory")
