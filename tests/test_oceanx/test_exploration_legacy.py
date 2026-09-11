import json
import uuid

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.exploration_legacy import ExplorationInput, exploration_action
from oceanx.research_learning import ResearchObservationDraft
from oceanx.tools import (
    OceanAssignmentInput,
    OceanAssignmentTool,
    OceanExplorationTool,
    OceanToolServices,
    create_ocean_discussion_tool_registry,
    create_ocean_expert_tool_registry,
    create_ocean_lead_tool_registry,
)


@pytest.fixture
def store(tmp_path):
    value = RequestStore(tmp_path / "state.sqlite3")
    value.create_research_task(workspace_id="ws", task_id="task", title="Research")
    yield value
    value.close()


def call(store, action, **kwargs):
    reward = kwargs.pop("belief_reward", None)
    return exploration_action(store, "ws", "task", ExplorationInput(action=action, **kwargs),
                              belief_reward=reward)


def candidate(name):
    return {"idea": name, "rationale": "Distinct mechanism", "test": "Compare independent data"}


def observation(store, task_id="task"):
    return store.record_research_observation(
        ResearchObservationDraft(
            workspace_id="ws",
            task_id=task_id,
            request_id="req",
            kind="result",
            statement=f"Independent experiment {uuid.uuid4()} contradicts the initial explanation",
            outcome="supported",
        )
    ).observation_id


def assignment(question=None):
    return OceanAssignmentInput(
        research_question=question,
        plan_goal=question or "Plot temperature",
        todos=[
            {
                "todo_id": "inspect",
                "question": "Inspect available evidence",
                "why_this_expert": "Data coverage",
                "profile_id": "data_reproducibility_expert",
                "expected_outputs": ["answer"],
                "answer_standard": {"sufficient_if": "Report available variables"},
            }
        ],
        dispatch=["inspect"],
    )


async def test_research_assignment_initializes_root_and_returns_loop_context(store, tmp_path):
    # Assignment now creates v2 even while historical engine cases remain below.
    from oceanx.exploration import ExplorationInput as CurrentInput
    from oceanx.exploration import exploration_action as current_action

    def call(store, action, **kwargs):
        return current_action(store, "ws", "task", CurrentInput(action=action, **kwargs))

    question = "坎佩切湾持续偏暖的可能成因是什么？"
    dispatched = []

    async def sink(payload, context):
        # Root is durable before the expert starts. No new fields reach WorkOrder parsing.
        assert call(store, "read")["goal"] == question
        assert {"plan_goal", "todos", "dispatch"} <= set(payload)
        assert not set(payload) - {"plan_goal", "todos", "dispatch", "research_test_ids"}
        dispatched.append(payload)
        observation(store)
        return {"results": [{"summary": "Data inspected"}]}

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
    context = ToolExecutionContext(
        cwd=tmp_path, request_id="req", turn_id="turn", tool_call_id="call", operation_id="op"
    )
    result = await tool.execute(assignment(question), context)
    assert not result.is_error
    payload = json.loads(result.output)
    tree = payload["research_exploration"]
    assert tree["mode"] == "iterative"
    assert tree["recommendation"] == {"action": "assess", "node_ids": ["root"]}
    assert tree["recent_evidence"]
    assert tree["tree_text"] == question
    assert "Interpret evidence before adjudicating state" in payload["research_next_step"]
    # A transport replay neither dispatches the expert again nor resets the tree.
    replay = await tool.execute(assignment(question), context)
    assert replay.metadata["replayed"]
    assert len(dispatched) == 1
    call(store, "propose", expected_revision=1,
         candidates=[{"id": "A", "claim": "A", "relation_to_children": "alternatives"}],
         tests=[{"id": "TA", "targets": ["A"], "method": "Compare data", "feasible": True,
                 "cost": 1, "discriminates": [{"outcome": "observed", "interpretation": "Support A"}]}])
    call(store, "pause", expected_revision=2)
    resumed = await tool.execute(assignment(question).model_copy(update={"research_test_ids": ("TA",)}), ToolExecutionContext(cwd=tmp_path))
    assert not resumed.is_error
    assert call(store, "read")["children"][0]["claim"] == "A"
    assert call(store, "read")["active"]


