"""Explicit scientific decisions, facts-only test recording and durable scope."""

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from oceanx.agent import OceanAgentBudget
from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.router import OceanRequestRouter
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.exploration import (
    ExpertTestInput,
    ExplorationInput,
    _synchronize,
    begin_tests,
    expert_test_action,
    exploration_action,
)
from oceanx.exploration import Test as ResearchTest
from oceanx.research_learning import ResearchObservationDraft
from oceanx.team.models import ExpertResult, WorkOrder
from oceanx.tools import (
    OceanAssignmentInput,
    OceanAssignmentTool,
    OceanExplorationTool,
    OceanToolServices,
)


@pytest.fixture
def store(tmp_path):
    value = RequestStore(tmp_path / "state.sqlite3")
    with value._transaction() as db:
        db.execute(
            "INSERT INTO workspace_records (workspace_id, path, revision, updated_at) "
            "VALUES (?, ?, 1, ?)",
            ("ws", str(tmp_path), "2026-09-11"),
        )
    value.create_research_task(workspace_id="ws", task_id="task", title="Research")
    yield value
    value.close()


def call(store, action="read", **kwargs):
    request_id = kwargs.pop("request_id", None)
    if action != "read" and "expected_revision" not in kwargs:
        kwargs["expected_revision"] = call(store)["revision"]
    return exploration_action(
        store, "ws", "task", ExplorationInput(action=action, **kwargs), request_id=request_id
    )


def hypothesis(key, relation="alternatives"):
    return {"id": key, "claim": f"Mechanism {key}", "relation_to_children": relation}


def plan(key, targets=(), **kwargs):
    return {"id": key, "targets": list(targets), "method": "Measure the bounded question", **kwargs}


def start(store, **kwargs):
    return call(store, "start", goal="Explain warming", mode="iterative", **kwargs)


def setup_pair(store, **kwargs):
    start(store, **kwargs)
    return call(
        store,
        "propose",
        candidates=[hypothesis("A"), hypothesis("B")],
        tests=[plan("AB", ["A", "B"])],
    )


def evidence(store, task="task"):
    return store.record_research_observation(
        ResearchObservationDraft(
            workspace_id="ws",
            task_id=task,
            request_id="req",
            kind="result",
            statement=f"Measured outcome {uuid.uuid4()}",
            outcome="supported",
        )
    ).observation_id


def result_data(refs=(), observed="observed"):
    return {
        "evidence_refs": list(refs),
        "outcome_observed": observed,
        "summary": "Measured evidence",
    }


def record(store, key, refs=(), observed="observed"):
    return call(store, "record", test_id=key, result=result_data(refs, observed))


def judge(store, node, status, **kwargs):
    return call(store, "adjudicate", node_id=node, status=status, **kwargs)


def work_order(store, key="work", task="task"):
    order = WorkOrder(
        work_order_id=key,
        task_id=task,
        parent_request_id="req",
        task_goal="Answer the bounded question",
        semantic_role="Scientific Expert",
        authority="expert",
        workspace_revision=1,
    )
    store.create_team_work_order(workspace_id="ws", work_order=order)
    return key


def expert(store, action="read", *, owner="work", nodes=("A",), **kwargs):
    return expert_test_action(
        store, "ws", "task", owner, nodes, ExpertTestInput(action=action, **kwargs)
    )


@pytest.mark.parametrize(
    "states",
    [
        ("established", "refuted"),
        ("refuted", "refuted"),
        ("unverifiable", "established"),
        ("supported", "contested"),
    ],
)
@pytest.mark.parametrize("relation", ["alternatives", "prerequisites"])
def test_children_only_summarize_without_inferred_parent_status(store, relation, states):
    setup_pair(store)
    raw = json.loads(
        store._connection.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
    )
    root = raw["hypotheses"]["root"]
    root.update(relation_to_children=relation, summary="Coordinator's existing judgment")
    for key, status in zip(("A", "B"), states, strict=True):
        raw["hypotheses"][key].update(status=status, summary="Measured or missing information")
    _synchronize(raw)
    assert root["status"] == "untested"
    assert root["summary"] == "Coordinator's existing judgment"
    assert root["history"] == []
    assert "A [" in root["children_summary"] and "B [" in root["children_summary"]


