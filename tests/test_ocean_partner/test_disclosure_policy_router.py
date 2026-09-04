"""Protocol v2 disclosure-policy mutations are explicit, durable, and replay-safe."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ocean_partner.backend.events import BackendClient
from ocean_partner.backend.host import OceanBackendHost


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
) -> tuple[BackendClient, _Recorder, dict[str, str]]:
    recorder = _Recorder()
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=recorder.send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_disclosure_handshake",
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
        "workspace_id": "ws_disclosure",
    }
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_disclosure_workspace",
            "type": "workspace.open",
            "payload": {"path": str(workspace_path)},
            "context": context,
            "expected_workspace_revision": 0,
        },
    )
    return client, recorder, context


@pytest.mark.asyncio
async def test_disclosure_policy_confirmation_is_versioned_visible_and_replay_safe(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_directory = tmp_path / "state"
    host = OceanBackendHost(state_directory, write_frame=lambda _frame: None)
    try:
        client, recorder, context = await _open_workspace(host, workspace)
        request = {
            "protocol_version": 2,
            "request_id": "req_disclosure_policy_v1",
            "type": "disclosure.policy.set",
            "payload": {
                "provider_id": "openai_codex",
                "metadata": "allow",
                "aggregate_statistics": "allow",
                "raw_bounded_sample": "deny",
                "document_text": "deny",
                "diagnostic_excerpt": "deny",
                "confirmed": True,
            },
            "context": context,
            "expected_workspace_revision": 1,
        }
        await host.router.handle_payload(client, request)

        updated = recorder.latest("disclosure.policy.updated")
        assert updated.payload.previous_revision == 1
        assert updated.payload.workspace_revision == 2
        assert updated.payload.policy.provider_id == "openai_codex"
        assert updated.payload.policy.policy_version == 1
        assert updated.payload.policy.raw_bounded_sample == "deny"
        assert updated.payload.policy.confirmed is True
        snapshot = host.store.workspace_snapshot("ws_disclosure")
        assert snapshot.revision == 2
        assert snapshot.disclosure_policy == updated.payload.policy.model_dump(mode="json")

        await host.router.handle_payload(client, request)
        assert host.store.workspace_snapshot("ws_disclosure").revision == 2
        assert recorder.latest("request.completed").request_id == "req_disclosure_policy_v1"

        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_disclosure_policy_get",
                "type": "disclosure.policy.get",
                "payload": {},
                "context": context,
            },
        )
        policy_result = recorder.latest("request.completed")
        assert policy_result.payload.result["policy"] == updated.payload.policy.model_dump(mode="json")
        assert [item["policy_version"] for item in policy_result.payload.result["versions"]] == [1]
        assert policy_result.payload.result["versions"][0]["confirmed_request_id"] == "req_disclosure_policy_v1"
        assert policy_result.payload.result["versions"][0]["confirmed"] is True
        assert host.store.workspace_snapshot("ws_disclosure").revision == 2

        v2 = {
            **request,
            "request_id": "req_disclosure_policy_v2",
            "payload": {**request["payload"], "raw_bounded_sample": "prompt"},
            "expected_workspace_revision": 2,
        }
        await host.router.handle_payload(client, v2)
        second_updated = recorder.latest("disclosure.policy.updated")
        assert second_updated.payload.policy.policy_version == 2
        assert second_updated.payload.policy.raw_bounded_sample == "prompt"
        assert host.store.workspace_snapshot("ws_disclosure").revision == 3
        history = host.store.list_disclosure_policy_versions(workspace_id="ws_disclosure")
        assert [item["policy_version"] for item in history] == [2, 1]
        assert [item["confirmed_request_id"] for item in history] == [
            "req_disclosure_policy_v2",
            "req_disclosure_policy_v1",
        ]

        stale = {
            **request,
            "request_id": "req_disclosure_policy_stale",
            "payload": {**request["payload"], "raw_bounded_sample": "allow"},
            "expected_workspace_revision": 2,
        }
        await host.router.handle_payload(client, stale)
        failed = recorder.latest("request.failed")
        assert failed.request_id == "req_disclosure_policy_stale"
        assert failed.payload.error.code == "workspace_revision_conflict"
        assert host.store.workspace_snapshot("ws_disclosure").revision == 3

        await host.close()
        host = OceanBackendHost(state_directory, write_frame=lambda _frame: None)
        restored = host.store.workspace_snapshot("ws_disclosure")
        assert restored.revision == 3
        assert restored.disclosure_policy is not None
        assert restored.disclosure_policy["policy_version"] == 2
        assert restored.disclosure_policy["raw_bounded_sample"] == "prompt"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_disclosure_policy_history_migration_backfills_an_existing_active_policy(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_directory = tmp_path / "state"
    host = OceanBackendHost(state_directory, write_frame=lambda _frame: None)
    try:
        client, _recorder, context = await _open_workspace(host, workspace)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_disclosure_migration_v1",
                "type": "disclosure.policy.set",
                "payload": {
                    "provider_id": "openai_codex",
                    "metadata": "allow",
                    "aggregate_statistics": "allow",
                    "raw_bounded_sample": "deny",
                    "document_text": "deny",
                    "diagnostic_excerpt": "deny",
                    "confirmed": True,
                },
                "context": context,
                "expected_workspace_revision": 1,
            },
        )
        await host.close()

        database = state_directory / "workspace.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE workspace_disclosure_policy_versions")
            connection.execute("DELETE FROM schema_migrations WHERE version = 9")

        host = OceanBackendHost(state_directory, write_frame=lambda _frame: None)
        history = host.store.list_disclosure_policy_versions(workspace_id="ws_disclosure")
        assert len(history) == 1
        assert history[0]["policy_version"] == 1
        assert history[0]["confirmed_request_id"] is None
        assert history[0]["confirmed"] is False
        restored = host.store.workspace_snapshot("ws_disclosure")
        assert restored.disclosure_policy is not None
        assert restored.disclosure_policy["confirmed"] is False
    finally:
        await host.close()
