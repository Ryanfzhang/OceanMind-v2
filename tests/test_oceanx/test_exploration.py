"""Current v2 contract; v1 regression fixtures live in test_exploration_legacy."""

import itertools
import json
import uuid
from dataclasses import replace

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.exploration import (
    ExplorationInput,
    _repair_needed,
    _synchronize,
    begin_tests,
    exploration_action,
)
from oceanx.exploration import (
    Test as ResearchTest,
)
from oceanx.research_learning import ResearchObservationDraft
from oceanx.tools import (
    OceanAssignmentInput,
    OceanAssignmentTool,
    OceanExplorationTool,
    OceanToolServices,
)


@pytest.fixture
def store(tmp_path):
    value = RequestStore(tmp_path / "state.sqlite3")
    value.create_research_task(workspace_id="ws", task_id="task", title="Research")
    yield value
    value.close()


def call(store, action="read", **kwargs):
    if action != "read" and "expected_revision" not in kwargs:
        kwargs["expected_revision"] = call(store)["revision"]
    return exploration_action(store, "ws", "task", ExplorationInput(action=action, **kwargs))


def hypothesis(key, relation="alternatives"):
    return {"id": key, "claim": f"Mechanism {key}", "relation_to_children": relation}


def test_plan(key, effects, cost=1, feasible=True):
    return {
        "id": key,
        "targets": list(effects),
        "method": "Measure and compare independent mechanism predictions",
        "discriminates": [{"outcome": "observed", "effects": effects}],
        "feasible": feasible,
        "cost": cost,
        "infeasible_reason": None if feasible else "Missing vertical flux observations",
    }


test_plan.__test__ = False  # Factory, not a test case.


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


def result(store, key, observed="observed", refs=None):
    return call(
        store,
        "record",
        test_id=key,
        result={
            "evidence_refs": refs or [evidence(store)],
            "outcome_observed": observed,
            "summary": "Independent observations and checks",
        },
    )


def start(store, **kwargs):
    return call(store, "start", goal="Explain warming", mode="iterative", **kwargs)


def setup_pair(store, **kwargs):
    start(store, **kwargs)
    return call(
        store,
        "propose",
        candidates=[hypothesis("A"), hypothesis("B")],
        tests=[test_plan("AB", {"A": "established", "B": "refuted"})],
    )


@pytest.mark.parametrize(
    "relation,states",
    list(
        itertools.product(
            ["alternatives", "prerequisites"],
            itertools.product(["established", "refuted", "unverifiable"], repeat=2),
        )
    ),
)
def test_strong_three_valued_truth_table_and_nested_unknown_summary(relation, states):
    def node(key, parent, status, rel="alternatives"):
        return {
            "id": key,
            "parent": parent,
            "claim": key,
            "status": status,
            "relation_to_children": rel,
            "summary": f"{key}: missing source X" if status == "unverifiable" else key,
            "history": [],
            "decompose_depth_without_test": 0,
        }

    tree = {
        "hypotheses": {
            "root": node("root", None, "untested"),
            "P": node("P", "root", "untested", relation),
            "A": node("A", "P", states[0]),
            "B": node("B", "P", states[1]),
        },
        "tests": {},
        "reflection_pending": [],
    }
    _synchronize(tree)
    if relation == "alternatives":
        expected = (
            "established"
            if "established" in states
            else "refuted"
            if set(states) == {"refuted"}
            else "unverifiable"
        )
    else:
        expected = (
            "refuted"
            if "refuted" in states
            else "established"
            if set(states) == {"established"}
            else "unverifiable"
        )
    assert tree["hypotheses"]["P"]["status"] == expected
    assert tree["hypotheses"]["root"]["status"] == expected
    if "unverifiable" in states:
        assert "missing source X" in tree["hypotheses"]["root"]["summary"]
    pending = list(tree["reflection_pending"])
    _synchronize(tree)
    assert tree["reflection_pending"] == pending


