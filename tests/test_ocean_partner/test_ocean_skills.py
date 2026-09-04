"""Capability-gated packaged Ocean research skill tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ocean_partner.agent_tools import ToolExecutionContext
from ocean_partner.backend.store import RequestStore
from ocean_partner.research_learning import SavedExperienceStatus
from ocean_partner.skills import (
    LITERATURE_CAPABILITY,
    OceanResourceUnavailableError,
    load_ocean_skill,
    ocean_reference_root,
    ocean_skill_dirs,
    ocean_skill_metadata,
    ocean_skill_prompt_section,
)
from ocean_partner.tools import OceanToolServices, create_ocean_expert_tool_registry


def test_core_ocean_skills_are_loaded_through_extra_skill_dirs_only():
    names = {skill.name for skill in ocean_skill_metadata()}

    assert {
        "ocean-question-and-scale-framing",
        "ocean-dataset-diagnosis",
        "ocean-analysis-design",
        "hypothesis-experiment-design",
        "ocean-physical-consistency-review",
        "ocean-map-and-figure-review",
        "claim-grounded-writing",
        "reproducibility-audit",
        "research-trajectory-planning",
        "paper-grounded-idea-framing",
    } <= names
    assert "paper-evidence-review" not in names
    content, _metadata = load_ocean_skill("ocean-analysis-design")
    assert "compute_" not in content


def test_literature_skill_remains_hidden_until_its_capability_is_enabled():
    default = {item.name for item in ocean_skill_metadata()}
    enabled = {item.name for item in ocean_skill_metadata(capabilities={LITERATURE_CAPABILITY})}

    assert "paper-evidence-review" not in default
    assert "paper-evidence-review" in enabled
    assert "paper-navigator" not in default
    assert "paper-navigator" in enabled
    assert len(ocean_skill_dirs()) == 1
    assert len(ocean_skill_dirs(capabilities={LITERATURE_CAPABILITY})) == 2


def test_packaged_references_are_available_on_demand_without_entering_skill_metadata():
    references = ocean_reference_root()

    assert (references / "data" / "cf-conventions.md").is_file()
    assert (references / "methods" / "transport.md").is_file()
    assert (references / "coding" / "matplotlib.md").is_file()
    assert all("references/" not in item.description for item in ocean_skill_metadata())


def test_skill_prompt_is_metadata_only_and_respects_the_literature_gate():
    prompt = ocean_skill_prompt_section(role="ocean_process_expert")
    enabled_prompt = ocean_skill_prompt_section(
        capabilities={LITERATURE_CAPABILITY},
        role="literature_reproduction_expert",
    )

    assert "ocean-analysis-design" in prompt
    assert "questions_to_resolve" not in prompt
    assert "paper-evidence-review" not in prompt
    assert "paper-evidence-review" in enabled_prompt
    assert "paper-navigator" not in prompt
    assert "paper-navigator" in enabled_prompt


def test_every_packaged_skill_declares_roles_and_role_gate_controls_loading():
    metadata = ocean_skill_metadata(capabilities={LITERATURE_CAPABILITY})

    assert all(item.roles for item in metadata)
    literature_names = {
        item.name
        for item in ocean_skill_metadata(
            capabilities={LITERATURE_CAPABILITY},
            role="literature_reproduction_expert",
        )
    }
    assert {"paper-navigator", "paper-evidence-review", "reproducibility-audit"} <= literature_names
    assert "ocean-dataset-diagnosis" not in literature_names

    content, _metadata = load_ocean_skill(
        "paper-evidence-review",
        capabilities={LITERATURE_CAPABILITY},
        role="literature_reproduction_expert",
    )
    assert "Choose reading depth from the question" in content
    with pytest.raises(OceanResourceUnavailableError):
        load_ocean_skill(
            "paper-evidence-review",
            capabilities={LITERATURE_CAPABILITY},
            role="data_reproducibility_expert",
        )


def test_agent_lists_and_loads_its_own_role_scoped_skill(tmp_path):
    usage_records: list[dict[str, object]] = []
    store = SimpleNamespace(
        record_resource_usage=lambda **record: usage_records.append(record)
    )
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws_agent_skills",
            provider_id="provider_fixture",
            store=store,
            skill_capabilities=(LITERATURE_CAPABILITY,),
            skill_role="literature_reproduction_expert",
            work_order_id="work_agent_skills",
        )
    )
    tools = {item.name: item for item in registry.list_tools()}

    listed = asyncio.run(
        tools["ocean_list_skills"].execute(
            tools["ocean_list_skills"].input_model(),
            ToolExecutionContext(cwd=tmp_path),
        )
    )
    listed_names = {item["name"] for item in json.loads(listed.output)["skills"]}
    assert {"paper-navigator", "paper-evidence-review"} <= listed_names
    assert "ocean-dataset-diagnosis" not in listed_names

    loaded = asyncio.run(
        tools["ocean_load_skill"].execute(
            tools["ocean_load_skill"].input_model(name="paper-evidence-review"),
            ToolExecutionContext(cwd=tmp_path),
        )
    )
    payload = json.loads(loaded.output)
    assert loaded.is_error is False
    assert payload["name"] == "paper-evidence-review"
    assert "Choose reading depth from the question" in payload["content"]
    assert usage_records[0]["resource_name"] == "paper-evidence-review"
    assert usage_records[0]["work_order_id"] == "work_agent_skills"


def test_agent_saves_short_experience_and_can_load_evolved_role_skill(tmp_path):
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id, task_id = "ws_evolved_skills", "task_evolved_skills"
    try:
        with store._transaction() as connection:
            connection.execute(
                """
                INSERT INTO workspace_records (workspace_id, path, revision, updated_at)
                VALUES (?, ?, 1, ?)
                """,
                (workspace_id, str(tmp_path), "2026-09-04T00:00:00+00:00"),
            )
        store.create_research_task(
            workspace_id=workspace_id,
            title="Evolved skills",
            task_id=task_id,
        )
        registry = create_ocean_expert_tool_registry(
            OceanToolServices(
                workspace_id=workspace_id,
                provider_id="provider_fixture",
                store=store,
                task_id=task_id,
                skill_role="ocean_process_expert",
                work_order_id="work_evolved_skills",
                expert_child_id="expert_evolved_skills",
                expert_result_origin_request_id="req_evolved_skills",
            )
        )
        tools = {item.name: item for item in registry.list_tools()}
        assert set(tools["ocean_save_experience"].input_model.model_fields) == {"text"}
        saved_result = asyncio.run(
            tools["ocean_save_experience"].execute(
                tools["ocean_save_experience"].input_model(
                    text="Preserve the physical water mask before spatial averaging."
                ),
                ToolExecutionContext(cwd=tmp_path),
            )
        )
        saved_payload = json.loads(saved_result.output)
        saved = store.list_saved_experiences(workspace_id=workspace_id)[0]
        assert saved_payload["experience_id"] == saved.experience_id
        assert saved.status is SavedExperienceStatus.PENDING
        assert saved.task_id == task_id
        assert saved.work_order_id == "work_evolved_skills"
        assert saved.agent_role == "ocean_process_expert"

        skill_markdown = """---