def test_record_is_facts_only_even_for_legacy_effects(store):
    setup_pair(store)
    with store._transaction() as db:
        raw = json.loads(
            db.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
        )
        raw["tests"]["AB"]["discriminates"] = [
            {"outcome": "observed", "effects": {"A": "established", "B": "refuted"}}
        ]
        db.execute("UPDATE research_exploration_trees SET tree_json = ?", (json.dumps(raw),))
    state = record(store, "AB", [evidence(store)])
    assert state["tests"]["AB"]["status"] == "done"
    assert all(node["status"] == "untested" for node in state["hypotheses"].values())
    assert state["executions_used"] == 0  # Recording is not a new execution.
    assert state["recommendation"]["action"] == "assess"


@pytest.mark.parametrize("status", ["supported", "refuted", "untested", "contested"])
def test_answer_is_coordinator_decision_not_all_root_states(store, status):
    setup_pair(store)
    judge(store, "A", status)
    state = call(
        store,
        "pause",
        decision="answered",
        completion_summary="Bounded answer",
        request_id="request_current",
    )
    assert state["stop_decision"]["exit"] == "answered"
    assert state["stop_decision"]["question_resolved"]
    assert state["stop_request_id"] == "request_current"
    assert state["hypotheses"]["A"]["status"] == status
    assert state["hypotheses"]["B"]["status"] == "untested"
    resumed = call(store, "resume")
    assert resumed["stop_request_id"] is None and resumed["stop_decision"] is None


def test_unable_to_answer_needs_no_forced_decomposition_or_review(store):
    start(store)
    call(store, "propose", candidates=[hypothesis("A")])
    judge(store, "A", "unverifiable", summary="Vertical flux is unavailable")
    state = call(store, "pause", decision="unable_to_answer")
    assert state["stop_decision"]["exit"] == "unable_to_answer"
    assert not state["stop_decision"]["question_resolved"]
    assert len(state["hypotheses"]) == 2


def test_completion_summary_is_explicit_answer_and_ideas_remain_distinct(store):
    call(store, "start", goal="Suggest ideas", mode="ideas")
    call(store, "propose", candidates=[hypothesis("A")], tests=[plan("t", ["A"])])
    with pytest.raises(ValueError, match="iterative"):
        begin_tests(store, "ws", "task", ["t"])
    state = call(store, "pause", completion_summary="An untested shortlist")
    assert state["stop_decision"]["exit"] == "ideas_delivered"
    assert not state["stop_decision"]["question_resolved"]


def test_running_tests_must_settle_but_interruption_preserves_them(store):
    setup_pair(store)
    begin_tests(store, "ws", "task", ["AB"])
    with pytest.raises(ValueError, match="running"):
        call(store, "pause", decision="answered")
    interrupted = call(store, "pause")
    assert interrupted["stop_decision"]["exit"] == "interrupted"
    assert interrupted["tests"]["AB"]["status"] == "running"
    call(store, "resume")
    record(store, "AB")
    assert call(store, "pause", completion_summary="Answer")["stop_decision"]["exit"] == "answered"


def test_explicit_status_judgments_keep_prior_evidence(store):
    setup_pair(store)
    support = evidence(store)
    judge(store, "A", "supported", summary="Support", evidence_refs=[support])
    record(store, "AB", observed="Opposite measurement")
    assert call(store)["hypotheses"]["A"]["status"] == "supported"
    state = judge(store, "A", "contested", summary="Conflicting estimates", test_id="AB")
    node = state["hypotheses"]["A"]
    assert node["status"] == "contested"
    assert node["history"][-1]["evidence_refs"] == [support]
    assert node["history"][-1]["summary"] == "Support"
    assert node["status_source"] == "coordinator"


def test_established_guard_applies_to_judgment_not_test_labels(store):
    setup_pair(store)
    call(store, "plan_test", tests=[plan("single", ["A"])])
    before = call(store)["revision"]
    with pytest.raises(ValueError, match="targets >= 2"):
        judge(store, "A", "established", test_id="single")
    assert call(store)["revision"] == before
    record(store, "AB")
    assert (
        judge(store, "A", "established", test_id="AB")["hypotheses"]["A"]["status"] == "established"
    )
    with pytest.raises(ValueError, match="reference this hypothesis"):
        judge(store, "B", "supported", test_id="single")
    with pytest.raises(ValueError, match="effects"):
        ResearchTest(
            **plan("new", ["A"]),
            discriminates=[{"outcome": "observed", "effects": {"A": "established"}}],
        )