async def test_direct_assignment_does_not_create_or_resume_tree(store, tmp_path):
    async def sink(payload, context):
        assert "research_question" not in payload
        return {"results": []}

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
    context = ToolExecutionContext(cwd=tmp_path)
    result = await tool.execute(assignment(), context)
    assert not result.is_error
    assert "research_exploration" not in json.loads(result.output)
    assert call(store, "read")["revision"] == 0
    call(store, "start", expected_revision=0, goal="Old research", mode="ideas")
    call(store, "pause", expected_revision=1)
    await tool.execute(assignment(), context)
    assert call(store, "read")["revision"] == 2
    assert not call(store, "read")["active"]


def test_lazy_tree_and_roles(store):
    assert call(store, "read")["active"] is False
    assert (
        store._connection.execute("SELECT count(*) FROM research_exploration_trees").fetchone()[0]
        == 0
    )
    services = OceanToolServices(
        workspace_id="ws", provider_id="test", store=store, task_id="task", skill_role="coordinator"
    )
    assert create_ocean_lead_tool_registry(services).get("ocean_exploration")
    assert create_ocean_expert_tool_registry(services).get("ocean_exploration") is None
    assert create_ocean_discussion_tool_registry(services).get("ocean_exploration") is None


def test_resume_bounded_tree_and_task_cleanup(store):
    call(
        store, "start", expected_revision=0, goal="Explore mechanisms", mode="ideas", node_budget=2
    )
    result = call(
        store,
        "propose",
        expected_revision=1,
        candidates=[candidate("Heat flux"), candidate("Advection")],
    )
    assert result["recommendation"]["action"] == "stop"
    call(store, "pause", expected_revision=2)
    reopened = RequestStore(store.path)
    try:
        assert call(reopened, "read")["active"] is False
        result = call(reopened, "resume", expected_revision=3, mode="iterative")
        assert result["recommendation"] == {"action": "test", "node_id": "idea_1"}
    finally:
        reopened.close()
    store.delete_research_task(task_id="task", expected_task_revision=None)
    assert (
        store._connection.execute("SELECT count(*) FROM research_exploration_trees").fetchone()[0]
        == 0
    )


def test_feedback_corrects_aggregates_and_explores_other_branch(store):
    call(store, "start", expected_revision=0, goal="Mechanisms", mode="iterative", node_budget=2)
    call(store, "propose", expected_revision=1, candidates=[candidate("A"), candidate("B")])
    evidence_id = observation(store)
    feedback = {
        "outcome": "contradicted",
        "summary": "Eliminates one explanation",
        "evidence_ids": [evidence_id],
        "information_gain": 0.8,
        "branch_status": "exhausted",
    }
    result = call(store, "record", expected_revision=2, node_id="idea_1", feedback=feedback)
    assert result["recommendation"] == {"action": "test", "node_id": "idea_2"}
    feedback.update(outcome="inconclusive", information_gain=0.2, summary="Later correction")
    call(store, "record", expected_revision=3, node_id="idea_1", feedback=feedback)
    first = call(store, "read")["children"][0]
    assert first["attempts"] == 1
    assert first["reward_sum"] == 0  # Hand-written gain no longer drives search.
    assert "feedback_history" not in call(store, "read", node_id="idea_1")["path"][0]


def test_guards_are_atomic(store):
    call(store, "start", expected_revision=0, goal="Mechanisms", mode="ideas", node_budget=2)
    with pytest.raises(ValueError, match="Duplicate"):
        call(store, "propose", expected_revision=1, candidates=[candidate("A"), candidate("a")])
    assert call(store, "read")["nodes_used"] == 0
    call(store, "propose", expected_revision=1, candidates=[candidate("A")])
    with pytest.raises(RequestStoreError, match="revision"):
        call(store, "pause", expected_revision=1)
    store.create_research_task(workspace_id="ws", task_id="other", title="Other")
    with pytest.raises(ValueError, match="Evidence"):
        call(
            store,
            "record",
            expected_revision=2,
            node_id="idea_1",
            feedback={
                "outcome": "supported",
                "summary": "Wrong task",
                "evidence_ids": [observation(store, "other")],
            },
        )
    with pytest.raises(RequestStoreError, match="bound"):
        exploration_action(store, "other_workspace", "task", ExplorationInput(action="read"))
    call(store, "pause", expected_revision=2)
    with pytest.raises(ValueError, match="paused"):
        call(store, "propose", expected_revision=3, candidates=[candidate("B")])


