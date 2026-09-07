"""Contract tests for the transport-independent Ocean Protocol v2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from oceanx.artifacts.models import ArtifactType
from oceanx.desktop_contract import (
    DESKTOP_RUNTIME_CAPABILITY_SCHEMA,
    DESKTOP_RUNTIME_CONNECTIONS,
)
from oceanx.doctor import desktop_runtime_capabilities
from oceanx.protocol.v2 import canonical_request_fields, parse_event, parse_request
from oceanx.protocol.v2.models import (
    ArtifactResourceGrantPayload,
    DesktopRuntimeCapabilitiesPayload,
    InteractionRequestedPayload,
)
from oceanx.protocol.v2.schema import (
    event_schema,
    generate_typescript,
    request_schema,
    write_protocol_artifacts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "protocol" / "v2" / "fixtures"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted((FIXTURE_ROOT / "valid").glob("*.json")))
def test_valid_protocol_fixtures_are_accepted(path: Path):
    payload = _load(path)

    if "event_id" in payload:
        parsed = parse_event(payload)
    else:
        parsed = parse_request(payload)

    assert parsed.protocol_version == 2


@pytest.mark.parametrize("path", sorted((FIXTURE_ROOT / "invalid").glob("*.json")))
def test_invalid_request_fixtures_are_rejected(path: Path):
    with pytest.raises(ValidationError):
        parse_request(_load(path))


def test_canonical_request_identity_excludes_transport_session_and_client_ids():
    first = parse_request(_load(FIXTURE_ROOT / "valid" / "workspace-open.json"))
    second_payload = first.model_dump(mode="json")
    second_payload["context"]["session_id"] = "ses_reconnected"  # type: ignore[index]
    second_payload["context"]["client_id"] = "client_desktop_reconnected"  # type: ignore[index]
    second = parse_request(second_payload)

    assert canonical_request_fields(first) == canonical_request_fields(second)


def test_report_images_are_bounded_viewer_resources() -> None:
    payload = ArtifactResourceGrantPayload(
        artifact_ref={"artifact_id": "report_fixture", "version": 1},
        file_name="attachment_001.png",
        purpose="report_image",
    )
    assert payload.purpose == "report_image"


def test_session_submit_accepts_a_typed_literature_acquisition_preference() -> None:
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_literature_mode",
            "type": "session.submit",
            "payload": {
                "text": "Find related papers",
                "context_refs": [],
                "literature_acquisition_mode": "search_only",
            },
            "context": {
                "session_id": "session_literature",
                "workspace_id": "workspace_literature",
                "client_id": "desktop_literature",
                "task_id": "task_literature",
            },
            "expected_workspace_revision": 0,
        }
    )

    assert request.type == "session.submit"
    assert request.payload.literature_acquisition_mode == "search_only"


def test_paper_selection_interaction_has_a_typed_unique_shortlist() -> None:
    payload = InteractionRequestedPayload(
        interaction_id="int_paper_selection",
        kind="paper_selection",
        question="Choose papers for full-text review",
        tool_name="ocean_request_paper_selection",
        options=(
            {
                "paper_id": "paper_loop_current",
                "title": "Loop Current variability",
                "topic": "Upper-ocean heat transport",
                "citation": "Example et al. (2025)",
            },
        ),
    )

    assert payload.options[0].paper_id == "paper_loop_current"
    with pytest.raises(ValidationError):
        InteractionRequestedPayload(
            interaction_id="int_missing_papers",
            kind="paper_selection",
            question="Choose papers",
            options=(),
        )


def test_desktop_runtime_projection_matches_protocol_inventory() -> None:
    payload = DesktopRuntimeCapabilitiesPayload.model_validate(
        desktop_runtime_capabilities()
    )

    assert payload.schema_version == DESKTOP_RUNTIME_CAPABILITY_SCHEMA
    assert tuple((item.id, item.label) for item in payload.connections) == (
        DESKTOP_RUNTIME_CONNECTIONS
    )
    assert any(item.id == "web_search" for item in payload.connections)
    assert any(item.id == "jina_reader" for item in payload.connections)


def test_agent_transcript_request_is_task_scoped_and_read_only() -> None:
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_agent_history_read",
            "type": "task.agent_transcript.get",
            "payload": {
                "task_id": "task_history",
                "parent_request_id": "req_analysis",
                "agent_id": "job_temperature",
                "work_order_id": "work_temperature_round_1",
            },
            "context": {
                "session_id": "session_history",
                "workspace_id": "workspace_history",
                "client_id": "desktop_history",
                "task_id": "task_history",
            },
        }
    )

    assert request.type == "task.agent_transcript.get"
    assert request.payload.agent_id == "job_temperature"


def test_checked_in_schema_and_typescript_are_reproducible(tmp_path: Path):
    generated_schema = tmp_path / "schema"
    generated_types = tmp_path / "protocol-v2.ts"
    generated_request, generated_event, generated_type_path = write_protocol_artifacts(
        schema_directory=generated_schema,
        typescript_path=generated_types,
    )

    assert generated_request.read_text(encoding="utf-8") == (
        REPOSITORY_ROOT / "protocol" / "v2" / "schema" / "request-envelope.json"
    ).read_text(encoding="utf-8")
    assert generated_event.read_text(encoding="utf-8") == (
        REPOSITORY_ROOT / "protocol" / "v2" / "schema" / "event-envelope.json"
    ).read_text(encoding="utf-8")
    assert generated_type_path.read_text(encoding="utf-8") == generate_typescript()
    checked_in_types = (
        REPOSITORY_ROOT
        / "frontend"
        / "packages"
        / "ocean-client"
        / "src"
        / "generated"
        / "protocol-v2.ts"
    ).read_text(encoding="utf-8")
    assert checked_in_types == generate_typescript()
    assert "export type OceanRequest =" in checked_in_types
    assert '"type": string' not in checked_in_types


def test_protocol_has_only_current_user_facing_result_models():
    """Electron carries Protocol v2; it does not extend the scientific domain."""

    exported_contract = "\n".join(
        (
            json.dumps(request_schema(), sort_keys=True),
            json.dumps(event_schema(), sort_keys=True),
            generate_typescript(),
        )
    ).lower()
    artifact_types = set(get_args(ArtifactType))

    assert "electron" not in exported_contract
    assert "spatialanchor" not in exported_contract
    assert "spatial_anchor" not in exported_contract
    assert artifact_types == {
        "project_context",
        "paper",
        "claim",
        "observation",
        "hypothesis",
        "dataset",
        "dataset_diagnosis",
        "interactive_view",
        "experiment",
        "decision",
        "report",
    }


def test_team_snapshot_event_carries_real_topology_and_interaction_state():
    event = parse_event(
        {
            "protocol_version": 2,
            "event_id": "evt_team_snapshot",
            "session_id": "ses_1",
            "workspace_id": "ws_1",
            "task_id": "task_1",
            "request_id": "req_1",
            "sequence": 7,
            "timestamp": "2026-08-09T00:00:00Z",
            "type": "team.snapshot",
            "payload": {
                "revision": 3,
                "strategy": "parallel_team",
                "agents": [
                    {
                        "agent_id": "coordinator",
                        "semantic_role": "Coordinator",
                        "authority": "coordinator",
                        "status": "working",
                        "activity": "Coordinating Expert work",
                    },
                    {
                        "agent_id": "work_data",
                        "profile_id": "data_reproducibility_expert",
                        "semantic_role": "Data & Reproducibility Expert",
                        "authority": "expert",
                        "status": "working",
                        "activity": "Inspecting the assigned source",
                    },
                ],
                "dependencies": [
                    {
                        "from_agent_id": "coordinator",
                        "to_agent_id": "work_data",
                        "kind": "delegation",
                    }
                ],
                "interactions": [
                    {
                        "interaction_id": "interaction_data_3",
                        "from_agent_id": "coordinator",
                        "to_agent_id": "work_data",
                        "kind": "delegation",
                        "summary": "Inspecting the assigned source",
                        "state": "active",
                    }
                ],
            },
        }
    )

    assert event.type == "team.snapshot"
    assert event.payload.interactions[0].state == "active"