@pytest.mark.parametrize("nonterminal", ["supported", "contested", "untested"])
def test_nonterminal_child_prevents_parent_close(store, nonterminal):
    state = setup_pair(store)
    tree = json.loads(
        store._connection.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
    )
    tree["hypotheses"]["A"]["status"] = "established"
    tree["hypotheses"]["B"]["status"] = nonterminal
    _synchronize(tree)
    assert tree["hypotheses"]["root"]["status"] == "untested"
    assert state["executions_used"] == 0


def test_direct_parent_conflict_survives_child_rollup_and_requires_test(store):
    setup_pair(store)
    tree = json.loads(
        store._connection.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
    )
    tree["tests"] = {}
    tree["hypotheses"]["A"]["status"] = "established"
    tree["hypotheses"]["B"]["status"] = "refuted"
    parent = dict(tree["hypotheses"]["root"])
    parent.update(id="P", parent="root", history=[])
    tree["hypotheses"]["P"] = parent
    tree["hypotheses"]["A"]["parent"] = "P"
    tree["hypotheses"]["B"]["parent"] = "P"
    parent.update(status="contested", status_source="test", summary="Conflicting observations")
    _synchronize(tree)
    assert parent["status"] == "contested"
    assert parent["summary"] == "Conflicting observations"
    assert "P" in _repair_needed(tree)
    assert tree["hypotheses"]["root"]["status"] not in {"established", "refuted", "unverifiable"}
    # A later direct test resolves the conflict; ordinary synthesis can resume.
    parent["status"] = "supported"
    _synchronize(tree)
    assert parent["status"] == "established"
    assert parent["status_source"] == "children"


def test_shared_test_charged_once_and_completion_requires_reflection(store):
    setup_pair(store)
    begin_tests(store, "ws", "task", ["AB"])
    state = result(store, "AB")
    assert state["executions_used"] == 1
    assert state["hypotheses"]["root"]["status"] == "established"
    assert state["recommendation"]["action"] == "reflect"
    with pytest.raises(ValueError, match="reflection"):
        call(store, "pause", completion_summary="Done")
    call(
        store, "reflect", coverage_complete=True, summary="Existing mechanisms cover this question"
    )
    ended = call(store, "pause", completion_summary="A explains the evidence; B rejected")
    assert ended["stop_decision"]["exit"] == "answered"
    with pytest.raises(ValueError):
        result(store, "AB")


def test_no_node_quota_and_no_charge_for_planning(store):
    start(store, execution_budget=1)
    state = call(store, "propose", candidates=[hypothesis(f"H{i}") for i in range(70)])
    assert len(state["hypotheses"]) == 71
    assert state["executions_used"] == 0
    assert "node_budget" not in ExplorationInput.model_fields


def test_two_decompositions_and_infeasible_reason_propagates(store):
    start(store)
    call(store, "propose", candidates=[hypothesis("A", "prerequisites")])
    call(store, "propose", node_id="A", candidates=[hypothesis("P")])
    state = call(
        store,
        "propose",
        node_id="P",
        candidates=[hypothesis("Q")],
        tests=[test_plan("missing", {"Q": "supported"}, feasible=False)],
    )
    assert state["hypotheses"]["Q"]["decompose_depth_without_test"] == 2
    assert state["hypotheses"]["root"]["status"] == "unverifiable"
    assert "Missing vertical flux" in state["hypotheses"]["root"]["summary"]
    call(
        store,
        "reflect",
        coverage_complete=True,
        summary="No other candidate supported by available data",
    )
    ended = call(store, "pause", completion_summary="Unable to answer")
    assert ended["stop_decision"]["exit"] == "unable_to_answer"
    call(store, "resume")
    reopened = call(store, "plan_test", tests=[test_plan("new", {"Q": "supported"})])
    assert reopened["hypotheses"]["root"]["status"] not in {
        "established",
        "refuted",
        "unverifiable",
    }


def test_feasible_test_on_second_decomposition_prevents_unverifiable(store):
    start(store)
    call(store, "propose", candidates=[hypothesis("A")])
    call(store, "propose", node_id="A", candidates=[hypothesis("P")])
    state = call(
        store,
        "propose",
        node_id="P",
        candidates=[hypothesis("Q")],
        tests=[test_plan("testQ", {"Q": "supported"})],
    )
    assert state["hypotheses"]["Q"]["status"] == "untested"
    assert state["recommendation"]["test_id"] == "testQ"