def test_deferred_work_cannot_earn_reward_and_requires_no_scientific_evidence():
    with pytest.raises(ValueError, match="Deferred"):
        ExplorationInput(
            action="record",
            expected_revision=1,
            feedback={
                "outcome": "deferred",
                "summary": "Code failed",
                "information_gain": 1,
            },
        )
    with pytest.raises(ValueError, match="observation IDs"):
        ExplorationInput(
            action="record",
            expected_revision=1,
            feedback={
                "outcome": "contradicted",
                "summary": "Unsupported claim",
            },
        )


def test_gap_selection_ignores_legacy_rewards(store):
    call(store, "start", expected_revision=0, goal="Mechanisms", mode="iterative", node_budget=8)
    call(
        store,
        "propose",
        expected_revision=1,
        candidates=[candidate("A"), candidate("B"), candidate("C")],
    )
    for index, reward in enumerate([0, 0, 1], start=1):
        call(
            store,
            "record",
            expected_revision=index + 1,
            node_id=f"idea_{index}",
            belief_reward={"reward": reward},
            feedback={
                "outcome": "inconclusive",
                "summary": "A bounded new constraint on the mechanism",
                "evidence_ids": [observation(store)],
                "information_gain": 0,
            },
        )
    assert call(store, "read")["recommendation"] == {"action": "expand", "node_id": "idea_1"}
    call(
        store,
        "propose",
        expected_revision=5,
        node_id="idea_1",
        candidates=[candidate("A condition")],
    )
    branch = call(store, "read", node_id="idea_4")
    assert [node["idea"] for node in branch["path"]] == ["A", "A condition"]
    assert "├── A [inconclusive]\n│   └── A condition [proposed]" in branch["tree_text"]
    assert "└── C [inconclusive]" in branch["tree_text"]


async def test_real_tool_receipt_and_role_guard(store, tmp_path):
    services = OceanToolServices(
        workspace_id="ws", provider_id="test", store=store, task_id="task", skill_role="coordinator"
    )
    tool = OceanExplorationTool(services)
    context = ToolExecutionContext(
        cwd=tmp_path,
        request_id="req",
        turn_id="turn",
        tool_call_id="call",
        operation_id="op",
    )
    result = await tool.execute(
        ExplorationInput(action="start", expected_revision=0, goal="Ideas", mode="ideas"), context
    )
    assert not result.is_error
    assert json.loads(result.output)["revision"] == 1
    replay = await tool.execute(
        ExplorationInput(action="start", expected_revision=0, goal="Ideas", mode="ideas"),
        context,
    )
    assert not replay.is_error
    assert replay.metadata["replayed"]
    assert call(store, "read")["revision"] == 1
    denied = OceanExplorationTool(
        OceanToolServices(
            workspace_id="ws",
            provider_id="test",
            store=store,
            task_id="task",
            skill_role="statistical_expert",
        )
    )
    assert (await denied.execute(ExplorationInput(action="read"), context)).is_error