def test_decomposition_and_new_tests_never_infer_or_reopen_status(store):
    start(store)
    call(store, "propose", candidates=[hypothesis("A")])
    call(store, "propose", node_id="A", candidates=[hypothesis("P")])
    state = call(store, "propose", node_id="P", candidates=[hypothesis("Q")])
    assert all(h["status"] == "untested" for h in state["hypotheses"].values())
    judge(store, "Q", "unverifiable", summary="Missing flux")
    state = call(store, "plan_test", tests=[plan("new", ["Q"])])
    assert state["hypotheses"]["Q"]["status"] == "unverifiable"
    begin_tests(store, "ws", "task", ["new"])  # Other untested leaves are not gates.
    record(store, "new")
    assert call(store)["hypotheses"]["Q"]["status"] == "unverifiable"


def test_shared_execution_accounting_and_budget_resume(store):
    setup_pair(store, execution_budget=1)
    call(store, "plan_test", tests=[plan("t2", ["A"]), plan("t3", ["B"])])
    begun = begin_tests(store, "ws", "task", ["AB", "t2"], execution_id="execution_one")
    assert begun["executions_used"] == 1
    record(store, "AB")
    record(store, "t2")
    with pytest.raises(ValueError, match="budget"):
        begin_tests(store, "ws", "task", ["t3"], execution_id="execution_two")
    # More mappings to the same already-charged execution do not charge again.
    begin_tests(store, "ws", "task", ["t3"], execution_id="execution_one")
    record(store, "t3")
    assert call(store)["executions_used"] == 1
    call(store, "plan_test", tests=[plan("t4", ["A"])])
    call(store, "resume", execution_budget=2)
    begin_tests(store, "ws", "task", ["t4"], execution_id="execution_two")
    assert call(store)["executions_used"] == 2
    assert call(store, "pause")["stop_decision"]["exit"] == "budget_exhausted"


def test_reflection_is_optional_and_does_not_force_candidates(store):
    setup_pair(store, reflection_interval_percent=1)
    call(store, "reflect", coverage_complete=False, summary="An optional reflection")
    begin_tests(store, "ws", "task", ["AB"])
    record(store, "AB")
    assert not call(store)["reflection_pending"]
    assert call(store, "pause", completion_summary="Answer")["stop_decision"]["exit"] == "answered"


def test_reference_revision_and_immutable_record_replay(store):
    setup_pair(store)
    before = call(store)["revision"]
    with pytest.raises(ValueError, match="observation"):
        record(store, "AB", ["foreign"])
    assert call(store)["revision"] == before
    with pytest.raises(RequestStoreError, match="revision"):
        call(store, "pause", expected_revision=0)
    first = record(store, "AB")
    assert record(store, "AB")["revision"] == first["revision"]
    with pytest.raises(ValueError, match="immutable"):
        record(store, "AB", observed="Different result")


def test_expert_retrospective_facts_do_not_change_hypotheses(store):
    setup_pair(store)
    work_order(store)
    judge(store, "A", "unverifiable", summary="Missing original test")
    before = call(store)
    data = {
        "tests": [plan("own", ["A"])],
        "test_id": "own",
        "result": result_data([evidence(store)]),
    }
    view = expert(store, "record", **data)
    assert set(view["hypotheses"]) == {"A"}
    assert set(view["tests"]) == {"own"}
    assert view["tests"]["own"]["registered_at"] is None
    assert "tree_text" not in view and "recent_evidence" not in view
    after = call(store)
    assert after["hypotheses"] == before["hypotheses"]
    assert after["executions_used"] == before["executions_used"]
    expert(store, "record", **data)
    assert call(store)["revision"] == after["revision"]


