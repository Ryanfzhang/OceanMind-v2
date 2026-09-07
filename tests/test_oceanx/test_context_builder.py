"""Model-context selection tests for active refs, budgets, and disclosure auditing."""

from __future__ import annotations

from pathlib import Path

import pytest

from oceanx.artifacts.models import ArtifactRef
from oceanx.backend.events import BackendClient
from oceanx.backend.host import OceanBackendHost
from oceanx.context import ModelDataDisclosurePolicy, OceanContextBuilder
from oceanx.skills import (
    OceanResourceUnavailableError,
    load_ocean_reference,
    load_ocean_skill,
    record_ocean_resource_use,
)


class _Recorder:
    async def send(self, _event) -> None:
        return None


async def _open(host: OceanBackendHost) -> BackendClient:
    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=_Recorder().send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_context_handshake",
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
            "request_id": "req_context_workspace",
            "type": "workspace.open",
            "payload": {"path": "/tmp/context-workspace"},
            "context": {
                "session_id": client.session_id,
                "client_id": client.client_id,
                "workspace_id": "ws_context",
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
        "workspace_id": "ws_context",
    }


@pytest.mark.asyncio
async def test_context_builder_uses_active_refs_summaries_and_audits_metadata_only(tmp_path: Path):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    client = await _open(host)
    for request_id, artifact_id, revision in (
        ("req_context_first", "observation_context_first", 1),
        ("req_context_second", "observation_context_second", 2),
    ):
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": request_id,
                "type": "artifact.create",
                "payload": {
                    "artifact_id": artifact_id,
                    "artifact_type": "observation",
                    "title": f"{artifact_id} title",
                    "summary": "A compact summary useful to the current research turn.",
                    "content": {
                        "statement": "A full observation body must remain outside the model context summary."
                    },
                },
                "context": _context(client),
                "expected_workspace_revision": revision,
            },
        )
    active = ArtifactRef(artifact_id="observation_context_first", version=1)
    snapshot = host.store.set_active_ref(
        workspace_id="ws_context",
        slot="active_observation",
        ref=active,
        expected_workspace_revision=3,
    )
    assert snapshot.active_refs["active_observation"] == active

    builder = OceanContextBuilder(store=host.store)
    builder.set_policy(
        workspace_id="ws_context",
        policy=ModelDataDisclosurePolicy(provider_id="provider_fixture", policy_version=7),
    )
    result = builder.build(
        workspace_id="ws_context", provider_id="provider_fixture", token_budget=500
    )

    assert result.payload["active_artifacts"][0]["ref"] == active.model_dump(mode="json")
    assert "content" not in result.payload["active_artifacts"][0]
    assert "full observation body" not in str(result.payload)
    assert result.estimated_tokens <= 500
    routing_result = builder.build(
        workspace_id="ws_context",
        provider_id="provider_fixture",
        token_budget=500,
        routing_only=True,
    )
    routing_entry = routing_result.payload["active_artifacts"][0]
    assert set(routing_entry) == {"ref", "artifact_type", "title", "active_slots"}
    assert "summary" not in routing_entry
    assert "projection" not in routing_entry
    audits = host.store.list_disclosure_audits(workspace_id="ws_context")
    assert audits[0]["provider_id"] == "provider_fixture"
    assert audits[0]["policy_version"] == 7
    assert audits[0]["content_type"] == "metadata"
    assert audits[0]["disposition"] == "allow"
    skill_content, skill = load_ocean_skill("ocean-analysis-design")
    reference_content, reference_version = load_ocean_reference("methods/anomaly.md")
    usage_id = record_ocean_resource_use(
        host.store,
        workspace_id="ws_context",
        resource_kind="skill",
        resource_name=skill.name,
        resource_version=skill.version,
        work_order_id="work_context_fixture",
    )
    assert "pre-run validation assertions" in skill_content
    assert "reference state" in reference_content
    assert reference_version.startswith("sha256:")
    usage = host.store.list_resource_usage(workspace_id="ws_context")[0]
    assert usage["usage_id"] == usage_id
    assert usage["work_order_id"] == "work_context_fixture"
    assert usage["resource_kind"] == "skill"
    assert usage["resource_name"] == "ocean-analysis-design"
    assert usage["resource_version"] == skill.version
    with pytest.raises(OceanResourceUnavailableError):
        load_ocean_skill("paper-evidence-review")
    changed_provider = builder.build(
        workspace_id="ws_context", provider_id="another_provider"
    )
    assert changed_provider.payload["disclosure_policy"] == {
        "provider_id": "another_provider",
        "policy_version": 8,
        "metadata": "allow",
        "raw_bounded_sample": "allow",
        "document_text": "allow",
        "diagnostic_excerpt": "allow",
    }
    await host.close()