async def test_exploration_through_langchain_adapter(store, tmp_path):
    registry = create_ocean_lead_tool_registry(
        OceanToolServices(
            workspace_id="ws",
            provider_id="test",
            store=store,
            task_id="task",
            skill_role="coordinator",
        )
    )
    tool = next(
        t
        for t in registry.as_langchain_tools(
            cwd=tmp_path,
            operation_id_factory=lambda request, turn, call: f"op_{call}",
        )
        if t.name == "ocean_exploration"
    )

    async def invoke(call_id, args):
        message = await tool.ainvoke(
            {"name": tool.name, "type": "tool_call", "id": call_id, "args": args},
            config={"configurable": {"request_id": "req_adapter"}},
        )
        assert message.status == "success", message.content
        return json.loads(message.content)

    assert (await invoke("read", {"action": "read"}))["active"] is False
    assert (
        await invoke(
            "start",
            {
                "action": "start",
                "expected_revision": 0,
                "goal": "Mechanisms",
                "mode": "ideas",
            },
        )
    )["revision"] == 1
    await invoke(
        "propose",
        {
            "action": "propose",
            "expected_revision": 1,
            "candidates": [
                {"id": "flux", "claim": "Heat flux", "relation_to_children": "alternatives"},
                {"id": "advection", "claim": "Advection", "relation_to_children": "prerequisites"},
            ],
        },
    )
    await invoke(
        "branch",
        {
            "action": "propose",
            "expected_revision": 2,
            "node_id": "advection",
            "candidates": [{"id": "upstream", "claim": "Upstream warm-water transport", "relation_to_children": "alternatives"}],
        },
    )
    branch = await invoke("read_branch", {"action": "read", "node_id": "upstream"})
    assert branch["hypotheses"]["upstream"]["parent"] == "advection"
    paused = await invoke("pause", {
        "action": "pause", "expected_revision": 3,
        "completion_summary": "The requested mechanism shortlist and tests are supplied.",
    })
    assert paused["active"] is False
    assert paused["stop_decision"]["exit"] == "ideas_delivered"
    assert not paused["stop_decision"]["question_resolved"]
    assert "shortlist" in paused["stop_decision"]["summary"]
    with pytest.raises(ValueError, match="not applicable"):
        await invoke("invalid", {"action": "read", "mode": "ideas"})


def test_migrate_existing_database_with_backup(tmp_path):
    path = tmp_path / "existing.sqlite3"
    old = RequestStore(path)
    old.create_research_task(workspace_id="ws", task_id="task", title="Preserved")
    old._connection.execute("DROP TABLE research_exploration_trees")
    old._connection.execute("DELETE FROM schema_migrations WHERE version=47")
    old.close()
    upgraded = RequestStore(path)
    try:
        assert call(upgraded, "read")["revision"] == 0
        assert list((tmp_path / "backups").glob("workspace-before-v47-*.sqlite3"))
    finally:
        upgraded.close()


def prepare_branches(store, budget=8):
    call(
        store,
        "start",
        expected_revision=0,
        goal="Root question",
        mode="iterative",
        node_budget=budget,
    )
    call(store, "propose", expected_revision=1, candidates=[candidate("A"), candidate("B")])


def record_branch(store, key, status="open", outcome="supported", gain=0.5, expandable=True):
    revision = call(store, "read")["revision"]
    return call(
        store,
        "record",
        expected_revision=revision,
        node_id=key,
        feedback={
            "outcome": outcome,
            "summary": "Coordinator assessed evidence and feasible continuation",
            "evidence_ids": [] if outcome == "deferred" else [observation(store)],
            "information_gain": gain,
            "branch_status": status,
            "expandable": expandable,
        },
    )


def test_supported_branches_expand_instead_of_finishing(store):
    prepare_branches(store)
    record_branch(store, "idea_1")
    result = record_branch(store, "idea_2", gain=0.1)
    assert result["recommendation"] == {"action": "expand", "node_id": "idea_1"}
    call(
        store,
        "propose",
        expected_revision=result["revision"],
        node_id="idea_1",
        candidates=[candidate("A refined")],
    )
    assert call(store, "read", node_id="idea_3")["path"][-1]["idea"] == "A refined"
    # Even an early pause must retain the unfulfilled next step, not claim completion.
    state = call(store, "read")
    paused = call(store, "pause", expected_revision=state["revision"])
    assert paused["stop_decision"]["reason"] == "interrupted"
    assert not paused["stop_decision"]["question_resolved"]
    assert paused["stop_decision"]["pending_recommendation"]