def test_expert_scope_ownership_and_foreign_evidence(store):
    setup_pair(store)
    work_order(store)
    work_order(store, "other")
    expert(store, "plan_test", tests=[plan("own", ["A"])])
    with pytest.raises(ValueError, match="authorized"):
        expert(store, "plan_test", tests=[plan("foreign", ["B"])])
    with pytest.raises(ValueError, match="own"):
        expert(store, "record", owner="other", test_id="own", result=result_data())
    with pytest.raises(ValueError, match="observation"):
        expert(store, "record", test_id="own", result=result_data(["foreign"]))
    assert not expert(store, nodes=())["tests"]  # Changed scope does not leak old facts.
    assert "own" not in expert(store, owner="other")["tests"]
    with pytest.raises(ValueError):
        ExpertTestInput(action="adjudicate", status="established")
    with pytest.raises(ValueError, match="effects"):
        ExpertTestInput(
            action="plan_test",
            tests=[
                {
                    **plan("bad", ["A"]),
                    "effects": {"A": "established"},
                }
            ],
        )


def test_expert_no_tree_notes_and_late_results_are_retained(store):
    work_order(store)
    data = {"tests": [plan("ordinary")], "test_id": "ordinary", "result": result_data()}
    view = expert(store, "record", nodes=(), **data)
    assert not view["active"] and not view["hypotheses"]
    assert "ordinary" in expert(store, nodes=())["tests"]
    assert (
        store._connection.execute("SELECT COUNT(*) FROM research_exploration_trees").fetchone()[0]
        == 0
    )
    count = len(store.list_research_observations(task_id="task"))
    expert(store, "record", nodes=(), **data)
    assert len(store.list_research_observations(task_id="task")) == count
    setup_pair(store)
    expert(store, "plan_test", tests=[plan("late", ["A"])])
    call(store, "pause")
    state = expert(store, "record", test_id="late", result=result_data())
    assert not state["active"] and state["tests"]["late"]["status"] == "done"
    assert call(store)["stop_decision"]["exit"] == "interrupted"


def test_expert_local_test_names_do_not_collide_across_work_orders(store):
    setup_pair(store)
    work_order(store)
    work_order(store, "other")
    first = expert(store, "plan_test", tests=[plan("T1", ["A"])])
    second = expert(store, "plan_test", owner="other", tests=[plan("T1", ["A"])])
    first_id = first["tests"]["T1"]["id"]
    second_id = second["tests"]["T1"]["id"]
    assert first_id != second_id
    assert {first_id, second_id} <= set(call(store)["tests"])
    expert(store, "record", test_id=first_id, result=result_data())
    assert expert(store)["tests"]["T1"]["status"] == "done"
    assert expert(store, owner="other")["tests"]["T1"]["status"] == "todo"
    with pytest.raises(ValueError, match="own"):
        expert(store, "record", owner="other", test_id=first_id, result=result_data())


def test_large_no_tree_fact_round_trips_without_new_report_limits(store):
    work_order(store)
    draft = plan(
        "large",
        discriminates=[
            {"outcome": str(i), "interpretation": '证据与局限\\"' * 200} for i in range(8)
        ],
    )
    view = expert(store, "record", nodes=(), tests=[draft], test_id="large", result=result_data())
    restored = expert(store, nodes=())
    assert restored["tests"] == view["tests"]
    rows = store.list_research_observations(task_id="task")
    assert len(rows) > 1 and all(len(row.statement) <= 8000 for row in rows)
    expert(store, "record", nodes=(), tests=[draft], test_id="large", result=result_data())
    assert len(store.list_research_observations(task_id="task")) == len(rows)
    assert (
        store._connection.execute("SELECT COUNT(*) FROM research_exploration_trees").fetchone()[0]
        == 0
    )


def test_expert_notes_do_not_reinterpret_ideation_as_execution_permission(store):
    work_order(store)
    call(store, "start", goal="Discuss ideas", mode="ideas")
    view = expert(
        store, "record", nodes=(), tests=[plan("reading")], test_id="reading", result=result_data()
    )
    assert view["tests"]["reading"]["status"] == "done"
    assert call(store)["mode"] == "ideas"
    assert call(store)["executions_used"] == 0