def test_established_requires_real_distinct_targets_and_effects():
    with pytest.raises(ValueError, match="two distinct"):
        ResearchTest(**test_plan("A", {"H": "established"}))
    padded = test_plan("A", {"H": "supported"})
    padded["targets"].append("unused")
    with pytest.raises(ValueError, match="Every target"):
        ResearchTest(**padded)


def test_record_rejects_persisted_single_target_established_atomically(store):
    start(store)
    call(
        store,
        "propose",
        candidates=[hypothesis("A")],
        tests=[test_plan("audit", {"A": "supported"})],
    )
    begin_tests(store, "ws", "task", ["audit"])
    # Bypass creation validation only in this fixture to exercise the write guard
    # against an invalid persisted discriminator, not just the input schema.
    with store._transaction():
        raw = json.loads(
            store._connection.execute(
                "SELECT tree_json FROM research_exploration_trees WHERE task_id = ?",
                ("task",),
            ).fetchone()[0]
        )
        raw["tests"]["audit"]["discriminates"][0]["effects"]["A"] = "established"
        store._connection.execute(
            "UPDATE research_exploration_trees SET tree_json = ? WHERE task_id = ?",
            (json.dumps(raw), "task"),
        )
    before = store._connection.execute(
        "SELECT revision, tree_json FROM research_exploration_trees WHERE task_id = ?",
        ("task",),
    ).fetchone()
    with pytest.raises(ValueError, match="established requires targets >= 2"):
        result(store, "audit")
    after = store._connection.execute(
        "SELECT revision, tree_json FROM research_exploration_trees WHERE task_id = ?",
        ("task",),
    ).fetchone()
    assert tuple(after) == tuple(before)


def test_opposing_evidence_is_contested_and_not_silently_overwritten(store):
    start(store)
    call(
        store,
        "propose",
        candidates=[hypothesis("A")],
        tests=[test_plan("support", {"A": "supported"})],
    )
    begin_tests(store, "ws", "task", ["support"])
    result(store, "support")
    call(store, "plan_test", tests=[test_plan("oppose", {"A": "refuted"})])
    begin_tests(store, "ws", "task", ["oppose"])
    state = result(store, "oppose")
    node = state["hypotheses"]["A"]
    assert node["status"] == "contested"
    assert "support" in node["summary"] and "oppose" in node["summary"]
    assert len(state["tests"]) == 2
    with pytest.raises(ValueError):
        call(store, "pause", completion_summary="Done")


def test_periodic_reflection_budget_resume_and_unexpected_outcome(store):
    start(store, execution_budget=2, reflection_interval_percent=50)
    call(
        store, "propose", candidates=[hypothesis("A")], tests=[test_plan("t1", {"A": "supported"})]
    )
    begin_tests(store, "ws", "task", ["t1"])
    assert call(store)["recommendation"]["action"] == "await_results"
    result(store, "t1", observed="Unexpected instrument failure")
    assert call(store)["hypotheses"]["A"]["status"] == "untested"
    assert call(store)["recommendation"]["action"] == "reflect"
    call(store, "reflect", coverage_complete=True, summary="Coverage unchanged; retry measurement")
    call(store, "plan_test", tests=[test_plan("t2", {"A": "supported"})])
    begin_tests(store, "ws", "task", ["t2"])
    result(store, "t2")
    paused = call(store, "pause")
    assert paused["stop_decision"]["exit"] == "budget_exhausted"
    resumed = call(store, "resume", execution_budget=3)
    assert resumed["executions_used"] == 2 and resumed["reflection_pending"]


def test_invalid_refs_revision_and_no_result_replay(store):
    setup_pair(store)
    begin_tests(store, "ws", "task", ["AB"])
    before = call(store)["revision"]
    with pytest.raises(ValueError, match="observation"):
        result(store, "AB", refs=["foreign"])
    assert call(store)["revision"] == before
    with pytest.raises(RequestStoreError, match="revision"):
        call(store, "pause", expected_revision=0)
    result(store, "AB")
    with pytest.raises(ValueError, match="unfinished"):
        result(store, "AB")