def test_solved_branch_continues_unless_coordinator_resolves_original_goal(store):
    prepare_branches(store)
    result = record_branch(store, "idea_1", status="solved")
    assert result["recommendation"] == {"action": "test", "node_id": "idea_2"}
    assert result["active_branch_id"] == "idea_2"
    paused = call(store, "pause", expected_revision=result["revision"])
    assert not paused["stop_decision"]["question_resolved"]
    resumed = call(store, "resume", expected_revision=paused["revision"])
    assert resumed["recommendation"] == {"action": "test", "node_id": "idea_2"}
    with pytest.raises(ValueError, match="critical gaps"):
        call(store, "pause", expected_revision=resumed["revision"], completion_summary="Done")
    resumed = record_branch(store, "idea_2", status="solved")
    paused = call(
        store, "pause", expected_revision=resumed["revision"],
        completion_summary="The user asked whether A can explain the event; its necessary checks passed.",
    )
    assert paused["stop_decision"]["reason"] == "question_resolved"
    assert paused["stop_decision"]["question_resolved"]
    reopened = RequestStore(store.path)
    try:
        assert call(reopened, "read")["stop_decision"] == paused["stop_decision"]
    finally:
        reopened.close()
    assert call(store, "read")["children"][1]["attempts"] == 1
    assert "[solved]" in paused["tree_text"]


def test_closed_branch_selects_another_and_all_closed_remain_unresolved(store):
    prepare_branches(store)
    first = record_branch(store, "idea_1", status="exhausted", outcome="contradicted")
    assert first["recommendation"] == {"action": "test", "node_id": "idea_2"}
    result = record_branch(store, "idea_2", status="blocked", outcome="deferred", gain=0)
    assert result["recommendation"]["reason"] == "no_eligible_branch"
    assert not result["recommendation"]["question_resolved"]
    assert call(store, "read")["children"][1]["outcome"] == "deferred"


def test_budget_exhaustion_tests_pending_nodes_before_pausing(store):
    prepare_branches(store, budget=2)
    result = record_branch(store, "idea_1")
    assert result["recommendation"] == {"action": "test", "node_id": "idea_2"}
    result = record_branch(store, "idea_2")
    assert result["recommendation"] == {
        "action": "pause", "reason": "node_budget_exhausted", "question_resolved": False,
    }


def test_children_require_parent_synthesis_before_completion(store):
    prepare_branches(store)
    record_branch(store, "idea_1")
    state = record_branch(store, "idea_2", status="solved")
    state = call(store, "propose", expected_revision=state["revision"], node_id="idea_1",
                 candidates=[candidate("Decisive followup")])
    state = record_branch(store, "idea_3", status="solved")
    assert state["recommendation"] == {"action": "synthesize", "node_id": "idea_1"}
    with pytest.raises(ValueError, match="critical gaps"):
        call(store, "pause", expected_revision=state["revision"], completion_summary="Done")
    state = record_branch(store, "idea_1", status="solved")
    assert call(store, "pause", expected_revision=state["revision"], completion_summary="Checks resolved")["stop_decision"]["question_resolved"]


def test_resume_recomputes_recommendation_without_branch_lock(store):
    prepare_branches(store)
    state = record_branch(store, "idea_1", status="exhausted", outcome="contradicted")
    assert state["active_branch_id"] == "idea_2"
    # Reopening the first branch must not steal the new branch's commitment.
    state = record_branch(store, "idea_1", gain=1)
    assert state["recommendation"] == {"action": "test", "node_id": "idea_2"}
    paused = call(store, "pause", expected_revision=state["revision"])
    reopened = RequestStore(store.path)
    try:
        assert call(reopened, "read")["recommendation"] == {"action": "paused"}
        state = call(reopened, "resume", expected_revision=paused["revision"])
        assert state["active_branch_id"] == "idea_2"
        assert state["recommendation"] == {"action": "test", "node_id": "idea_2"}
    finally:
        reopened.close()


def test_local_child_support_cannot_resolve_whole_question(store):
    prepare_branches(store)
    call(
        store, "propose", expected_revision=2, node_id="idea_1", candidates=[candidate("A detail")]
    )
    state = record_branch(store, "idea_3", status="solved")
    assert state["recommendation"] == {"action": "test", "node_id": "idea_2"}
    assert state["stop_decision"] is None
    assert call(store, "read")["revision"] == 4