def test_legacy_reflection_flags_and_optional_nodes_do_not_block_work(store):
    setup_pair(store, execution_budget=1)
    call(store, "propose", candidates=[hypothesis(f"H{i}") for i in range(70)])
    with store._transaction() as db:
        raw = json.loads(
            db.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
        )
        raw["reflection_pending"] = ["Historical periodic trigger"]
        db.execute("UPDATE research_exploration_trees SET tree_json = ?", (json.dumps(raw),))
    begin_tests(store, "ws", "task", ["AB"])
    record(store, "AB")
    state = call(store, "pause", decision="answered")
    assert state["executions_used"] == 1
    assert len(state["hypotheses"]) == 73
    assert state["stop_decision"]["exit"] == "answered"


def test_expert_execution_reference_uses_existing_ownership_and_counts_once(store):
    setup_pair(store)
    work_order(store)
    store.mark_team_work_running("work")
    store.start_code_execution(
        execution_id="exec1",
        workspace_id="ws",
        task_id="task",
        work_order_id="work",
        child_id="child",
        request={},
        started_at="2026-09-11T00:00:00Z",
    )
    for key in ("first", "second"):
        view = expert(
            store,
            "record",
            tests=[plan(key, ["A"])],
            test_id=key,
            result=result_data(),
            execution_id="exec1",
        )
        assert view["tests"][key]["executed_at"] == "2026-09-11T00:00:00Z"
    assert len(store.list_code_executions("work")) == 1
    assert call(store)["executions_used"] == 0
    with pytest.raises(ValueError, match="Execution"):
        expert(store, "record", test_id="first", result=result_data(), execution_id="foreign")


def test_legacy_read_only_preserves_bytes(store):
    from oceanx.exploration_legacy import ExplorationInput as OldInput
    from oceanx.exploration_legacy import exploration_action as old_action

    old_action(
        store,
        "ws",
        "task",
        OldInput(
            action="start",
            expected_revision=0,
            goal="Old",
            mode="iterative",
        ),
    )
    before = store._connection.execute(
        "SELECT tree_json FROM research_exploration_trees"
    ).fetchone()[0]
    assert call(store)["legacy_read_only"]
    with pytest.raises(ValueError, match="Historical"):
        call(store, "resume")
    work_order(store)
    assert expert(store, nodes=())["legacy_read_only"]
    with pytest.raises(ValueError, match="Historical"):
        expert(store, "plan_test", nodes=(), tests=[plan("new")])
    assert (
        store._connection.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
        == before
    )


def assignment_tool(store, tmp_path, monkeypatch):
    """Use the production tool/router path, substituting only Expert execution."""
    router = object.__new__(OceanRequestRouter)
    router.store = store
    router.agent_budget = OceanAgentBudget()
    router._agent_request_budgets = {}
    monkeypatch.setattr(
        store,
        "get_request",
        lambda _: SimpleNamespace(
            request_type="session.submit",
            workspace_id="ws",
            task_id="task",
        ),
    )
    started = []

    async def execute_plan(**kwargs):
        results = []
        for order in kwargs["plan"].work_orders:
            assert not hasattr(order, "research_test_ids")
            existing = store.get_team_work(order.work_order_id)
            if existing is not None and existing.result is not None:
                results.append(existing.result)
                continue
            started.append(order.work_order_id)
            store.create_team_work_order(workspace_id="ws", work_order=order)
            store.mark_team_work_running(order.work_order_id)
            result = ExpertResult(
                work_order_id=order.work_order_id,
                status="completed",
                result_origin="agent_submitted",
                text="Evidence returned",
            )
            store.complete_team_work(result)
            results.append(result)
        return tuple(results)

    router.team_orchestrator = SimpleNamespace(execute_plan=execute_plan)

    async def sink(payload, context):
        assert "research_question" not in payload
        return await router._assign_team_work(
            workspace_id="ws",
            workspace_path=tmp_path,
            provider_id="test",
            task_id="task",
            payload=payload,
            context=context,
        )

    tool = OceanAssignmentTool(
        OceanToolServices(
            workspace_id="ws",
            provider_id="test",
            store=store,
            task_id="task",
            skill_role="coordinator",
            team_assign_sink=sink,
        )
    )
    return tool, sink, started


def assignment_args(experts=1, test_ids=("AB",)):
    return OceanAssignmentInput(
        research_question="Explain warming",
        research_test_ids=list(test_ids),
        plan_goal="Explain warming",
        todos=[
            {
                "todo_id": f"inspect{i}",
                "question": "Compare mechanisms",
                "why_this_expert": "Independent analysis",
                "profile_id": "data_reproducibility_expert",
                "expert_key": f"expert{i}",
                "expected_outputs": ["answer"],
            }
            for i in range(experts)
        ],
        dispatch=[f"inspect{i}" for i in range(experts)],
    )


