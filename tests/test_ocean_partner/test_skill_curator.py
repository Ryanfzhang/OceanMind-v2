from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from ocean_partner.backend.store import RequestStore
from ocean_partner.research_learning import SavedExperienceStatus
from ocean_partner.skill_curator import SkillCurator, SkillCuratorDecision
from ocean_partner.skills import LITERATURE_CAPABILITY, load_ocean_skill, ocean_skill_metadata
from ocean_partner.task_results import TaskResultStore


class _TaskWorkspaces:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_task_root(self, task_id: str) -> Path:
        root = self.root / task_id
        root.mkdir(parents=True, exist_ok=True)
        return root


def test_curator_turns_explicit_notes_into_one_complete_role_scoped_skill(
    tmp_path: Path,
) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    results = TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path / "tasks"))
    workspace_id = "ws_curator"
    try:
        saved_ids: list[str] = []
        for index in range(2):
            task_id = f"task_curator_{index}"
            store.create_research_task(
                workspace_id=workspace_id,
                title=f"Curator task {index}",
                task_id=task_id,
            )
            saved = store.save_experience(
                workspace_id=workspace_id,
                task_id=task_id,
                request_id=f"req_curator_{index}",
                work_order_id=f"work_curator_{index}",
                agent_id=f"expert_curator_{index}",
                agent_role="data_reproducibility_expert",
                text="Verify coordinate units before computing horizontal gradients.",
            )
            saved_ids.append(saved.experience_id)

        async def reviewer(payload, read_skill):
            assert all("content" not in item for item in payload["installed_skills"])
            assert "task_evidence" not in payload
            assert {item["experience_id"] for item in payload["saved_experiences"]} == set(
                saved_ids
            )
            return [
                SkillCuratorDecision(
                    decision="create",
                    experience_ids=tuple(saved_ids),
                    target_skill="coordinate-unit-check",
                    skill_markdown="""---
name: coordinate-unit-check
description: Verify coordinate units before derivative calculations.
metadata:
  roles:
    - data_reproducibility_expert
---

# Coordinate unit check

Before a horizontal derivative, verify coordinate units and convert angular coordinates to a
physical distance appropriate to the declared grid.
""",
                    reason="The notes describe the same reusable data-analysis safeguard.",
                    confidence=0.96,
                )
            ]

        curator = SkillCurator(store=store, task_results=results, reviewer=reviewer)
        created = asyncio.run(curator.review_workspace(workspace_id=workspace_id))

        assert len(created) == 1
        assert created[0].origin_request_id == "req_curator_1"
        assert created[0].content["role"] == "skill_update"
        assert created[0].content["skill_name"] == "coordinate-unit-check"
        revisions = store.list_evolved_skill_revisions(
            workspace_id=workspace_id,
            skill_name="coordinate-unit-check",
        )
        assert len(revisions) == 1
        assert revisions[0].roles == ("data_reproducibility_expert",)
        assert revisions[0].content.startswith("---")
        assert {
            item.status for item in store.list_saved_experiences(workspace_id=workspace_id)
        } == {SavedExperienceStatus.ABSORBED}
        assert asyncio.run(curator.review_workspace(workspace_id=workspace_id)) == ()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_curator_reads_selected_skill_and_notifies_after_commit(tmp_path, monkeypatch):
    import ocean_partner.skill_curator as module
    from ocean_partner.protocol.v2.models import parse_event

    store = RequestStore(tmp_path / "state.sqlite3")
    results = TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path / "tasks"))
    store.create_research_task(workspace_id="ws_curator", title="Review", task_id="task_review")
    note = store.save_experience(
        workspace_id="ws_curator",
        task_id="task_review",
        request_id="req_review",
        agent_id="coordinator",
        agent_role="coordinator",
        text="Preserve explicit axis ranges when reproducing scientific figures.",
    )
    metadata = ocean_skill_metadata(capabilities=(LITERATURE_CAPABILITY,), role=None)[0]
    body = load_ocean_skill(metadata.name, capabilities=(LITERATURE_CAPABILITY,))[0]
    updated = body + "\nPreserve explicit axis ranges when reproducing figures.\n"

    class Model:
        calls = 0

        def bind_tools(self, tools):
            assert tools[0].name == "read_skill"
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                payload = json.loads(messages[1].content)
                assert all("content" not in skill for skill in payload["installed_skills"])
                assert body not in messages[1].content
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_skill",
                            "args": {"name": metadata.name},
                            "id": "read_1",
                        }
                    ],
                )
            assert isinstance(messages[-1], ToolMessage)
            assert json.loads(messages[-1].content)["content"] == body
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "SkillCuratorDecisionBatch",
                        "id": "review_1",
                        "args": {
                            "decisions": [
                                {
                                    "decision": "update",
                                    "experience_ids": [note.experience_id],
                                    "target_skill": metadata.name,
                                    "skill_markdown": updated,
                                    "reason": "Preserve a reusable rendering safeguard.",
                                    "confidence": 0.9,
                                }
                            ]
                        },
                    }
                ],
            )

    model = Model()
    monkeypatch.setattr(
        module, "load_skill_reviewer_profile", lambda: SimpleNamespace(model="review-model")
    )
    monkeypatch.setattr(module, "create_chat_model", lambda profile: model)
    events = []

    async def emit(event):
        assert results.list(task_id="task_review")
        assert (
            store.list_evolved_skill_revisions(workspace_id="ws_curator")[0].content
            == updated.strip()
        )
        events.append(parse_event(event.model_dump(mode="json")))

    try:
        curator = SkillCurator(store=store, task_results=results, event_emitter=emit)
        created = await curator.review_workspace(workspace_id="ws_curator")
        assert model.calls == 2
        assert len(created) == len(events) == 1
        assert events[0].type == "task.results.changed"
        assert events[0].task_id == "task_review"
        assert events[0].payload.result_ids == (created[0].ref.result_id,)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_curator_cannot_update_unread_skill(tmp_path):
    store = RequestStore(tmp_path / "state.sqlite3")
    results = TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path / "tasks"))
    store.create_research_task(workspace_id="ws_review", title="Review", task_id="task_review")
    note = store.save_experience(
        workspace_id="ws_review",
        task_id="task_review",
        request_id="req_review",
        agent_id="coordinator",
        agent_role="coordinator",
        text="A useful lesson.",
    )
    metadata = ocean_skill_metadata(capabilities=(LITERATURE_CAPABILITY,))[0]
    body = load_ocean_skill(metadata.name, capabilities=(LITERATURE_CAPABILITY,))[0]

    async def reviewer(payload, read_skill):
        return [
            SkillCuratorDecision(
                decision="update",
                experience_ids=(note.experience_id,),
                target_skill=metadata.name,
                skill_markdown=body,
                reason="Update proposed without reading.",
                confidence=1,
            )
        ]

    try:
        assert (
            await SkillCurator(
                store=store, task_results=results, reviewer=reviewer
            ).review_workspace(workspace_id="ws_review")
            == ()
        )
        assert store.list_evolved_skill_revisions(workspace_id="ws_review") == ()
        assert (
            store.list_saved_experiences(workspace_id="ws_review")[0].status
            is SavedExperienceStatus.PENDING
        )
    finally:
        store.close()


