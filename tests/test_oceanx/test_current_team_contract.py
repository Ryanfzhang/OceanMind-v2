"""Current Coordinator -> Expert contract without legacy execution roles."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import xarray as xr
from pydantic import ValidationError

from oceanx.agent import OceanAgentBudget
from oceanx.agent_contract import (
    AssistantTurnComplete,
    ConversationMessage,
    ErrorEvent,
    TextBlock,
    UsageSnapshot,
)
from oceanx.agent_tools import ToolExecutionContext
from oceanx.artifacts.models import (
    ArtifactProjection,
    ArtifactVersion,
)
from oceanx.backend.auth import principal_for_client_kind
from oceanx.backend.events import BackendClient, EventBus
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import CodeExecutionRecord, RequestStore, TeamWorkRecord
from oceanx.expert_deliverables import ExpertDeliverableError, ExpertDeliverableService
from oceanx.expert_execution import ExpertCodeExecutionService
from oceanx.expert_recovery import (
    execution_result_fingerprint,
    write_execution_result_manifest,
)
from oceanx.protocol.v2.models import (
    ArtifactCreatedEvent,
    ArtifactCreatedPayload,
    ArtifactSummaryPayload,
    ArtifactVersionCreatedEvent,
    ProtocolErrorPayload,
    RequestCompletedEvent,
    RequestCompletedPayload,
    RequestFailedEvent,
    RequestFailedPayload,
    new_event_id,
    parse_request,
)
from oceanx.runtime import (
    OCEAN_EXPERT_WORKSTREAM_POLICY,
    build_ocean_runtime_composition,
)
from oceanx.task_results import TaskResultRef
from oceanx.team.models import (
    ChildAuthority,
    CoordinatorAnswerBasis,
    CoordinatorDecision,
    CoordinatorResult,
    CoordinatorTodo,
    EvidenceRef,
    ExpertConclusion,
    ExpertDecision,
    ExpertOutput,
    ExpertResult,
    ExpertResultOrigin,
    FindingBasis,
    ResultBundle,
    UsageRecord,
    WorkBudget,
    WorkFailureCode,
    WorkOrder,
    WorkPlan,
    WorkStatus,
    WorkstreamCheckpoint,
    WorkstreamPhase,
    expert_output_item_id,
)
from oceanx.team.orchestrator import (
    OceanTeamError,
    OceanTeamOrchestrator,
    _ParticipantBinding,
    _ParticipantRunResult,
    _ParticipantState,
    _ParticipantUsage,
    capabilities_for_authority,
)
from oceanx.team.profiles import (
    AGENT_PROFILES,
    agent_profile_prompt_section,
    bind_agent_profile,
    get_agent_profile,
    profile_system_prompt,
)
from oceanx.tools import (
    OceanAssignmentInput,
    OceanTodoInput,
    OceanToolServices,
    _FrameworkResultEvent,
    _result_materialization_key,
    create_ocean_discussion_tool_registry,
    create_ocean_expert_tool_registry,
    create_ocean_lead_tool_registry,
    create_ocean_tool_registry,
)


def _historical_artifact_event_factory(
    artifact: ArtifactVersion,
    projection: ArtifactProjection,
    _previous_revision: int,
    workspace_revision: int,
    event_id: str,
):
    """Build the journal event needed by legacy Artifact fixtures.

    Current Expert output tests must not reach back into ExpertDeliverableService:
    output registration is no longer one of that service's responsibilities.
    """

    summary = ArtifactSummaryPayload(
        ref=artifact.ref,
        artifact_type=artifact.artifact_type,
        title=artifact.title,
        summary=artifact.summary,
        projection=projection,
    )
    event_type = ArtifactCreatedEvent if artifact.ref.version == 1 else ArtifactVersionCreatedEvent
    return (
        event_type(
            protocol_version=2,
            event_id=event_id,
            session_id=None,
            workspace_id=artifact.workspace_id,
            request_id=None,
            sequence=0,
            timestamp=datetime.now(timezone.utc),
            type=("artifact.created" if artifact.ref.version == 1 else "artifact.version.created"),
            payload=ArtifactCreatedPayload(
                artifact=summary,
                manifest_uri=artifact.manifest_uri,
                workspace_revision=workspace_revision,
            ),
        ),
        None,
    )


def _order(order_id: str, profile_id: str = "data_reproducibility_expert") -> WorkOrder:
    profile = get_agent_profile(profile_id)
    return WorkOrder(
        work_order_id=order_id,
        todo_id=f"todo_{order_id}",
        parent_request_id="req_current_team",
        task_goal="Answer this bounded scientific question from the supplied sources.",
        profile_id=profile.profile_id,
        semantic_role=profile.display_name,
        authority=profile.authority,
        allowed_capabilities=tuple(sorted(capabilities_for_authority(profile.authority))),
        outcome_intents=("answer",),
        done_when="Return evidence, method, checks, limitations, and a decision.",
        workspace_revision=3,
    )


def _todo(order: WorkOrder) -> CoordinatorTodo:
    assert order.todo_id is not None
    assert order.profile_id is not None
    return CoordinatorTodo(
        todo_id=order.todo_id,
        question=order.task_goal,
        depends_on=order.depends_on,
        profile_id=order.profile_id,
        expected_outputs=order.outcome_intents,
    )


def test_retired_manual_metadata_does_not_block_restore_or_new_delegation(tmp_path):
    store = RequestStore(tmp_path / "legacy.sqlite3")
    try:
        with store._transaction() as db:
            db.execute(
                "INSERT INTO workspace_records (workspace_id, path, revision, updated_at) VALUES (?, ?, ?, ?)",
                ("ws_legacy", str(tmp_path), 3, datetime.now().astimezone().isoformat()),
            )
        task = store.create_research_task(workspace_id="ws_legacy", title="Legacy task")
        old = _order("work_legacy").model_copy(
            update={"task_id": task.task_id, "job_key": "job_legacy"}
        )
        store.create_team_work_order(workspace_id="ws_legacy", work_order=old)
        legacy = old.model_dump(mode="json")
        legacy["assigned_manuals"] = ["paper-navigator", "reproducibility-audit"]
        raw = json.dumps(legacy)
        with store._transaction() as db:
            db.execute(
                "UPDATE team_work_records SET work_order_json = ? WHERE work_order_id = ?",
                (raw, old.work_order_id),
            )
        store.close()
        store = RequestStore(tmp_path / "legacy.sqlite3")
        assert store.get_team_work(old.work_order_id).work_order == old
        assert (
            store.create_team_work_order(workspace_id="ws_legacy", work_order=old).work_order == old
        )
        assert store.task_snapshot(task_id=task.task_id).task.task_id == task.task_id
        new = old.model_copy(update={"work_order_id": "work_next", "session_round": 2})
        assert (
            store.create_team_work_order(workspace_id="ws_legacy", work_order=new).work_order == new
        )
        assert (
            store._connection.execute(
                "SELECT work_order_json FROM team_work_records WHERE work_order_id = ?",
                (old.work_order_id,),
            ).fetchone()[0]
            == raw
        )
        legacy["unknown_field"] = "still invalid"
        with pytest.raises(ValidationError, match="unknown_field"):
            WorkOrder.model_validate(store._readable_work_order_payload(legacy))
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["task.open", "artifact.create"])
async def test_task_validation_error_identifies_operation_model_and_field(
    tmp_path, monkeypatch, operation
):
    store = RequestStore(tmp_path / "errors.sqlite3")
    events = []

    async def send(event):
        events.append(event)

    client = BackendClient(
        transport="stdio",
        expected_client_kind="desktop",
        sender=send,
        client_id="client_validation",
        session_id="session_validation",
        principal=principal_for_client_kind("desktop"),
    )
    router = OceanRequestRouter(store=store, event_bus=EventBus())

    async def invalid_dispatch(_client, _request):
        WorkOrder.model_validate({**_order("work_invalid").model_dump(), "unexpected": True})

    monkeypatch.setattr(router, "_dispatch", invalid_dispatch)
    try:
        await router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_validation",
                "type": operation,
                "payload": (
                    {"task_id": "task_validation"}
                    if operation == "task.open"
                    else {"artifact_type": "claim", "title": "Invalid artifact", "content": {}}
                ),
                "expected_workspace_revision": 0,
                "context": {
                    "client_id": client.client_id,
                    "session_id": client.session_id,
                    "workspace_id": "ws_validation",
                },
            },
        )
        error = [e for e in events if e.type == "request.failed"][-1].payload.error
        if operation == "artifact.create":
            assert error.code == "invalid_artifact"
            assert "Artifact" in error.message
            return
        assert error.code == "store_error"
        assert "task.open" in error.message
        assert "WorkOrder" in error.message
        assert "unexpected" in error.message
        assert "Artifact" not in error.message
        assert tuple(error.details["validation"][0]["loc"]) == ("unexpected",)
        assert "input" not in error.details["validation"][0]
    finally:
        store.close()

@pytest.mark.parametrize("retired_phase", ["output_ready", "submitted", "accepted"])
def test_retired_workstream_phases_are_not_runtime_inputs(retired_phase: str) -> None:
    with pytest.raises(ValidationError):
        WorkstreamCheckpoint.model_validate({"phase": retired_phase})


async def _open_test_workspace(host, *, workspace_id: str, path) -> None:
    async def send(_event: object) -> None:
        return None

    client = BackendClient(transport="stdio", expected_client_kind="desktop", sender=send)
    await host.event_bus.register(client)
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": f"req_{workspace_id}_handshake",
            "type": "system.handshake",
            "payload": {
                "client_kind": "desktop",
                "client_version": "test",
                "supported_protocol_versions": [2],
            },
        },
    )
    await host.router.handle_payload(
        client,
        {
            "protocol_version": 2,
            "request_id": f"req_{workspace_id}_open",
            "type": "workspace.open",
            "payload": {"path": str(path)},
            "context": {
                "client_id": client.client_id,
                "session_id": client.session_id,
                "workspace_id": workspace_id,
            },
            "expected_workspace_revision": 0,
        },
    )


@pytest.mark.asyncio
async def test_cancel_agent_request_cancels_the_foreground_task(tmp_path: Path) -> None:
    """Cancellation follows the owning asyncio task, not a second engine API."""

    store = RequestStore(tmp_path / "cancel.sqlite3")
    emitted: list[object] = []

    async def send(event: object) -> None:
        emitted.append(event)

    client = BackendClient(
        transport="stdio",
        expected_client_kind="desktop",
        sender=send,
        client_id="client_cancel",
        session_id="session_cancel",
        workspace_id="ws_cancel",
        principal=principal_for_client_kind("desktop"),
    )
    router = OceanRequestRouter(store=store, event_bus=EventBus())
    target = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_cancel_target",
            "type": "session.submit",
            "payload": {"text": "Run until the user stops this analysis."},
            "context": {
                "client_id": client.client_id,
                "session_id": client.session_id,
                "workspace_id": client.workspace_id,
            },
            "expected_workspace_revision": 0,
        }
    )
    cancel_request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_cancel_control",
            "type": "request.cancel",
            "payload": {"target_request_id": target.request_id},
            "context": {
                "client_id": client.client_id,
                "session_id": client.session_id,
                "workspace_id": client.workspace_id,
            },
            "expected_workspace_revision": 0,
        }
    )
    assert client.principal is not None
    for request in (target, cancel_request):
        store.reserve(request, principal=client.principal.key)
        store.mark_in_progress(request.request_id)

    started = asyncio.Event()

    async def foreground() -> None:
        started.set()
        await asyncio.Event().wait()

    foreground_task = asyncio.create_task(foreground())
    await started.wait()
    router._agent_tasks[target.request_id] = foreground_task

    await router._cancel_agent_request(
        client,
        cancel_request=cancel_request,
        target_request_id=target.request_id,
        reason="Cancelled by user",
    )

    assert foreground_task.cancelled()
    assert store.get_request(target.request_id).state == "cancelled"
    assert store.get_request(cancel_request.request_id).state == "completed"
    assert [getattr(event, "type", None) for event in emitted] == ["request.completed"]
    store.close()


def test_role_pool_has_five_work_owners_and_one_discussion_partner() -> None:
    assert len(AGENT_PROFILES) == 6
    assert sum(profile.authority is ChildAuthority.EXPERT for profile in AGENT_PROFILES) == 5
    assert sum(profile.authority is ChildAuthority.DISCUSSION for profile in AGENT_PROFILES) == 1
    assert len({profile.profile_id for profile in AGENT_PROFILES}) == 6
    with pytest.raises(ValueError, match="unknown OceanMind agent profile"):
        get_agent_profile("unknown_role")


def test_profile_binding_attaches_only_trusted_professional_identity() -> None:
    payload: dict[str, object] = {"profile_id": "ocean_process_expert"}
    profile = bind_agent_profile(payload)
    assert profile is not None
    assert payload["semantic_role"] == "Ocean Process & Mechanism Expert"
    assert payload["authority"] == "expert"
    assert "assigned_manuals" not in payload


def test_coordinator_routes_profiles_without_receiving_manual_checklists() -> None:
    services = OceanToolServices(
        workspace_id="ws_contract_boundary",
        provider_id="provider_fixture",
        store=SimpleNamespace(),
        team_assign_sink=lambda _payload, _context: None,
    )
    composition = build_ocean_runtime_composition(services)
    prompt = "\n".join(composition.profile.system_prompt_sections)
    catalog = agent_profile_prompt_section()

    assert "# Ocean Research Manuals" not in prompt
    assert "questions_to_resolve" not in prompt
    assert "Default Manuals:" not in catalog
    assert "Profiles are routing jurisdictions, not task templates" in catalog
    assert "Never expand a profile's ownership list" in prompt
    assert "A report is never an Expert output" in prompt
    assert "Coordinator later incorporates the accepted evidence" in prompt
    assert "several scientifically distinct views" in prompt
    assert "Split at natural" in prompt
    assert "Sharing one dataset is not a reason to merge" in prompt
    assert "Keep work together only when it shares" in prompt
    assert (
        "Most analytical requests with several explicit subquestions need two or three todos"
        in prompt
    )
    assert "normally uses three to six" not in prompt


def test_expert_views_remain_simple_and_regions_exclude_land() -> None:
    prompt = " ".join(OCEAN_EXPERT_WORKSTREAM_POLICY.split())

    assert "do not merge unrelated panels into one opaque payload" in prompt
    assert "do not publish intermediate debugging plots" in prompt
    assert "actual valid-data or water-domain mask" in prompt
    assert "clipped away from land and out-of-domain cells" in prompt
    assert "Never fabricate a rectangular scientific overlay" in prompt


def test_expert_profile_completion_is_bounded_by_work_order() -> None:
    for profile in AGENT_PROFILES:
        if profile.authority is not ChildAuthority.EXPERT:
            continue
        prompt = profile_system_prompt(profile)
        flattened = " ".join(prompt.split())
        assert "Coordinator alone decides" in flattened
        assert "advisory self-assessment" in flattened
        assert "Accept only when downstream experts" not in prompt
        assert "prefer Zarr for chunked or lazy analysis" in " ".join(prompt.split())
        if profile.profile_id == "visualization_communication_expert":
            assert "each region as its own labeled spatial-context mask" in flattened

    data_prompt = get_agent_profile("data_reproducibility_expert").instructions
    assert "none is mandatory unless the assigned question" in data_prompt
    assert "exhaustive checksums" in data_prompt
    assert "not default completion conditions" in data_prompt


def test_assignment_schema_describes_answer_level_contract() -> None:
    properties = OceanTodoInput.model_json_schema()["properties"]

    assert "bounded scientific question" in properties["question"]["description"]
    assert "Answer-level outcomes" in properties["expected_outputs"]["description"]
    assert (
        "generated figures that carry material evidence"
        in properties["expected_outputs"]["description"]
    )
    assert "Do not enumerate domain properties" in properties["expected_outputs"]["description"]
    assert "Evidence-sufficiency stopping condition" in properties["done_when"]["description"]


def test_assignment_carries_the_complete_plan_but_coordinator_owns_readiness() -> None:
    assignment = OceanAssignmentInput(
        plan_goal="Explain the temperature-salinity structure.",
        todos=(
            OceanTodoInput(
                todo_id="data_scope",
                question="Establish the source coverage and variables.",
                why_this_expert="This Expert owns source evidence.",
                profile_id="data_reproducibility_expert",
                expected_outputs=("answer",),
                done_when="Return the source facts needed by the analysis.",
            ),
            OceanTodoInput(
                todo_id="water_mass",
                depends_on=("data_scope",),
                question="Identify water masses from temperature-salinity evidence.",
                why_this_expert="This Expert owns water-mass interpretation.",
                profile_id="ocean_process_expert",
                expected_outputs=("answer", "interactive_view"),
                done_when="Return an evidence-linked water-mass interpretation.",
            ),
        ),
        # Structural validation deliberately does not infer whether data_scope
        # was accepted; this dispatch choice belongs to the Coordinator.
        dispatch=("water_mass",),
    )

    assert assignment.dispatch == ("water_mass",)
    assert len(assignment.todos) == 2

    with pytest.raises(ValidationError, match="acyclic graph"):
        OceanAssignmentInput(
            plan_goal="Invalid cyclic plan.",
            todos=(
                assignment.todos[0].model_copy(update={"depends_on": ("water_mass",)}),
                assignment.todos[1],
            ),
            dispatch=("water_mass",),
        )

    with pytest.raises(ValidationError, match="at most one todo per Expert instance"):
        OceanAssignmentInput(
            plan_goal="Ask one stable role two independent questions in sequence.",
            todos=(
                OceanTodoInput(
                    todo_id="horizontal_structure",
                    question="Describe horizontal structure.",
                    why_this_expert="This role owns the physical interpretation.",
                    profile_id="ocean_process_expert",
                    expected_outputs=("answer",),
                    done_when="Return the horizontal interpretation.",
                ),
                OceanTodoInput(
                    todo_id="vertical_structure",
                    question="Describe vertical structure.",
                    why_this_expert="This role owns the physical interpretation.",
                    profile_id="ocean_process_expert",
                    expected_outputs=("answer",),
                    done_when="Return the vertical interpretation.",
                ),
            ),
            dispatch=("horizontal_structure", "vertical_structure"),
        )


def test_assignment_allows_parallel_experts_of_the_same_profile() -> None:
    todos = tuple(
        OceanTodoInput(
            todo_id=todo_id,
            question=question,
            why_this_expert="This Expert instance owns one independent evidence chain.",
            profile_id="ocean_process_expert",
            expert_key=expert_key,
            expected_outputs=("answer",),
            done_when="Return one independently reviewable conclusion.",
        )
        for todo_id, expert_key, question in (
            ("horizontal", "horizontal_analyst", "Resolve the horizontal structure."),
            ("vertical", "vertical_analyst", "Resolve the vertical structure."),
            ("water_mass", "water_mass_analyst", "Resolve the T-S water masses."),
        )
    )

    assignment = OceanAssignmentInput(
        plan_goal="Resolve three independent ocean-process questions in parallel.",
        todos=todos,
        dispatch=("horizontal", "vertical", "water_mass"),
    )

    assert [todo.profile_id for todo in assignment.todos] == ["ocean_process_expert"] * 3
    assert [todo.expert_key for todo in assignment.todos] == [
        "horizontal_analyst",
        "vertical_analyst",
        "water_mass_analyst",
    ]

    with pytest.raises(ValidationError, match="ocean_process_expert/horizontal_analyst"):
        OceanAssignmentInput(
            plan_goal=assignment.plan_goal,
            todos=(
                todos[0],
                todos[1].model_copy(update={"expert_key": "horizontal_analyst"}),
            ),
            dispatch=("horizontal", "vertical"),
        )


def test_plan_is_one_parallel_coordinator_wave() -> None:
    orders = (
        _order("work_data"),
        _order("work_science", "ocean_process_expert"),
    )
    plan = WorkPlan(
        plan_id="plan_current_team",
        parent_request_id="req_current_team",
        workspace_revision=3,
        reason_codes=("bounded_parallel_work",),
        todos=tuple(_todo(order) for order in orders),
        dispatch=tuple(order.todo_id for order in orders if order.todo_id is not None),
        work_orders=orders,
    )
    dumped = plan.model_dump(mode="json")
    assert len(dumped["work_orders"]) == 2
    assert all(item["depends_on"] == [] for item in dumped["work_orders"])
    with pytest.raises(ValidationError, match="work orders must match"):
        WorkPlan(
            plan_id="plan_empty",
            parent_request_id="req_current_team",
            workspace_revision=3,
            reason_codes=("no_work",),
            todos=(
                CoordinatorTodo(
                    todo_id="todo_empty",
                    question="Return one bounded fact.",
                    profile_id="data_reproducibility_expert",
                ),
            ),
            dispatch=("todo_empty",),
        )


def test_execute_plan_starts_independent_experts_in_parallel(tmp_path) -> None:
    class ParallelProbe(OceanTeamOrchestrator):
        def __init__(self) -> None:
            self._plans = {}
            self.started = 0
            self.max_active = 0
            self.active = 0
            self.release = asyncio.Event()

        async def delegate(self, *, work_order, **_kwargs):
            self.started += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.started == 2:
                self.release.set()
            await asyncio.wait_for(self.release.wait(), timeout=1)
            self.active -= 1
            return ExpertResult(
                work_order_id=work_order.work_order_id,
                status=WorkStatus.COMPLETED,
                text=f"{work_order.semantic_role} returned.",
            )

    async def scenario() -> None:
        orders = (
            _order("work_parallel_data"),
            _order("work_parallel_science", "ocean_process_expert"),
        )
        plan = WorkPlan(
            plan_id="plan_parallel_runtime",
            parent_request_id="req_current_team",
            workspace_revision=3,
            reason_codes=("bounded_parallel_work",),
            todos=tuple(_todo(order) for order in orders),
            dispatch=tuple(order.todo_id for order in orders if order.todo_id is not None),
            work_orders=orders,
        )
        orchestrator = ParallelProbe()
        results = await orchestrator.execute_plan(
            workspace_id="ws_current_team",
            workspace_path=tmp_path,
            provider_id="provider_fixture",
            task_id=None,
            plan=plan,
        )

        assert len(results) == 2
        assert orchestrator.max_active == 2

    asyncio.run(scenario())


def test_receiver_contract_requires_complete_expert_result() -> None:
    result = ExpertResult(
        work_order_id="work_data",
        status=WorkStatus.COMPLETED,
        expert_decision=ExpertDecision.ACCEPTED,
        text="The source metadata and bounded values were inspected.",
        evidence_refs=(EvidenceRef(kind="code_execution", ref="codeexec_example"),),
        method_summary="Opened only metadata and bounded coordinate values.",
        checks_performed=("Coordinates and units were present.",),
        limitations=("No full-array statistics were requested.",),
    )
    assert result.status is WorkStatus.COMPLETED
    coordinator = CoordinatorResult(
        decision=CoordinatorDecision.ANSWERED,
        answer_basis=CoordinatorAnswerBasis.EXPERT_EVIDENCE,
        answer_markdown="The data contract is established.",
        evidence_refs=result.evidence_refs,
        confidence=0.9,
    )
    assert coordinator.answer_basis is CoordinatorAnswerBasis.EXPERT_EVIDENCE


def test_analysis_conclusions_link_to_persisted_expert_outputs() -> None:
    output = ExpertOutput(
        item_id="result_section_26n",
        execution_id="exec_section_26n",
        output_name="section_26n.json",
        size_bytes=128,
        sha256="a" * 64,
        result_kind="interactive_view",
        title="26°N temperature section",
    )
    conclusion = ExpertConclusion(
        conclusion_id="conclusion_thermocline",
        statement="The thermocline shoals during the observed interval.",
        basis=FindingBasis.OBSERVATION,
        output_ids=(output.item_id,),
        confidence=0.8,
    )

    result = ExpertResult(
        work_order_id="work_section_26n",
        status=WorkStatus.COMPLETED,
        text="The requested section analysis is ready.",
        outputs=(output,),
        conclusions=(conclusion,),
    )

    assert result.conclusions[0].output_ids == (result.outputs[0].item_id,)
    with pytest.raises(ValidationError, match="at least one output or evidence reference"):
        ExpertResult(
            work_order_id="work_section_unbound",
            status=WorkStatus.COMPLETED,
            text="This scientific conclusion is not bound to the generated view.",
            outputs=(output,),
            conclusions=(conclusion.model_copy(update={"output_ids": ()}),),
        )
    with pytest.raises(ValidationError, match="unavailable outputs"):
        ExpertResult(
            work_order_id="work_section_invalid",
            status=WorkStatus.COMPLETED,
            text="This conclusion points to a missing view.",
            outputs=(output,),
            conclusions=(conclusion.model_copy(update={"output_ids": ("result_missing",)}),),
        )


def test_incomplete_expert_result_is_a_valid_scientific_terminal_without_failure_code() -> None:
    result = ExpertResult(
        work_order_id="work_partial",
        status=WorkStatus.INCOMPLETE,
        expert_decision=ExpertDecision.INSUFFICIENT_EVIDENCE,
        text="The available evidence supports only a bounded partial answer.",
        error="One material scientific question remains unresolved.",
        unresolved_questions=("The missing question requires new evidence.",),
    )
    assert result.status is WorkStatus.INCOMPLETE
    assert result.failure_code is None


def _team_record(
    *,
    order_id: str,
    job_key: str,
    session_round: int,
    status: WorkStatus,
    summary: str,
    decision: ExpertDecision | None,
    updated_at: str,
    limitations: tuple[str, ...] = (),
) -> TeamWorkRecord:
    order = _order(order_id).model_copy(update={"job_key": job_key, "session_round": session_round})
    result = ExpertResult(
        work_order_id=order_id,
        status=status,
        expert_decision=decision,
        summary=summary,
        evidence_refs=(EvidenceRef(kind="external", ref=f"evidence:{order_id}"),),
        limitations=limitations,
        confidence=0.8,
    )
    return TeamWorkRecord(
        workspace_id="ws_recovery",
        work_order=order,
        state=status,
        result=result,
        checkpoint=WorkstreamCheckpoint(),
        resume_count=0,
        created_at=updated_at,
        updated_at=updated_at,
    )


def test_backend_does_not_recover_a_coordinator_decision_from_expert_rounds(tmp_path) -> None:
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"), event_bus=EventBus()
    )
    records = [
        _team_record(
            order_id="work_data_round_1",
            job_key="job_data",
            session_round=1,
            status=WorkStatus.INCOMPLETE,
            summary="Superseded partial result.",
            decision=ExpertDecision.NEEDS_REVISION,
            updated_at="2026-08-17T00:00:01+00:00",
        ),
        _team_record(
            order_id="work_data_round_2",
            job_key="job_data",
            session_round=2,
            status=WorkStatus.COMPLETED,
            summary="Latest complete result.",
            decision=ExpertDecision.ACCEPTED,
            updated_at="2026-08-17T00:00:02+00:00",
        ),
    ]

    assert records[-1].result is not None
    assert records[-1].result.text == "Latest complete result."
    assert not hasattr(router, "_recover_coordinator_result_from_team_records")


def test_partial_expert_result_does_not_become_a_backend_coordinator_decision(tmp_path) -> None:
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"), event_bus=EventBus()
    )
    records = [
        _team_record(
            order_id="work_data",
            job_key="job_data",
            session_round=1,
            status=WorkStatus.INCOMPLETE,
            summary="Useful bounded result.",
            decision=ExpertDecision.INSUFFICIENT_EVIDENCE,
            limitations=("One requested field remains unknown.",),
            updated_at="2026-08-17T00:00:01+00:00",
        )
    ]

    assert records[0].result is not None
    assert records[0].result.text == "Useful bounded result."
    assert not hasattr(router, "_recover_coordinator_result_from_team_records")


def test_model_error_cannot_supply_expert_results_to_coordinator(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    router = OceanRequestRouter(store=store, event_bus=EventBus())
    task = store.create_research_task(
        workspace_id="ws_recovery", title="Recovery", task_id="task_recovery"
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_recovery",
            "type": "session.submit",
            "payload": {"text": "Answer the question."},
            "context": {
                "session_id": "ses_recovery",
                "workspace_id": "ws_recovery",
                "client_id": "client_recovery",
                "task_id": task.task_id,
            },
            "expected_workspace_revision": 0,
            "expected_task_revision": 0,
        }
    )
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    store.begin_task_request(
        task_id=task.task_id,
        request_id=request.request_id,
        expected_task_revision=0,
    )
    store.start_task_workflow(
        request_id=request.request_id,
        task_id=task.task_id,
        workspace_id="ws_recovery",
    )
    records = [
        _team_record(
            order_id="work_data",
            job_key="job_data",
            session_round=1,
            status=WorkStatus.COMPLETED,
            summary="The result is already durable.",
            decision=ExpertDecision.ACCEPTED,
            updated_at="2026-08-17T00:00:01+00:00",
        )
    ]

    assert records[0].result is not None
    assert records[0].result.text == "The result is already durable."
    assert not hasattr(router, "_recover_coordinator_result_after_model_error")


def test_team_completion_is_visible_only_after_request_terminal_commit(tmp_path) -> None:
    """A prepared Coordinator result must not outrun the durable request terminal."""

    store = RequestStore(tmp_path / "state.sqlite3")
    router = OceanRequestRouter(store=store, event_bus=EventBus())
    task = store.create_research_task(
        workspace_id="ws_terminal_truth",
        title="Terminal truth",
        task_id="task_terminal_truth",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_terminal_truth",
            "type": "session.submit",
            "payload": {"text": "Answer the question."},
            "context": {
                "session_id": "ses_terminal_truth",
                "workspace_id": "ws_terminal_truth",
                "client_id": "client_terminal_truth",
                "task_id": task.task_id,
            },
            "expected_workspace_revision": 0,
            "expected_task_revision": 0,
        }
    )
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    store.begin_task_request(
        task_id=task.task_id,
        request_id=request.request_id,
        expected_task_revision=0,
    )
    store.start_task_workflow(
        request_id=request.request_id,
        task_id=task.task_id,
        workspace_id="ws_terminal_truth",
    )
    store.record_coordinator_result(
        request_id=request.request_id,
        result=CoordinatorResult(
            decision=CoordinatorDecision.ANSWERED,
            answer_basis=CoordinatorAnswerBasis.GENERAL_KNOWLEDGE,
            answer_markdown="The answer is ready.",
            confidence=0.9,
        ),
    )
    store.update_task_workflow_progress(
        request_id=request.request_id,
        activity="Coordinator conclusion ready",
        checkpoint={
            "phase": "coordinator_result",
            "outcome_status": "completed",
            "decision": "answered",
        },
    )

    prepared = router._team_snapshot_payload(
        workspace_id="ws_terminal_truth",
        parent_request_id=request.request_id,
        revision=1,
    )
    assert prepared.request_id == request.request_id
    assert prepared.status == "working"
    assert prepared.agents[0].status == "working"

    terminal = RequestCompletedEvent(
        protocol_version=2,
        event_id=new_event_id(),
        session_id="ses_terminal_truth",
        workspace_id="ws_terminal_truth",
        task_id=task.task_id,
        request_id=request.request_id,
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        type="request.completed",
        payload=RequestCompletedPayload(result={"assistant_text": "The answer is ready."}),
    )
    store.commit_task_terminal_with_checkpoint(
        request_id=request.request_id,
        task_id=task.task_id,
        terminal_event=terminal,
        messages=[],
        provider_id="provider_fixture",
        model_id="model_fixture",
        runtime_profile_fingerprint="runtime_fixture",
        system_prompt_fingerprint="prompt_fixture",
        compaction_generation=0,
        usage_summary={},
    )

    committed = router._team_snapshot_payload(
        workspace_id="ws_terminal_truth",
        parent_request_id=request.request_id,
        revision=2,
    )
    assert committed.request_id == request.request_id
    assert committed.status == "completed"
    assert committed.agents[0].status == "completed"


def test_team_snapshot_exposes_complete_coordinator_todo_plan(tmp_path) -> None:
    orders = tuple(
        _order(f"work_plan_{index}").model_copy(
            update={
                "todo_id": f"question_{index}",
                "parent_request_id": "req_visible_plan",
                "workspace_revision": 3,
            }
        )
        for index in range(1, 4)
    )
    todos = tuple(_todo(order) for order in orders)
    plan = WorkPlan(
        plan_id="plan_visible",
        parent_request_id="req_visible_plan",
        workspace_revision=3,
        reason_codes=("coordinator_assignment",),
        todos=todos,
        dispatch=(todos[0].todo_id,),
        work_orders=(orders[0],),
    )
    store = RequestStore(tmp_path / "state.sqlite3")
    router = OceanRequestRouter(
        store=store,
        event_bus=EventBus(),
        team_orchestrator=SimpleNamespace(plan_for=lambda _request_id: plan),
    )

    snapshot = router._team_snapshot_payload(
        workspace_id="ws_visible_plan",
        parent_request_id="req_visible_plan",
        revision=1,
    )

    assert [todo.todo_id for todo in snapshot.todos] == [
        "question_1",
        "question_2",
        "question_3",
    ]
    assert [todo.state for todo in snapshot.todos] == ["queued", "pending", "pending"]


def test_expert_message_history_survives_checkpoint_compaction(tmp_path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    identity = {
        "workspace_id": "ws_history",
        "task_scope": "task_history",
        "participant_key": "data_reproducibility_expert",
        "job_key": "job_history",
        "work_order_id": "work_history",
    }
    first = {"role": "user", "content": [{"type": "text", "text": "First assignment"}]}
    second = {"role": "assistant", "content": [{"type": "text", "text": "First finding"}]}
    store.save_expert_session_checkpoint(
        **identity,
        messages=[first, second],
        compaction_generation=0,
    )
    compacted = {"role": "user", "content": [{"type": "text", "text": "Session memory"}]}
    latest = {"role": "assistant", "content": [{"type": "text", "text": "Latest finding"}]}
    store.save_expert_session_checkpoint(
        **identity,
        messages=[compacted, latest],
        compaction_generation=1,
    )

    history = store.get_expert_session_message_history(
        workspace_id=identity["workspace_id"],
        task_scope=identity["task_scope"],
        participant_key=identity["participant_key"],
        job_key=identity["job_key"],
    )

    assert history == (first, second, compacted, latest)


def test_failed_request_cannot_keep_a_prepared_completed_team_snapshot(tmp_path) -> None:
    """A finalization exception must replace a prepared success with an incomplete terminal."""

    store = RequestStore(tmp_path / "state.sqlite3")
    router = OceanRequestRouter(store=store, event_bus=EventBus())
    task = store.create_research_task(
        workspace_id="ws_failed_terminal",
        title="Failed terminal",
        task_id="task_failed_terminal",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_failed_terminal",
            "type": "session.submit",
            "payload": {"text": "Answer the question."},
            "context": {
                "session_id": "ses_failed_terminal",
                "workspace_id": "ws_failed_terminal",
                "client_id": "client_failed_terminal",
                "task_id": task.task_id,
            },
            "expected_workspace_revision": 0,
            "expected_task_revision": 0,
        }
    )
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    store.begin_task_request(
        task_id=task.task_id,
        request_id=request.request_id,
        expected_task_revision=0,
    )
    store.start_task_workflow(
        request_id=request.request_id,
        task_id=task.task_id,
        workspace_id="ws_failed_terminal",
    )
    store.record_coordinator_result(
        request_id=request.request_id,
        result=CoordinatorResult(
            decision=CoordinatorDecision.ANSWERED,
            answer_basis=CoordinatorAnswerBasis.GENERAL_KNOWLEDGE,
            answer_markdown="The answer was prepared but not committed.",
            confidence=0.9,
        ),
    )
    store.update_task_workflow_progress(
        request_id=request.request_id,
        activity="Coordinator conclusion ready",
        checkpoint={"phase": "coordinator_result", "outcome_status": "completed"},
    )
    failure = RequestFailedEvent(
        protocol_version=2,
        event_id=new_event_id(),
        session_id="ses_failed_terminal",
        workspace_id="ws_failed_terminal",
        task_id=task.task_id,
        request_id=request.request_id,
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        type="request.failed",
        payload=RequestFailedPayload(
            error=ProtocolErrorPayload(
                code="model_error",
                message="Finalization failed",
                recoverable=True,
                details={},
            )
        ),
    )
    store.commit_task_terminal_without_checkpoint(
        request_id=request.request_id,
        terminal_event=failure,
    )

    snapshot = router._team_snapshot_payload(
        workspace_id="ws_failed_terminal",
        parent_request_id=request.request_id,
        revision=1,
    )
    assert snapshot.status == "incomplete"
    assert snapshot.agents[0].status == "incomplete"


def test_agent_job_identity_survives_recovery_wording_changes() -> None:
    base = {
        "todo_id": "data_inventory",
        "profile_id": "data_reproducibility_expert",
        "authority": "expert",
        "task_goal": "Inspect the registered source.",
        "input_refs": ({"kind": "dataset", "ref": "dataset_a@v1"},),
        "outcome_intents": ("variables", "coordinates"),
    }
    recovery = {
        **base,
        "task_goal": "Continue and return the missing coordinate metadata.",
        "outcome_intents": ("coordinates",),
    }
    assert OceanRequestRouter._agent_job_key(
        base, task_scope="task_one"
    ) == OceanRequestRouter._agent_job_key(recovery, task_scope="task_one")
    # Todo ids, source changes, and assignment wording do not split a stable
    # professional role inside one research task.
    assert OceanRequestRouter._agent_job_key(
        base, task_scope="task_one"
    ) == OceanRequestRouter._agent_job_key(
        {
            **base,
            "todo_id": "different_scientific_job",
            "input_refs": ({"kind": "dataset", "ref": "dataset_b@v2"},),
        },
        task_scope="task_one",
    )
    assert OceanRequestRouter._agent_job_key(
        base, task_scope="task_one"
    ) != OceanRequestRouter._agent_job_key(
        {**base, "profile_id": "ocean_dynamics_expert"},
        task_scope="task_one",
    )
    assert OceanRequestRouter._agent_job_key(
        base, task_scope="task_one"
    ) != OceanRequestRouter._agent_job_key(base, task_scope="task_two")
    first_parallel = {**base, "expert_key": "horizontal_analyst"}
    same_parallel_follow_up = {
        **recovery,
        "todo_id": "horizontal_follow_up",
        "expert_key": "horizontal_analyst",
    }
    sibling_parallel = {**base, "expert_key": "vertical_analyst"}
    assert OceanRequestRouter._agent_job_key(
        first_parallel, task_scope="task_one"
    ) == OceanRequestRouter._agent_job_key(
        same_parallel_follow_up, task_scope="task_one"
    )
    assert OceanRequestRouter._agent_job_key(
        first_parallel, task_scope="task_one"
    ) != OceanRequestRouter._agent_job_key(
        sibling_parallel, task_scope="task_one"
    )


def test_same_expert_session_gets_a_distinct_replay_stable_id_per_round() -> None:
    first = OceanRequestRouter._agent_round_id(
        parent_request_id="req_rounds",
        job_key="job_data_session",
        operation_suffix="operation_a",
    )
    replay = OceanRequestRouter._agent_round_id(
        parent_request_id="req_rounds",
        job_key="job_data_session",
        operation_suffix="operation_a",
    )
    follow_up = OceanRequestRouter._agent_round_id(
        parent_request_id="req_rounds",
        job_key="job_data_session",
        operation_suffix="operation_b",
    )

    assert replay == first
    assert follow_up != first


def test_expert_session_capsule_preserves_prior_round_evidence() -> None:
    first = _order("work_session_round_1").model_copy(
        update={
            "job_key": "job_data_session",
            "session_round": 1,
            "task_goal": "Identify the variables in the selected source.",
            "outcome_intents": ("variables",),
        }
    )
    result = ExpertResult(
        work_order_id=first.work_order_id,
        status=WorkStatus.COMPLETED,
        result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
        expert_decision=ExpertDecision.ACCEPTED,
        text="Temperature and salinity were identified.",
        method_summary="Read the source metadata once.",
        checks_performed=("Matched names to dimensions.",),
        suggested_next_step="Inspect temporal coverage if the Coordinator needs it.",
        confidence=0.95,
    )
    record = SimpleNamespace(
        work_order=first,
        state=WorkStatus.COMPLETED,
        result=result,
        checkpoint=SimpleNamespace(model_dump=lambda **_kwargs: {"phase": "completed"}),
    )

    capsule = OceanRequestRouter._agent_session_capsule([record])

    assert '"round_count": 1' in capsule
    assert '"outputs": []' in capsule
    assert '"conclusions": []' in capsule
    assert "Temperature and salinity were identified." in capsule


def test_expert_session_capsule_preserves_backend_recovered_partial_work() -> None:
    order = _order("work_recovered_capsule").model_copy(update={"job_key": "job_recovered_capsule"})
    result = ExpertResult(
        work_order_id=order.work_order_id,
        status=WorkStatus.INCOMPLETE,
        result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
        text="The computed section is durable although delivery was interrupted.",
        failure_code=WorkFailureCode.BUDGET_EXHAUSTED,
        error="provider stopped before final prose",
    )
    record = SimpleNamespace(
        work_order=order,
        state=WorkStatus.INCOMPLETE,
        result=result,
    )

    capsule = OceanRequestRouter._agent_session_capsule([record])

    assert '"round_count": 1' in capsule
    assert '"result_origin": "backend_recovered"' in capsule
    assert "computed section is durable" in capsule


def test_participant_budget_keeps_deep_capacity_but_bounds_one_standard_round(tmp_path) -> None:
    budget = OceanAgentBudget(
        max_turns=77,
        max_tool_calls=91,
        max_wall_seconds=1_234.0,
        max_input_tokens=765_432,
        max_output_tokens=123_456,
    )
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"),
        event_bus=EventBus(),
        agent_budget=budget,
    )

    quick = router._participant_work_budget(
        "req_shared_pool",
        authority=ChildAuthority.EXPERT,
        sibling_count=8,
        budget_tier="quick",
    )
    standard = router._participant_work_budget(
        "req_shared_pool",
        authority=ChildAuthority.DISCUSSION,
        sibling_count=1,
        budget_tier="standard",
    )
    deep = router._participant_work_budget(
        "req_shared_pool",
        authority=ChildAuthority.EXPERT,
        sibling_count=1,
        budget_tier="deep",
    )

    assert quick == WorkBudget(
        max_turns=24,
        max_tool_calls=12,
        max_wall_seconds=300.0,
        max_input_tokens=160_000,
        max_output_tokens=32_000,
    )
    assert standard == WorkBudget(
        max_turns=64,
        max_tool_calls=32,
        max_wall_seconds=600.0,
        max_input_tokens=320_000,
        max_output_tokens=64_000,
    )
    assert deep == WorkBudget(
        max_turns=budget.max_turns,
        max_tool_calls=budget.max_tool_calls,
        max_wall_seconds=budget.max_wall_seconds,
        max_input_tokens=budget.max_input_tokens,
        max_output_tokens=budget.max_output_tokens,
    )


def test_interrupted_assignment_continuation_consumes_its_round_budget(tmp_path) -> None:
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"),
        event_bus=EventBus(),
    )
    total = WorkBudget(
        max_turns=64,
        max_tool_calls=32,
        max_wall_seconds=600.0,
        max_input_tokens=320_000,
        max_output_tokens=64_000,
    )
    order = _order("work_budget_round_one")
    result = ExpertResult(
        work_order_id=order.work_order_id,
        status=WorkStatus.COMPLETED,
        result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
        text="A partial result that the Coordinator may review.",
        usage=UsageRecord(
            turns=20,
            tool_calls=12,
            input_tokens=120_000,
            output_tokens=24_000,
            wall_seconds=180.0,
        ),
    )
    record = SimpleNamespace(result=result)

    remaining = router._remaining_participant_work_budget(total, [record])

    assert remaining == WorkBudget(
        max_turns=44,
        max_tool_calls=20,
        max_wall_seconds=420.0,
        max_input_tokens=200_000,
        max_output_tokens=40_000,
    )


def test_interrupted_assignment_does_not_resume_without_delivery_capacity(tmp_path) -> None:
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"),
        event_bus=EventBus(),
    )
    total = WorkBudget(
        max_turns=24,
        max_tool_calls=12,
        max_wall_seconds=300.0,
        max_input_tokens=160_000,
        max_output_tokens=32_000,
    )
    result = ExpertResult(
        work_order_id="work_budget_exhausted",
        status=WorkStatus.INCOMPLETE,
        result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
        text="The bounded round returned only partial evidence.",
        usage=UsageRecord(
            turns=24,
            tool_calls=12,
            input_tokens=159_000,
            output_tokens=31_000,
            wall_seconds=299.0,
        ),
    )

    assert (
        router._remaining_participant_work_budget(
            total,
            [SimpleNamespace(result=result)],
        )
        is None
    )


def test_new_coordinator_follow_up_receives_a_fresh_bounded_budget(tmp_path) -> None:
    router = OceanRequestRouter(
        store=RequestStore(tmp_path / "state.sqlite3"),
        event_bus=EventBus(),
    )
    total = WorkBudget(
        max_turns=64,
        max_tool_calls=32,
        max_wall_seconds=600.0,
        max_input_tokens=320_000,
        max_output_tokens=64_000,
    )

    assert router._remaining_participant_work_budget(total, []) == total


def test_backend_does_not_infer_the_coordinator_decision_from_expert_states() -> None:
    assert not hasattr(OceanRequestRouter, "_fallback_coordinator_decision")


def test_finalizer_prefers_the_report_and_builds_one_figure_notebook(
    tmp_path, monkeypatch
) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(
                host,
                workspace_id="ws_final_delivery",
                path=workspace,
            )
            task = host.store.create_research_task(
                workspace_id="ws_final_delivery",
                title="Final delivery",
                task_id="task_final_delivery",
            )
            view_spec = {
                "schema_version": "ocean-interactive-spatial/v2",
                "view_kind": "spatial_map",
                "variable": "temperature",
                "units": "degC",
                "longitude_coordinate": "longitude",
                "latitude_coordinate": "latitude",
                "colorbar": {
                    "label": "Temperature (degC)",
                    "colormap": "viridis",
                    "levels": [10.0, 15.0, 20.0, 25.0],
                },
            }
            view_data = bytes(xr.Dataset(
                {"temperature": (("latitude", "longitude"), [[20.0, 21.0], [22.0, 23.0]])},
                coords={"longitude": [-90.0, -89.0], "latitude": [20.0, 21.0]},
                attrs={
                    "ocean_view_schema": "ocean-view-netcdf/v1",
                    "ocean_view_type": "map.field2d",
                    "ocean_view": json.dumps(view_spec),
                },
            ).to_netcdf())
            first_view = host.task_results.put(
                workspace_id="ws_final_delivery",
                task_id=task.task_id,
                kind="interactive_view",
                title="Surface temperature",
                summary="Preserved final temperature field.",
                content={
                    "view_kind": "spatial_map",
                    "data_file": "data.nc",
                    "dataset_file": "data.nc",
                },
                files={"data.nc": view_data},
                origin_request_id="req_final_delivery",
                execution_id="codeexec_figures",
                materialization_key="first-view",
            )
            second_view = host.task_results.put(
                workspace_id="ws_final_delivery",
                task_id=task.task_id,
                kind="interactive_view",
                title="Surface salinity",
                summary="Second accepted view over preserved data.",
                content={
                    "view_kind": "spatial_map",
                    "data_file": "data.nc",
                    "dataset_file": "data.nc",
                },
                files={"data.nc": view_data},
                origin_request_id="req_final_delivery",
                execution_id="codeexec_figures",
                materialization_key="second-view",
            )
            report_ref = host.router._materialize_coordinator_report(
                workspace_id="ws_final_delivery",
                task_id=task.task_id,
                request_id="req_final_delivery",
                answer_markdown="# Scientific report\n\nThe basin is warmer in the south.",
                result_refs=(first_view.ref, second_view.ref),
            )
            assert report_ref is not None
            report = host.task_results.get(report_ref)
            assert report.kind == "report"
            assert report.content["role"] == "coordinator_report"
            assert report.source_refs == (
                first_view.ref.model_dump(mode="json"),
                second_view.ref.model_dump(mode="json"),
            )

            supplement_ref = host.router._materialize_figure_reproduction_notebook(
                workspace_id="ws_final_delivery",
                task_id=task.task_id,
                request_id="req_final_delivery",
                result_refs=(first_view.ref, second_view.ref, report.ref),
            )

            assert supplement_ref is not None
            supplement = host.task_results.get(supplement_ref)
            assert supplement.kind == "file"
            assert supplement.content["role"] == "supplementary_figure_notebook"
            assert supplement.content["renderer"] == "nature-python-templates/v3"
            assert len(supplement.content["data_files"]) == 2
            assert len(supplement.files) == 1
            assert supplement.files[0].mime_type == "application/x-ipynb+json"
            notebook_path = host.task_results.file_path(
                ref=supplement.ref,
                relative_path="analysis.ipynb",
            )
            assert notebook_path.parent.parent.name == "supplementary"
            assert not tuple(notebook_path.parent.glob("*.nc"))
            task_root = notebook_path.parent.parent.parent
            assert list(task_root.rglob("analysis.ipynb")) == [notebook_path]
            supplementary_result_root = (
                task_root / "results" / supplement.ref.result_id / "v0001"
            )
            assert {path.name for path in supplementary_result_root.iterdir()} == {
                "manifest.json"
            }
            merged = json.loads(notebook_path.read_text(encoding="utf-8"))
            assert merged["metadata"]["oceanmind"]["schema_version"] == (
                "ocean-supplementary-analysis-notebook/v1"
            )
            code_cells = [
                cell for cell in merged["cells"] if cell.get("cell_type") == "code"
            ]
            assert len(code_cells) == 3
            renderer_source = "".join(code_cells[0]["source"])
            assert "def render_oceanmind_view" in renderer_source
            assert '"svg.fonttype": "none"' in renderer_source
            assert "def save_publication_figure" in renderer_source
            assert "SCATTER_SIZE_MIN = 10.0" in renderer_source
            assert "CONTOUR_LEVELS = 24" in renderer_source
            assert "plot_temperature" not in json.dumps(merged)
            assert all(
                "render_oceanmind_view(resolve_result_file" in "".join(cell["source"])
                for cell in code_cells[1:]
            )
            namespace: dict[str, object] = {}
            monkeypatch.chdir(notebook_path.parent)
            exec(  # noqa: S102 - the generated notebook cell is the subject of this test
                compile(renderer_source, "analysis.ipynb", "exec"), namespace
            )
            first_data_file = supplement.content["data_files"][0]["file"]
            assert namespace["resolve_result_file"](first_data_file) == (
                notebook_path.parent / first_data_file
            ).resolve(strict=True)
            for cell in code_cells[1:]:
                exec(  # noqa: S102 - simulates Jupyter Run All
                    compile("".join(cell["source"]), "analysis.ipynb", "exec"),
                    namespace,
                )
            figure = namespace["render_oceanmind_view"](
                (notebook_path.parent / first_data_file).resolve(strict=True)
            )
            assert len(figure.axes) >= 2

            # A follow-up analysis gets its own editable notebook. Neither a retry nor
            # a new round may replace the researcher's edits or redirect the old link.
            edited = notebook_path.read_text() + "\n"
            notebook_path.write_text(edited)
            retry_ref = host.router._materialize_figure_reproduction_notebook(
                workspace_id="ws_final_delivery", task_id=task.task_id,
                request_id="req_final_delivery", result_refs=(first_view.ref,),
            )
            assert retry_ref == supplement_ref
            next_ref = host.router._materialize_figure_reproduction_notebook(
                workspace_id="ws_final_delivery", task_id=task.task_id,
                request_id="req_followup_delivery", result_refs=(second_view.ref,),
            )
            next_path = host.task_results.file_path(ref=next_ref, relative_path="analysis.ipynb")
            assert next_path != notebook_path
            assert notebook_path.read_text() == edited
            assert host.task_results.file_path(ref=supplement_ref, relative_path="analysis.ipynb") == notebook_path
            projector = host.task_workspace_projector
            assert projector.write_supplementary_notebook(
                task_id=task.task_id, request_id="req_final_delivery", content=b"replacement",
            ).read_text() == edited

            answer = host.router._canonical_user_answer(
                candidate=(
                    "Registered result.nc with publish_report(...). "
                    "ScientificFigure rejected a keyword. ACCEPTED."
                ),
                decision=CoordinatorDecision.INSUFFICIENT_EVIDENCE,
                result_refs=(first_view.ref, second_view.ref, report.ref, supplement.ref),
            )
            assert answer.startswith("# Scientific report")
            assert "publish_report" not in answer
            assert "[[result:task_final_delivery/" in answer
            assert "Open the complete research report" in answer
        finally:
            await host.close()

    asyncio.run(scenario())


def test_code_runtime_pins_assignment_and_installs_scientific_view(tmp_path) -> None:
    """Compaction-safe code receives its WorkOrder and a real view builder."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_execution_runtime",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_execution_runtime",
                title="Execution runtime fixture",
            )
            order = _order("work_execution_runtime", "ocean_process_expert").model_copy(
                update={
                    "task_id": task.task_id,
                    "task_goal": "Diagnose the bounded temperature-salinity structure.",
                    "context_summary": "Use the supplied scientific source only.",
                    "outcome_intents": ("answer", "interactive_view"),
                    "constraints": ("Keep the result bounded.",),
                    "done_when": "Return one checked view and its interpretation.",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_execution_runtime",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)

            execution = await host.expert_code_execution.run_python(
                workspace_id="ws_execution_runtime",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Read the pinned assignment and create one scientific view.",
                code=(
                    "import json, os\n"
                    "manifest = json.load(open(os.environ['OCEAN_INPUT_MANIFEST']))\n"
                    "figure = ScientificFigure(plot_kind='profile', title='T-S fixture')\n"
                    "panel = figure.panel(x=[34.8, 35.1], y=[0.0, 100.0], "
                    "x_label='Salinity', y_label='Depth', y_reverse=True)\n"
                    "panel.line()\n"
                    "figure.save('runtime_view.nc')\n"
                    "print(manifest['assignment']['task_goal'])\n"
                    "print(manifest['runtime']['scientific_view']['import'])\n"
                    "print('publish_report' in globals())\n"
                ),
            )

            assert execution.state == "succeeded"
            assert "runtime_view.nc" in execution.output_files
            assert len(execution.discovered_results) == 1
            discovered = execution.discovered_results[0]
            assert discovered["schema_version"] == "ocean-result-event/v1"
            assert discovered["kind"] == "interactive_view"
            assert discovered["view_kind"] == "profile"
            assert discovered["title"] == "T-S fixture"
            assert discovered["summary"] == ""
            assert discovered["data_output"] == "runtime_view.nc"
            assert discovered["view_type"] == "profile.line"
            assert order.task_goal in execution.stdout
            assert "from oceanx.scientific_view import ScientificFigure" in execution.stdout
            assert execution.stdout.rstrip().endswith("False")
        finally:
            await host.close()

    asyncio.run(scenario())


def test_code_runtime_preserves_intermediate_state_for_one_logical_expert(tmp_path) -> None:
    """Separate code calls share scratch state without mixing it into deliverables."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_persistent_expert_work",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_persistent_expert_work",
                title="Persistent Expert workspace fixture",
            )
            order = _order("work_persistent_expert_work").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_persistent_expert_work",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_persistent_expert_work",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)

            first = await host.expert_code_execution.run_python(
                workspace_id="ws_persistent_expert_work",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Save a reusable intermediate checkpoint.",
                code=(
                    "import os, pathlib\n"
                    "work = pathlib.Path(os.environ['OCEAN_WORK_DIR'])\n"
                    "(work / 'section-values.txt').write_text('27.39,36.02', encoding='utf-8')\n"
                    "print(work)\n"
                ),
            )
            assert first.state == "succeeded"
            assert first.output_files == ()

            second = await host.expert_code_execution.run_python(
                workspace_id="ws_persistent_expert_work",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:2",
                purpose="Reuse the checkpoint and publish one formal result.",
                code=(
                    "import json, os, pathlib\n"
                    "work = pathlib.Path(os.environ['OCEAN_WORK_DIR'])\n"
                    "values = work.joinpath('section-values.txt').read_text(encoding='utf-8')\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "out.joinpath('section.json').write_text(json.dumps({'values': values}), encoding='utf-8')\n"
                    "manifest = json.load(open(os.environ['OCEAN_INPUT_MANIFEST']))\n"
                    "print(manifest['runtime']['storage']['working_directory']['path'])\n"
                ),
            )

            assert second.state == "succeeded"
            assert second.output_files == ("section.json",)
            assert json.loads(
                (
                    Path(second.work_root)
                    / "executions"
                    / second.execution_id
                    / "outputs"
                    / "section.json"
                ).read_text(encoding="utf-8")
            ) == {"values": "27.39,36.02"}
            assert Path(second.work_root, "workspace", "section-values.txt").is_file()
            assert str(Path(second.work_root, "workspace")) in second.stdout
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("review", [False, True])
def test_default_expert_runtime_uses_the_ocean_model_profile_response_ceiling(
    tmp_path, monkeypatch, review
) -> None:
    import oceanx.agent as agent_module
    from oceanx.agent_tools import ToolRegistry
    from oceanx.model_config import OceanModelProfile

    captured: dict[str, object] = {}

    async def capture_engine(**kwargs):
        captured.update(kwargs)

        async def close() -> None:
            return None

        return SimpleNamespace(), close

    async def build_composition(**_kwargs):
        return SimpleNamespace(
            profile=SimpleNamespace(tool_registry=ToolRegistry()),
            system_prompt="fixture expert prompt",
        )

    def load_profile(role):
        captured["model_role"] = role
        return OceanModelProfile(
            name="fixture",
            label="Fixture",
            provider="openai",
            model="fixture-model",
            base_url=None,
            credential_slot="fixture",
            api_key="fixture-key",
            max_tokens=65_536,
        )

    monkeypatch.setattr(agent_module, "load_model_profile", load_profile)
    monkeypatch.setattr(
        agent_module,
        "build_ocean_expert_runtime",
        build_composition,
    )
    monkeypatch.setattr(agent_module, "build_deep_agent_engine", capture_engine)

    async def scenario() -> None:
        runtime = await agent_module.build_default_ocean_expert_runtime(
            SimpleNamespace(
                provider_id="provider_fixture",
                workspace_id="workspace_fixture",
                work_order_id="work_fixture",
                expert_child_id=None,
                agent_thread_id="expert:fixture",
                store=SimpleNamespace(
                    get_team_work=lambda _id: SimpleNamespace(work_order=SimpleNamespace(review=review))
                ),
            ),
            tmp_path,
            OceanAgentBudget(),
            lambda _request_id, _turn_id, _tool_call_id: "operation_fixture",
        )
        assert runtime.model_id == "fixture-model"

    asyncio.run(scenario())

    assert captured["profile"].max_tokens == 65_536
    assert captured["thread_id"] == "expert:fixture"
    assert captured["model_role"] == ("coordinator" if review else "expert")


def test_shared_team_usage_counts_only_measured_terminal_results() -> None:
    records = [
        SimpleNamespace(
            result=SimpleNamespace(usage=UsageRecord(input_tokens=120, output_tokens=30))
        ),
        SimpleNamespace(result=None),
        SimpleNamespace(
            result=SimpleNamespace(usage=UsageRecord(input_tokens=45, output_tokens=5))
        ),
    ]

    assert OceanRequestRouter._team_work_token_usage(records) == 200


def test_completed_round_waits_as_one_logical_expert_node(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(host, workspace_id="ws_session_rounds", path=workspace)
            task = host.store.create_research_task(
                workspace_id="ws_session_rounds",
                title="Session rounds",
                task_id="task_session_rounds",
            )
            request = parse_request(
                {
                    "protocol_version": 2,
                    "request_id": "req_session_rounds",
                    "type": "session.submit",
                    "payload": {"text": "Inspect the selected data."},
                    "context": {
                        "session_id": "ses_session_rounds",
                        "workspace_id": "ws_session_rounds",
                        "client_id": "client_session_rounds",
                        "task_id": task.task_id,
                    },
                    "expected_workspace_revision": 1,
                    "expected_task_revision": 0,
                }
            )
            host.store.reserve(request, principal="user:local:desktop")
            host.store.mark_in_progress(request.request_id)
            host.store.begin_task_request(
                task_id=task.task_id,
                request_id=request.request_id,
                expected_task_revision=0,
            )
            host.store.start_task_workflow(
                request_id=request.request_id,
                task_id=task.task_id,
                workspace_id="ws_session_rounds",
            )

            for round_number, goal in (
                (1, "Identify variables."),
                (2, "Return the missing temporal coverage."),
            ):
                order = _order(f"work_session_node_{round_number}").model_copy(
                    update={
                        "parent_request_id": request.request_id,
                        "workspace_revision": 1,
                        "job_key": "job_one_logical_expert",
                        "session_round": round_number,
                        "task_goal": goal,
                    }
                )
                host.store.create_team_work_order(
                    workspace_id="ws_session_rounds", work_order=order
                )
                host.store.complete_team_work(
                    ExpertResult(
                        work_order_id=order.work_order_id,
                        status=WorkStatus.COMPLETED,
                        expert_decision=ExpertDecision.ACCEPTED,
                        text=f"Round {round_number} returned useful evidence.",
                        method_summary="Inspected the assigned source.",
                        checks_performed=("Checked the requested outcome.",),
                        confidence=0.9,
                    )
                )

            snapshot = host.router._team_snapshot_payload(
                workspace_id="ws_session_rounds",
                parent_request_id=request.request_id,
                revision=1,
            )

            assert snapshot.status == "working"
            assert len(snapshot.agents) == 2
            expert = snapshot.agents[1]
            assert expert.agent_id == "job_one_logical_expert"
            assert expert.status == "waiting"
            assert expert.activity == "Waiting for Coordinator feedback"
            assert expert.task_goal == "Return the missing temporal coverage."
        finally:
            await host.close()

    asyncio.run(scenario())


def test_snapshot_keeps_parallel_same_profile_experts_separate(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(host, workspace_id="ws_parallel_profile", path=workspace)
            task = host.store.create_research_task(
                workspace_id="ws_parallel_profile",
                title="Parallel same-profile Experts",
                task_id="task_parallel_profile",
            )
            request = parse_request(
                {
                    "protocol_version": 2,
                    "request_id": "req_parallel_profile",
                    "type": "session.submit",
                    "payload": {"text": "Resolve three independent process questions."},
                    "context": {
                        "session_id": "ses_parallel_profile",
                        "workspace_id": "ws_parallel_profile",
                        "client_id": "client_parallel_profile",
                        "task_id": task.task_id,
                    },
                    "expected_workspace_revision": 1,
                    "expected_task_revision": 0,
                }
            )
            host.store.reserve(request, principal="user:local:desktop")
            host.store.mark_in_progress(request.request_id)
            host.store.begin_task_request(
                task_id=task.task_id,
                request_id=request.request_id,
                expected_task_revision=0,
            )
            host.store.start_task_workflow(
                request_id=request.request_id,
                task_id=task.task_id,
                workspace_id="ws_parallel_profile",
            )

            for index, expert_key in enumerate(
                ("horizontal_analyst", "vertical_analyst", "water_mass_analyst"),
                start=1,
            ):
                order = _order(
                    f"work_parallel_profile_{index}",
                    "ocean_process_expert",
                ).model_copy(
                    update={
                        "task_id": task.task_id,
                        "parent_request_id": request.request_id,
                        "workspace_revision": 1,
                        "job_key": f"job_{expert_key}",
                        "expert_key": expert_key,
                    }
                )
                host.store.create_team_work_order(
                    workspace_id="ws_parallel_profile",
                    work_order=order,
                )

            snapshot = host.router._team_snapshot_payload(
                workspace_id="ws_parallel_profile",
                parent_request_id=request.request_id,
                revision=1,
            )

            experts = snapshot.agents[1:]
            assert len(experts) == 3
            assert {expert.profile_id for expert in experts} == {"ocean_process_expert"}
            assert {expert.expert_key for expert in experts} == {
                "horizontal_analyst",
                "vertical_analyst",
                "water_mass_analyst",
            }
            assert {expert.agent_id for expert in experts} == {
                "job_horizontal_analyst",
                "job_vertical_analyst",
                "job_water_mass_analyst",
            }
            horizontal_records = host.router._agent_job_records(
                workspace_id="ws_parallel_profile",
                task_id=task.task_id,
                parent_request_id=request.request_id,
                job_key="job_horizontal_analyst",
                profile_id="ocean_process_expert",
                authority="expert",
                expert_key="horizontal_analyst",
            )
            assert [record.work_order.expert_key for record in horizontal_records] == [
                "horizontal_analyst"
            ]
        finally:
            await host.close()

    asyncio.run(scenario())


def test_latest_expert_round_controls_the_single_canvas_node(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(host, workspace_id="ws_latest_round", path=workspace)
            task = host.store.create_research_task(
                workspace_id="ws_latest_round",
                title="Latest round",
                task_id="task_latest_round",
            )
            request = parse_request(
                {
                    "protocol_version": 2,
                    "request_id": "req_latest_round",
                    "type": "session.submit",
                    "payload": {"text": "Inspect the source."},
                    "context": {
                        "session_id": "ses_latest_round",
                        "workspace_id": "ws_latest_round",
                        "client_id": "client_latest_round",
                        "task_id": task.task_id,
                    },
                    "expected_workspace_revision": 1,
                    "expected_task_revision": 0,
                }
            )
            host.store.reserve(request, principal="user:local:desktop")
            host.store.mark_in_progress(request.request_id)
            host.store.begin_task_request(
                task_id=task.task_id,
                request_id=request.request_id,
                expected_task_revision=0,
            )
            host.store.start_task_workflow(
                request_id=request.request_id,
                task_id=task.task_id,
                workspace_id="ws_latest_round",
            )

            first = _order("work_latest_round_1").model_copy(
                update={
                    "parent_request_id": request.request_id,
                    "workspace_revision": 1,
                    "job_key": "job_latest_round",
                    "session_round": 1,
                }
            )
            second = first.model_copy(
                update={
                    "work_order_id": "work_latest_round_2",
                    # Legacy builds derived job_key from todo_id. Role-level
                    # projection must still keep this as the same Expert node.
                    "job_key": "job_legacy_second_todo_same_role",
                    "session_round": 2,
                    "task_goal": "Resolve the remaining limitation.",
                }
            )
            host.store.create_team_work_order(workspace_id="ws_latest_round", work_order=first)
            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=first.work_order_id,
                    status=WorkStatus.COMPLETED,
                    expert_decision=ExpertDecision.ACCEPTED,
                    text="The first round completed.",
                    method_summary="Inspected the source.",
                    checks_performed=("Checked the first outcome.",),
                )
            )
            host.store.create_team_work_order(workspace_id="ws_latest_round", work_order=second)
            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=second.work_order_id,
                    status=WorkStatus.INCOMPLETE,
                    expert_decision=ExpertDecision.INSUFFICIENT_EVIDENCE,
                    text="The follow-up found a material unresolved limitation.",
                    method_summary="Checked the missing evidence.",
                    limitations=("The requested fact remains unsupported.",),
                )
            )

            snapshot = host.router._team_snapshot_payload(
                workspace_id="ws_latest_round",
                parent_request_id=request.request_id,
                revision=1,
            )

            assert len(snapshot.agents) == 2
            expert = snapshot.agents[1]
            assert expert.agent_id == "job_latest_round"
            assert expert.work_order_id == second.work_order_id
            assert expert.status == "incomplete"
            assert expert.task_goal == "Resolve the remaining limitation."
        finally:
            await host.close()

    asyncio.run(scenario())


def test_coordinator_follow_up_creates_a_new_round_with_prior_session_memory(
    tmp_path,
) -> None:
    from oceanx.backend.host import OceanBackendHost

    class RecordingOrchestrator:
        def __init__(self, store) -> None:
            self.store = store
            self.plans: list[WorkPlan] = []

        async def execute_plan(self, *, workspace_id, plan, **_kwargs):
            self.plans.append(plan)
            results: list[ExpertResult] = []
            for order in plan.work_orders:
                self.store.create_team_work_order(workspace_id=workspace_id, work_order=order)
                result = ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.COMPLETED,
                    result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
                    expert_decision=ExpertDecision.ACCEPTED,
                    text=f"Evidence returned for session round {order.session_round}.",
                    method_summary="Used the smallest sufficient inspection.",
                    checks_performed=("Checked the requested outcome.",),
                    confidence=0.9,
                )
                self.store.complete_team_work(result)
                results.append(result)
            return tuple(results)

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        original_orchestrator = host.router.team_orchestrator
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(host, workspace_id="ws_follow_up", path=workspace)
            task = host.store.create_research_task(
                workspace_id="ws_follow_up",
                title="Follow-up",
                task_id="task_follow_up",
            )
            request = parse_request(
                {
                    "protocol_version": 2,
                    "request_id": "req_follow_up",
                    "type": "session.submit",
                    "payload": {"text": "Inspect the selected data."},
                    "context": {
                        "session_id": "ses_follow_up",
                        "workspace_id": "ws_follow_up",
                        "client_id": "client_follow_up",
                        "task_id": task.task_id,
                    },
                    "expected_workspace_revision": 1,
                    "expected_task_revision": 0,
                }
            )
            host.store.reserve(request, principal="user:local:desktop")
            host.store.mark_in_progress(request.request_id)
            host.store.begin_task_request(
                task_id=task.task_id,
                request_id=request.request_id,
                expected_task_revision=0,
            )
            host.store.start_task_workflow(
                request_id=request.request_id,
                task_id=task.task_id,
                workspace_id="ws_follow_up",
            )
            recorder = RecordingOrchestrator(host.store)
            host.router.team_orchestrator = recorder

            def assignment(
                goal: str,
                output: str,
                *,
                data_todo_id: str = "data_expert",
            ) -> dict[str, object]:
                return {
                    "plan_goal": "Inspect the selected data and answer the user's request.",
                    "todos": [
                        OceanTodoInput(
                            todo_id=data_todo_id,
                            question=goal,
                            why_this_expert="This Expert owns source evidence.",
                            profile_id="data_reproducibility_expert",
                            expected_outputs=(output,),
                            done_when="Return enough evidence for the requested answer.",
                            budget_tier="quick",
                        ).model_dump(mode="json"),
                        OceanTodoInput(
                            todo_id="scientific_interpretation",
                            depends_on=(data_todo_id,),
                            question="Interpret the source facts if the Coordinator dispatches this todo.",
                            why_this_expert="This Expert owns scientific interpretation.",
                            profile_id="ocean_process_expert",
                            expected_outputs=("answer",),
                            done_when="Return the requested interpretation.",
                            budget_tier="quick",
                        ).model_dump(mode="json"),
                    ],
                    "dispatch": [data_todo_id],
                }

            first = await host.router._execute_team_plan(
                workspace_id="ws_follow_up",
                workspace_path=workspace,
                provider_id="provider_fixture",
                task_id=task.task_id,
                payload=assignment("Identify the variables.", "variables"),
                context=ToolExecutionContext(
                    cwd=workspace,
                    request_id=request.request_id,
                    operation_id="operation_first_round",
                ),
            )
            second = await host.router._execute_team_plan(
                workspace_id="ws_follow_up",
                workspace_path=workspace,
                provider_id="provider_fixture",
                task_id=task.task_id,
                payload=assignment(
                    "Return the temporal coverage still needed by the Coordinator.",
                    "temporal_coverage",
                    data_todo_id="data_temporal_coverage",
                ),
                context=ToolExecutionContext(
                    cwd=workspace,
                    request_id=request.request_id,
                    operation_id="operation_second_round",
                ),
            )
            next_request = parse_request(
                {
                    "protocol_version": 2,
                    "request_id": "req_follow_up_next_message",
                    "type": "session.submit",
                    "payload": {"text": "Now return the remaining spatial coverage."},
                    "context": {
                        "session_id": "ses_follow_up",
                        "workspace_id": "ws_follow_up",
                        "client_id": "client_follow_up",
                        "task_id": task.task_id,
                    },
                    "expected_workspace_revision": 1,
                    "expected_task_revision": 0,
                }
            )
            host.store.reserve(next_request, principal="user:local:desktop")
            host.store.mark_in_progress(next_request.request_id)
            third = await host.router._execute_team_plan(
                workspace_id="ws_follow_up",
                workspace_path=workspace,
                provider_id="provider_fixture",
                task_id=task.task_id,
                payload=assignment(
                    "Return the spatial coverage still needed by the Coordinator.",
                    "spatial_coverage",
                    data_todo_id="data_spatial_coverage",
                ),
                context=ToolExecutionContext(
                    cwd=workspace,
                    request_id=next_request.request_id,
                    operation_id="operation_third_round_new_message",
                ),
            )

            first_order = recorder.plans[0].work_orders[0]
            second_order = recorder.plans[1].work_orders[0]
            third_order = recorder.plans[2].work_orders[0]
            assert first_order.job_key == second_order.job_key
            assert second_order.job_key == third_order.job_key
            assert first_order.work_order_id != second_order.work_order_id
            assert second_order.work_order_id != third_order.work_order_id
            assert first_order.session_round == 1
            assert second_order.session_round == 2
            assert third_order.session_round == 3
            assert first_order.budget == second_order.budget == third_order.budget
            assert "Evidence returned for session round 1." in second_order.context_summary
            assert "Evidence returned for session round 2." in third_order.context_summary
            assert "do not repeat or recompute" in second_order.context_summary
            assert "do not repeat or recompute" in third_order.context_summary
            assert "work_plan" not in first
            assert set(first["expert_results"][0]) == {"text", "outputs"}
            assert first["todo_progress"]["data_expert"]["session_round"] == 1
            assert second["todo_progress"]["data_temporal_coverage"]["session_round"] == 2
            assert third["todo_progress"]["data_spatial_coverage"]["session_round"] == 3
        finally:
            host.router.team_orchestrator = original_orchestrator
            await host.close()

    asyncio.run(scenario())


def test_provider_disconnect_after_saved_submission_reuses_exact_expert_result(
    tmp_path,
) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        runtime_started = False

        async def unexpected_runtime(*_args, **_kwargs):
            nonlocal runtime_started
            runtime_started = True
            raise AssertionError("a saved ExpertResult must not launch another model")

        host.team.expert_runtime_factory = unexpected_runtime
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_saved_submission",
                path=tmp_path,
            )
            order = _order("work_saved_submission").model_copy(update={"workspace_revision": 1})
            host.store.create_team_work_order(
                workspace_id="ws_saved_submission",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            host.store.record_workstream_draft(
                order.work_order_id,
                ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.COMPLETED,
                    result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
                    text="This candidate was persisted before the connection stopped.",
                ),
                phase=WorkstreamPhase.RESULT_READY,
            )
            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.INCOMPLETE,
                    result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                    text="The provider disconnected after result persistence.",
                    failure_code=WorkFailureCode.NETWORK_FAILURE,
                    error="connection lost",
                )
            )

            result = await host.team.delegate(
                workspace_id="ws_saved_submission",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=None,
                work_order=order,
            )

            assert runtime_started is False
            assert result.status is WorkStatus.COMPLETED
            assert result.result_origin is ExpertResultOrigin.AGENT_SUBMITTED
            assert result.text == ("This candidate was persisted before the connection stopped.")
            saved = host.store.get_team_work(order.work_order_id)
            assert saved is not None
            assert saved.resume_count == 1
            assert saved.state is WorkStatus.COMPLETED
        finally:
            await host.close()

    asyncio.run(scenario())


def test_receiver_rejects_task_result_owned_only_by_another_task(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(host, workspace_id="ws_task_owned_delivery", path=tmp_path)
            old_task = host.store.create_research_task(
                workspace_id="ws_task_owned_delivery",
                title="Old task",
                task_id="task_owned_delivery_old",
            )
            current_task = host.store.create_research_task(
                workspace_id="ws_task_owned_delivery",
                title="Current task",
                task_id="task_owned_delivery_current",
            )
            old_result = host.task_results.put(
                workspace_id="ws_task_owned_delivery",
                task_id=old_task.task_id,
                kind="report",
                title="Old task report",
                files={"report.md": b"# This belongs only to the old task.\n"},
                materialization_key="old-task-only",
            )
            order = _order("work_owned_delivery_current").model_copy(
                update={
                    "task_id": current_task.task_id,
                    "workspace_revision": host.store.workspace_snapshot(
                        "ws_task_owned_delivery"
                    ).revision,
                }
            )
            binding = _ParticipantBinding(
                workspace_id="ws_task_owned_delivery",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=current_task.task_id,
                work_order=order,
            )
            result = ExpertResult(
                work_order_id=order.work_order_id,
                status=WorkStatus.COMPLETED,
                text="Attempt to reuse an old task result.",
                outputs=(
                    ExpertOutput(
                        item_id="result_old_task",
                        execution_id="exec_old_task",
                        output_name="old.md",
                        size_bytes=1,
                        sha256="0" * 64,
                        result_ref=old_result.ref,
                        result_kind="report",
                    ),
                ),
            )

            with pytest.raises(OceanTeamError, match="not owned by the current task"):
                host.team._validate_result_refs(
                    task_id=binding.task_id,
                    result=result,
                )
        finally:
            await host.close()

    asyncio.run(scenario())


def test_normal_child_final_answer_completes_an_expert_round(tmp_path) -> None:
    """The ordinary final answer is the only Expert handoff boundary."""

    from oceanx.agent import OceanAgentRuntime
    from oceanx.backend.host import OceanBackendHost

    guard_instructions: list[str] = []

    class FinalAnswerEngine:
        def set_system_prompt(self, _prompt: str) -> None:
            return None

        def set_max_turns(self, _max_turns: int | None) -> None:
            return None

        def set_final_response_guard(self, guard) -> None:
            instruction = guard(
                ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="Candidate answer")],
                )
            )
            guard_instructions.append(instruction)

        async def submit_message(self, _text: str, *, request_id: str | None = None):
            yield AssistantTurnComplete(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="The bounded scientific answer is complete.")],
                ),
                usage=UsageSnapshot(input_tokens=20, output_tokens=8),
                turn_id=f"{request_id}:turn:1",
                request_id=request_id,
            )

    async def discussion_runtime_factory(
        services, _workspace_path, _budget, _operation_id_factory
    ) -> OceanAgentRuntime:
        async def close() -> None:
            return None

        return OceanAgentRuntime(
            provider_id=services.provider_id,
            model_id="fixture-model",
            engine=FinalAnswerEngine(),
            base_system_prompt="fixture discussion runtime",
            _close=close,
        )

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        host.team.discussion_runtime_factory = discussion_runtime_factory
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_direct_child_result",
                path=tmp_path,
            )
            order = _order("work_direct_child_result", "scientific_discussion_partner").model_copy(
                update={"workspace_revision": 1}
            )

            result = await host.team.delegate(
                workspace_id="ws_direct_child_result",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=None,
                work_order=order,
            )

            assert result.status is WorkStatus.COMPLETED
            assert result.result_origin.value == "agent_submitted"
            assert result.text == "The bounded scientific answer is complete."
            assert result.outputs == ()
            assert result.failure_code is None
            assert guard_instructions == []

            durable_order = _order(
                "work_direct_child_missing_report", "scientific_discussion_partner"
            ).model_copy(
                update={
                    "workspace_revision": 1,
                    "outcome_intents": ("answer", "report"),
                }
            )
            report_ready_result = await host.team.delegate(
                workspace_id="ws_direct_child_result",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=None,
                work_order=durable_order,
            )
            assert report_ready_result.status is WorkStatus.COMPLETED
            assert report_ready_result.result_origin.value == "agent_submitted"
            assert report_ready_result.expert_decision is ExpertDecision.ACCEPTED
            assert report_ready_result.outputs == ()
            assert report_ready_result.conclusions == ()
            assert report_ready_result.failure_code is None
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("checkpoint_resume", [False, True])
def test_transport_failure_retries_same_work_order_before_coordinator_sees_it(
    tmp_path, monkeypatch, checkpoint_resume,
) -> None:
    """A transient provider failure resumes the same Expert conversation."""

    from oceanx.agent import OceanAgentRuntime
    from oceanx.backend.host import OceanBackendHost

    runtime_calls = 0
    loaded_message_counts: list[int] = []
    resumes = 0
    async def sleep(delay):
        pass
    monkeypatch.setattr("oceanx.model_recovery.asyncio.sleep", sleep)

    class RecoveringEngine:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt
            self._messages: list[ConversationMessage] = []
            self._compaction_generation = 0
            if not checkpoint_resume:
                self.resume_message = None

        async def resume_message(self, *, request_id=None):
            nonlocal resumes
            resumes += 1
            self.attempt = 2
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="Recovered Expert answer.")]),
                usage=UsageSnapshot(input_tokens=12, output_tokens=4),
            )

        @property
        def messages(self) -> list[ConversationMessage]:
            return list(self._messages)

        @property
        def compaction_generation(self) -> int:
            return self._compaction_generation

        def load_messages(
            self,
            messages: list[ConversationMessage],
            *,
            compaction_generation: int = 0,
        ) -> None:
            self._messages = list(messages)
            self._compaction_generation = compaction_generation
            loaded_message_counts.append(len(messages))

        def set_system_prompt(self, _prompt: str) -> None:
            return None

        def set_max_turns(self, _max_turns: int | None) -> None:
            return None

        async def submit_message(self, text: str, *, request_id: str | None = None):
            self._messages.append(ConversationMessage(role="user", content=[TextBlock(text=text)]))
            if self.attempt == 1:
                yield ErrorEvent(
                    message="peer closed an incomplete response body",
                    code="network_failure",
                    retryable=True,
                )
                return
            answer = ConversationMessage(
                role="assistant",
                content=[TextBlock(text="Recovered Expert answer.")],
            )
            self._messages.append(answer)
            yield AssistantTurnComplete(
                message=answer,
                usage=UsageSnapshot(input_tokens=12, output_tokens=4),
                turn_id=f"{request_id}:turn:1",
                request_id=request_id,
            )

    async def discussion_runtime_factory(
        services, _workspace_path, _budget, _operation_id_factory
    ) -> OceanAgentRuntime:
        nonlocal runtime_calls
        runtime_calls += 1

        async def close() -> None:
            return None

        return OceanAgentRuntime(
            provider_id=services.provider_id,
            model_id="fixture-model",
            engine=RecoveringEngine(runtime_calls),
            base_system_prompt="fixture discussion runtime",
            _close=close,
        )

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        host.team.discussion_runtime_factory = discussion_runtime_factory
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_transport_recovery",
                path=tmp_path,
            )
            order = _order("work_transport_recovery", "scientific_discussion_partner").model_copy(
                update={"workspace_revision": 1}
            )

            result = await host.team.delegate(
                workspace_id="ws_transport_recovery",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=None,
                work_order=order,
            )

            assert result.status is WorkStatus.COMPLETED
            assert result.text == "Recovered Expert answer."
            assert runtime_calls == (1 if checkpoint_resume else 2)
            assert resumes == (1 if checkpoint_resume else 0)
            # The first provider call produced no complete assistant turn, so
            # its lone user prompt is not restored and duplicated. The retry
            # remains the same logical Expert but starts from the last safe
            # conversation boundary (empty in this fixture).
            assert loaded_message_counts == []
            record = host.store.get_team_work(order.work_order_id)
            assert record is not None
            assert record.resume_count == (0 if checkpoint_resume else 1)
            # Recovery retained the original Coordinator assignment identity.
            assert record.work_order.work_order_id == order.work_order_id
            assert record.work_order.session_round == order.session_round
        finally:
            await host.close()

    asyncio.run(scenario())


def test_only_transient_transport_failures_are_automatically_continued() -> None:
    """Budgets and semantic failures return to the Coordinator immediately."""

    from oceanx.team.orchestrator import OceanTeamOrchestrator

    def result(code: WorkFailureCode) -> ExpertResult:
        return ExpertResult(
            work_order_id="work_failure_policy",
            status=WorkStatus.INCOMPLETE,
            result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
            summary="The Expert session stopped before the handoff completed.",
            failure_code=code,
            error=code.value,
        )

    assert OceanTeamOrchestrator._should_continue_workstream(
        result(WorkFailureCode.NETWORK_FAILURE)
    )
    assert OceanTeamOrchestrator._should_continue_workstream(
        result(WorkFailureCode.PROVIDER_RATE_LIMIT)
    )
    assert not OceanTeamOrchestrator._should_continue_workstream(
        result(WorkFailureCode.BUDGET_EXHAUSTED)
    )
    assert not OceanTeamOrchestrator._should_continue_workstream(
        result(WorkFailureCode.CONTRACT_FAILURE)
    )
    assert not OceanTeamOrchestrator._should_continue_workstream(result(WorkFailureCode.UNKNOWN))
    # A stream already exhausted its retry allowance; do not multiply it in the outer loop.
    assert not OceanTeamOrchestrator._should_continue_workstream(result(WorkFailureCode.PROVIDER_UNAVAILABLE))


def test_literature_expert_starts_without_python_but_code_still_fails_closed(tmp_path, monkeypatch) -> None:
    from oceanx.backend.host import OceanBackendHost
    from oceanx.sandbox import SandboxUnavailableError

    def unavailable_runtime():
        raise SandboxUnavailableError("fixture interpreter was removed")

    monkeypatch.setattr(
        "oceanx.expert_execution.current_python_runtime",
        unavailable_runtime,
    )

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_runtime_unavailable",
                path=tmp_path,
            )
            started = []

            async def text_only_participant(binding):
                started.append(binding)
                return _ParticipantRunResult(
                    child_id=binding.child_id,
                    state=_ParticipantState.COMPLETED,
                    last_assistant_text="The literature search identified a relevant paper.",
                )

            monkeypatch.setattr(host.team, "_run_participant", text_only_participant)
            order = _order("work_runtime_unavailable", "literature_reproduction_expert").model_copy(update={"workspace_revision": 1})
            result = await host.team.delegate(
                workspace_id="ws_runtime_unavailable",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=None,
                work_order=order,
            )

            assert result.status is WorkStatus.COMPLETED
            assert len(started) == 1
            from oceanx.expert_execution import ExpertRuntimeUnavailableError
            with pytest.raises(ExpertRuntimeUnavailableError, match="fixture interpreter was removed"):
                host.expert_code_execution.require_runtime()
            assert host.store.list_code_executions(order.work_order_id) == ()
        finally:
            await host.close()

    asyncio.run(scenario())


def test_started_code_execution_is_never_left_running_after_internal_failure(
    tmp_path, monkeypatch
) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def fail_after_start(**_kwargs):
        raise OSError("fixture post-start failure")

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_terminal_execution",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_terminal_execution",
                title="Terminal execution fixture",
            )
            order = _order("work_terminal_execution").model_copy(update={"workspace_revision": 1})
            host.store.create_team_work_order(
                workspace_id="ws_terminal_execution",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            monkeypatch.setattr(
                host.expert_code_execution,
                "_run_started_python",
                fail_after_start,
            )

            with pytest.raises(OSError, match="fixture post-start failure"):
                await host.expert_code_execution.run_python(
                    workspace_id="ws_terminal_execution",
                    task_id=task.task_id,
                    work_order_id=order.work_order_id,
                    child_id=order.work_order_id,
                    purpose="Exercise terminal cleanup.",
                    code="print('fixture')",
                )

            records = host.store.list_code_executions(order.work_order_id)
            assert len(records) == 1
            assert records[0].state == "failed"
            assert records[0].ended_at is not None
        finally:
            await host.close()

    asyncio.run(scenario())


def test_agent_job_code_attempts_reuse_the_work_order_tool_safety_budget(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_execution_budget",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_execution_budget",
                title="Execution budget fixture",
            )
            order = _order("work_execution_budget").model_copy(
                update={
                    "workspace_revision": 1,
                    "budget_tier": "quick",
                    "budget": WorkBudget(max_tool_calls=12),
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_execution_budget",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            for index in range(6):
                execution_id = f"codeexec_{index:032x}"
                host.store.start_code_execution(
                    execution_id=execution_id,
                    workspace_id="ws_execution_budget",
                    task_id=task.task_id,
                    work_order_id=order.work_order_id,
                    child_id=order.work_order_id,
                    request={"purpose": "bounded fixture"},
                    started_at=f"2026-08-14T00:00:{index:02d}+00:00",
                )
                host.store.finish_code_execution(
                    execution_id=execution_id,
                    state="succeeded",
                    result={"fixture": True},
                    ended_at=f"2026-08-14T00:00:{index:02d}+00:00",
                )

            assert host.expert_code_execution._next_execution_attempt(order.work_order_id) == (
                7,
                12,
            )
            assert len(host.store.list_code_executions(order.work_order_id)) == 6
        finally:
            await host.close()

    asyncio.run(scenario())


def test_code_attempt_allowance_resets_for_a_new_assignment_to_the_same_expert(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_assignment_budget",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_assignment_budget",
                title="Assignment budget fixture",
            )
            first = _order("work_assignment_first").model_copy(
                update={
                    "workspace_revision": 1,
                    "budget_tier": "quick",
                    "job_key": "stable_data_expert",
                    "budget": WorkBudget(max_tool_calls=12),
                }
            )
            second = _order("work_assignment_second").model_copy(
                update={
                    "workspace_revision": 1,
                    "budget_tier": "quick",
                    "job_key": "stable_data_expert",
                    "budget": WorkBudget(max_tool_calls=12),
                }
            )
            for order in (first, second):
                host.store.create_team_work_order(
                    workspace_id="ws_assignment_budget",
                    work_order=order,
                )
                host.store.mark_team_work_running(order.work_order_id)

            for index in range(6):
                execution_id = f"codeexec_{index + 100:032x}"
                host.store.start_code_execution(
                    execution_id=execution_id,
                    workspace_id="ws_assignment_budget",
                    task_id=task.task_id,
                    work_order_id=first.work_order_id,
                    child_id=first.work_order_id,
                    request={"purpose": "first assignment fixture"},
                    started_at=f"2026-08-19T00:00:{index:02d}+00:00",
                )
                host.store.finish_code_execution(
                    execution_id=execution_id,
                    state="succeeded",
                    result={"fixture": True, "result_fingerprint": f"fingerprint-{index}"},
                    ended_at=f"2026-08-19T00:00:{index:02d}+00:00",
                )

            assert host.expert_code_execution._next_execution_attempt(second.work_order_id) == (
                1,
                12,
            )
        finally:
            await host.close()

    asyncio.run(scenario())


def test_output_ready_checkpoint_survives_same_workstream_resume(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_output_checkpoint",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_output_checkpoint",
                title="Output checkpoint fixture",
            )
            order = _order("work_output_checkpoint").model_copy(
                update={
                    "job_key": "job_output_checkpoint",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(workspace_id="ws_output_checkpoint", work_order=order)
            host.store.mark_team_work_running(order.work_order_id)
            execution = await host.expert_code_execution.run_python(
                workspace_id="ws_output_checkpoint",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Create one reusable output.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR']) / 'result.txt'\n"
                    "out.write_text('durable result', encoding='utf-8')\n"
                    "print(out.name)\n"
                ),
            )
            assert execution.state == "succeeded"
            bundle_path = Path(execution.result_bundle_path)
            assert bundle_path.is_file()
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            assert bundle["fingerprint"] == execution.result_fingerprint
            assert bundle["outputs"][0]["name"] == "result.txt"
            assert bundle["outputs"][0]["sha256"]
            record = host.store.get_team_work(order.work_order_id)
            assert record is not None
            # Producing a file does not let the backend decide that the
            # scientific result is ready; only the Expert's validated result
            # envelope advances that phase.
            assert record.checkpoint.phase is WorkstreamPhase.RUNNING
            assert record.checkpoint.output_ready_execution_ids == (execution.execution_id,)
            assert record.checkpoint.result_bundle.items == ()

            draft = ExpertResult(
                work_order_id=order.work_order_id,
                status=WorkStatus.COMPLETED,
                expert_decision=ExpertDecision.ACCEPTED,
                text="The bounded result is scientifically ready for delivery.",
                evidence_refs=(EvidenceRef(kind="code_execution", ref=execution.execution_id),),
                method_summary="Created and checked one bounded durable output.",
                checks_performed=("The output file was readable.",),
                limitations=("This fixture represents one bounded result.",),
            )
            result_ready = host.store.record_workstream_draft(
                order.work_order_id,
                draft,
                phase=WorkstreamPhase.RESULT_READY,
            )
            assert result_ready.checkpoint.phase is WorkstreamPhase.RESULT_READY
            assert result_ready.checkpoint.draft_result is not None
            assert result_ready.checkpoint.draft_result.text == draft.text
            assert result_ready.checkpoint.draft_result.outputs == ()
            assert result_ready.checkpoint.result_bundle.items == ()

            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.INCOMPLETE,
                    text="The output exists but final submission timed out.",
                    failure_code=WorkFailureCode.BUDGET_EXHAUSTED,
                    error="provider-active budget reached",
                )
            )
            continued = order.model_copy(
                update={"task_goal": "Publish the already-created result without recomputing."}
            )
            resumed = host.store.resume_team_work(
                workspace_id="ws_output_checkpoint",
                work_order=continued,
                max_resumes=1,
            )
            assert resumed.work_order.work_order_id == order.work_order_id
            assert resumed.resume_count == 1
            assert resumed.result is not None
            assert resumed.checkpoint.phase is WorkstreamPhase.INCOMPLETE
            assert resumed.checkpoint.draft_result is not None
            assert resumed.checkpoint.draft_result.text == draft.text
            assert resumed.checkpoint.draft_result.outputs == ()

            delivering = host.store.mark_team_work_running(order.work_order_id)
            assert delivering.checkpoint.phase is WorkstreamPhase.DELIVERING
            probe = await host.expert_code_execution.run_python(
                workspace_id="ws_output_checkpoint",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:2",
                purpose="Read the durable checkpoint manifest, not the filesystem.",
                code=(
                    "import json, os, pathlib\n"
                    "m = json.load(open(os.environ['OCEAN_INPUT_MANIFEST']))\n"
                    "p = pathlib.Path(m['prior_executions'][0]['outputs'][0]['path'])\n"
                    "print(m['prior_executions'][0]['execution_id'])\n"
                    "print(p.read_text(encoding='utf-8'))\n"
                ),
            )
            assert probe.state == "succeeded"
            assert execution.execution_id in probe.stdout
            assert "durable result" in probe.stdout

        finally:
            await host.close()

    asyncio.run(scenario())


def test_result_bundle_is_incremental_and_failure_safe(tmp_path) -> None:
    """Successful outputs accumulate; retries replace only their named item."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_incremental_bundle",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_incremental_bundle",
                title="Incremental result bundle fixture",
            )
            order = _order("work_incremental_bundle").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_incremental_bundle",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_incremental_bundle", work_order=order
            )
            host.store.mark_team_work_running(order.work_order_id)

            first = await host.expert_code_execution.run_python(
                workspace_id="ws_incremental_bundle",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Create the first bounded result.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "(out / 'first.txt').write_text('version one', encoding='utf-8')\n"
                ),
            )
            assert first.state == "succeeded"
            first_ref = TaskResultRef(
                task_id=task.task_id,
                result_id="result_incremental_first",
            )
            host.store.record_workstream_results(
                order.work_order_id,
                (first_ref,),
                output_bindings={
                    "first.txt": (first.execution_id, first_ref),
                },
            )
            after_first = host.store.get_team_work(order.work_order_id)
            assert after_first is not None
            first_item = after_first.checkpoint.result_bundle.items[0]
            assert first_item.output_name == "first.txt"
            assert first_item.execution_id == first.execution_id

            second = await host.expert_code_execution.run_python(
                workspace_id="ws_incremental_bundle",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:2",
                purpose="Add an independent second result.",
                code=(
                    "import json, os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "(out / 'second.json').write_text(json.dumps({'value': 2}), encoding='utf-8')\n"
                ),
            )
            assert second.state == "succeeded"
            second_ref = TaskResultRef(
                task_id=task.task_id,
                result_id="result_incremental_second",
            )
            host.store.record_workstream_results(
                order.work_order_id,
                (second_ref,),
                output_bindings={
                    "second.json": (second.execution_id, second_ref),
                },
            )
            after_second = host.store.get_team_work(order.work_order_id)
            assert after_second is not None
            assert tuple(
                item.output_name for item in after_second.checkpoint.result_bundle.items
            ) == ("first.txt", "second.json")

            replacement = await host.expert_code_execution.run_python(
                workspace_id="ws_incremental_bundle",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:3",
                purpose="Replace only the corrected first result.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "(out / 'first.txt').write_text('corrected version', encoding='utf-8')\n"
                ),
            )
            assert replacement.state == "succeeded"
            host.store.record_workstream_results(
                order.work_order_id,
                (first_ref,),
                output_bindings={
                    "first.txt": (replacement.execution_id, first_ref),
                },
            )
            after_replacement = host.store.get_team_work(order.work_order_id)
            assert after_replacement is not None
            items = {
                item.output_name: item for item in after_replacement.checkpoint.result_bundle.items
            }
            assert set(items) == {"first.txt", "second.json"}
            assert items["first.txt"].item_id != first_item.item_id
            assert items["first.txt"].execution_id == replacement.execution_id
            assert items["first.txt"].sha256 != first_item.sha256
            assert items["second.json"].execution_id == second.execution_id

            failed = await host.expert_code_execution.run_python(
                workspace_id="ws_incremental_bundle",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:4",
                purpose="Save a complete result before a later program error.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "(out / 'partial.txt').write_text('saved before error', encoding='utf-8')\n"
                    "raise RuntimeError('later optional step failed')\n"
                ),
            )
            assert failed.state == "failed"
            partial_ref = TaskResultRef(
                task_id=task.task_id,
                result_id="result_incremental_partial",
            )
            host.store.record_workstream_results(
                order.work_order_id,
                (partial_ref,),
                output_bindings={"partial.txt": (failed.execution_id, partial_ref)},
            )
            after_failure = host.store.get_team_work(order.work_order_id)
            assert after_failure is not None
            assert {item.output_name for item in after_failure.checkpoint.result_bundle.items} == {
                "first.txt",
                "second.json",
                "partial.txt",
            }
            checkpoint_contract = host.team._checkpoint_contract(
                _ParticipantBinding(
                    workspace_id="ws_incremental_bundle",
                    workspace_path=tmp_path,
                    provider_id="provider_fixture",
                    task_id=task.task_id,
                    work_order=order,
                    checkpoint=after_failure.checkpoint,
                )
            )
            failed_execution = next(
                item
                for item in checkpoint_contract["executions"]
                if item["execution_id"] == failed.execution_id
            )
            assert failed_execution["execution_state"] == "failed"
            assert failed_execution["output_files"] == ["partial.txt"]

            completed = host.store.complete_team_work(
                ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.COMPLETED,
                    summary="The Expert completed the bounded result.",
                )
            )
            assert completed.result is not None
            assert completed.result.outputs == after_failure.checkpoint.result_bundle.items
        finally:
            await host.close()

    asyncio.run(scenario())