def assignment_context(tmp_path, operation="op"):
    return ToolExecutionContext(
        cwd=tmp_path,
        request_id="req",
        turn_id="turn",
        tool_call_id=operation,
        operation_id=operation,
    )


@pytest.mark.parametrize(
    "experts,test_ids,budget,expected",
    [
        (1, ("AB", "extra"), 1, 1),
        (2, ("AB",), 2, 2),
        (3, ("AB", "extra"), 3, 3),
        (2, (), 1, 0),  # No new counter rule for unregistered research/inspection.
    ],
)
async def test_assignment_reserves_per_expert_work_order(
    store, tmp_path, monkeypatch, experts, test_ids, budget, expected
):
    setup_pair(store, execution_budget=budget)
    call(store, "plan_test", tests=[plan("extra", ["A"])])
    tool, _sink, started = assignment_tool(store, tmp_path, monkeypatch)
    result = await tool.execute(assignment_args(experts, test_ids), assignment_context(tmp_path))
    assert not result.is_error, result.output
    assert len(started) == experts
    state = call(store)
    assert state["executions_used"] == expected
    for test_id in test_ids:
        assert set(state["tests"][test_id]["execution_ids"]) == set(started)
        assert state["tests"][test_id]["status"] == "running"


async def test_assignment_budget_rejects_whole_wave_before_dispatch(store, tmp_path, monkeypatch):
    setup_pair(store, execution_budget=1)
    tool, _sink, started = assignment_tool(store, tmp_path, monkeypatch)
    response = await tool.execute(assignment_args(2), assignment_context(tmp_path))
    assert response.is_error and "budget" in response.output.lower()
    assert not started
    state = call(store)
    assert state["executions_used"] == 0 and state["tests"]["AB"]["status"] == "todo"


async def test_assignment_budget_receipt_and_work_order_replay_then_followup(
    store, tmp_path, monkeypatch
):
    setup_pair(store, execution_budget=2)
    call(store, "plan_test", tests=[plan("extra", ["A"])])
    tool, sink, started = assignment_tool(store, tmp_path, monkeypatch)
    args = assignment_args(test_ids=("AB", "extra"))
    context = assignment_context(tmp_path)
    first = await tool.execute(args, context)
    assert not first.is_error, first.output
    replay = await tool.execute(args, context)
    assert replay.metadata["replayed"]
    assert len(started) == 1 and call(store)["executions_used"] == 1
    # The backend can also replay an interrupted receipt with the same durable
    # WorkOrder. Already-returned test facts must not be recharged or reset.
    record(store, "AB")
    record(store, "extra")
    await sink(
        args.model_dump(mode="json", exclude_unset=True, exclude={"research_question"}), context
    )
    assert len(started) == 1 and call(store)["executions_used"] == 1
    assert call(store)["tests"]["AB"]["status"] == "done"
    # A new same-Expert follow-up is a different WorkOrder and costs one attempt.
    call(store, "plan_test", tests=[plan("followup", ["A"])])
    followup = await tool.execute(
        assignment_args(test_ids=("followup",)),
        assignment_context(tmp_path, "next"),
    )
    assert not followup.is_error, followup.output
    assert len(started) == 2 and started[0] != started[1]
    assert call(store)["executions_used"] == 2


async def test_coordinator_tool_remains_role_isolated(store, tmp_path):
    services = OceanToolServices(
        workspace_id="ws", provider_id="test", store=store, task_id="task", skill_role="coordinator"
    )
    context = ToolExecutionContext(
        cwd=tmp_path, request_id="req", turn_id="turn", tool_call_id="start", operation_id="op"
    )
    response = await OceanExplorationTool(services).execute(
        ExplorationInput(action="start", expected_revision=0, goal="Goal", mode="iterative"),
        context,
    )
    assert not response.is_error
    denied = OceanExplorationTool(replace(services, skill_role="data_reproducibility_expert"))
    assert (await denied.execute(ExplorationInput(action="read"), context)).is_error
