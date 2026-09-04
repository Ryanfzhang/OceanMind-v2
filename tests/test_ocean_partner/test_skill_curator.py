from __future__ import annotations

import asyncio
from pathlib import Path

from ocean_partner.backend.store import RequestStore
from ocean_partner.research_learning import SavedExperienceStatus
from ocean_partner.skill_curator import SkillCurator, SkillCuratorDecision
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

        async def reviewer(payload):
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
            item.status
            for item in store.list_saved_experiences(workspace_id=workspace_id)
        } == {SavedExperienceStatus.ABSORBED}
        assert asyncio.run(curator.review_workspace(workspace_id=workspace_id)) == ()
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

        async def reviewer(_payload):
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