def test_unexpandable_parent_does_not_hide_open_child(store):
    prepare_branches(store, budget=3)
    record_branch(store, "idea_1")
    state = record_branch(store, "idea_2", status="blocked", outcome="deferred", gain=0)
    call(
        store,
        "propose",
        expected_revision=state["revision"],
        node_id="idea_1",
        candidates=[candidate("A detail")],
    )
    state = call(store, "read")
    call(
        store,
        "record",
        expected_revision=state["revision"],
        node_id="idea_1",
        feedback={
            "outcome": "supported",
            "summary": "No further siblings needed; child still needs testing",
            "evidence_ids": [observation(store)],
            "expandable": False,
        },
    )
    assert call(store, "read")["recommendation"] == {"action": "test", "node_id": "idea_3"}


def test_closed_nodes_do_not_force_more_experiments(store):
    prepare_branches(store)
    record_branch(store, "idea_1", status="solved", outcome="contradicted")
    state = record_branch(store, "idea_2", status="solved")
    assert state["recommendation"]["action"] == "pause"
    assert state["stop_decision"] is None


def test_completion_summary_is_only_accepted_for_pause(store):
    with pytest.raises(ValueError, match="not applicable"):
        call(store, "start", expected_revision=0, goal="Question", mode="iterative",
             completion_summary="Answered")
    prepare_branches(store)
    with pytest.raises(ValueError):
        call(store, "pause", expected_revision=2, completion_summary="  ")
    assert call(store, "read")["revision"] == 2


def test_priority_then_cost_selects_critical_gap_without_sampling(store):
    call(store, "start", expected_revision=0, goal="Mechanisms", mode="iterative")
    state = call(store, "propose", expected_revision=1, candidates=[
        {**candidate("Optional"), "priority": "supporting", "cost": "low"},
        {**candidate("Conflict expensive"), "priority": "contradiction", "cost": "high"},
        {**candidate("Conflict cheap"), "priority": "contradiction", "cost": "low"},
    ])
    assert state["recommendation"] == {"action": "test", "node_id": "idea_3"}
    state = record_branch(store, "idea_3", status="blocked", outcome="deferred", gain=0)
    assert state["recommendation"] == {"action": "test", "node_id": "idea_2"}
    state = record_branch(store, "idea_2", status="solved")
    # Optional work need not prevent a scientifically justified completion.
    state = call(store, "pause", expected_revision=state["revision"],
                 completion_summary="Critical conflict resolved; unavailable data disclosed.")
    assert state["stop_decision"]["question_resolved"]


def test_solved_cannot_hide_explicit_followup():
    from oceanx.exploration_legacy import Feedback

    with pytest.raises(ValueError, match="follow-up"):
        Feedback(outcome="supported", evidence_ids=["obs"], summary="Initial support",
                 branch_status="solved", follow_up="Resolve conflicting transport estimates")


def test_refuted_branches_can_seed_a_new_root_alternative(store):
    prepare_branches(store)
    record_branch(store, "idea_1", status="exhausted", outcome="contradicted")
    state = record_branch(store, "idea_2", status="blocked", outcome="deferred", gain=0)
    assert state["recommendation"]["action"] == "pause"
    state = call(store, "propose", expected_revision=state["revision"], node_id="root",
                 candidates=[{
                     "idea": "Reduced cooling could explain the remaining warming",
                     "rationale": "The refuted idea_1 transport explanation leaves a heat-budget gap",
                     "test": "Test the vertical exchange contribution with existing temperature and velocity",
                     "priority": "key_gap", "cost": "low",
                 }])
    assert state["recommendation"] == {"action": "test", "node_id": "idea_3"}
    node = call(store, "read", node_id="idea_3")["path"][-1]
    assert node["parent_id"] == "root"
    assert "idea_1" in node["rationale"]
    with pytest.raises(ValueError, match="critical gaps"):
        call(store, "pause", expected_revision=state["revision"], completion_summary="Done")