def test_skill_install_rejects_stale_version_without_absorbing_notes(tmp_path):
    from ocean_partner.backend.store import RequestStoreError

    store = RequestStore(tmp_path / "state.sqlite3")
    store.create_research_task(workspace_id="ws_review", title="Review", task_id="task_review")
    notes = [
        store.save_experience(
            workspace_id="ws_review",
            task_id="task_review",
            request_id=f"req_review_{i}",
            agent_id="coordinator",
            agent_role="coordinator",
            text=f"Lesson {i}",
        )
        for i in range(2)
    ]
    args = {
        "workspace_id": "ws_review",
        "skill_name": "test-skill",
        "description": "A skill",
        "roles": ("coordinator",),
        "content": "skill body",
        "reviewer_model": "reviewer",
        "review_reason": "A lesson",
    }
    try:
        store.install_evolved_skill_revision(
            **args, source_experience_ids=(notes[0].experience_id,), expected_version=0
        )
        with pytest.raises(RequestStoreError, match="changed during review"):
            store.install_evolved_skill_revision(
                **args, source_experience_ids=(notes[1].experience_id,), expected_version=0
            )
        assert len(store.list_evolved_skill_revisions(workspace_id="ws_review")) == 1
        pending = store.list_saved_experiences(
            workspace_id="ws_review", status=SavedExperienceStatus.PENDING
        )
        assert [n.experience_id for n in pending] == [notes[1].experience_id]
    finally:
        store.close()


def test_curator_can_dismiss_or_leave_explicit_notes_pending(tmp_path: Path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    results = TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path / "tasks"))
    workspace_id, task_id = "ws_inbox", "task_inbox"
    try:
        store.create_research_task(workspace_id=workspace_id, title="Inbox", task_id=task_id)
        ignored = store.save_experience(
            workspace_id=workspace_id,
            task_id=task_id,
            request_id="req_inbox",
            agent_id="coordinator",
            agent_role="coordinator",
            text="This is ordinary progress from the current task.",
        )
        deferred = store.save_experience(
            workspace_id=workspace_id,
            task_id=task_id,
            request_id="req_inbox",
            agent_id="coordinator",
            agent_role="coordinator",
            text="A possibly useful user preference that still lacks enough context.",
        )

        async def reviewer(_payload, read_skill):
            return [
                SkillCuratorDecision(
                    decision="ignore",
                    experience_ids=(ignored.experience_id,),
                    reason="Routine task progress is not reusable guidance.",
                    confidence=0.99,
                ),
                SkillCuratorDecision(
                    decision="pending",
                    experience_ids=(deferred.experience_id,),
                    reason="Keep this note for a later semantic review.",
                    confidence=0.5,
                ),
            ]

        curator = SkillCurator(store=store, task_results=results, reviewer=reviewer)
        assert asyncio.run(curator.review_workspace(workspace_id=workspace_id)) == ()
        statuses = {
            item.experience_id: item.status
            for item in store.list_saved_experiences(workspace_id=workspace_id)
        }
        assert statuses[ignored.experience_id] is SavedExperienceStatus.DISMISSED
        assert statuses[deferred.experience_id] is SavedExperienceStatus.PENDING
        assert store.list_workspaces_with_pending_experiences() == ()
        store.save_experience(
            workspace_id=workspace_id,
            task_id=task_id,
            request_id="req_inbox_2",
            agent_id="coordinator",
            agent_role="coordinator",
            text="A new note reopens semantic review for this workspace.",
        )
        assert store.list_workspaces_with_pending_experiences() == (workspace_id,)
    finally:
        store.close()
