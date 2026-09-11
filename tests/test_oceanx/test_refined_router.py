"""Offline integration of question-led follow-ups and scientific result transport."""

import json

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.events import EventBus
from oceanx.backend.host import OceanBackendHost
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import RequestStore
from oceanx.exploration import ExplorationInput, exploration_action
from oceanx.protocol.v2.models import parse_request
from oceanx.team.models import (
    CoordinatorResult,
    EvidenceRef,
    ExpertReport,
    ExpertResult,
    ExpertResultOrigin,
    WorkStatus,
)
from oceanx.tools import OceanAssignmentInput, OceanTodoInput
from tests.test_oceanx.test_current_team_contract import _open_test_workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_continuation_reuses_referenced_round_scope_sources_and_standard(tmp_path, recovered):
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        await _open_test_workspace(host, workspace_id="ws_refined", path=tmp_path)
        task = host.store.create_research_task(workspace_id="ws_refined", title="Refined")
        request = parse_request(
            {
                "protocol_version": 2,
                "request_id": "req_refined",
                "type": "session.submit",
                "payload": {"text": "Investigate the relationship."},
                "context": {
                    "workspace_id": "ws_refined",
                    "task_id": task.task_id,
                    "session_id": "session_refined",
                    "client_id": "client_refined",
                },
                "expected_workspace_revision": 1,
                "expected_task_revision": 0,
            }
        )
        host.store.reserve(request, principal="user:local:desktop")
        host.store.mark_in_progress(request.request_id)
        sources = (
            EvidenceRef(kind="dataset", ref="selected_source", locator="source_1"),
            EvidenceRef(kind="dataset", ref="unselected_source", locator="source_2"),
        )
        host.router._task_source_refs = lambda **_: sources
        orders = []

        class Recorder:
            async def close(self):
                pass

            async def execute_plan(self, **kwargs):
                results = []
                for order in kwargs["plan"].work_orders:
                    orders.append(order)
                    host.store.create_team_work_order(workspace_id="ws_refined", work_order=order)
                    result = ExpertResult(
                        work_order_id=order.work_order_id,
                        status=WorkStatus.COMPLETED,
                        report=ExpertReport.from_response("The result supports an association."),
                        result_origin=(
                            ExpertResultOrigin.BACKEND_RECOVERED
                            if recovered
                            else ExpertResultOrigin.AGENT_SUBMITTED
                        ),
                    )
                    host.store.complete_team_work(result)
                    results.append(result)
                return tuple(results)

        host.router.team_orchestrator = Recorder()

        async def dispatch(index, *, todo_id=None, **fields):
            todo = OceanTodoInput(
                todo_id=todo_id or f"question_{index}",
                question=f"Question {index}",
                why_this_expert="Domain interpretation",
                profile_id="ocean_process_expert",
                expert_key="analyst",
                expected_outputs=("answer",),
                **fields,
            )
            payload = OceanAssignmentInput(
                plan_goal="Investigate the relationship.",
                todos=(todo,),
                dispatch=(todo.todo_id,),
            ).model_dump(mode="json", exclude_unset=True)
            return await host.router._execute_team_plan(
                workspace_id="ws_refined",
                workspace_path=tmp_path,
                provider_id="fixture",
                task_id=task.task_id,
                payload=payload,
                context=ToolExecutionContext(
                    cwd=tmp_path, request_id=request.request_id, operation_id=f"operation_{index}"
                ),
            )

        await dispatch(
            1,
            source_handles=("source_1",),
            constraints=("Do not claim causation.",),
            context="Scientific background. " * 650,
            target_node="H1",
            alternative_nodes=("H2",),
            answer_standard={
                "sufficient_level": "association",
                "sufficient_if": "Resolve seasonal confounding.",
            },
            required_outputs=({"id": "figure", "requirement": "One figure."},),
            suggested_path=("A seasonal comparison may help.",),
        )
        await dispatch(2, target_node="H3", answer_standard={"sufficient_if": "Describe coverage."})
        value = await dispatch(
            3,
            todo_id="question_2",
            continuation={
                "source_report_ref": orders[0].work_order_id,
                "approved_lead_id": "lead_1",
            },
        )
        first, second, third = orders
        assert first.job_key == second.job_key == third.job_key
        assert [order.session_round for order in orders] == [1, 2, 3]
        assert third.mode == "continue"
        assert third.work_order_id != second.work_order_id
        assert third.task_goal == "Question 3"
        assert third.question_ref == first.question_ref
        assert third.input_refs == first.input_refs == sources[:1]
        assert second.input_refs == sources
        assert third.constraints == first.constraints
        assert third.answer_standard == first.answer_standard
        assert third.required_outputs == first.required_outputs
        assert third.target_node == "H1" and third.alternative_nodes == ("H2",)
        assert len(third.context_summary) <= 16_000
        assert "Question 1" in third.context_summary
        assert "report" in value["expert_results"][0]
        assert "text" not in value["expert_results"][0]
    finally:
        await host.close()


@pytest.mark.parametrize(
    "decision,expected",
    [
        ("answered", "answered"),
        ("unable_to_answer", "insufficient_evidence"),
    ],
)
def test_explicit_scientific_outcome_survives_receipt_without_review_gate(
    tmp_path, decision, expected
):
    store = RequestStore(tmp_path / "state.sqlite3")
    try:
        task = store.create_research_task(workspace_id="ws", title="Research")
        router = OceanRequestRouter(store=store, event_bus=EventBus())

        def call(action, **kwargs):
            revision = exploration_action(
                store, "ws", task.task_id, ExplorationInput(action="read")
            )["revision"]
            return exploration_action(
                store,
                "ws",
                task.task_id,
                ExplorationInput(action=action, expected_revision=revision, **kwargs),
                request_id="req_now",
            )

        call("start", goal="Does a relationship exist?", mode="iterative")
        call(
            "propose",
            candidates=[
                {
                    "id": "H1",
                    "claim": "A relationship exists.",
                    "relation_to_children": "alternatives",
                }
            ],
        )
        call(
            "adjudicate",
            node_id="H1",
            status="supported",
            summary="An association is supported, not a mechanism.",
        )
        call("pause", decision=decision, completion_summary="Answer at the requested scope.")
        assert router._explicit_research_outcome("ws", task.task_id, "req_now") == expected
        assert router._explicit_research_outcome("ws", task.task_id, "req_later") is None
        result = CoordinatorResult(answer_markdown="A bounded answer.", research_outcome=expected)
        assert (
            CoordinatorResult.model_validate_json(result.model_dump_json()).research_outcome
            == expected
        )
        assert json.loads(result.model_dump_json())["decision"] == "answered"
        request = parse_request(
            {
                "protocol_version": 2,
                "request_id": "req_now",
                "type": "session.submit",
                "payload": {"text": "Does a relationship exist?"},
                "context": {
                    "workspace_id": "ws",
                    "task_id": task.task_id,
                    "session_id": "session_refined",
                    "client_id": "client_refined",
                },
                "expected_workspace_revision": 1,
                "expected_task_revision": 0,
            }
        )
        store.reserve(request, principal="user:local:desktop")
        store.mark_in_progress(request.request_id)
        store.record_coordinator_result(request_id=request.request_id, result=result)
        restored = store.get_coordinator_result(request.request_id)
        assert restored.research_outcome == expected
    finally:
        store.close()
