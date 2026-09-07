from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from typer.testing import CliRunner

from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.curator_budget import CuratorBudgetExceeded, CuratorLimits, ReviewBudget
from oceanx.curator_evidence import CuratorEvidence, redact, round_context
from oceanx.skill_curator import SkillCurator, SkillCuratorDecision
from oceanx.skill_history import rollback_skill
from oceanx.task_results import TaskResultStore
from oceanx.team.models import ChildAuthority, ExpertResult, WorkOrder, WorkStatus
from tests.test_oceanx.test_skill_curator import _end_round, _TaskWorkspaces


@pytest.fixture
def store(tmp_path):
    store = RequestStore(tmp_path / "state.sqlite3")
    yield store
    store.close()


def note(store, task="task_one", request="req_one", workspace="ws_one", ended=True):
    store.create_research_task(workspace_id=workspace, task_id=task, title=task)
    saved = store.save_experience(
        workspace_id=workspace,
        task_id=task,
        request_id=request,
        agent_id="coordinator",
        agent_role="coordinator",
        text="Preserve grid masks; tested against the reference mean.",
        turn_id="turn_capture",
        tool_call_id="call_capture",
    )
    if ended:
        _end_round(store, workspace, task, request)
    return saved


def message(store, note, text, role="assistant"):
    return store.append_task_transcript_item(
        task_id=note.task_id,
        item_id="message_" + uuid4().hex,
        request_id=note.request_id,
        role=role,
        text=text,
    )


def decision(note):
    return SkillCuratorDecision(
        decision="create",
        experience_ids=(note.experience_id,),
        target_skill="mask-guidance",
        skill_markdown="---\nname: mask-guidance\ndescription: Preserve masks for spatial averages.\nmetadata:\n  roles: [coordinator]\n---\nPreserve source masks for spatial averages.\n",
        reason="Verified method, with a bounded applicability.",
        confidence=0.9,
    )


def test_capture_attaches_tool_and_message_position(store):
    saved = note(store)
    message(store, saved, "A correction")
    second = store.save_experience(
        workspace_id=saved.workspace_id,
        task_id=saved.task_id,
        request_id=saved.request_id,
        agent_id="coordinator",
        agent_role="coordinator",
        text="Another lesson",
        turn_id="turn_b",
        tool_call_id="call_b",
    )
    assert second.source_context["transcript_sequence_at_save"] == 1
    assert second.source_context["tool_call_id"] == "call_b"
    assert second.source_context["loaded_skills"] == []


def test_expert_source_identity_and_all_evidence_kinds(store, tmp_path):
    saved = note(store)
    with store._transaction() as db:
        db.execute(
            "INSERT INTO workspace_records (workspace_id,path,revision,updated_at) VALUES (?,?,1,?)",
            (saved.workspace_id, str(tmp_path), "2026-09-07T00:00:00+00:00"),
        )
    order = WorkOrder(
        work_order_id="work_evidence",
        task_id=saved.task_id,
        job_key="job_evidence",
        parent_request_id=saved.request_id,
        task_goal="Verify masks",
        semantic_role="Data & Reproducibility Expert",
        authority=ChildAuthority.EXPERT,
        workspace_revision=1,
    )
    store.create_team_work_order(workspace_id=saved.workspace_id, work_order=order)
    store.complete_team_work(
        ExpertResult(
            work_order_id=order.work_order_id,
            status=WorkStatus.COMPLETED,
            text="Validated mask against reference",
        )
    )
    store.record_resource_usage(
        usage_id="usage_evidence",
        workspace_id=saved.workspace_id,
        resource_kind="skill",
        resource_name="ocean-analysis-design",
        resource_version="v2",
        work_order_id=order.work_order_id,
        request_id="child_request",
        agent_id="expert_one",
    )
    with store._transaction() as db:
        db.execute(
            "INSERT INTO expert_session_message_history VALUES (?,?,?,?,1,?,?,?)",
            (
                saved.workspace_id,
                saved.task_id,
                "expert:Data & Reproducibility Expert",
                order.job_key,
                json.dumps({"role": "assistant", "content": "Validated", "reasoning": "HIDDEN"}),
                "hash",
                "2026-09-07T00:00:00+00:00",
            ),
        )
        db.execute(
            "INSERT INTO code_executions VALUES (?,?,?,?,?,'succeeded',?,?,?,?)",
            (
                "exec_evidence",
                saved.workspace_id,
                saved.task_id,
                order.work_order_id,
                "child_one",
                json.dumps({"code": "check_mask()"}),
                json.dumps({"stdout": "Mask matches reference"}),
                "2026-09-07T00:00:00+00:00",
                "2026-09-07T00:01:00+00:00",
            ),
        )
    expert_note = store.save_experience(
        workspace_id=saved.workspace_id,
        task_id=saved.task_id,
        request_id="child_request",
        work_order_id=order.work_order_id,
        agent_id="expert_one",
        agent_role="data_reproducibility_expert",
        text="Verified mask handling",
    )
    assert expert_note.request_id == saved.request_id
    assert expert_note.source_context["loaded_skills"][0]["resource_version"] == "v2"
    assert expert_note.source_context["execution_ids_at_save"] == ["exec_evidence"]
    assert expert_note.source_context["expert_history_sequence_at_save"] == 1
    evidence = CuratorEvidence(store, (expert_note,))
    for kind, identifier, expected in (
        ("results", order.work_order_id, "Validated mask"),
        ("executions", "exec_evidence", "Mask matches reference"),
        ("expert_messages", "1", "Validated"),
    ):
        assert evidence.read(expert_note.experience_id, kind)["records"]
        content = evidence.read(expert_note.experience_id, kind, identifier)["content"]
        assert expected in content and "HIDDEN" not in content


