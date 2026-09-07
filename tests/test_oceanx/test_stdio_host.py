"""JSONL adapter tests: stdout only emits framed Protocol v2 events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oceanx.backend.events import EventBus
from oceanx.backend.host import JsonlStdioAdapter, OceanBackendHost, PROTOCOL_PREFIX
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import RequestStore
from oceanx.protocol.v2.models import parse_request


@pytest.mark.asyncio
async def test_stdio_adapter_emits_only_protocol_prefixed_json_frames(tmp_path: Path):
    frames: list[str] = []

    def write_frame(frame: str) -> None:
        frames.append(frame)

    event_bus = EventBus()
    adapter = JsonlStdioAdapter(
        router=OceanRequestRouter(
            store=RequestStore(tmp_path / "state.sqlite3"),
            event_bus=event_bus,
        ),
        event_bus=event_bus,
        write_frame=write_frame,
    )
    await adapter.handle_line(
        json.dumps(
            {
                "protocol_version": 2,
                "request_id": "req_stdio_handshake",
                "type": "system.handshake",
                "payload": {
                    "client_kind": "desktop",
                    "client_version": "0.1.0",
                    "supported_protocol_versions": [2],
                },
            }
        )
    )
    assert adapter.client.client_id is not None
    assert adapter.client.session_id is not None
    await adapter.handle_line("not json")

    assert len(frames) == 2
    assert all(frame.startswith(PROTOCOL_PREFIX) and frame.endswith("\n") for frame in frames)
    events = [json.loads(frame.removeprefix(PROTOCOL_PREFIX)) for frame in frames]
    assert [event["type"] for event in events] == ["system.ready", "system.error"]
    assert all(event["protocol_version"] == 2 for event in events)
    assert all("OHJSON:" not in frame[len(PROTOCOL_PREFIX) :] for frame in frames)


@pytest.mark.asyncio
async def test_stdio_session_and_workspace_requests_have_single_terminal_event(tmp_path: Path):
    frames: list[str] = []
    event_bus = EventBus()
    adapter = JsonlStdioAdapter(
        router=OceanRequestRouter(
            store=RequestStore(tmp_path / "state.sqlite3"),
            event_bus=event_bus,
        ),
        event_bus=event_bus,
        write_frame=frames.append,
    )
    await adapter.handle_line(
        json.dumps(
            {
                "protocol_version": 2,
                "request_id": "req_stdio_ready",
                "type": "system.handshake",
                "payload": {
                    "client_kind": "desktop",
                    "client_version": "0.1.0",
                    "supported_protocol_versions": [2],
                },
            }
        )
    )
    context = {
        "session_id": adapter.client.session_id,
        "client_id": adapter.client.client_id,
        "workspace_id": "ws_stdio",
    }
    await adapter.handle_line(
        json.dumps(
            {
                "protocol_version": 2,
                "request_id": "req_stdio_workspace",
                "type": "workspace.open",
                "payload": {"path": "/tmp/stdio-workspace"},
                "context": context,
                "expected_workspace_revision": 0,
            }
        )
    )

    events = [json.loads(frame.removeprefix(PROTOCOL_PREFIX)) for frame in frames]
    request_events = [event for event in events if event.get("request_id") == "req_stdio_workspace"]
    assert [event["type"] for event in request_events] == [
        "request.accepted",
        "workspace.changed",
        "request.completed",
    ]
    assert sum(event["type"].startswith("request.") and event["type"] != "request.accepted" for event in request_events) == 1


@pytest.mark.asyncio
async def test_host_close_interrupts_active_requests_before_closing_its_store(tmp_path: Path):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_host_close_active",
            "type": "workspace.open",
            "payload": {"path": "/tmp/host-close"},
            "context": {
                "session_id": "ses_host_close",
                "client_id": "client_host_close",
                "workspace_id": "ws_host_close",
            },
        }
    )
    host.store.reserve(request, principal="user:local:desktop")
    host.store.mark_in_progress(request.request_id)

    await host.close()
    reopened = RequestStore(tmp_path / "state" / "workspace.sqlite3")
    record = reopened.get_request(request.request_id)
    reopened.close()

    assert record is not None
    assert record.state == "interrupted"