def test_materialized_result_is_bound_to_bundle_and_survives_resume(tmp_path) -> None:
    """A view ref is checkpointed with its output before final answer delivery."""

    store = RequestStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(timezone.utc).isoformat()
        with store._transaction() as connection:
            connection.execute(
                """
                INSERT INTO workspace_records (workspace_id, path, revision, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                ("ws_bound_result", str(tmp_path), 3, now),
            )
        task = store.create_research_task(
            workspace_id="ws_bound_result",
            title="Bound ResultBundle fixture",
        )
        order = _order("work_bound_result").model_copy(
            update={
                "task_id": task.task_id,
                "job_key": "job_bound_result",
            }
        )
        store.create_team_work_order(
            workspace_id="ws_bound_result",
            work_order=order,
        )
        store.mark_team_work_running(order.work_order_id)
        execution_id = "codeexec_bound_result"
        store.start_code_execution(
            execution_id=execution_id,
            workspace_id="ws_bound_result",
            task_id=task.task_id,
            work_order_id=order.work_order_id,
            child_id=f"{order.work_order_id}:run:1",
            request={"purpose": "Create one interactive section."},
            started_at=now,
        )
        store.finish_code_execution(
            execution_id=execution_id,
            state="succeeded",
            result={
                "output_files": ["section.json", "preview.png"],
                "outputs": [
                    {
                        "name": "section.json",
                        "bytes": 17,
                        "sha256": "a" * 64,
                    },
                    {
                        "name": "preview.png",
                        "bytes": 23,
                        "sha256": "b" * 64,
                    },
                ],
            },
            ended_at=now,
        )
        raw_only = store.get_team_work(order.work_order_id)
        assert raw_only is not None
        assert raw_only.checkpoint.output_ready_execution_ids == (execution_id,)
        assert raw_only.checkpoint.result_bundle.items == ()
        result_ref = TaskResultRef(
            task_id=task.task_id,
            result_id="result_section_26n",
        )
        checkpointed = store.record_workstream_results(
            order.work_order_id,
            (result_ref,),
            output_bindings={
                "section.json": (execution_id, result_ref),
                "preview.png": (execution_id, result_ref),
            },
            result_metadata={
                "kind": "interactive_view",
                "title": "26°N temperature section",
                "summary": "A durable time-depth section.",
            },
        )
        assert checkpointed.checkpoint.result_refs == (result_ref,)
        bundle_item = checkpointed.checkpoint.result_bundle.items[0]
        assert bundle_item.result_ref == result_ref
        assert bundle_item.result_kind == "interactive_view"
        assert bundle_item.title == "26°N temperature section"
        assert bundle_item.output_name == "section.json"
        assert bundle_item.supporting_output_names == ("preview.png",)
        assert len(checkpointed.checkpoint.result_bundle.items) == 1

        interrupted = store.complete_team_work(
            ExpertResult(
                work_order_id=order.work_order_id,
                status=WorkStatus.INCOMPLETE,
                result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                summary="The view is durable; only final delivery was interrupted.",
                failure_code=WorkFailureCode.BUDGET_EXHAUSTED,
                error="provider response interrupted",
            )
        )
        assert interrupted.result is not None
        assert interrupted.result.result_refs == (result_ref,)
        assert interrupted.result.outputs[0].result_ref == result_ref

        resumed = store.resume_team_work(
            workspace_id="ws_bound_result",
            work_order=order,
            max_resumes=1,
        )
        assert resumed.resume_count == 1
        assert resumed.checkpoint.result_refs == (result_ref,)
        assert resumed.checkpoint.result_bundle.items[0].result_ref == result_ref
        assert store.list_code_executions(order.work_order_id)[0].execution_id == execution_id

        follow_up = _order("work_bound_result_follow_up").model_copy(
            update={
                "task_id": task.task_id,
                "job_key": order.job_key,
                "session_round": 2,
                "parent_request_id": "req_bound_result_follow_up",
            }
        )
        inherited = store.create_team_work_order(
            workspace_id="ws_bound_result",
            work_order=follow_up,
        )
        assert inherited.checkpoint.result_refs == (result_ref,)
        assert len(inherited.checkpoint.result_bundle.items) == 1
        assert inherited.checkpoint.result_bundle.items[0].title == ("26°N temperature section")
        store.mark_team_work_running(follow_up.work_order_id)
        follow_up_result = store.complete_team_work(
            ExpertResult(
                work_order_id=follow_up.work_order_id,
                status=WorkStatus.COMPLETED,
                result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
                summary="Added only the missing explanation; prior view remains complete.",
            )
        )
        assert follow_up_result.result is not None
        assert follow_up_result.result.result_refs == (result_ref,)
        assert len(follow_up_result.result.outputs) == 1
    finally:
        store.close()


def test_run_code_returns_candidates_without_publishing_them(tmp_path, monkeypatch) -> None:
    """Expert saves are durable candidates; they are not formal task results."""

    from oceanx import tools as ocean_tools_module
    from oceanx.task_results import TaskResultError

    calls: list[tuple[tuple[TaskResultRef, ...], dict[str, object], dict[str, str]]] = []

    class Store:
        def record_workstream_results(
            self, _work_order_id, refs, *, output_bindings, result_metadata
        ):
            calls.append((refs, output_bindings, result_metadata))
            output_name = next(iter(output_bindings))
            return SimpleNamespace(
                checkpoint=SimpleNamespace(
                    result_bundle=ResultBundle(
                        items=(
                            ExpertOutput(
                                item_id="result_first_view_item",
                                execution_id="codeexec_immediate_checkpoint",
                                output_name=output_name,
                                size_bytes=64,
                                sha256="a" * 64,
                                result_ref=first_ref,
                                result_kind="interactive_view",
                                title="First view",
                            ),
                        )
                    )
                )
            )

    class CodeExecution:
        async def run_python(self, **_kwargs):
            return SimpleNamespace(
                execution_id="codeexec_immediate_checkpoint",
                state="succeeded",
                attempt_number=1,
                discovered_results=(
                    {
                        "schema_version": "ocean-result-event/v1",
                        "kind": "interactive_view",
                        "title": "First view",
                        "view_kind": "profile",
                        "data_output": "first.json",
                    },
                    {
                        "schema_version": "ocean-result-event/v1",
                        "kind": "interactive_view",
                        "title": "Second view",
                        "view_kind": "profile",
                        "data_output": "second.json",
                    },
                ),
                outputs=(
                    {"name": "first.json", "bytes": 64, "sha256": "a" * 64},
                    {"name": "second.json", "bytes": 64, "sha256": "b" * 64},
                ),
                as_payload=lambda: {
                    "execution_id": "codeexec_immediate_checkpoint",
                    "state": "failed",
                    "stdout": "",
                    "stderr": "A later optional step failed.",
                    "attempt_number": 4,
                    "attempt_limit": 12,
                },
            )

    first_ref = TaskResultRef(
        task_id="task_immediate_checkpoint",
        result_id="result_first_view",
    )

    async def materialize(_services, item, *, execution_id):
        assert execution_id == "codeexec_immediate_checkpoint"
        if item.data_output == "first.json":
            return ocean_tools_module._MaterializedResult(
                ref=first_ref,
                kind="interactive_view",
                title="First view",
                summary="",
            )
        assert len(calls) == 1
        raise TaskResultError("second view fixture failed")

    monkeypatch.setattr(
        ocean_tools_module,
        "_materialize_declared_result",
        materialize,
    )

    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws_immediate_checkpoint",
            provider_id="provider_fixture",
            store=Store(),
            task_id="task_immediate_checkpoint",
            work_order_id="work_immediate_checkpoint",
            expert_child_id="work_immediate_checkpoint:run:1",
            expert_code_execution=CodeExecution(),
            expert_deliverables=SimpleNamespace(),
        )
    )
    tool = next(item for item in registry.list_tools() if item.name == "ocean_expert_run_code")
    result = asyncio.run(
        tool.execute(
            tool.input_model(
                purpose="Create two independent views.",
                code="print('fixture')",
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
    )

    assert result.is_error is False
    assert calls == []
    payload = json.loads(result.output)
    assert payload["candidate_result_count"] == 2
    assert [item["title"] for item in payload["candidate_results"]] == [
        "First view",
        "Second view",
    ]
    assert payload["publication_state"] == "awaiting_coordinator_review"
    assert "attempts_remaining" not in payload


def test_run_code_preserves_failed_execution_candidate_without_publishing(
    tmp_path, monkeypatch
) -> None:
    """A declared output survives a later failure but remains unreviewed."""

    from oceanx import tools as ocean_tools_module

    first_ref = TaskResultRef(
        task_id="task_auto_checkpoint",
        result_id="result_auto_checkpoint",
    )
    calls: list[tuple[tuple[TaskResultRef, ...], dict[str, object]]] = []

    class Store:
        def record_workstream_results(
            self, _work_order_id, refs, *, output_bindings, result_metadata
        ):
            assert result_metadata["title"] == "Automatically saved profile"
            assert result_metadata["claims"] == ("The upper ocean is warmer than the deep layer.",)
            calls.append((refs, output_bindings))
            return SimpleNamespace(
                checkpoint=SimpleNamespace(
                    result_bundle=ResultBundle(
                        items=(
                            ExpertOutput(
                                item_id="result_auto_checkpoint_item",
                                execution_id="codeexec_auto_checkpoint",
                                output_name="profile.json",
                                size_bytes=64,
                                sha256="a" * 64,
                                result_ref=first_ref,
                                result_kind="interactive_view",
                                title="Automatically saved profile",
                            ),
                        )
                    )
                )
            )

    class CodeExecution:
        async def run_python(self, **_kwargs):
            return SimpleNamespace(
                execution_id="codeexec_auto_checkpoint",
                state="failed",
                discovered_results=(
                    {
                        "schema_version": "ocean-result-event/v1",
                        "kind": "interactive_view",
                        "view_kind": "profile",
                        "title": "Automatically saved profile",
                        "summary": "Saved before a later optional error.",
                        "conclusions": ["The upper ocean is warmer than the deep layer."],
                        "data_output": "profile.json",
                    },
                ),
                outputs=({"name": "profile.json", "bytes": 64, "sha256": "a" * 64},),
                as_payload=lambda: {
                    "execution_id": "codeexec_auto_checkpoint",
                    "state": "failed",
                    "stdout": "",
                    "stderr": "later optional step failed",
                },
            )

    async def materialize(_services, item, *, execution_id):
        assert execution_id == "codeexec_auto_checkpoint"
        assert item.data_output == "profile.json"
        return ocean_tools_module._MaterializedResult(
            ref=first_ref,
            kind="interactive_view",
            title=item.title,
            summary=item.summary,
            output_names=("profile.json",),
        )

    monkeypatch.setattr(ocean_tools_module, "_materialize_declared_result", materialize)
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws_auto_checkpoint",
            provider_id="provider_fixture",
            store=Store(),
            task_id="task_auto_checkpoint",
            work_order_id="work_auto_checkpoint",
            expert_child_id="work_auto_checkpoint:run:1",
            expert_code_execution=CodeExecution(),
            expert_deliverables=SimpleNamespace(),
        )
    )
    tool = next(item for item in registry.list_tools() if item.name == "ocean_expert_run_code")
    response = asyncio.run(
        tool.execute(
            tool.input_model(
                purpose="Create one complete profile before an optional later step.",
                code="print('fixture')",
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
    )

    assert response.is_error is False
    assert calls == []
    payload = json.loads(response.output)
    assert payload["state"] == "failed"
    assert payload["candidate_result_count"] == 1
    assert payload["candidate_results"][0]["title"] == "Automatically saved profile"
    assert payload["candidate_results"][0]["claims"] == [
        "The upper ocean is warmer than the deep layer."
    ]
    assert payload["publication_state"] == "awaiting_coordinator_review"


@pytest.mark.parametrize("request_id", ["request_candidate_publication", "request_followup"])
def test_coordinator_can_publish_valid_candidate_from_failed_envelope(
    tmp_path, monkeypatch, request_id
) -> None:
    from oceanx import tools as ocean_tools_module

    execution_id = "codeexec_candidate_publication"
    output_name = "profile.json"
    output_id = expert_output_item_id(
        execution_id=execution_id,
        output_name=output_name,
    )
    candidate = ExpertOutput(
        item_id=output_id,
        execution_id=execution_id,
        output_name=output_name,
        size_bytes=64,
        sha256="a" * 64,
        result_kind="interactive_view",
        title="Reviewed profile",
        claims=("The upper layer is warmer.",),
    )
    result = ExpertResult(
        work_order_id="work_candidate_publication",
        status=WorkStatus.COMPLETED,
        result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
        text="The profile analysis is complete.",
        outputs=(candidate,),
        conclusions=(
            ExpertConclusion(
                conclusion_id="conclusion_candidate_publication",
                statement="The upper layer is warmer.",
                basis=FindingBasis.ARTIFACT,
                output_ids=(output_id,),
            ),
        ),
    )
    work_record = SimpleNamespace(
        work_order=SimpleNamespace(
            work_order_id="work_candidate_publication",
            parent_request_id="request_candidate_publication",
        ),
        result=result,
    )
    execution = SimpleNamespace(
        execution_id=execution_id,
        state="failed",
        result={
            "outputs": ({"name": output_name, "bytes": 64, "sha256": "a" * 64},),
            "discovered_results": (
                {
                    "schema_version": "ocean-result-event/v1",
                    "kind": "interactive_view",
                    "view_kind": "profile",
                    "title": "Reviewed profile",
                    "conclusions": ("The upper layer is warmer.",),
                    "data_output": output_name,
                },
            ),
        },
    )
    published_ref = TaskResultRef(
        task_id="task_candidate_publication",
        result_id="result_candidate_publication",
    )
    publications: list[dict[str, object]] = []

    class Store:
        def list_task_team_work(self, **_kwargs):
            return [work_record]

        def get_code_execution(self, candidate_id):
            return execution if candidate_id == execution_id else None

        def record_workstream_results(
            self, work_order_id, refs, *, output_bindings, result_metadata
        ):
            publications.append(
                {
                    "work_order_id": work_order_id,
                    "refs": refs,
                    "output_bindings": output_bindings,
                    "result_metadata": result_metadata,
                }
            )

        def record_research_observation(self, _draft):
            return None

    async def materialize(_services, item, *, execution_id):
        assert _services.expert_result_origin_request_id == request_id
        assert item.title == "Reviewed profile"
        assert execution_id == "codeexec_candidate_publication"
        return ocean_tools_module._MaterializedResult(
            ref=published_ref,
            kind="interactive_view",
            title=item.title,
            summary=item.summary,
            output_names=(output_name,),
        )

    monkeypatch.setattr(ocean_tools_module, "_materialize_declared_result", materialize)
    services = OceanToolServices(
        workspace_id="ws_candidate_publication",
        provider_id="provider_fixture",
        store=Store(),
        task_id="task_candidate_publication",
        expert_deliverables=SimpleNamespace(),
    )
    lead_registry = create_ocean_lead_tool_registry(services)
    publish_tool = next(
        tool for tool in lead_registry.list_tools() if tool.name == "ocean_publish_outputs"
    )
    # This store double tests publication scope; durable receipts are tested separately.
    monkeypatch.setattr(publish_tool, "_model_operation", lambda _context: None)
    resource_tool = lead_registry.get("ocean_resources")
    inventory = resource_tool._task_outputs()
    assert inventory[0]["state"] == "candidate"
    assert inventory[0]["citation"] is None
    assert inventory[0]["origin_request_id"] == "request_candidate_publication"
    response = asyncio.run(
        publish_tool.execute(
            publish_tool.input_model(
                accepted_paths=(f"outputs/{output_name}",),
                review_summary="The claim is bound to the inspected profile and execution evidence.",
            ),
            ToolExecutionContext(cwd=tmp_path, request_id=request_id),
        )
    )

    assert response.is_error is False
    assert len(publications) == 1
    assert publications[0]["refs"] == (published_ref,)
    assert publications[0]["result_metadata"]["coordinator_review"].startswith("The claim is bound")
    work_record.result = result.model_copy(update={
        "outputs": (candidate.model_copy(update={"result_ref": published_ref}),),
    })
    inventory = resource_tool._task_outputs()
    assert inventory[0]["state"] == "published"
    assert "[[result:task_candidate_publication/result_candidate_publication@v1|" in inventory[0]["citation"]
    expert_registry = create_ocean_expert_tool_registry(services)
    assert "ocean_publish_outputs" not in {tool.name for tool in expert_registry.list_tools()}


def test_coordinator_publication_promotes_terminal_candidate_without_recompute(
    tmp_path,
) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_terminal_candidate",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_terminal_candidate",
                title="Terminal candidate fixture",
            )
            order = _order("work_terminal_candidate").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_terminal_candidate",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_terminal_candidate",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            execution = await host.expert_code_execution.run_python(
                workspace_id="ws_terminal_candidate",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Create one immutable candidate.",
                code=(
                    "import pathlib, os\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR'])\n"
                    "(out / 'profile.json').write_text('{}', encoding='utf-8')\n"
                ),
            )
            output_record = execution.outputs[0]
            output_id = expert_output_item_id(
                execution_id=execution.execution_id,
                output_name="profile.json",
            )
            candidate = ExpertOutput(
                item_id=output_id,
                execution_id=execution.execution_id,
                output_name="profile.json",
                size_bytes=int(output_record["bytes"]),
                sha256=str(output_record["sha256"]),
                result_kind="interactive_view",
                title="Candidate profile",
            )
            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=order.work_order_id,
                    status=WorkStatus.COMPLETED,
                    result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
                    text="The candidate is ready for review.",
                    outputs=(candidate,),
                    conclusions=(
                        ExpertConclusion(
                            conclusion_id="conclusion_terminal_candidate",
                            statement="The profile supports the bounded conclusion.",
                            basis=FindingBasis.ARTIFACT,
                            output_ids=(output_id,),
                        ),
                    ),
                )
            )
            before = host.store.get_team_work(order.work_order_id)
            assert before is not None and before.result is not None
            assert before.result.outputs[0].result_ref is None

            result_ref = TaskResultRef(
                task_id=task.task_id,
                result_id="result_terminal_candidate",
            )
            host.store.record_workstream_results(
                order.work_order_id,
                (result_ref,),
                output_bindings={
                    "profile.json": (execution.execution_id, result_ref),
                },
                result_metadata={
                    "kind": "interactive_view",
                    "title": "Candidate profile",
                },
            )
            after = host.store.get_team_work(order.work_order_id)
            assert after is not None and after.result is not None
            assert after.result.outputs[0].result_ref == result_ref
            assert after.result.conclusions[0].output_ids == (output_id,)
            assert len(host.store.list_code_executions(order.work_order_id)) == 1
            links = host.store.list_research_observation_links(task_id=task.task_id)
            assert any(link.relation.value == "supersedes" for link in links)
        finally:
            await host.close()

    asyncio.run(scenario())


def test_normal_expert_answer_is_assembled_with_runtime_bound_conclusions(tmp_path) -> None:
    output = ExpertOutput(
        item_id="result_profile",
        execution_id="codeexec_profile",
        output_name="profile.json",
        size_bytes=64,
        sha256="a" * 64,
        result_kind="interactive_view",
        title="Temperature profile",
        claims=("The thermocline shoals toward the shelf.",),
    )
    checkpoint = WorkstreamCheckpoint(result_bundle=ResultBundle(items=(output,)))

    class Store:
        def get_team_work(self, _work_order_id):
            return SimpleNamespace(checkpoint=checkpoint)

        def list_code_executions(self, _work_order_id):
            return ()

        def list_resource_usage(self, **_kwargs):
            return ()

    orchestrator = OceanTeamOrchestrator.__new__(OceanTeamOrchestrator)
    orchestrator.store = Store()
    binding = _ParticipantBinding(
        workspace_id="ws_runtime_result",
        workspace_path=tmp_path,
        provider_id="provider_fixture",
        task_id="task_runtime_result",
        work_order=_order("work_runtime_result"),
        checkpoint=checkpoint,
    )
    child = _ParticipantRunResult(
        child_id="work_runtime_result:run:1",
        state=_ParticipantState.COMPLETED,
        last_assistant_text="The profile supports a shelfward-shoaling thermocline.",
        usage=_ParticipantUsage(turns=2, tool_calls=1, input_tokens=120, output_tokens=40),
    )

    result = orchestrator._terminal_result(binding, child)

    assert result.status is WorkStatus.COMPLETED
    assert result.text == child.last_assistant_text
    assert result.outputs == (output,)
    assert result.conclusions[0].statement == output.claims[0]
    assert result.conclusions[0].output_ids == (output.item_id,)


def test_expert_text_does_not_complete_a_requested_view(tmp_path) -> None:
    """Ending the Expert round cannot stand in for missing durable outputs."""

    checkpoint = WorkstreamCheckpoint()

    class Store:
        def get_team_work(self, _work_order_id):
            return SimpleNamespace(checkpoint=checkpoint)

        def list_code_executions(self, _work_order_id):
            return ()

        def list_resource_usage(self, **_kwargs):
            return ()

    orchestrator = OceanTeamOrchestrator.__new__(OceanTeamOrchestrator)
    orchestrator.store = Store()
    order = _order("work_missing_requested_outputs").model_copy(
        update={"outcome_intents": ("answer", "interactive_view", "report")}
    )
    binding = _ParticipantBinding(
        workspace_id="ws_missing_requested_outputs",
        workspace_path=tmp_path,
        provider_id="provider_fixture",
        task_id="task_missing_requested_outputs",
        work_order=order,
        checkpoint=checkpoint,
    )
    child = _ParticipantRunResult(
        child_id="work_missing_requested_outputs:run:1",
        state=_ParticipantState.COMPLETED,
        last_assistant_text="I finished the analysis, but no output was published.",
        usage=_ParticipantUsage(turns=1, input_tokens=100, output_tokens=20),
    )

    result = orchestrator._terminal_result(binding, child)

    assert result.status is WorkStatus.INCOMPLETE
    assert result.expert_decision is ExpertDecision.INSUFFICIENT_EVIDENCE
    assert result.outputs == ()
    assert "interactive_view" in result.suggested_next_step
    assert "report" not in result.suggested_next_step
    assert "same Expert session" in result.suggested_next_step


def test_cancelled_expert_keeps_valid_result_from_failed_execution(tmp_path) -> None:
    """A late exception or cancellation cannot erase a framework-saved result."""

    checkpoint = WorkstreamCheckpoint()
    execution = SimpleNamespace(
        execution_id="codeexec_failed_after_save",
        work_order_id="work_failed_after_save",
        state="failed",
        result={
            "outputs": ({"name": "profile.json", "bytes": 64, "sha256": "a" * 64},),
            "discovered_results": (
                {
                    "schema_version": "ocean-result-event/v1",
                    "kind": "interactive_view",
                    "view_kind": "profile",
                    "title": "Preserved profile",
                    "conclusions": ("The upper layer is warmer.",),
                    "data_output": "profile.json",
                },
            ),
        },
    )

    class Store:
        def get_team_work(self, _work_order_id):
            return SimpleNamespace(checkpoint=checkpoint)

        def list_code_executions(self, _work_order_id):
            return (execution,)

        def list_resource_usage(self, **_kwargs):
            return ()

    orchestrator = OceanTeamOrchestrator.__new__(OceanTeamOrchestrator)
    orchestrator.store = Store()
    binding = _ParticipantBinding(
        workspace_id="ws_failed_after_save",
        workspace_path=tmp_path,
        provider_id="provider_fixture",
        task_id="task_failed_after_save",
        work_order=_order("work_failed_after_save"),
        checkpoint=checkpoint,
    )
    child = _ParticipantRunResult(
        child_id="work_failed_after_save:run:1",
        state=_ParticipantState.CANCELLED,
        error="request token budget reached",
        usage=_ParticipantUsage(turns=6, input_tokens=469_415, output_tokens=2_000),
    )

    result = orchestrator._terminal_result(binding, child)

    assert result.status is WorkStatus.CANCELLED
    assert [output.title for output in result.outputs] == ["Preserved profile"]
    assert result.outputs[0].execution_id == execution.execution_id
    assert result.conclusions[0].statement == "The upper layer is warmer."
    assert result.conclusions[0].output_ids == (result.outputs[0].item_id,)
    assert result.evidence_refs == (EvidenceRef(kind="code_execution", ref=execution.execution_id),)


def test_expert_receives_dataset_context_before_first_model_turn(tmp_path, monkeypatch) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(host, workspace_id="ws_context", path=tmp_path)
            task_id = "task_prepared_context"
            host.store.create_research_task(
                workspace_id="ws_context", task_id=task_id, title="Context test"
            )
            context = {
                "sources": [{
                    "handle": "source_1", "path": "/data/chlorophyll.nc",
                    "inspection": "ready", "dimensions": {"time": 365},
                    "data_variables": [{"name": "chlorophyll", "attrs": {"units": "mg m-3"}}],
                }],
            }
            source = SimpleNamespace(manifest=context["sources"][0])
            events = []

            async def prepare_context(**kwargs):
                assert kwargs["sources"] == (source,)
                assert kwargs["task_id"] == task_id
                events.append("prepared")
                return context

            async def participant(binding):
                assert events == ["prepared"]
                assert binding.analysis_context == context
                # Exercise the real prompt builder, not just the binding.
                spec = host.team._participant_spec(binding)
                assert "/data/chlorophyll.nc" in spec.prompt
                assert "chlorophyll" in spec.prompt and "mg m-3" in spec.prompt
                assert "OCEAN_INPUT_MANIFEST" in spec.prompt
                assert "first code execution prepares" not in spec.prompt
                events.append("started")
                return _ParticipantRunResult(
                    child_id=binding.child_id, state=_ParticipantState.COMPLETED,
                    last_assistant_text="Dataset metadata received without discovery calls.",
                )

            monkeypatch.setattr(host.expert_code_execution, "resolve_work_order_sources", lambda **kw: (source,))
            monkeypatch.setattr(host.expert_code_execution, "get_task_dataset_context", prepare_context)
            monkeypatch.setattr(host.team, "_run_participant", participant)
            order = _order("work_prepared_context").model_copy(update={"workspace_revision": 1})
            result = await host.team.delegate(
                workspace_id="ws_context", workspace_path=tmp_path,
                provider_id="provider_fixture", task_id=task_id, work_order=order,
            )
            assert result.status is WorkStatus.COMPLETED
            assert events == ["prepared", "started"]
            assert host.store.list_code_executions(order.work_order_id) == ()
        finally:
            await host.close()

    asyncio.run(scenario())


def test_participant_dataset_context_keeps_schema_but_drops_bulky_attrs() -> None:
    context = {
        "schema_version": "ocean-analysis-context/v1",
        "context_id": "datasetctx_fixture",
        "scope": "task",
        "task_id": "task_context_fixture",
        "sources": [
            {
                "handle": "CMEMS1",
                "path": "/data/cmems",
                "inspection": "ready",
                "dimensions": {"time": 365, "depth": 50},
                "attrs": {"history": "very large and irrelevant"},
                "coordinates": [
                    {
                        "name": "depth",
                        "dims": ["depth"],
                        "shape": [50],
                        "dtype": "float32",
                        "coordinate_role": "depth",
                        "extent": [0.49, 5727.0],
                        "attrs": {"units": "m", "positive": "down", "noise": "drop"},
                    }
                ],
                "members": [
                    {
                        "path": "/data/cmems/temp.zarr",
                        "format": "zarr",
                        "inspection": "ready",
                        "attrs": {"history": "drop"},
                        "data_variables": [
                            {
                                "name": "temp",
                                "dims": ["time", "depth", "lat", "lon"],
                                "shape": [365, 50, 63, 87],
                                "dtype": "float32",
                                "attrs": {
                                    "units": "degC",
                                    "standard_name": "sea_water_potential_temperature",
                                    "history": "drop",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    compact = OceanTeamOrchestrator._participant_analysis_context(context)

    assert compact["context_id"] == "datasetctx_fixture"
    source = compact["sources"][0]
    assert "attrs" not in source
    assert source["coordinates"][0]["units"] == "m"
    assert "noise" not in source["coordinates"][0]
    variable = source["members"][0]["data_variables"][0]
    assert variable["units"] == "degC"
    assert variable["standard_name"] == "sea_water_potential_temperature"


def test_exact_retry_reuses_formal_execution_without_starting_python(tmp_path) -> None:
    """A response retry returns the saved execution instead of recomputing it."""

    task_id = "task_reuse_formal_execution"
    work_order_id = "work_reuse_formal_execution"
    execution_id = "codeexec_reuse_formal_execution"
    task_root = tmp_path / "task"
    work_root = task_root / "experts" / "job_reuse_formal_execution"
    execution_root = work_root / "executions" / execution_id
    code_root = execution_root / "code"
    code_root.mkdir(parents=True)
    code = "print('saved result')\n"
    (code_root / "analysis.py").write_text(code, encoding="utf-8")
    result_bundle_path = work_root / "result-bundles" / f"{execution_id}.json"
    result_bundle_path.parent.mkdir()
    result_bundle_path.write_text("{}", encoding="utf-8")

    result_ref = TaskResultRef(
        task_id=task_id,
        result_id="result_reuse_formal_execution",
    )
    order = _order(work_order_id).model_copy(
        update={"task_id": task_id, "job_key": "job_reuse_formal_execution"}
    )
    work = TeamWorkRecord(
        workspace_id="ws_reuse_formal_execution",
        work_order=order,
        state=WorkStatus.RUNNING,
        result=None,
        checkpoint=WorkstreamCheckpoint(
            phase=WorkstreamPhase.DELIVERING,
            successful_execution_ids=(execution_id,),
            output_ready_execution_ids=(execution_id,),
            result_bundle=ResultBundle(
                items=(
                    ExpertOutput(
                        item_id="result_item_reuse_formal_execution",
                        execution_id=execution_id,
                        output_name="section.json",
                        size_bytes=17,
                        sha256="a" * 64,
                        result_ref=result_ref,
                    ),
                )
            ),
            result_refs=(result_ref,),
        ),
        resume_count=1,
        created_at="2026-08-22T00:00:00Z",
        updated_at="2026-08-22T00:01:00Z",
    )
    execution = CodeExecutionRecord(
        execution_id=execution_id,
        workspace_id="ws_reuse_formal_execution",
        task_id=task_id,
        work_order_id=work_order_id,
        child_id=f"{work_order_id}:run:1",
        state="succeeded",
        request={"purpose": "Create the saved result."},
        result={
            "returncode": 0,
            "stdout": "saved result\n",
            "stderr": "",
            "duration_seconds": 1.0,
            "output_files": ["section.json"],
            "outputs": [{"name": "section.json", "bytes": 17, "sha256": "a" * 64}],
            "output_bytes": 17,
            "limit_trigger": None,
            "code_path": str(work_root / "analysis.py"),
            "work_root": str(work_root),
            "result_bundle_path": str(result_bundle_path),
            "result_fingerprint": "b" * 64,
            "attempt_number": 1,
            "attempt_limit": 12,
        },
        started_at="2026-08-22T00:00:00Z",
        ended_at="2026-08-22T00:00:01Z",
    )

    class Store:
        def get_research_task(self, candidate_task_id):
            assert candidate_task_id == task_id
            return SimpleNamespace(
                task_id=task_id,
                workspace_id="ws_reuse_formal_execution",
            )

        def get_team_work(self, candidate_work_order_id):
            assert candidate_work_order_id == work_order_id
            return work

        def list_agent_job_code_executions(
            self, *, workspace_id, task_id: str, parent_request_id, job_key
        ):
            assert workspace_id == "ws_reuse_formal_execution"
            assert task_id == "task_reuse_formal_execution"
            assert parent_request_id == order.parent_request_id
            assert job_key == order.job_key
            return (execution,)

    class TaskWorkspaces:
        def ensure_task_root(self, candidate_task_id):
            assert candidate_task_id == task_id
            return task_root

    service = ExpertCodeExecutionService.__new__(ExpertCodeExecutionService)
    service.store = Store()
    service.task_workspaces = TaskWorkspaces()
    service._runtime = None
    service._runtime_error = "runtime must not be consulted for a formal retry"

    reused = asyncio.run(
        service.run_python(
            workspace_id="ws_reuse_formal_execution",
            task_id=task_id,
            work_order_id=work_order_id,
            child_id=f"{work_order_id}:run:2",
            purpose="Retry delivery of the saved result.",
            code=code,
        )
    )

    assert reused.execution_id == execution_id
    assert reused.reused_existing_execution is True
    assert reused.result_fingerprint == "b" * 64


def test_expert_download_script_persists_for_next_analysis_call(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario():
        async def serve(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\n1,2,3\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            workspace_id = "ws_script_download"
            await _open_test_workspace(host, workspace_id=workspace_id, path=tmp_path)
            task = host.store.create_research_task(workspace_id=workspace_id, title="Download fixture")
            order = _order("work_script_download").model_copy(update={
                "task_id": task.task_id,
                "workspace_revision": host.store.workspace_snapshot(workspace_id).revision,
            })
            host.store.create_team_work_order(workspace_id=workspace_id, work_order=order)
            host.store.mark_team_work_running(order.work_order_id)
            port = server.sockets[0].getsockname()[1]
            common = {"workspace_id": workspace_id, "task_id": task.task_id,
                      "work_order_id": order.work_order_id, "child_id": f"{order.work_order_id}:run:1"}
            downloaded = await host.expert_code_execution.run_python(
                **common, purpose="Download a small public fixture using Python.",
                code=(
                    "import os, pathlib, urllib.request\n"
                    "folder = pathlib.Path(os.environ['OCEAN_WORK_DIR']) / 'downloads'\n"
                    "folder.mkdir(exist_ok=True)\n"
                    f"with urllib.request.urlopen('http://127.0.0.1:{port}/data.csv', timeout=3) as r:\n"
                    "    (folder / 'data.csv').write_bytes(r.read())\n"
                    "print('downloaded fixture')\n"
                ),
            )
            assert downloaded.state == "succeeded", downloaded.stderr
            # The server is gone: the next call must use the durable download.
            server.close()
            await server.wait_closed()
            analyzed = await host.expert_code_execution.run_python(
                **common, purpose="Analyze the cached download without transferring it again.",
                code=(
                    "import os, pathlib\n"
                    "source = pathlib.Path(os.environ['OCEAN_WORK_DIR']) / 'downloads' / 'data.csv'\n"
                    "assert sum(map(int, source.read_text().strip().split(','))) == 6\n"
                    "print('sum=6')\n"
                ),
            )
            assert analyzed.state == "succeeded", analyzed.stderr
            assert "sum=6" in analyzed.stdout
            assert downloaded.work_root == analyzed.work_root
        finally:
            server.close()
            await server.wait_closed()
            await host.close()

    asyncio.run(scenario())


def test_followup_work_order_reuses_same_expert_session_evidence(tmp_path) -> None:
    """A Coordinator delta gets prior evidence without replaying the old model loop."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_agent_job_memory",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_agent_job_memory",
                title="Agent job memory fixture",
            )
            revision = host.store.workspace_snapshot("ws_agent_job_memory").revision
            first = _order("work_agent_job_memory_1").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_agent_job_memory",
                    "session_round": 1,
                    "workspace_revision": revision,
                }
            )
            host.store.create_team_work_order(workspace_id="ws_agent_job_memory", work_order=first)
            host.store.mark_team_work_running(first.work_order_id)
            execution = await host.expert_code_execution.run_python(
                workspace_id="ws_agent_job_memory",
                task_id=task.task_id,
                work_order_id=first.work_order_id,
                child_id=f"{first.work_order_id}:run:1",
                purpose="Establish one reusable scientific fact.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR']) / 'facts.json'\n"
                    "out.write_text('{\"temperature_unit\": \"degrees_C\"}', encoding='utf-8')\n"
                    "print('temperature_unit=degrees_C')\n"
                ),
            )
            host.store.complete_team_work(
                ExpertResult(
                    work_order_id=first.work_order_id,
                    status=WorkStatus.COMPLETED,
                    summary="The temperature unit is degrees_C.",
                    evidence_refs=(EvidenceRef(kind="code_execution", ref=execution.execution_id),),
                )
            )

            second = _order("work_agent_job_memory_2").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": first.job_key,
                    "session_round": 2,
                    "parent_request_id": "req_agent_job_memory_next_message",
                    "task_goal": "Return the exact previously established unit.",
                    "workspace_revision": revision,
                }
            )
            host.store.create_team_work_order(workspace_id="ws_agent_job_memory", work_order=second)
            host.store.mark_team_work_running(second.work_order_id)
            binding = _ParticipantBinding(
                workspace_id="ws_agent_job_memory",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=task.task_id,
                work_order=second,
            )
            checkpoint = host.team._checkpoint_contract(binding)
            assert checkpoint["execution_scope"] == "agent_job"
            assert [item["execution_id"] for item in checkpoint["executions"]] == [
                execution.execution_id
            ]
            assert "temperature_unit=degrees_C" in checkpoint["executions"][0]["stdout_excerpt"]
            before = host.store.list_agent_job_code_executions(
                workspace_id="ws_agent_job_memory", task_id=task.task_id,
                parent_request_id=second.parent_request_id, job_key=second.job_key,
            )
            saved = host.expert_code_execution.read_expert_file(
                workspace_id="ws_agent_job_memory", task_id=task.task_id,
                work_order_id=second.work_order_id,
                path=checkpoint["executions"][0]["logs"]["stdout"],
            )
            assert "temperature_unit=degrees_C" in saved["content"]
            assert host.store.list_agent_job_code_executions(
                workspace_id="ws_agent_job_memory", task_id=task.task_id,
                parent_request_id=second.parent_request_id, job_key=second.job_key,
            ) == before

            reused = await host.expert_code_execution.run_python(
                workspace_id="ws_agent_job_memory",
                task_id=task.task_id,
                work_order_id=second.work_order_id,
                child_id=f"{second.work_order_id}:run:1",
                purpose="Read one omitted exact value from durable prior evidence.",
                code=(
                    "import json, os, pathlib\n"
                    "m = json.load(open(os.environ['OCEAN_INPUT_MANIFEST']))\n"
                    "prior = m['prior_executions'][0]\n"
                    "print(prior['origin_work_order_id'])\n"
                    "print(pathlib.Path(prior['logs']['stdout']).read_text(encoding='utf-8'))\n"
                    "print(pathlib.Path(prior['outputs'][0]['path']).read_text(encoding='utf-8'))\n"
                ),
            )
            # A follow-up assignment keeps the Expert's durable evidence and
            # work root, but receives its own bounded execution allowance.
            assert reused.attempt_number == 1
            assert reused.work_root == execution.work_root
            assert Path(reused.result_bundle_path).is_file()
            assert first.work_order_id in reused.stdout
            assert "temperature_unit=degrees_C" in reused.stdout
            assert '"temperature_unit": "degrees_C"' in reused.stdout

            scoped = host.store.list_agent_job_code_executions(
                workspace_id="ws_agent_job_memory",
                task_id=task.task_id,
                parent_request_id=first.parent_request_id,
                job_key="job_agent_job_memory",
            )
            assert [item.execution_id for item in scoped] == [
                execution.execution_id,
                reused.execution_id,
            ]
            assert (
                host.store.list_agent_job_code_executions(
                    workspace_id="ws_agent_job_memory",
                    task_id=task.task_id,
                    parent_request_id=first.parent_request_id,
                    job_key="job_other_expert",
                )
                == ()
            )
        finally:
            await host.close()

    asyncio.run(scenario())