def test_learning_cli_rejects_missing_database_without_creating_one(tmp_path):
    from oceanx.cli import app

    result = CliRunner().invoke(app, ["learning-status", "--state-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "No existing state database" in result.output
    assert list(tmp_path.iterdir()) == []


def test_api_settings_keep_learning_budget(monkeypatch, tmp_path):
    from oceanx.cli import app
    from oceanx.model_config import OceanModelSetup, save_desktop_model_profiles

    monkeypatch.setenv("OCEANMIND_CONFIG_DIR", str(tmp_path))
    setup = OceanModelSetup(
        provider="openai", model="fixture-model", base_url=None, api_key="fixture-key"
    )
    save_desktop_model_profiles(
        setups={role: setup for role in ("coordinator", "expert", "skill_curator")}
    )
    # Write the setting through the same JSON configuration path users configure.
    config = tmp_path / "settings.json"
    payload = json.loads(config.read_text())
    payload["curator_limits"] = {"daily_token_budget": 1234}
    config.write_text(json.dumps(payload))
    save_desktop_model_profiles(
        setups={role: setup for role in ("coordinator", "expert", "skill_curator")}
    )
    assert CuratorLimits.from_settings().daily_token_budget == 1234

    from oceanx.storage import OceanPaths

    state_dir = tmp_path / "state"
    database = OceanPaths.for_state_root(state_dir).database
    store = RequestStore(database)
    store.close()
    status = CliRunner().invoke(app, ["learning-status", "--state-dir", str(state_dir)])
    assert status.exit_code == 0
    assert json.loads(status.output)["remaining_reservation_budget"] == 1234


def test_provider_usage_above_reservation_counts_toward_review_limit(store):
    budget = ReviewBudget(store, "ws_one", CuratorLimits(review_token_budget=30_000))
    call_id = budget.reserve([HumanMessage(content="test")], "schema")
    budget.finish(
        call_id,
        AIMessage(
            content="",
            usage_metadata={
                "input_tokens": 25_000,
                "output_tokens": 1_000,
                "total_tokens": 26_000,
            },
        ),
    )
    assert budget.reserved == 26_000
    with pytest.raises(CuratorBudgetExceeded, match="Per-review"):
        budget.reserve([HumanMessage(content="test")], "schema")


def test_read_scope_pagination_later_correction_and_redaction(store):
    saved = note(store)
    foreign = note(store, task="task_other", request="req_other", workspace="ws_other")
    private = message(store, foreign, "UNRELATED PRIVATE TASK")
    for i in range(10):
        message(store, saved, f"Evidence {i}")
    last = message(store, saved, 'Correction: not validated. api_key="secret-value" ' + "x" * 5000)
    evidence = CuratorEvidence(store, (saved,))
    first = evidence.read(saved.experience_id)
    second = evidence.read(saved.experience_id, cursor=first["next_cursor"])
    assert len(first["records"]) == 8
    assert len(second["records"]) == 3
    result = evidence.read(saved.experience_id, record_id=last.item_id)
    assert "not validated" in result["content"] and "secret-value" not in result["content"]
    assert result["next_offset"] == 4000
    assert (
        evidence.read(saved.experience_id, record_id=last.item_id, offset=4000)["next_offset"]
        is None
    )
    with pytest.raises(ValueError, match="not related"):
        evidence.read(saved.experience_id, record_id=private.item_id)
    with pytest.raises(ValueError, match="outside"):
        evidence.read(foreign.experience_id)
    with pytest.raises(ValueError):
        evidence.read(saved.experience_id, kind="/etc/passwd")


@pytest.mark.asyncio
async def test_unknown_or_active_round_does_not_call_reviewer(store, tmp_path):
    saved = note(store, ended=False)
    calls = []

    async def reviewer(*args):
        calls.append(args)
        return []

    curator = SkillCurator(
        store=store,
        task_results=TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path)),
        reviewer=reviewer,
    )
    assert await curator.review_workspace(workspace_id=saved.workspace_id) == ()
    _end_round(store, saved.workspace_id, saved.task_id, saved.request_id)
    store.begin_task_request(
        task_id=saved.task_id, request_id="followup", expected_task_revision=None
    )
    assert await curator.review_workspace(workspace_id=saved.workspace_id) == ()
    assert calls == []


