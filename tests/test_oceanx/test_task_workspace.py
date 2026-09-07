"""User-visible task folders remain isolated projections of canonical Ocean state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oceanx.backend.events import BackendClient
from oceanx.backend.host import OceanBackendHost
from oceanx.task_workspace import TASKS_DIRECTORY_NAME
from oceanx.team.models import ChildAuthority, WorkOrder


class _Recorder:
    def __init__(self) -> None:
        self.events = []

    async def send(self, event) -> None:
        self.events.append(event)

    def latest(self, event_type: str):
        return [event for event in self.events if event.type == event_type][-1]


async def _open_workspace(
    host: OceanBackendHost,
    workspace: Path,
) -> tuple[BackendClient, _Recorder, dict[str, str]]:
    recorder = _Recorder()
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=recorder.send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_task_folder_handshake",
            "type": "system.handshake",
            "payload": {
                "client_kind": "desktop",
                "client_version": "test",
                "supported_protocol_versions": [2],
            },
        },
    )
    context = {
        "client_id": str(client.client_id),
        "session_id": str(client.session_id),
        "workspace_id": "ws_task_folders",
    }
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_task_folder_workspace",
            "type": "workspace.open",
            "payload": {"path": str(workspace)},
            "context": context,
            "expected_workspace_revision": 0,
        },
    )
    return client, recorder, context


async def _create_task(
    host: OceanBackendHost,
    client: BackendClient,
    recorder: _Recorder,
    context: dict[str, str],
    *,
    request_id: str,
    title: str,
) -> str:
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": request_id,
            "type": "task.create",
            "payload": {"title": title},
            "context": context,
        },
    )
    return str(recorder.latest("request.completed").payload.result["task"]["task_id"])


@pytest.mark.asyncio
async def test_task_open_renders_before_result_manifests_are_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_fast_task_create",
            title="Fast task navigation",
        )
        result = host.task_results.put(
            workspace_id="ws_task_folders",
            task_id=task_id,
            kind="report",
            title="Deferred report",
            files={"report.md": b"# Deferred report\n"},
        )
        original_list_payloads = host.task_results.list_payloads
        calls: list[str] = []

        def observed_list_payloads(*, task_id: str):
            calls.append(task_id)
            return original_list_payloads(task_id=task_id)

        monkeypatch.setattr(host.task_results, "list_payloads", observed_list_payloads)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_fast_task_open",
                "type": "task.open",
                "payload": {"task_id": task_id},
                "context": context,
            },
        )

        snapshot = recorder.latest("task.snapshot")
        assert snapshot.payload.task_results == ()
        assert calls == []

        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_fast_task_results",
                "type": "task.output.list",
                "payload": {"task_id": task_id, "limit": 500},
                "context": context,
            },
        )

        assert calls == [task_id]
        listed = recorder.latest("request.completed").payload.result["task_results"]
        assert [item["result_ref"]["result_id"] for item in listed] == [
            result.ref.result_id
        ]
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_task_open_restores_each_visible_request_team_canvas(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_team_history_task_create",
            title="Persistent team history",
        )
        parent_request_id = "req_historical_team"
        host.store.append_task_transcript_item(
            task_id=task_id,
            item_id="tr_history_user",
            role="user",
            text="Inspect the historical team.",
            request_id=parent_request_id,
        )
        host.store.append_task_transcript_item(
            task_id=task_id,
            item_id="tr_history_answer",
            role="assistant",
            text="The historical conclusion is ready.",
            request_id=parent_request_id,
        )
        host.store.create_team_work_order(
            workspace_id="ws_task_folders",
            work_order=WorkOrder(
                work_order_id="work_historical_team",
                task_id=task_id,
                parent_request_id=parent_request_id,
                task_goal="Inspect the bounded dataset.",
                profile_id="data_reproducibility_expert",
                semantic_role="Data & Reproducibility Expert",
                authority=ChildAuthority.EXPERT,
                workspace_revision=host.store.workspace_snapshot("ws_task_folders").revision,
            ),
        )

        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_team_history_open",
                "type": "task.open",
                "payload": {"task_id": task_id},
                "context": context,
            },
        )

        failures = [event for event in recorder.events if event.type == "request.failed"]
        assert not failures, failures[-1].model_dump(mode="json") if failures else None
        snapshot = recorder.latest("task.snapshot")
        assert [team.request_id for team in snapshot.payload.team_snapshots] == [
            parent_request_id
        ]
        assert snapshot.payload.team_snapshots[0].agents[1].semantic_role == (
            "Data & Reproducibility Expert"
        )
    finally:
        await host.close()


def _task_root(workspace: Path, task_id: str) -> Path:
    matches = []
    for candidate in (workspace / TASKS_DIRECTORY_NAME).iterdir():
        manifest_path = candidate / "task-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["task"]["task_id"] == task_id:
            matches.append(candidate)
    assert len(matches) == 1
    return matches[0]


@pytest.mark.asyncio
async def test_task_directories_reuse_canonical_data_without_cross_task_mutation(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    source = workspace / "incoming" / "shared.nc"
    source.parent.mkdir(parents=True)
    source_bytes = b"CDF\x01shared scientific input"
    source.write_bytes(source_bytes)
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        first_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_task_folder_first",
            title="First SST analysis",
        )
        second_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_task_folder_second",
            title="Second SST analysis",
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_task_folder_import",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "incoming/shared.nc",
                    "materialization_level": "materialized_snapshot",
                    "materialization_acknowledged": True,
                    "title": "Shared ocean input",
                },
                "context": {**context, "task_id": first_id},
                "expected_workspace_revision": 1,
            },
        )
        ref = recorder.latest("artifact.created").payload.artifact.ref
        host.store.link_task_artifact(
            task_id=second_id,
            ref=ref,
            relation="supporting",
            origin_request_id="req_task_folder_reuse",
        )
        second_root = host.task_workspace_projector.sync(second_id)
        first_root = _task_root(workspace, first_id)
        first_data = next(path for path in (first_root / "data").glob("*.nc"))
        second_data = next(path for path in (second_root / "data").glob("*.nc"))
        artifact = host.store.get_artifact(workspace_id="ws_task_folders", ref=ref)
        canonical = host.paths.resolve_uri(
            next(file.uri for file in artifact.files if file.uri.endswith(".nc"))
        )

        assert first_data.read_bytes() == source_bytes
        assert second_data.read_bytes() == source_bytes
        assert first_data.stat().st_ino != canonical.stat().st_ino
        assert second_data.stat().st_ino != canonical.stat().st_ino

        first_data.write_bytes(b"user-edited task copy")
        note = first_root / "my-notes.txt"
        note.write_text("keep this", encoding="utf-8")
        host.task_workspace_projector.sync(first_id)

        assert first_data.read_bytes() == b"user-edited task copy"
        assert second_data.read_bytes() == source_bytes
        assert canonical.read_bytes() == source_bytes
        assert note.read_text(encoding="utf-8") == "keep this"
        manifest = json.loads((first_root / "task-manifest.json").read_text(encoding="utf-8"))
        projected = next(item for item in manifest["materialized_files"] if item["path"].endswith(".nc"))
        assert projected["status"] == "user_modified"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_task_directory_preserves_zarr_store_structure(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = workspace / "incoming" / "shared.zarr"
    (store / "sst").mkdir(parents=True)
    (store / ".zgroup").write_text('{"zarr_format":2}', encoding="utf-8")
    (store / "sst" / ".zarray").write_text('{"zarr_format":2}', encoding="utf-8")
    (store / "sst" / "0").write_bytes(b"chunk")
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_task_folder_zarr_task",
            title="Zarr task",
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_task_folder_zarr_import",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "incoming/shared.zarr",
                    "materialization_level": "materialized_snapshot",
                    "materialization_acknowledged": True,
                    "title": "Shared Zarr input",
                },
                "context": {**context, "task_id": task_id},
                "expected_workspace_revision": 1,
            },
        )
        task_root = _task_root(workspace, task_id)
        stores = list((task_root / "data").glob("*.zarr"))
        assert len(stores) == 1
        assert (stores[0] / ".zgroup").is_file()
        assert (stores[0] / "sst" / ".zarray").is_file()
        assert (stores[0] / "sst" / "0").read_bytes() == b"chunk"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_task_directory_is_stable_across_rename_and_legacy_open(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        task_id = await _create_task(
            host,
            client,
            recorder,
            context,
            request_id="req_task_folder_create",
            title="Original task title",
        )
        original_root = _task_root(workspace, task_id)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_task_folder_observation",
                "type": "artifact.create",
                "payload": {
                    "artifact_id": "observation_task_folder",
                    "artifact_type": "observation",
                    "title": "Task-folder evidence",
                    "content": {"statement": "A report should be visible in its Task."},
                },
                "context": {**context, "task_id": task_id},
                "expected_workspace_revision": 1,
            },
        )
        # Reports are no longer user-created protocol objects. The Expert that
        # performs a computation publishes the report and reproducibility package.
        assert (original_root / "report").is_dir()
        revision = host.store.get_research_task(task_id).task_revision
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_task_folder_rename",
                "type": "task.rename",
                "payload": {"task_id": task_id, "title": "Renamed scientific task"},
                "context": context,
                "expected_task_revision": revision,
            },
        )
        assert _task_root(workspace, task_id) == original_root
        assert "Renamed scientific task" in (original_root / "README.md").read_text(
            encoding="utf-8"
        )

        legacy = host.store.create_research_task(
            workspace_id="ws_task_folders",
            title="Legacy task",
        )
        assert not any(
            json.loads(path.read_text(encoding="utf-8"))["task"]["task_id"] == legacy.task_id
            for path in (workspace / TASKS_DIRECTORY_NAME).glob("*/task-manifest.json")
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_task_folder_legacy_open",
                "type": "task.open",
                "payload": {"task_id": legacy.task_id},
                "context": context,
            },
        )
        legacy_root = _task_root(workspace, legacy.task_id)
        assert (legacy_root / "analysis").is_dir()
        assert (legacy_root / "views").is_dir()
        assert (legacy_root / "report").is_dir()
        assert (legacy_root / "report").is_dir()
    finally:
        await host.close()