def test_sibling_expert_reuses_task_result_without_inheriting_private_memory(
    tmp_path,
) -> None:
    """A downstream Expert sees immutable outputs, not the upstream transcript."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_shared_team_result",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_shared_team_result",
                title="Shared team result fixture",
            )
            revision = host.store.workspace_snapshot("ws_shared_team_result").revision
            producer = _order("work_shared_result_producer", "ocean_process_expert").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_shared_result_producer",
                    "todo_id": "todo_shared_result_producer",
                    "workspace_revision": revision,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_shared_team_result", work_order=producer
            )
            host.store.mark_team_work_running(producer.work_order_id)
            execution = await host.expert_code_execution.run_python(
                workspace_id="ws_shared_team_result",
                task_id=task.task_id,
                work_order_id=producer.work_order_id,
                child_id=producer.work_order_id,
                purpose="Create a checked result for a downstream visualization Expert.",
                code=(
                    "import os, pathlib\n"
                    "out = pathlib.Path(os.environ['OCEAN_OUTPUT_DIR']) / 'result.md'\n"
                    "out.write_text('# Shared result\\n\\nChecked value: 42.\\n', encoding='utf-8')\n"
                    "print('private producer log must not enter sibling memory')\n"
                ),
            )
            assert execution.state == "succeeded"
            shared_ref = TaskResultRef(
                task_id=task.task_id,
                result_id="result_shared_result_producer",
            )
            host.store.record_workstream_results(
                producer.work_order_id,
                (shared_ref,),
                output_bindings={
                    "result.md": (execution.execution_id, shared_ref),
                },
            )

            consumer = _order(
                "work_shared_result_consumer", "visualization_communication_expert"
            ).model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_shared_result_consumer",
                    "todo_id": "todo_shared_result_consumer",
                    "depends_on": (producer.todo_id,),
                    "workspace_revision": revision,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_shared_team_result", work_order=consumer
            )
            host.store.mark_team_work_running(consumer.work_order_id)
            binding = _ParticipantBinding(
                workspace_id="ws_shared_team_result",
                workspace_path=tmp_path,
                provider_id="provider_fixture",
                task_id=task.task_id,
                work_order=consumer,
            )
            checkpoint = host.team._checkpoint_contract(binding)
            assert checkpoint["executions"] == []
            assert checkpoint["shared_results"] == [
                {
                    "execution_id": execution.execution_id,
                    "origin_work_order_id": producer.work_order_id,
                    "origin_profile_id": producer.profile_id,
                    "origin_role": producer.semantic_role,
                    "purpose": "Create a checked result for a downstream visualization Expert.",
                    "execution_state": "succeeded",
                    "output_files": ["result.md"],
                    "result_bundle_path": execution.result_bundle_path,
                    "result_fingerprint": execution.result_fingerprint,
                }
            ]
            assert (
                host.store.list_agent_job_code_executions(
                    workspace_id="ws_shared_team_result",
                    task_id=task.task_id,
                    parent_request_id=consumer.parent_request_id,
                    job_key=consumer.job_key,
                )
                == ()
            )

            shared_output = host.expert_deliverables._execution_output(
                workspace_id="ws_shared_team_result",
                task_id=task.task_id,
                work_order_id=consumer.work_order_id,
                execution_id=execution.execution_id,
                output_name="result.md",
            )
            assert shared_output.read_text(encoding="utf-8").endswith("Checked value: 42.\n")
            # The immutable shared_results entry above is now the complete
            # provenance boundary.  Result consumers do not republish another
            # Expert's output merely to attach publisher metadata.
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_independent_review_forwards_full_result_and_reads_only_assigned_evidence(tmp_path, partial):
    from oceanx.backend.host import OceanBackendHost
    from oceanx.expert_execution import ExpertCodeExecutionError

    async def scenario():
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(host, workspace_id="ws_review", path=tmp_path)
            task = host.store.create_research_task(workspace_id="ws_review", title="Review")
            service = host.expert_code_execution
            producer = _order("work_author", "ocean_process_expert").model_copy(update={
                "task_id": task.task_id, "job_key": "job_author", "expert_key": "author",
                "workspace_revision": host.store.workspace_snapshot("ws_review").revision,
            })
            host.store.create_team_work_order(workspace_id="ws_review", work_order=producer)
            host.store.mark_team_work_running(producer.work_order_id)
            result = ExpertResult(
                work_order_id=producer.work_order_id,
                status=WorkStatus.INCOMPLETE if partial else WorkStatus.COMPLETED,
                # Long text must not silently lose its final caveat in a capsule.
                text="Evidence paragraph. " * 350 + "Unresolved flux attribution.",
                outputs=(ExpertOutput(
                    item_id="result_review_fixture", execution_id="codeexec_review_fixture",
                    output_name="evidence.nc", size_bytes=10, sha256="a" * 64,
                    title="Original evidence", summary="Candidate, not yet independently verified",
                ),),
                error="Model service unavailable" if partial else None,
                result_origin=(ExpertResultOrigin.BACKEND_RECOVERED if partial
                               else ExpertResultOrigin.AGENT_SUBMITTED),
            )
            root = service.task_workspaces.expert_session_root(task.task_id, producer.job_key)
            code = root / "executions" / "codeexec_review_fixture" / "code" / "analysis.py"
            code.parent.mkdir(parents=True, exist_ok=True)
            code.write_text("weighted_mean = sum(values * weights) / sum(weights)\n")
            private = root / "private-transcript.json"
            private.write_text("private reasoning must not be inherited")
            rawlog = code.parent.parent / "logs" / "stdout.txt"
            rawlog.parent.mkdir()
            rawlog.write_text("large private log content is not injected")
            now = datetime.now(timezone.utc).isoformat()
            host.store.start_code_execution(
                execution_id="codeexec_review_fixture", workspace_id="ws_review",
                task_id=task.task_id, work_order_id=producer.work_order_id,
                child_id=producer.work_order_id, request={"purpose": "calculation"}, started_at=now,
            )
            host.store.finish_code_execution(
                execution_id="codeexec_review_fixture", state="succeeded",
                result={"work_root": str(root), "stdout": rawlog.read_text()}, ended_at=now,
            )
            host.store.complete_team_work(result)
            foreign_task = host.store.create_research_task(workspace_id="ws_review", title="Other task")
            foreign = producer.model_copy(update={
                "work_order_id": "work_other_task", "task_id": foreign_task.task_id,
                "job_key": "job_other_task",
            })
            host.store.create_team_work_order(workspace_id="ws_review", work_order=foreign)
            host.store.complete_team_work(ExpertResult(
                work_order_id=foreign.work_order_id, status=WorkStatus.COMPLETED,
                text="Other task must not replace the declared dependency",
            ))
            reviewer = _order("work_review", "ocean_process_expert").model_copy(update={
                "task_id": task.task_id, "job_key": "job_review", "expert_key": "physical_review",
                "review": True, "depends_on": (producer.todo_id,),
                "workspace_revision": producer.workspace_revision,
            })
            host.store.create_team_work_order(workspace_id="ws_review", work_order=reviewer)
            evidence = service.review_evidence(reviewer.work_order_id)
            assert evidence[0]["result"] == result.coordinator_payload()
            assert evidence[0]["interruption"] == result.error
            assert evidence[0]["round_state"] == result.status.value
            binding = _ParticipantBinding(
                workspace_id="ws_review", workspace_path=tmp_path, provider_id="fixture",
                task_id=task.task_id, work_order=reviewer,
            )
            spec = host.team._participant_spec(binding)
            assert result.text in spec.prompt
            assert "private reasoning" not in spec.prompt
            assert code.read_text() == service.read_expert_file(
                workspace_id="ws_review", task_id=task.task_id,
                work_order_id=reviewer.work_order_id, path=str(code),
            )["content"]
            assert "private reasoning" not in json.dumps(evidence)
            assert "Other task" not in json.dumps(evidence)
            assert "large private log content" not in json.dumps(evidence)
            for forbidden in (private, tmp_path / "outside.txt"):
                with pytest.raises(ExpertCodeExecutionError, match="outside"):
                    service.read_expert_file(
                        workspace_id="ws_review", task_id=task.task_id,
                        work_order_id=reviewer.work_order_id, path=str(forbidden),
                    )
            # Ordinary dependents still cannot read another Expert's private code.
            ordinary = reviewer.model_copy(update={"work_order_id": "work_ordinary", "review": False})
            host.store.create_team_work_order(workspace_id="ws_review", work_order=ordinary)
            assert service.review_evidence(ordinary.work_order_id) == []
            with pytest.raises(ExpertCodeExecutionError, match="outside"):
                service.read_expert_file(
                    workspace_id="ws_review", task_id=task.task_id,
                    work_order_id=ordinary.work_order_id, path=str(code),
                )
            assert host.team._expert_session_key(
                workspace_id="ws_review", task_id=task.task_id, work_order=reviewer,
            ) != host.team._expert_session_key(
                workspace_id="ws_review", task_id=task.task_id, work_order=producer,
            )
            assert host.store.get_team_work(producer.work_order_id).result == result
            # A pending newer round must not silently fall back to old completion.
            pending = producer.model_copy(update={"work_order_id": "work_author_followup", "session_round": 2})
            host.store.create_team_work_order(workspace_id="ws_review", work_order=pending)
            assert service.review_evidence(reviewer.work_order_id)[0]["result"] is None
        finally:
            await host.close()

    asyncio.run(scenario())


def test_review_assignment_requires_explicit_dependencies_and_identity():
    todo = dict(
        todo_id="review", question="Check the numerical evidence", why_this_expert="Physical review",
        profile_id="ocean_process_expert", expected_outputs=("answer",), done_when="Report checks",
        review=True,
    )
    with pytest.raises(ValidationError, match="review requires"):
        OceanTodoInput(**todo)
    parsed = OceanTodoInput(**todo, depends_on=("analysis",), expert_key="physical_review")
    assert parsed.review is True
    order = _order("work_review_schema").model_dump()
    order.update(review=True, depends_on=("analysis",), expert_key="physical_review")
    assert WorkOrder.model_validate(order).review is True


def test_identical_stdout_retry_reuses_saved_execution_without_losing_evidence(
    tmp_path,
) -> None:
    """An exact retry returns the first durable execution without a second run."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_no_progress_execution",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_no_progress_execution",
                title="No-progress execution fixture",
            )
            order = _order("work_no_progress_execution").model_copy(
                update={
                    "job_key": "job_no_progress_execution",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_no_progress_execution",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            code = "print('value=1')\n"
            first = await host.expert_code_execution.run_python(
                workspace_id="ws_no_progress_execution",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:1",
                purpose="Produce one bounded result.",
                code=code,
            )
            second = await host.expert_code_execution.run_python(
                workspace_id="ws_no_progress_execution",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:run:2",
                purpose="Confirm the same bounded result.",
                code=code,
            )
            assert first.result_fingerprint == second.result_fingerprint
            assert second.execution_id == first.execution_id
            assert second.reused_existing_execution is True
            assert second.output_files == ()
            assert Path(second.result_bundle_path).is_file()
            assert len(host.store.list_code_executions(order.work_order_id)) == 1
        finally:
            await host.close()

    asyncio.run(scenario())


def test_backend_restart_terminates_orphaned_code_execution(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_execution_recovery",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_execution_recovery",
                title="Execution recovery fixture",
            )
            order = _order("work_execution_recovery").model_copy(update={"workspace_revision": 1})
            host.store.create_team_work_order(
                workspace_id="ws_execution_recovery",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            host.store.start_code_execution(
                execution_id="codeexec_0123456789abcdef0123456789abcdef",
                workspace_id="ws_execution_recovery",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=order.work_order_id,
                request={"purpose": "recovery fixture"},
                started_at="2026-08-14T00:00:00+00:00",
            )

            report = host.expert_recovery.recover()
            assert report.settled_executions == 0
            assert report.failed_executions == 1
            assert report.interrupted_workstreams == 1
            record = host.store.get_code_execution("codeexec_0123456789abcdef0123456789abcdef")
            assert record is not None
            assert record.state == "failed"
            assert record.ended_at is not None
            assert record.result is not None
            assert record.result["failure_code"] == "backend_interrupted"
        finally:
            await host.close()

    asyncio.run(scenario())


def test_backend_restart_settles_complete_execution_manifest_without_rerun(
    tmp_path,
) -> None:
    """A complete filesystem commit wins over a missing SQLite acknowledgement."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_execution_manifest_recovery",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_execution_manifest_recovery",
                title="Execution manifest recovery fixture",
            )
            job_key = "job_execution_manifest_recovery"
            order = _order("work_execution_manifest_recovery").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": job_key,
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_execution_manifest_recovery",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)

            execution_id = "codeexec_1123456789abcdef0123456789abcdef"
            code = "print('saved result')\n"
            work_root = host.task_workspace_projector.expert_session_root(task.task_id, job_key)
            ownership = json.loads((work_root / "agent-workspace.json").read_text(encoding="utf-8"))
            assert ownership["ownership"] == "single_logical_agent"
            assert ownership["publication_authority"] == "coordinator_only"
            sibling_root = host.task_workspace_projector.expert_session_root(
                task.task_id,
                "job_execution_manifest_recovery_sibling",
            )
            assert sibling_root != work_root
            assert (sibling_root / "workspace").is_dir()
            execution_root = work_root / "executions" / execution_id
            code_root = execution_root / "code"
            output_root = execution_root / "outputs"
            logs_root = execution_root / "logs"
            for directory in (code_root, output_root, logs_root):
                directory.mkdir(parents=True, exist_ok=True)
            (work_root / "analysis.py").write_text(code, encoding="utf-8")
            (code_root / "analysis.py").write_text(code, encoding="utf-8")
            (logs_root / "stdout.txt").write_text("saved result\n", encoding="utf-8")
            (logs_root / "stderr.txt").write_text("", encoding="utf-8")
            host.store.start_code_execution(
                execution_id=execution_id,
                workspace_id="ws_execution_manifest_recovery",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=order.work_order_id,
                request={
                    "purpose": "recovery fixture",
                    "code_path": str(work_root / "analysis.py"),
                    "attempt_number": 1,
                    "attempt_limit": 4,
                },
                started_at="2026-08-14T00:00:00+00:00",
            )
            manifest = {
                "schema_version": "ocean-execution-result/v1",
                "execution_id": execution_id,
                "state": "succeeded",
                "returncode": 0,
                "limit_trigger": None,
                "duration_seconds": 0.1,
                "output_files": [],
                "outputs": [],
                "output_bytes": 0,
                "stdout": "saved result\n",
                "stderr": "",
                "logs": {
                    "stdout": str(logs_root / "stdout.txt"),
                    "stderr": str(logs_root / "stderr.txt"),
                },
                "output_root": str(output_root),
                "input_manifest": str(execution_root / "inputs.json"),
                "code_path": str(work_root / "analysis.py"),
                "work_root": str(work_root),
                "attempt_number": 1,
                "attempt_limit": 4,
                "ended_at": "2026-08-14T00:00:01+00:00",
            }
            manifest["fingerprint"] = execution_result_fingerprint(manifest)
            manifest_path = work_root / "result-bundles" / f"{execution_id}.json"
            write_execution_result_manifest(manifest_path, manifest)

            report = host.expert_recovery.recover()
            assert report.settled_executions == 1
            assert report.failed_executions == 0
            assert report.interrupted_workstreams == 1
            recovered = host.store.get_code_execution(execution_id)
            assert recovered is not None and recovered.state == "succeeded"
            assert recovered.result is not None
            assert recovered.result["result_fingerprint"] == manifest["fingerprint"]

            reused = await host.expert_code_execution.run_python(
                workspace_id="ws_execution_manifest_recovery",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=f"{order.work_order_id}:retry",
                purpose="transport retry",
                code=code,
            )
            assert reused.execution_id == execution_id
            assert reused.reused_existing_execution is True
            assert len(host.store.list_code_executions(order.work_order_id)) == 1
        finally:
            await host.close()

    asyncio.run(scenario())


def test_backend_restart_restores_formal_result_bundle_binding(tmp_path) -> None:
    """An atomically written TaskResult remains deliverable without Expert prose."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_result_binding_recovery",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_result_binding_recovery",
                title="Result binding recovery fixture",
            )
            order = _order("work_result_binding_recovery").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_result_binding_recovery",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_result_binding_recovery",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            execution_id = "codeexec_2123456789abcdef0123456789abcdef"
            raw_report = b"# Durable report\n\nThe checked value is 42.\n"
            digest = hashlib.sha256(raw_report).hexdigest()
            host.store.start_code_execution(
                execution_id=execution_id,
                workspace_id="ws_result_binding_recovery",
                task_id=task.task_id,
                work_order_id=order.work_order_id,
                child_id=order.work_order_id,
                request={"purpose": "materialize one report"},
                started_at="2026-08-14T00:00:00+00:00",
            )
            host.store.finish_code_execution(
                execution_id=execution_id,
                state="succeeded",
                result={
                    "output_files": ["report.md"],
                    "outputs": [
                        {
                            "name": "report.md",
                            "bytes": len(raw_report),
                            "sha256": digest,
                        }
                    ],
                },
                ended_at="2026-08-14T00:00:01+00:00",
            )
            result = host.task_results.put(
                workspace_id="ws_result_binding_recovery",
                task_id=task.task_id,
                kind="report",
                title="Durable report",
                summary="Checked value 42.",
                files={"report.md": raw_report},
                work_order_id=order.work_order_id,
                execution_id=execution_id,
                execution_output_names=("report.md",),
                materialization_key="durable-report-fixture",
            )
            assert host.store.get_team_work(order.work_order_id).checkpoint.result_refs == ()

            report = host.expert_recovery.recover()
            assert report.restored_result_bindings == 1
            assert report.interrupted_workstreams == 1
            recovered_work = host.store.get_team_work(order.work_order_id)
            assert recovered_work is not None
            assert recovered_work.result is not None
            assert recovered_work.result.result_origin is ExpertResultOrigin.BACKEND_RECOVERED
            assert recovered_work.result.result_refs == (result.ref,)
            assert recovered_work.result.outputs[0].result_ref == result.ref
            assert OceanRequestRouter._durable_team_result_refs([recovered_work]) == (result.ref,)
        finally:
            await host.close()

    asyncio.run(scenario())


def test_backend_restart_preserves_unsubmitted_expert_conclusion(tmp_path) -> None:
    """Completed Expert prose becomes a partial result instead of wasted tokens."""

    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        state_root = tmp_path / "state"
        host = OceanBackendHost(state_root, write_frame=lambda _frame: None)
        task_id: str
        order: WorkOrder
        try:
            await _open_test_workspace(
                host,
                workspace_id="ws_expert_prose_recovery",
                path=tmp_path,
            )
            task = host.store.create_research_task(
                workspace_id="ws_expert_prose_recovery",
                title="Expert prose recovery fixture",
            )
            task_id = task.task_id
            order = _order("work_expert_prose_recovery").model_copy(
                update={
                    "task_id": task.task_id,
                    "job_key": "job_expert_prose_recovery",
                    "workspace_revision": 1,
                }
            )
            host.store.create_team_work_order(
                workspace_id="ws_expert_prose_recovery",
                work_order=order,
            )
            host.store.mark_team_work_running(order.work_order_id)
            messages = [
                ConversationMessage.from_user_text("Analyze the bounded source."),
                ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text="The checked section shows a persistent subsurface maximum.")
                    ],
                ),
            ]
            session_key = host.team._expert_session_key(
                workspace_id="ws_expert_prose_recovery",
                task_id=task.task_id,
                work_order=order,
            )
            host.store.save_expert_session_checkpoint(
                workspace_id=session_key[0],
                task_scope=session_key[1],
                participant_key=session_key[2],
                job_key=session_key[3],
                work_order_id=order.work_order_id,
                messages=[message.model_dump(mode="json") for message in messages],
                compaction_generation=0,
            )

            report = host.expert_recovery.recover()
            assert report.interrupted_workstreams == 1
            recovered = host.store.get_team_work(order.work_order_id)
            assert recovered is not None
            assert recovered.state is WorkStatus.INCOMPLETE
            assert recovered.result is not None
            assert recovered.result.result_origin is ExpertResultOrigin.BACKEND_RECOVERED
            assert recovered.result.text == messages[-1].text
        finally:
            await host.close()

        restarted = OceanBackendHost(state_root, write_frame=lambda _frame: None)
        try:
            session_key = restarted.team._expert_session_key(
                workspace_id="ws_expert_prose_recovery",
                task_id=task_id,
                work_order=order,
            )
            checkpoint = restarted.store.get_expert_session_checkpoint(
                workspace_id=session_key[0],
                task_scope=session_key[1],
                participant_key=session_key[2],
                job_key=session_key[3],
            )
            assert checkpoint is not None
            restored = ConversationMessage.model_validate(checkpoint.messages[-1])
            assert restored.text == ("The checked section shows a persistent subsurface maximum.")
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_public_deliverables_are_only_view_or_reproducible_report() -> None:
    report = _FrameworkResultEvent(
        kind="report",
        title="Checked result report",
        report_output="report.md",
        conclusions=("The report summarizes the checked analysis.",),
        report_checks=("Recomputed the aggregate independently.",),
        report_limitations=("Result covers only the selected period.",),
    )
    assert report.kind == "report"
    with pytest.raises(ValidationError):
        _FrameworkResultEvent(
            kind="report",
            title="Incomplete report",
        )