@pytest.mark.asyncio
async def test_publication_rechecks_late_correction(store, tmp_path):
    saved = note(store)

    async def reviewer(payload, read_skill, read_evidence):
        message(store, saved, "The proposed method was invalidated by a later check")
        return [decision(saved)]

    curator = SkillCurator(
        store=store,
        task_results=TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path)),
        reviewer=reviewer,
    )
    assert await curator.review_workspace(workspace_id=saved.workspace_id) == ()
    assert store.list_evolved_skill_revisions(workspace_id=saved.workspace_id) == ()
    assert store.list_saved_experiences(workspace_id=saved.workspace_id)[0].reviewed_at is None


def test_read_budget_and_changed_task_are_enforced(store):
    saved = note(store)
    record = message(store, saved, "x" * 5000)
    evidence = CuratorEvidence(store, (saved,), max_chars=64)
    assert len(evidence.read(saved.experience_id, record_id=record.item_id)["content"]) == 64
    with pytest.raises(ValueError, match="budget"):
        evidence.read(saved.experience_id)
    evidence = CuratorEvidence(store, (saved,))
    message(store, saved, "New correction")
    with pytest.raises(ValueError, match="changed"):
        evidence.read(saved.experience_id)


def test_budget_survives_restart_and_ambiguous_failures(tmp_path):
    path = tmp_path / "budget.sqlite3"
    limits = CuratorLimits(review_token_budget=100_000, daily_token_budget=10_000)
    store = RequestStore(path)
    budget = ReviewBudget(store, "ws_one", limits)
    budget.reserve([HumanMessage(content="test")], "schema")
    store.close()  # An in-flight request may have billed: do not refund on crash.
    store = RequestStore(path)
    try:
        with pytest.raises(CuratorBudgetExceeded, match="Daily"):
            ReviewBudget(store, "ws_two", limits).reserve([HumanMessage(content="test")], "schema")
    finally:
        store.close()


def test_upgrade_preserves_existing_experiences_and_reopens(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    original = RequestStore(path)
    saved = note(original)
    original.close()
    # Reconstruct the previous schema in a disposable fixture, retaining its data.
    with sqlite3.connect(path) as db:
        db.executescript("""
            ALTER TABLE saved_experiences DROP COLUMN source_context_json;
            ALTER TABLE resource_usage_records DROP COLUMN request_id;
            ALTER TABLE resource_usage_records DROP COLUMN agent_id;
            DROP TABLE curator_calls;
            DROP TABLE curator_review_attempts;
            DELETE FROM schema_migrations WHERE version = 46;
        """)
    upgraded = RequestStore(path)
    try:
        restored = upgraded.list_saved_experiences(workspace_id=saved.workspace_id)[0]
        assert restored.experience_id == saved.experience_id
        assert restored.text == saved.text and restored.source_context == {}
        assert (
            upgraded._connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 46"
            ).fetchone()[0]
            == 1
        )
    finally:
        upgraded.close()
    reopened = RequestStore(path)
    reopened.close()


def test_review_budget_denies_before_call(store):
    with pytest.raises(CuratorBudgetExceeded, match="Per-review"):
        ReviewBudget(store, "ws", CuratorLimits(review_token_budget=1)).reserve(
            [HumanMessage(content="test")], "schema"
        )


def test_redaction_includes_private_keys_urls_and_hidden_reasoning():
    value = redact(
        {
            "api_key": "private",
            "reasoning": "hidden",
            "content": [
                {"type": "thinking", "thinking": "secret thinking"},
                {
                    "type": "text",
                    "text": "https://alice:password@host/path?token=private /Users/alice/private/data.nc Bearer abc.def.ghi",
                },
            ],
        }
    )
    text = json.dumps(value)
    assert all(
        secret not in text
        for secret in (
            "password",
            "token=private",
            "abc.def.ghi",
            "hidden",
            "secret thinking",
            "/Users/alice",
        )
    )


