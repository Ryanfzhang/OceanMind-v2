from __future__ import annotations

from ocean_partner.backend.store import RequestStore
from ocean_partner.research_learning import (
    ObservationKind,
    ObservationRelation,
    ResearchObservationDraft,
    SavedExperienceStatus,
    lesson_key,
    observations_from_expert_result,
)
from ocean_partner.task_results import TaskResultRef
from ocean_partner.team.models import (
    ChildAuthority,
    ExpertConclusion,
    ExpertOutput,
    ExpertResult,
    FindingBasis,
    WorkOrder,
    WorkStatus,
)


def test_expert_result_becomes_typed_task_learning_without_private_transcript() -> None:
    order = WorkOrder(
        work_order_id="work_learning_fixture",
        task_id="task_learning_fixture",
        job_key="job_learning_fixture",
        parent_request_id="req_learning_fixture",
        task_goal="Estimate a bounded section mean.",
        semantic_role="Ocean Process & Mechanism Expert",
        authority=ChildAuthority.EXPERT,
        workspace_revision=1,
    )
    result = ExpertResult(
        work_order_id=order.work_order_id,
        status=WorkStatus.COMPLETED,
        text="The bounded estimate is supported.",
        method_summary="Area-weight the valid grid cells before averaging.",
        checks_performed=("Verified units before aggregation.",),
        limitations=("The estimate covers one seasonal window.",),
        unresolved_questions=("Does the pattern persist interannually?",),
        confidence=0.85,
    )

    observations = observations_from_expert_result(
        workspace_id="ws_learning_fixture",
        task_id="task_learning_fixture",
        order=order,
        result=result,
    )

    assert {item.kind for item in observations} == {
        ObservationKind.METHOD,
        ObservationKind.QUALITY,
        ObservationKind.LIMITATION,
        ObservationKind.EVIDENCE_GAP,
    }
    reusable = [item for item in observations if item.reusable]
    assert {item.kind for item in reusable} == {
        ObservationKind.METHOD,
        ObservationKind.QUALITY,
    }
    assert all(item.lesson_key for item in reusable)


def test_conclusion_observation_keeps_its_supporting_view_reference() -> None:
    order = WorkOrder(
        work_order_id="work_learning_view",
        task_id="task_learning_view",
        job_key="job_learning_view",
        parent_request_id="req_learning_view",
        task_goal="Interpret the bounded section.",
        semantic_role="Ocean Process & Mechanism Expert",
        authority=ChildAuthority.EXPERT,
        workspace_revision=1,
    )
    result_ref = TaskResultRef(
        task_id="task_learning_view",
        result_id="result_section_view",
    )
    output = ExpertOutput(
        item_id="output_section_view",
        execution_id="exec_section_view",
        output_name="section.json",
        size_bytes=128,
        sha256="b" * 64,
        result_ref=result_ref,
        result_kind="interactive_view",
    )
    result = ExpertResult(
        work_order_id=order.work_order_id,
        status=WorkStatus.COMPLETED,
        text="The section interpretation is ready.",
        outputs=(output,),
        conclusions=(
            ExpertConclusion(
                conclusion_id="conclusion_section_view",
                statement="The section contains a shallow subsurface maximum.",
                basis=FindingBasis.OBSERVATION,
                output_ids=(output.item_id,),
                confidence=0.85,
            ),
        ),
    )

    observations = observations_from_expert_result(
        workspace_id="ws_learning_view",
        task_id="task_learning_view",
        order=order,
        result=result,
    )

    assert len(observations) == 1
    assert observations[0].kind is ObservationKind.DATA
    assert observations[0].evidence_refs == (result_ref.key,)
    assert observations[0].reusable is False