def test_result_materialization_identity_ignores_presentation_prose() -> None:
    execution_id = "codeexec_0123456789abcdef0123456789abcdef"
    original = _FrameworkResultEvent(
        kind="interactive_view",
        title="Seasonal chlorophyll",
        summary="First wording",
        view_kind="time_series",
        data_output="seasonal.json",
        units="mg m-3",
    )
    continuation = original.model_copy(
        update={"title": "Chlorophyll seasonal cycle", "summary": "Paraphrased wording"}
    )
    different_result = original.model_copy(update={"data_output": "other.json"})

    assert _result_materialization_key(
        original, execution_id=execution_id
    ) == _result_materialization_key(continuation, execution_id=execution_id)
    assert _result_materialization_key(
        original, execution_id=execution_id
    ) != _result_materialization_key(different_result, execution_id=execution_id)


def test_structured_interactive_views_use_renderer_safe_shapes() -> None:
    metadata = ExpertDeliverableService._validate_structured_data(
        {
            "plot_kind": "time_series",
            "axes": [{"name": "month", "values": ["Jan", "Feb"]}],
            "series": [{"name": "chlorophyll", "units": "mg m-3", "values": [0.2, 0.3]}],
            "spatial_context": {
                "region_key": "example-region",
                "fit_policy": "region_change",
                "features": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"label": "Region A"},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[-92, 20], [-80, 20], [-80, 30], [-92, 30], [-92, 20]]
                                ],
                            },
                        }
                    ],
                },
            },
        },
        expected_kind="time_series",
    )
    assert metadata["point_count"] == 4
    assert metadata["spatial_context"]["region_key"] == "example-region"

    ExpertDeliverableService._validate_structured_data(
        {
            "plot_kind": "hovmoller",
            "axes": [
                {"name": "month", "values": ["Jan", "Feb"]},
                {"name": "depth", "units": "m", "values": [0, 20, 50]},
            ],
            "series": [
                {
                    "name": "chlorophyll",
                    "units": "mg m-3",
                    "values": [0.2, 0.3, 0.1, 0.2, 0.08, 0.1],
                },
            ],
        },
        expected_kind="hovmoller",
    )

    with pytest.raises(ExpertDeliverableError, match="row-major"):
        ExpertDeliverableService._validate_structured_data(
            {
                "plot_kind": "section",
                "axes": [
                    {"name": "distance", "values": [0, 10]},
                    {"name": "depth", "values": [0, 50]},
                ],
                "series": [{"name": "temperature", "values": [20, 18, 16]}],
            },
            expected_kind="section",
        )


