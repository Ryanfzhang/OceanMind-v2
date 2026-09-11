"""Question-led assignments preserve ordinary answers and the existing outputs."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oceanx.expert_execution import ExpertCodeExecutionError, ExpertCodeExecutionService
from oceanx.team.models import (
    AnswerStandard,
    ChildAuthority,
    CoordinatorResult,
    EvidenceRef,
    ExpertOutput,
    ExpertReport,
    ExpertResult,
    ExpertResultOrigin,
    RequiredOutput,
    WorkContinuation,
    WorkOrder,
    WorkStatus,
    WorkstreamCheckpoint,
)
from oceanx.team.orchestrator import (
    OceanTeamOrchestrator,
    _ParticipantBinding,
    _ParticipantRunResult,
    _ParticipantState,
    _ParticipantUsage,
)


def _order(**updates) -> WorkOrder:
    return WorkOrder.model_validate({
        "work_order_id": "work_refined",
        "task_id": "task_refined",
        "todo_id": "todo_refined",
        "parent_request_id": "req_refined",
        "task_goal": "Does the seasonal relationship persist after deseasonalizing?",
        "semantic_role": "Ocean Process & Mechanism Expert",
        "profile_id": "ocean_process_expert",
        "authority": ChildAuthority.EXPERT,
        "workspace_revision": 1,
        **updates,
    })


def _orchestrator(tmp_path, order=None, checkpoint=None):
    checkpoint = checkpoint or WorkstreamCheckpoint()
    orchestrator = object.__new__(OceanTeamOrchestrator)
    orchestrator.store = SimpleNamespace(
        get_team_work=lambda _: SimpleNamespace(checkpoint=checkpoint),
        list_code_executions=lambda _: (),
        list_resource_usage=lambda **_: (),
    )
    binding = _ParticipantBinding(
        workspace_id="ws_refined", workspace_path=tmp_path, provider_id="fixture",
        task_id="task_refined", work_order=order or _order(), checkpoint=checkpoint,
    )
    return orchestrator, binding


def _finish(orchestrator, binding, text, state=_ParticipantState.COMPLETED):
    return orchestrator._terminal_result(binding, _ParticipantRunResult(
        child_id="child_refined", state=state, last_assistant_text=text,
        usage=_ParticipantUsage(turns=1, tool_calls=0, input_tokens=20, output_tokens=10),
    ))


def test_standard_is_canonical_and_does_not_judge_scientific_levels():
    legacy = _order(done_when="Resolve whether seasonality explains the association.")
    assert legacy.answer_standard.sufficient_if.startswith("Resolve whether")
    assert "done_when" not in legacy.model_dump()
    assert "done_when" not in WorkOrder.model_json_schema()["properties"]
    canonical = _order(
        answer_standard={
            "sufficient_if": "Use the new declared standard.",
            "sufficient_level": "causal_attribution", "max_level": "description",
        },
        done_when="Historical wording must not override the canonical field.",
    )
    assert canonical.answer_standard.sufficient_if == "Use the new declared standard."
    # These declarations are for the Coordinator to assess, not a level-order gate.
    assert canonical.answer_standard.max_level == "description"
    assert _order().answer_standard is None


def test_participant_receives_required_scope_and_replaceable_suggestions(tmp_path):
    order = _order(
        question_ref="question_refined", target_node="H1", alternative_nodes=("H2",),
        answer_standard=AnswerStandard(
            sufficient_level="association", sufficient_if="Resolve the seasonal confound.",
            max_level="mechanism_consistent", not_allowed=("Claim causal attribution.",),
        ),
        required_outputs=(RequiredOutput(id="answer", requirement="Answer the user's question."),),
        suggested_path=("Consider a seasonal comparison.",), hints=("Coverage may differ.",),
        mode="continue", session_round=2,
        continuation=WorkContinuation(source_report_ref="work_prior", approved_lead_id="lead_1"),
    )
    orchestrator, binding = _orchestrator(tmp_path, order)
    orchestrator._source_contract = lambda **_: []
    orchestrator._checkpoint_contract = lambda _: {}
    spec = orchestrator._participant_spec(binding)
    payload = json.loads(spec.prompt.rsplit("\n\n", 1)[1])
    for key, value in order.scientific_assignment().items():
        assert payload[key] == value
    assert "optional and replaceable" in spec.prompt
    assert "adds useful discriminating evidence" in spec.prompt
    assert "No separate Test registration or method approval" in spec.prompt
    assert "another code call only for" not in spec.prompt


@pytest.mark.asyncio
async def test_execution_manifest_receives_the_same_scientific_assignment(tmp_path):
    """Exercise real manifest assembly while replacing only sandbox execution."""
    order = _order(
        question_ref="question_refined", target_node="H1", alternative_nodes=("H2",),
        answer_standard={"sufficient_if": "Resolve the observed seasonal conflict."},
        required_outputs=({"id": "answer", "requirement": "A bounded answer."},),
        suggested_path=("Optional seasonal split.",), hints=("Unequal coverage.",),
        continuation={"gap_refs": ["gap_1"], "question_delta": "Check the remaining gap."},
    )
    work_root = tmp_path / "expert"
    work_root.mkdir()
    service = object.__new__(ExpertCodeExecutionService)
    service.store = SimpleNamespace(
        get_research_task=lambda _: SimpleNamespace(workspace_id="ws_refined"),
        get_team_work=lambda _: SimpleNamespace(work_order=order),
        list_request_code_executions=lambda **_: (),
        start_code_execution=lambda **_: None,
    )
    service.task_workspaces = SimpleNamespace(
        expert_session_root=lambda *_: work_root,
        ensure_task_root=lambda _: tmp_path,
    )
    service._reusable_successful_execution = lambda **_: None
    service._next_execution_attempt = lambda _: (1, 64)
    service.require_runtime = lambda: SimpleNamespace(
        environment_name="fixture", version="3.13", requirements=(),
    )
    service.resolve_work_order_sources = lambda **_: ()
    service._agent_job_executions = lambda _: ()
    service.review_evidence = lambda _: ()

    async def context(**_):
        return {}

    async def capture(**kwargs):
        return json.loads(kwargs["input_manifest"].read_text())

    service.get_task_dataset_context = context
    service._run_started_python = capture
    manifest = await service.run_python(
        workspace_id="ws_refined", task_id=order.task_id, work_order_id=order.work_order_id,
        child_id="child_refined", purpose="Address the authorized gap.", code="print('fixture')",
    )
    assert manifest["assignment"] == order.scientific_assignment()
    assert "done_when" not in manifest["assignment"]
    assert (work_root / "analysis.py").read_text() == "print('fixture')"


def test_ordinary_markdown_populates_report_without_submission_or_scientific_verdict(tmp_path):
    orchestrator, binding = _orchestrator(tmp_path)
    text = "The deseasonalized relation is weak. [[output:comparison.nc|Comparison]]"
    result = _finish(orchestrator, binding, text)
    assert result.status is WorkStatus.COMPLETED
    assert result.text == text
    assert result.expert_decision is None
    assert result.report.answer.statement == text
    assert result.report.answer.level is None
    assert result.report.answer.direction is None
    assert result.report.leads == result.report.limitations == ()
    assert result.coordinator_payload() == {"report": {"answer": {"statement": text}}, "outputs": []}
    assert CoordinatorResult(answer_markdown=text).research_outcome is None


def _report_payload():
    return {
        "answer": {
            "statement": "Seasonality explains most of the observed association.",
            "direction": "opposes", "level": "association",
            "evidence_refs": [{"kind": "code_execution", "ref": "codeexec_seasonal"}],
            "open_gaps": ["gap_coverage"],
        },
        "tests": ["test_seasonal"],
        "limitations": [{
            "id": "lim_coverage", "statement": "Coverage differs across seasons.",
            "would_change": "uncertain", "rationale": "The spatial comparison remains limited.",
        }],
        "leads": [{
            "id": "lead_lag", "observation": "The lag differs between regions.",
            "proposed_question": "Does the regional lag persist after controlling season?",
        }],
        "path_deviations": ["Used deseasonalized anomalies instead of the suggested raw correlation."],
        "open_conflicts": ["The regional and basin-scale estimates disagree."],
        "required_outputs_status": [{"id": "answer", "disposition": "partial"}],
    }


@pytest.mark.parametrize("wrapper", ["bare", "report", "fenced"])
def test_optional_structured_report_is_populated_once_and_preserves_open_science(tmp_path, wrapper):
    payload = _report_payload()
    text = json.dumps({"report": payload} if wrapper == "report" else payload)
    if wrapper == "fenced":
        text = "```json\n" + text + "\n```"
    orchestrator, binding = _orchestrator(tmp_path)
    result = _finish(orchestrator, binding, text)
    assert result.status is WorkStatus.COMPLETED  # Open scientific issues are not a receiver gate.
    assert result.report == ExpertReport.model_validate(payload)
    assert result.text == result.report.to_markdown()
    handoff = result.coordinator_payload()
    assert "text" not in handoff
    assert handoff["report"]["leads"][0]["id"] == "lead_lag"
    assert handoff["report"]["answer"]["open_gaps"] == ["gap_coverage"]
    restored = ExpertResult.model_validate_json(result.model_dump_json())
    assert restored.report == result.report
    assert restored.coordinator_payload() == handoff


@pytest.mark.parametrize("format", ["json", "markdown", "explicit_support"])
def test_execution_provenance_survives_report_handoff_without_inventing_claim_support(tmp_path, format):
    orchestrator, binding = _orchestrator(tmp_path)
    execution = SimpleNamespace(execution_id="codeexec_actual", state="succeeded", result={})
    orchestrator.store.list_code_executions = lambda _: (execution,)
    author_refs = [{"kind": "paper", "ref": "paper_explicit"}] if format == "explicit_support" else []
    answer = {"statement": "The observed relation remains uncertain."}
    if author_refs:
        answer["evidence_refs"] = author_refs
    text = answer["statement"] if format == "markdown" else json.dumps({"report": {"answer": answer}})

    result = _finish(orchestrator, binding, text)
    handoff = result.coordinator_payload()
    assert result.status is WorkStatus.COMPLETED
    assert [ref.ref for ref in result.evidence_refs] == [execution.execution_id]
    assert [ref["ref"] for ref in handoff["evidence_refs"]] == [execution.execution_id]
    assert [ref.ref for ref in result.report.answer.evidence_refs] == [ref["ref"] for ref in author_refs]
    assert "text" not in handoff
    restored = ExpertResult.model_validate_json(result.model_dump_json())
    assert restored.coordinator_payload() == handoff


@pytest.mark.parametrize("text", [
    "{invalid JSON", '{"answer": {"statement": "", "direction": "approved"}}',
    '{"some_user_data": [1, 2, 3]}',
])
def test_unrecognized_json_remains_an_ordinary_answer(tmp_path, text):
    orchestrator, binding = _orchestrator(tmp_path)
    result = _finish(orchestrator, binding, text)
    assert result.status is WorkStatus.COMPLETED
    assert result.report.answer.statement == text


def test_recovered_report_and_outputs_keep_the_execution_notice(tmp_path):
    orchestrator, binding = _orchestrator(tmp_path)
    partial = _finish(orchestrator, binding, json.dumps(_report_payload()), _ParticipantState.FAILED)
    assert partial.status is WorkStatus.INCOMPLETE
    assert partial.result_origin is ExpertResultOrigin.BACKEND_RECOVERED
    handoff = partial.coordinator_payload()
    assert handoff["report"]["answer"]["direction"] == "opposes"
    assert "not a scientific verdict" in handoff["execution_notice"]
    assert "text" not in handoff


def test_canonical_report_projects_text_without_changing_output_contract():
    output = ExpertOutput(
        item_id="result_1", execution_id="codeexec_seasonal", output_name="comparison.nc",
        size_bytes=128, sha256="a" * 64, result_kind="interactive_view", title="Seasonal comparison",
    )
    result = ExpertResult(
        work_order_id="work_refined", status=WorkStatus.COMPLETED,
        text="A stale and contradictory alternative answer.",
        report=ExpertReport.model_validate(_report_payload()), outputs=(output,),
        evidence_refs=(EvidenceRef(kind="code_execution", ref="codeexec_seasonal"),),
    )
    assert "stale" not in result.text
    assert result.outputs == (output,)
    assert result.coordinator_payload()["outputs"] == [output.coordinator_payload()]
    assert result.coordinator_payload()["report"]["answer"]["evidence_refs"][0]["ref"] == "codeexec_seasonal"


def test_historical_text_only_payload_remains_readable():
    result = ExpertResult(work_order_id="work_old", status=WorkStatus.COMPLETED, text="Historical answer.")
    assert result.report is None
    assert result.coordinator_payload() == {"text": "Historical answer.", "outputs": []}


@pytest.mark.parametrize("state", [_ParticipantState.COMPLETED, _ParticipantState.FAILED])
def test_retry_preserves_the_full_prior_report_when_reusing_its_answer(tmp_path, state):
    orchestrator, binding = _orchestrator(tmp_path)
    prior = ExpertResult(
        work_order_id=binding.work_order.work_order_id, status=WorkStatus.COMPLETED,
        report=ExpertReport.model_validate(_report_payload()),
    )
    binding.prior_result = prior
    # On a failed retry the existing recovery path also needs evidence or prose.
    text = "Provider stopped during a short diagnostic." if state is _ParticipantState.FAILED else ""
    result = _finish(orchestrator, binding, text, state)
    assert result.report == prior.report
    assert result.coordinator_payload()["report"]["leads"][0]["id"] == "lead_lag"


def _record(number, report, *, job_key="job_refined", task_id="task_refined"):
    order = _order(
        work_order_id=f"work_{number}", session_round=number + 1,
        job_key=job_key, task_id=task_id,
    )
    return SimpleNamespace(
        workspace_id="ws_refined", work_order=order, state=WorkStatus.COMPLETED,
        result=ExpertResult(work_order_id=order.work_order_id, status=WorkStatus.COMPLETED, report=report),
    )


def test_capsule_packs_whole_values_under_budget_and_preserves_old_open_items():
    from oceanx.backend.router import OceanRequestRouter

    refs = [{"kind": "external", "ref": f"https://example.test/{index}/" + "a" * 800,
             "locator": "exact/path/" + "b" * 1_200} for index in range(6)]
    report = ExpertReport.model_validate({
        "answer": {"statement": "Answer " * 1000, "evidence_refs": refs},
        "limitations": [{
            "id": f"limitation_{index}_" + "x" * 90, "statement": "Limitation " * 450,
            "rationale": "Reason " * 500, "evidence_refs": refs,
        } for index in range(6)],
        "leads": [{
            "id": f"lead_{index}", "observation": "Observation " * 500,
            "proposed_question": "Question " * 500, "evidence_refs": refs,
        } for index in range(6)],
    })
    oldest = _record(0, ExpertReport.model_validate(_report_payload()))
    records = [oldest, *(_record(index, report) for index in range(1, 5))]
    raw = OceanRequestRouter._agent_session_capsule(records)
    assert len(raw) <= 10_000
    capsule = json.loads(raw)
    assert capsule["omitted_counts"]["items"] > 0
    assert capsule["earlier_open_items"][0]["work_order_id"] == "work_0"
    assert capsule["earlier_open_items"][0]["leads"][0]["id"] == "lead_lag"
    assert all(entry["full_report_path"] == "expert-report:" + entry["work_order_id"]
               for entry in capsule["rounds"])
    for entry in capsule["rounds"]:
        assert not ("report" in entry["result"] and "text" in entry["result"])
    identifiers = {item.id for item in report.limitations} | {item.id for item in report.leads}
    identifiers |= {"lim_coverage", "lead_lag", "answer"}

    def check_exact(value):
        if isinstance(value, list):
            for item in value:
                check_exact(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key == "id":
                    assert item in identifiers
                if key == "ref" and item.startswith("https://"):
                    assert item in {ref["ref"] for ref in refs}
                if key == "locator":
                    assert item == refs[0]["locator"]
                check_exact(item)

    check_exact(capsule)


def test_full_report_virtual_read_is_paginated_and_same_session_only():
    previous = _record(0, ExpertReport.model_validate(_report_payload()))
    current = _record(1, ExpertReport.from_response("Current answer."))
    sibling = _record(2, ExpertReport.from_response("Private sibling report."), job_key="job_sibling")
    other_task = _record(3, ExpertReport.from_response("Another task report."), task_id="task_other")
    records = {item.work_order.work_order_id: item for item in (previous, current, sibling, other_task)}
    service = object.__new__(ExpertCodeExecutionService)
    service.store = SimpleNamespace(
        get_research_task=lambda _: SimpleNamespace(workspace_id="ws_refined"),
        get_team_work=records.get,
    )
    offset = 0
    pieces = []
    while True:
        page = service.read_expert_file(
            workspace_id="ws_refined", task_id="task_refined", work_order_id="work_1",
            path="expert-report:work_0", offset=offset, limit=120,
        )
        pieces.append(page["content"])
        assert page["path"] == "expert-report:work_0"
        if page["eof"]:
            break
        offset = page["next_offset"]
    restored = json.loads("".join(pieces))
    assert restored["result"] == previous.result.coordinator_payload()
    assert restored["assignment"] == previous.work_order.scientific_assignment()
    for path in ("expert-report:work_2", "expert-report:work_3", "expert-report:missing"):
        with pytest.raises(ExpertCodeExecutionError, match="outside this Expert's session"):
            service.read_expert_file(
                workspace_id="ws_refined", task_id="task_refined", work_order_id="work_1", path=path,
            )