def test_completing_team_work_persists_observations_and_projects_research_state(
    tmp_path,
) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id = "ws_projection_fixture"
    task_id = "task_projection_fixture"
    try:
        with store._transaction() as connection:
            connection.execute(
                """
                INSERT INTO workspace_records (workspace_id, path, revision, updated_at)
                VALUES (?, ?, 1, ?)
                """,
                (workspace_id, str(tmp_path), "2026-08-22T00:00:00+00:00"),
            )
        store.create_research_task(
            workspace_id=workspace_id,
            title="Projection fixture",
            task_id=task_id,
        )
        order = WorkOrder(
            work_order_id="work_projection_fixture",
            task_id=task_id,
            job_key="job_projection_fixture",
            parent_request_id="req_projection_fixture",
            task_goal="Compute a bounded diagnostic.",
            semantic_role="Data & Reproducibility Expert",
            authority=ChildAuthority.EXPERT,
            workspace_revision=1,
        )
        store.create_team_work_order(workspace_id=workspace_id, work_order=order)
        store.complete_team_work(
            ExpertResult(
                work_order_id=order.work_order_id,
                status=WorkStatus.COMPLETED,
                text="The bounded diagnostic is complete.",
                method_summary="Use the declared coordinate units.",
                checks_performed=("Confirmed coordinate units.",),
                unresolved_questions=("A longer record is still unavailable.",),
                confidence=0.8,
            )
        )

        observations = store.list_research_observations(task_id=task_id)
        assert {item.kind for item in observations} == {
            ObservationKind.METHOD,
            ObservationKind.QUALITY,
            ObservationKind.EVIDENCE_GAP,
        }
        state = store.project_research_state(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        assert state.round_count == 1
        assert state.completed_rounds == 1
        assert state.status == "limited"
        assert state.evidence_gaps == ("A longer record is still unavailable.",)
    finally:
        store.close()


def test_research_observations_do_not_implicitly_create_skill_candidates(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id = "ws_learning_gate"
    statement = "Verify coordinate units before computing horizontal gradients."
    key = lesson_key(ObservationKind.QUALITY, statement)
    try:
        for index in range(3):
            task_id = f"task_learning_{index}"
            store.create_research_task(
                workspace_id=workspace_id,
                title=f"Learning task {index}",
                task_id=task_id,
            )
            store.record_research_observation(
                ResearchObservationDraft(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    request_id=f"req_learning_{index}",
                    kind=ObservationKind.QUALITY,
                    statement=statement,
                    outcome="supported",
                    confidence=0.9,
                    reusable=True,
                    lesson_key=key,
                )
            )

        assert store.list_experience_candidates(workspace_id=workspace_id) == ()
        assert store.list_saved_experiences(workspace_id=workspace_id) == ()
    finally:
        store.close()


def test_observation_linker_supersedes_weaker_unpublished_memory(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id = "ws_observation_linker"
    task_id = "task_observation_linker"
    try:
        store.create_research_task(
            workspace_id=workspace_id,
            title="Observation linker fixture",
            task_id=task_id,
        )
        common = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "request_id": "req_observation_linker",
            "work_order_id": "work_observation_linker",
            "kind": ObservationKind.RESULT,
            "statement": "The upper layer is warmer than the deep layer.",
            "outcome": "supported",
            "confidence": 0.8,
        }
        weaker = store.record_research_observation(
            ResearchObservationDraft(**common)
        )
        stronger = store.record_research_observation(
            ResearchObservationDraft(
                **common,
                evidence_refs=("task_observation_linker/result_profile@v1",),
            )
        )

        links = store.list_research_observation_links(task_id=task_id)
        assert len(links) == 1
        assert links[0].source_observation_id == stronger.observation_id
        assert links[0].target_observation_id == weaker.observation_id
        assert links[0].relation is ObservationRelation.SUPERSEDES
    finally:
        store.close()


def test_explicit_saved_experience_is_idempotent_and_keeps_server_identity(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id = "ws_learning_reject"
    try:
        task_id = "task_learning_explicit"
        store.create_research_task(
            workspace_id=workspace_id,
            title="Explicit learning",
            task_id=task_id,
        )
        kwargs = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "request_id": "req_learning_explicit",
            "work_order_id": "work_learning_explicit",
            "agent_id": "expert_learning_explicit",
            "agent_role": "ocean_process_expert",
            "text": " Preserve the observed mask before computing a regional mean. ",
        }
        first = store.save_experience(**kwargs)
        repeated = store.save_experience(**kwargs)
        assert repeated.experience_id == first.experience_id
        assert first.text == "Preserve the observed mask before computing a regional mean."
        assert first.status is SavedExperienceStatus.PENDING
        assert first.work_order_id == "work_learning_explicit"
        assert store.list_workspaces_with_pending_experiences() == (workspace_id,)
    finally:
        store.close()


def test_task_deletion_removes_only_pending_saved_experience(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    workspace_id = "ws_learning_delete"
    try:
        task_id = "task_delete_experience"
        store.create_research_task(
            workspace_id=workspace_id,
            title="Delete experience",
            task_id=task_id,
        )
        store.record_research_observation(
            ResearchObservationDraft(
                workspace_id=workspace_id,
                task_id=task_id,
                request_id="req_delete_experience",
                kind=ObservationKind.QUALITY,
                statement="One task-local observation.",
                outcome="supported",
                confidence=0.9,
            )
        )
        store.save_experience(
            workspace_id=workspace_id,
            task_id=task_id,
            request_id="req_delete_experience",
            agent_id="coordinator",
            agent_role="coordinator",
            text="One pending reusable lesson.",
        )

        store.delete_research_task(
            task_id=task_id,
            expected_task_revision=None,
        )

        assert store.list_research_observations(task_id=task_id) == ()
        assert store.list_saved_experiences(workspace_id=workspace_id) == ()
    finally:
        store.close()