def install(store, saved, content="Use a verified mask", expected=0):
    return store.install_evolved_skill_revision(
        workspace_id=saved.workspace_id,
        skill_name="mask-guidance",
        description="Masks",
        roles=("coordinator",),
        content=content,
        source_experience_ids=(saved.experience_id,),
        reviewer_model="reviewer",
        review_reason="Checked",
        expected_version=expected,
    )


def test_rollback_creates_new_revision_and_preserves_sources(store):
    first = note(store)
    install(store, first)
    second = note(store, task="task_two", request="req_two")
    install(store, second, "Changed method", 1)
    restored = rollback_skill(
        store,
        workspace_id=first.workspace_id,
        skill_name="mask-guidance",
        version=1,
        expected_version=2,
    )
    assert restored.version == 3 and restored.content == "Use a verified mask"
    assert restored.source_experience_ids == (first.experience_id,)
    assert len(store.list_evolved_skill_revisions(workspace_id=first.workspace_id)) == 3
    assert (
        store.list_saved_experiences(workspace_id=first.workspace_id)[0].status.value == "absorbed"
    )
    with pytest.raises(RequestStoreError, match="changed"):
        rollback_skill(
            store,
            workspace_id=first.workspace_id,
            skill_name="mask-guidance",
            version=1,
            expected_version=2,
        )
    with pytest.raises(RequestStoreError):
        rollback_skill(
            store, workspace_id="foreign", skill_name="mask-guidance", version=1, expected_version=3
        )


def test_store_rejects_task_change_atomically(store):
    saved = note(store)
    snapshot = round_context(store, saved)
    message(store, saved, "Late correction")
    with pytest.raises(RequestStoreError, match="changed"):
        store.install_evolved_skill_revision(
            workspace_id=saved.workspace_id,
            skill_name="mask-guidance",
            description="Masks",
            roles=("coordinator",),
            content="method",
            source_experience_ids=(saved.experience_id,),
            reviewer_model="reviewer",
            review_reason="checked",
            expected_contexts={saved.experience_id: snapshot},
        )
    assert (
        store.list_saved_experiences(workspace_id=saved.workspace_id)[0].status.value == "pending"
    )


@pytest.mark.asyncio
async def test_empty_inbox_never_runs_automatic_recap(store, tmp_path):
    async def reviewer(*args):
        pytest.fail("No recap was requested")

    curator = SkillCurator(
        store=store,
        task_results=TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path)),
        reviewer=reviewer,
    )
    assert await curator.review_workspace(workspace_id="ws") == ()


@pytest.mark.asyncio
async def test_actual_model_evidence_tool_round(store, tmp_path, monkeypatch):
    import oceanx.skill_curator as module
    from oceanx.model_config import OceanModelProfile

    saved = note(store)
    record = message(store, saved, "User correction: use area weights")

    class Model:
        calls = 0

        def bind_tools(self, tools):
            assert {tool.name if hasattr(tool, "name") else tool.__name__ for tool in tools} == {
                "read_skill",
                "read_evidence",
                "SkillCuratorDecisionBatch",
            }
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_evidence",
                            "args": {
                                "experience_id": saved.experience_id,
                                "record_id": record.item_id,
                            },
                            "id": "read",
                        }
                    ],
                )
            assert "area weights" in messages[-1].content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "SkillCuratorDecisionBatch",
                        "args": {"decisions": [decision(saved).model_dump()]},
                        "id": "publish",
                    }
                ],
            )

    model = Model()
    profile = OceanModelProfile("review", "review", "openai", "fake", None, "slot", "unused")
    monkeypatch.setattr(module, "load_skill_reviewer_profile", lambda: profile)

    def create(profile):
        assert profile.max_tokens == 4096
        return model

    monkeypatch.setattr(module, "create_chat_model", create)
    curator = SkillCurator(
        store=store, task_results=TaskResultStore(task_workspaces=_TaskWorkspaces(tmp_path))
    )
    assert len(await curator.review_workspace(workspace_id=saved.workspace_id)) == 1
    assert model.calls == 2
    assert store._connection.execute("SELECT COUNT(*) FROM curator_calls").fetchone()[0] == 2
    attempt = store._connection.execute("SELECT * FROM curator_review_attempts").fetchone()
    assert attempt["state"] == "finished" and record.item_id in attempt["evidence_reads_json"]
