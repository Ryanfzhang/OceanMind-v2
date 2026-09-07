"""An attaching terminal must not guess revision zero for an existing workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oceanx.backend.events import BackendClient
from oceanx.backend.host import OceanBackendHost


class _Recorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def send(self, event: object) -> None:
        self.events.append(event)


async def _handshake_and_open(
    host: OceanBackendHost,
    *,
    request_prefix: str,
    workspace_path: Path,
    include_expected_revision: bool,
) -> tuple[_Recorder, BackendClient]:
    recorder = _Recorder()
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=recorder.send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": f"{request_prefix}_handshake",
            "type": "system.handshake",
            "payload": {
                "client_kind": "desktop",
                "client_version": "workspace-reopen-test",
                "supported_protocol_versions": [2],
            },
        },
    )
    assert client.client_id is not None
    assert client.session_id is not None
    request: dict[str, Any] = {
        "protocol_version": 2,
        "request_id": f"{request_prefix}_workspace",
        "type": "workspace.open",
        "payload": {"path": str(workspace_path)},
        "context": {
            "client_id": client.client_id,
            "session_id": client.session_id,
            "workspace_id": "ws_reopen",
        },
    }
    if include_expected_revision:
        request["expected_workspace_revision"] = 0
    await host.router.handle_payload(client, request)
    return recorder, client


@pytest.mark.asyncio
async def test_workspace_open_without_a_known_revision_attaches_to_existing_workspace(tmp_path: Path):
    state_dir = tmp_path / "state"
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    initial = OceanBackendHost(state_dir, write_frame=lambda _frame: None)
    try:
        _recorder, client = await _handshake_and_open(
            initial,
            request_prefix="req_initial",
            workspace_path=workspace_path,
            include_expected_revision=True,
        )
        assert initial.store.workspace_snapshot("ws_reopen").revision == 1
        await initial.event_bus.unregister(client)
    finally:
        await initial.close()

    reopened = OceanBackendHost(state_dir, write_frame=lambda _frame: None)
    try:
        recorder, client = await _handshake_and_open(
            reopened,
            request_prefix="req_reopen",
            workspace_path=workspace_path,
            include_expected_revision=False,
        )
        event_types = [getattr(event, "type", None) for event in recorder.events]
        assert "request.completed" in event_types
        assert "request.failed" not in event_types
        assert reopened.store.workspace_snapshot("ws_reopen").revision == 1
        await reopened.event_bus.unregister(client)
    finally:
        await reopened.close()