@pytest.mark.asyncio
async def test_context_builder_creates_an_all_allow_default_without_confirmation(
    tmp_path: Path,
):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    await _open(host)
    builder = OceanContextBuilder(store=host.store)

    policy = builder.policy_for(
        workspace_id="ws_context", provider_id="provider_fixture"
    )

    assert policy.metadata == "allow"
    assert policy.aggregate_statistics == "allow"
    assert policy.raw_bounded_sample == "allow"
    assert policy.document_text == "allow"
    assert policy.diagnostic_excerpt == "allow"
    await host.close()


@pytest.mark.asyncio
async def test_task_context_never_serializes_artifacts_owned_only_by_another_task(
    tmp_path: Path,
):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    client = await _open(host)
    old_task = host.store.create_research_task(
        workspace_id="ws_context",
        title="Old task",
        task_id="task_context_old",
    )
    current_task = host.store.create_research_task(
        workspace_id="ws_context",
        title="Current task",
        task_id="task_context_current",
    )
    revision = host.store.workspace_snapshot("ws_context").revision
    for request_id, artifact_id, artifact_type, title, summary, content in (
        (
            "req_context_old",
            "observation_old_task_only",
            "observation",
            "Old task result",
            "This result must never enter another task's model context.",
            {"statement": "A result owned exclusively by the old task."},
        ),
        (
            "req_context_current",
            "dataset_current_task_only",
            "dataset",
            "Current task source",
            "The only artifact visible to the current task.",
            {"format": "fixture"},
        ),
    ):
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": request_id,
                "type": "artifact.create",
                "payload": {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "title": title,
                    "summary": summary,
                    "content": content,
                },
                "context": _context(client),
                "expected_workspace_revision": revision,
            },
        )
        revision = host.store.workspace_snapshot("ws_context").revision
    old_ref = ArtifactRef(artifact_id="observation_old_task_only", version=1)
    current_ref = ArtifactRef(artifact_id="dataset_current_task_only", version=1)
    host.store.link_task_artifact(
        task_id=old_task.task_id,
        ref=old_ref,
        relation="delivery",
        origin_request_id="req_context_old",
    )
    host.store.link_task_artifact(
        task_id=current_task.task_id,
        ref=current_ref,
        relation="source",
        origin_request_id="req_context_current",
    )

    result = OceanContextBuilder(store=host.store).build(
        workspace_id="ws_context",
        provider_id="provider_fixture",
        task_id=current_task.task_id,
        token_budget=500,
        routing_only=True,
    )

    assert result.payload["workspace"]["access_scope"] == "task"
    assert result.payload["workspace"]["task_id"] == current_task.task_id
    assert result.payload["workspace"]["artifact_count"] == 1
    serialized = str(result.payload)
    assert current_ref.artifact_id in serialized
    assert old_ref.artifact_id not in serialized
    assert old_ref not in result.excluded_refs
    await host.close()


@pytest.mark.asyncio
async def test_context_builder_honors_a_metadata_denial_without_serializing_artifacts(
    tmp_path: Path,
):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    client = await _open(host)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": "req_context_denied_artifact",
            "type": "artifact.create",
            "payload": {
                "artifact_id": "observation_context_denied",
                "artifact_type": "observation",
                "title": "Denied context",
                "summary": "Must not enter the provider context.",
                "content": {"statement": "The denied observation body stays local."},
            },
            "context": _context(client),
            "expected_workspace_revision": 1,
        },
    )
    builder = OceanContextBuilder(store=host.store)
    builder.set_policy(
        workspace_id="ws_context",
        policy=ModelDataDisclosurePolicy(
            provider_id="provider_fixture",
            policy_version=2,
            metadata="deny",
        ),
    )

    result = builder.build(workspace_id="ws_context", provider_id="provider_fixture")
    assert result.payload["active_artifacts"] == []
    assert result.payload["recent_artifacts"] == []
    assert result.excluded_refs == (
        ArtifactRef(artifact_id="observation_context_denied", version=1),
    )
    assert host.store.list_disclosure_audits(workspace_id="ws_context")[0]["disposition"] == "deny"
    await host.close()