def test_layered_scientific_view_contract_preserves_scientific_semantics() -> None:
    payload = {
        "schema_version": "ocean-scientific-view/v1",
        "plot_kind": "ts_diagram",
        "data": {
            "salinity": [35.1, 35.5, 35.8],
            "temperature": [11.0, 19.0, 25.0],
            "depth": [800.0, 150.0, 20.0],
        },
        "axes": {
            "x": {"field": "salinity", "label": "Practical salinity", "range": [34.8, 36.0]},
            "y": {"field": "temperature", "label": "Potential temperature", "units": "°C"},
        },
        "layers": [
            {
                "type": "contour",
                "label": "sigma0",
                "paths": [{"level": 26.0, "points": [[35.0, 10.0], [35.8, 24.0]]}],
                "style": {"color": "#929ca2"},
            },
            {
                "type": "scatter",
                "x": "salinity",
                "y": "temperature",
                "color": "depth",
                "color_scale": "log",
                "label": "Water-column samples",
            },
            {
                "type": "annotation",
                "items": [{"x": 35.8, "y": 25.0, "text": "20 m"}],
            },
        ],
    }

    metadata = ExpertDeliverableService._validate_structured_data(
        payload, expected_kind="ts_diagram"
    )

    assert metadata["renderer_schema"] == "ocean-scientific-view/v1"
    assert metadata["layer_types"] == ["contour", "scatter", "annotation"]
    assert metadata["point_count"] == 11

    invalid = json.loads(json.dumps(payload))
    invalid["layers"][1]["color"] = "missing_depth"
    with pytest.raises(ExpertDeliverableError, match="color field is unknown"):
        ExpertDeliverableService._validate_structured_data(invalid, expected_kind="ts_diagram")