name: physical-mask-averaging
description: Preserve physical masks before spatial aggregation.
metadata:
  roles:
    - ocean_process_expert
---

# Physical mask averaging

Apply the observed water-domain mask before spatial aggregation.
"""
        store.install_evolved_skill_revision(
            workspace_id=workspace_id,
            skill_name="physical-mask-averaging",
            description="Preserve physical masks before spatial aggregation.",
            roles=("ocean_process_expert",),
            content=skill_markdown,
            source_experience_ids=(saved.experience_id,),
            reviewer_model="reviewer-fixture",
            review_reason="Reusable physical analysis safeguard.",
        )
        listed = asyncio.run(
            tools["ocean_list_skills"].execute(
                tools["ocean_list_skills"].input_model(),
                ToolExecutionContext(cwd=tmp_path),
            )
        )
        listed_payload = json.loads(listed.output)
        assert "physical-mask-averaging" in {
            item["name"] for item in listed_payload["skills"]
        }
        loaded = asyncio.run(
            tools["ocean_load_skill"].execute(
                tools["ocean_load_skill"].input_model(name="physical-mask-averaging"),
                ToolExecutionContext(cwd=tmp_path),
            )
        )
        assert json.loads(loaded.output)["content"] == skill_markdown.strip()
    finally:
        store.close()