def test_new_root_direction_after_refutation_and_record_count_does_not_block(store):
    start(store)
    call(
        store,
        "propose",
        candidates=[hypothesis("A")],
        tests=[test_plan("reject", {"A": "refuted"})],
    )
    begin_tests(store, "ws", "task", ["reject"])
    result(store, "reject")
    state = call(
        store,
        "reflect",
        coverage_complete=False,
        summary="Refutation reveals another mechanism",
        candidates=[hypothesis("B")],
        tests=[test_plan("testB", {"B": "supported"})],
    )
    assert state["recommendation"]["test_id"] == "testB"
    assert state["hypotheses"]["A"]["status"] == "refuted"


def test_shallowest_effect_then_cost(store):
    start(store)
    call(store, "propose", candidates=[hypothesis("A"), hypothesis("B")])
    call(
        store,
        "propose",
        node_id="A",
        candidates=[hypothesis("C")],
        tests=[
            test_plan("deep", {"C": "supported"}, cost=1),
            test_plan("shallow", {"B": "supported"}, cost=100),
        ],
    )
    assert call(store)["recommendation"]["test_id"] == "shallow"


def test_legacy_read_only_preserves_bytes(store):
    from oceanx.exploration_legacy import (
        ExplorationInput as OldInput,
    )
    from oceanx.exploration_legacy import (
        exploration_action as old_action,
    )

    old_action(
        store,
        "ws",
        "task",
        OldInput(action="start", expected_revision=0, goal="Old", mode="iterative"),
    )
    before = store._connection.execute(
        "SELECT tree_json FROM research_exploration_trees"
    ).fetchone()[0]
    assert call(store)["legacy_read_only"]
    with pytest.raises(ValueError, match="Historical"):
        call(store, "resume")
    assert (
        store._connection.execute("SELECT tree_json FROM research_exploration_trees").fetchone()[0]
        == before
    )


async def test_assignment_budget_handoff_and_durable_replay(store, tmp_path):
    setup_pair(store)
    payloads = []

    async def sink(payload, context):
        payloads.append(payload)
        assert "research_test_ids" not in payload and "research_question" not in payload
        assert call(store)["tests"]["AB"]["status"] == "running"
        return {"results": [{"summary": "Evidence ready"}]}

    services = OceanToolServices(
        workspace_id="ws",
        provider_id="test",
        store=store,
        task_id="task",
        skill_role="coordinator",
        team_assign_sink=sink,
    )
    tool = OceanAssignmentTool(services)
    args = OceanAssignmentInput(
        research_question="Explain warming",
        research_test_ids=["AB"],
        plan_goal="Explain warming",
        todos=[
            {
                "todo_id": "inspect",
                "question": "Compare mechanisms",
                "why_this_expert": "Independent data analysis",
                "profile_id": "data_reproducibility_expert",
                "expected_outputs": ["answer"],
                "done_when": "Report evidence",
            }
        ],
        dispatch=["inspect"],
    )
    context = ToolExecutionContext(
        cwd=tmp_path, request_id="req", turn_id="turn", tool_call_id="call", operation_id="op"
    )
    first = await tool.execute(args, context)
    assert not first.is_error, first.output
    replay = await tool.execute(args, context)
    assert replay.metadata["replayed"]
    assert len(payloads) == 1 and call(store)["executions_used"] == 1


async def test_tool_new_schema_and_role_isolation(store, tmp_path):
    services = OceanToolServices(
        workspace_id="ws", provider_id="test", store=store, task_id="task", skill_role="coordinator"
    )
    tool = OceanExplorationTool(services)
    context = ToolExecutionContext(
        cwd=tmp_path, request_id="req", turn_id="turn", tool_call_id="start", operation_id="op"
    )
    response = await tool.execute(
        ExplorationInput(action="start", expected_revision=0, goal="Goal", mode="iterative"),
        context,
    )
    assert not response.is_error
    assert json.loads(response.output)["schema_version"] == 2
    denied = OceanExplorationTool(replace(services, skill_role="data_reproducibility_expert"))
    assert (await denied.execute(ExplorationInput(action="read"), context)).is_error