def test_generic_scientific_figure_contract_validates_panels_and_layers() -> None:
    payload = {
        "schema_version": "ocean-scientific-figure/v2",
        "plot_kind": "time_series",
        "data": {
            "month": ["Apr", "May", "Jun"],
            "estimate": [12.0, 18.0, 15.0],
            "lower": [10.0, 16.0, 13.0],
            "upper": [14.0, 20.0, 17.0],
            "x": [0.0, 1.0],
            "y": [0.0, 1.0],
            "u": [0.2, -0.1],
            "v": [0.1, 0.25],
        },
        "layout": {"columns": 2},
        "panels": [
            {
                "id": "estimate",
                "axes": {
                    "x": {"field": "month", "scale": "category"},
                    "y": {"field": "estimate"},
                },
                "layers": [
                    {"type": "band", "x": "month", "y0": "lower", "y1": "upper"},
                    {"type": "line", "x": "month", "y": "estimate"},
                ],
            },
            {
                "id": "vectors",
                "axes": {"x": {"field": "x"}, "y": {"field": "y"}},
                "layers": [
                    {"type": "vector", "x": "x", "y": "y", "u": "u", "v": "v"},
                    {
                        "type": "annotation",
                        "items": [{"x": 1.0, "y": 1.0, "text": "Observed", "arrow": True}],
                    },
                ],
            },
        ],
    }

    metadata = ExpertDeliverableService._validate_structured_data(
        payload, expected_kind="time_series"
    )

    assert metadata["renderer_schema"] == "ocean-scientific-figure/v2"
    assert metadata["panel_count"] == 2
    assert metadata["layer_types"] == ["band", "line", "vector", "annotation"]

    invalid = json.loads(json.dumps(payload))
    invalid["panels"][1]["id"] = "estimate"
    with pytest.raises(ExpertDeliverableError, match="panel ids must be unique"):
        ExpertDeliverableService._validate_structured_data(invalid, expected_kind="time_series")


def test_tool_surfaces_match_each_responsibility(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        base = {
            "workspace_id": "ws_current_team",
            "provider_id": "provider_fixture",
            "store": host.store,
            "artifacts": host.artifact_service,
        }
        lead = create_ocean_lead_tool_registry(
            OceanToolServices(
                **base,
                skill_role="coordinator",
                team_assign_sink=lambda _payload, _context: None,
                paper_selection_sink=lambda _payload, _context: None,
                expert_deliverables=host.expert_deliverables,
            )
        )
        expert = create_ocean_expert_tool_registry(
            OceanToolServices(
                **base,
                skill_role="data_reproducibility_expert",
                expert_code_execution=host.expert_code_execution,
                expert_deliverables=host.expert_deliverables,
            )
        )
        discussion = create_ocean_discussion_tool_registry(
            OceanToolServices(**base, skill_role="scientific_discussion_partner")
        )
        standalone = create_ocean_tool_registry(
            OceanToolServices(**base, skill_role="coordinator")
        )
        assert {tool.name for tool in lead.list_tools()} == {
            "ocean_resources",
            "ocean_list_skills",
            "ocean_load_skill",
            "web_search",
            "ocean_assign",
            "ocean_request_paper_selection",
            "ocean_publish_outputs",
        }
        assert {tool.name for tool in standalone.list_tools()} == {
            "ocean_resources",
            "ocean_list_skills",
            "ocean_load_skill",
            "web_search",
        }
        assert {tool.name for tool in expert.list_tools()} == {
            "ocean_list_skills",
            "ocean_load_skill",
            "ocean_expert_run_code",
            "ocean_read_file",
        }
        assert {tool.name for tool in discussion.list_tools()} == {
            "ocean_list_skills",
            "ocean_load_skill",
        }
        literature_expert = create_ocean_expert_tool_registry(
            OceanToolServices(
                **base,
                skill_capabilities=("web.search",),
                skill_role="literature_reproduction_expert",
            )
        )
        assert {tool.name for tool in literature_expert.list_tools()} == {
            "ocean_list_skills",
            "ocean_load_skill",
            "web_search",
        }
        literature_reader = create_ocean_expert_tool_registry(
            OceanToolServices(
                **base,
                skill_capabilities=("web.search", "jina.reader"),
                skill_role="literature_reproduction_expert",
            )
        )
        assert {tool.name for tool in literature_reader.list_tools()} == {
            "web_search",
            "jina_reader",
            "ocean_list_skills",
            "ocean_load_skill",
        }
    finally:
        import asyncio

        asyncio.run(host.close())


def test_paper_selection_tool_returns_the_researchers_exact_choice(tmp_path) -> None:
    captured: list[dict[str, object]] = []

    async def select(payload, _context):
        captured.append(payload)
        return {
            "selected_paper_ids": ["paper_2"],
            "selected_papers": [payload["papers"][1]],
        }

    registry = create_ocean_lead_tool_registry(
        OceanToolServices(
            workspace_id="ws_paper_selection",
            provider_id="provider_fixture",
            store=SimpleNamespace(),
            paper_selection_sink=select,
        )
    )
    tool = next(
        item for item in registry.list_tools()
        if item.name == "ocean_request_paper_selection"
    )
    result = asyncio.run(
        tool.execute(
            tool.input_model(
                question="Choose the papers to review in full.",
                papers=(
                    {
                        "paper_id": "paper_1",
                        "title": "Shelf circulation",
                        "topic": "Shelf exchange",
                        "evidence_scope": "abstract",
                        "evidence_summary": "Reports the circulation metric used for shelf exchange.",
                        "validation_target": "Compare the reported exchange direction with the task data.",
                    },
                    {
                        "paper_id": "paper_2",
                        "title": "Loop Current variability",
                        "topic": "Heat transport",
                        "evidence_scope": "public_excerpt",
                        "evidence_summary": "Reports Loop Current heat-transport variability.",
                        "validation_target": "Cross-check the task's seasonal temperature structure.",
                    },
                ),
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
    )

    assert result.is_error is False
    assert captured[0]["papers"][1]["paper_id"] == "paper_2"
    assert json.loads(result.output)["selected_paper_ids"] == ["paper_2"]


def test_accepted_expert_result_terminates_the_child_query(tmp_path) -> None:
    """Discussion work returns ordinary text and has no result-formatting tool."""
    registry = create_ocean_discussion_tool_registry(
        OceanToolServices(
            workspace_id="ws_result_boundary",
            provider_id="provider_fixture",
            store=SimpleNamespace(),
            skill_role="scientific_discussion_partner",
        )
    )
    assert {tool.name for tool in registry.list_tools()} == {
        "ocean_list_skills",
        "ocean_load_skill",
    }


def test_result_handoff_cannot_supply_backend_owned_outputs(tmp_path) -> None:
    """The model-visible code tool cannot accept backend-owned output metadata."""
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws_result_bundle",
            provider_id="provider_fixture",
            store=SimpleNamespace(),
            work_order_id="work_result_bundle",
            expert_code_execution=SimpleNamespace(),
        )
    )
    tool = next(item for item in registry.list_tools() if item.name == "ocean_expert_run_code")
    assert set(tool.input_model.model_fields) == {"purpose", "code"}


def test_minimal_coordinator_answer_uses_safe_transport_defaults() -> None:
    result = CoordinatorResult(answer_markdown="The requested conclusion is ready.")

    assert result.decision is CoordinatorDecision.ANSWERED
    assert result.answer_basis is CoordinatorAnswerBasis.GENERAL_KNOWLEDGE
    assert result.evidence_refs == ()
    assert result.deliverable_refs == ()
    assert result.limitations == ()
    assert result.confidence == 0.0


def test_task_result_store_is_task_scoped_and_idempotent(tmp_path) -> None:
    from oceanx.backend.host import OceanBackendHost

    async def scenario() -> None:
        host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
        try:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            await _open_test_workspace(host, workspace_id="ws_task_results", path=workspace)
            first_task = host.store.create_research_task(
                workspace_id="ws_task_results",
                title="First task",
                task_id="task_result_first",
            )
            second_task = host.store.create_research_task(
                workspace_id="ws_task_results",
                title="Second task",
                task_id="task_result_second",
            )

            first = host.task_results.put(
                workspace_id="ws_task_results",
                task_id=first_task.task_id,
                kind="report",
                title="Task-local report",
                files={"report.md": b"# First task\n"},
                materialization_key="same-logical-output",
            )
            repeated = host.task_results.put(
                workspace_id="ws_task_results",
                task_id=first_task.task_id,
                kind="report",
                title="Task-local report",
                files={"report.md": b"# This must not replace the first result\n"},
                materialization_key="same-logical-output",
            )
            second = host.task_results.put(
                workspace_id="ws_task_results",
                task_id=second_task.task_id,
                kind="report",
                title="Task-local report",
                files={"report.md": b"# Second task\n"},
                materialization_key="same-logical-output",
            )

            assert repeated.ref == first.ref
            assert repeated.files == first.files
            assert second.ref != first.ref
            assert host.task_results.list(task_id=first_task.task_id) == (first,)
            assert host.task_results.list(task_id=second_task.task_id) == (second,)
            assert (
                host.task_results.file_path(ref=first.ref, relative_path="report.md").read_text(
                    encoding="utf-8"
                )
                == "# First task\n"
            )
            assert (
                host.task_results.file_path(ref=second.ref, relative_path="report.md").read_text(
                    encoding="utf-8"
                )
                == "# Second task\n"
            )
        finally:
            await host.close()

    asyncio.run(scenario())
