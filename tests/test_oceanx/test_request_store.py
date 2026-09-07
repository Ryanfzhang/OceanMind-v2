"""Durability and idempotency tests for the Phase 1 RequestStore."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from oceanx.backend.store import RequestIdConflict, RequestStore, WorkspaceRevisionConflict
from oceanx.protocol.v2.models import (
    ProtocolErrorPayload,
    RequestCancelledEvent,
    RequestCancelledPayload,
    RequestCompletedEvent,
    RequestCompletedPayload,
    RequestFailedEvent,
    RequestFailedPayload,
    WorkspaceChangedEvent,
    WorkspaceChangedPayload,
    new_event_id,
    parse_request,
)
from oceanx.team.models import (
    CoordinatorAnswerBasis,
    CoordinatorDecision,
    CoordinatorResult,
)


def test_fresh_store_never_creates_retired_execution_tables(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert not tables.intersection(
        {
            "analysis_runs",
            "analysis_attempts",
            "analysis_run_events",
            "analysis_executor_leases",
            "team_work_attempts",
            "multi_agent_tasks",
            "multi_agent_proposals",
            "task_map_states",
            "review_records",
            "verification_records",
        }
    )
    assert {"team_work_records", "code_executions"}.issubset(tables)
    store.close()


def _request(
    request_id: str,
    *,
    request_type: str = "workspace.open",
    payload: dict[str, object] | None = None,
    session_id: str = "ses_store",
    client_id: str = "client_desktop_store",
    workspace_id: str = "ws_store",
    expected_workspace_revision: int | None = None,
):
    return parse_request(
        {
            "protocol_version": 2,
            "request_id": request_id,
            "type": request_type,
            "payload": payload or {"path": "/tmp/ocean-workspace"},
            "context": {
                "session_id": session_id,
                "client_id": client_id,
                "workspace_id": workspace_id,
            },
            "expected_workspace_revision": expected_workspace_revision,
        }
    )


def _completed_event(request, result: dict[str, object], revision: int | None = None):
    return RequestCompletedEvent(
        protocol_version=2,
        event_id=new_event_id(),
        session_id=request.context.session_id,
        workspace_id=request.context.workspace_id,
        request_id=request.request_id,
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        type="request.completed",
        payload=RequestCompletedPayload(result=result, workspace_revision=revision),
    )


def _cancelled_event(request, reason: str = "cancelled"):
    return RequestCancelledEvent(
        protocol_version=2,
        event_id=new_event_id(),
        session_id=request.context.session_id,
        workspace_id=request.context.workspace_id,
        request_id=request.request_id,
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        type="request.cancelled",
        payload=RequestCancelledPayload(reason=reason),
    )


def _interrupted_event(record):
    return RequestFailedEvent(
        protocol_version=2,
        event_id=new_event_id(),
        session_id=record.session_id,
        workspace_id=record.workspace_id,
        request_id=record.request_id,
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        type="request.failed",
        payload=RequestFailedPayload(
            error=ProtocolErrorPayload(
                code="request_interrupted",
                message="Backend restarted before this request finished",
                recoverable=True,
                details={},
            )
        ),
    )


def test_same_request_id_replays_semantic_identity_but_rejects_payload_conflict(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    first = _request("req_idempotent", session_id="ses_first", client_id="client_first")
    replay = _request("req_idempotent", session_id="ses_second", client_id="client_second")

    reserved = store.reserve(first, principal="user:local:desktop")
    retried = store.reserve(replay, principal="user:local:desktop")

    assert reserved.created is True
    assert retried.created is False
    assert retried.record.canonical_hash == reserved.record.canonical_hash

    conflict = _request("req_idempotent", payload={"path": "/tmp/different"})
    with pytest.raises(RequestIdConflict):
        store.reserve(conflict, principal="user:local:desktop")


def test_terminal_event_is_durable_before_connection_replay(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    request = _request("req_terminal")
    store = RequestStore(path)
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    event = _completed_event(request, {"ok": True}, revision=4)
    stored = store.commit_terminal(request.request_id, event)
    store.close()

    restarted = RequestStore(path)
    replay = restarted.get_request(request.request_id)

    assert stored.terminal is True
    assert replay is not None
    assert replay.state == "completed"
    assert replay.terminal_event is not None
    assert replay.terminal_event.event_id == event.event_id
    assert restarted.get_event(event.event_id) == replay.terminal_event


def test_workflow_progress_update_preserves_lifecycle_state(tmp_path: Path) -> None:
    store = RequestStore(tmp_path / "state.sqlite3")
    task = store.create_research_task(
        workspace_id="ws_progress",
        title="Progress state",
        task_id="task_progress",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_progress",
            "type": "session.submit",
            "payload": {"text": "Inspect this task."},
            "context": {
                "session_id": "ses_progress",
                "workspace_id": "ws_progress",
                "client_id": "client_progress",
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
        workspace_id="ws_progress",
    )
    store.transition_task_workflow(
        request_id=request.request_id,
        state="working",
        activity="Expert work completed",
    )

    updated = store.update_task_workflow_progress(
        request_id=request.request_id,
        activity="Coordinator conclusion ready",
        checkpoint={"phase": "coordinator_result"},
    )

    assert updated is not None
    assert updated.state == "working"
    assert updated.activity == "Coordinator conclusion ready"
    assert updated.checkpoint == {"phase": "coordinator_result"}


def test_paper_selection_shortlist_survives_task_snapshot_reload(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = RequestStore(path)
    task = store.create_research_task(
        workspace_id="ws_paper_selection",
        title="Literature shortlist",
        task_id="task_paper_selection",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_paper_selection",
            "type": "session.submit",
            "payload": {"text": "Find relevant papers."},
            "context": {
                "session_id": "ses_paper_selection",
                "workspace_id": "ws_paper_selection",
                "client_id": "client_paper_selection",
                "task_id": task.task_id,
            },
            "expected_workspace_revision": 0,
            "expected_task_revision": 0,
        }
    )
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    store.create_interaction(
        interaction_id="int_paper_selection",
        request_id=request.request_id,
        task_id=task.task_id,
        workspace_id="ws_paper_selection",
        session_id="ses_paper_selection",
        principal="user:local:desktop",
        question="Choose papers for full-text review",
        kind="paper_selection",
        options=(
            {
                "paper_id": "paper_1",
                "title": "Loop Current variability",
                "topic": "Upper-ocean heat transport",
            },
        ),
    )
    store.close()

    restarted = RequestStore(path)
    interaction = restarted.task_snapshot(task_id=task.task_id).interactions[0]

    assert interaction.kind == "paper_selection"
    assert interaction.options[0]["paper_id"] == "paper_1"
    assert interaction.as_payload()["options"][0]["topic"] == "Upper-ocean heat transport"
    restarted.close()


def test_terminal_request_closes_orphaned_tool_receipts(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    request = _request("req_terminal_tool")
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)
    operation_id = "op_terminal_tool"
    store.begin_tool_call(
        operation_id=operation_id,
        request_id=request.request_id,
        turn_id="turn_terminal_tool",
        tool_call_id="call_terminal_tool",
        tool_name="ocean_assign",
    )

    store.commit_terminal(
        request.request_id,
        _completed_event(request, {"ok": True}),
    )

    record = store.get_tool_call_record(operation_id)
    assert record is not None
    assert record.state == "completed"
    assert record.result is not None
    assert record.result["is_error"] is True
    assert record.result["metadata"]["request_terminal"] is True


def test_workspace_mutation_domain_event_and_terminal_commit_together(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    request = _request("req_workspace_atomic", expected_workspace_revision=0)
    store.reserve(request, principal="user:local:desktop")
    store.mark_in_progress(request.request_id)

    def events(snapshot, previous_revision: int, change: str | None):
        changed = None
        if change is not None:
            changed = WorkspaceChangedEvent(
                protocol_version=2,
                event_id=new_event_id(),
                session_id=request.context.session_id,
                workspace_id=request.context.workspace_id,
                request_id=request.request_id,
                sequence=0,
                timestamp=datetime.now(timezone.utc),
                type="workspace.changed",
                payload=WorkspaceChangedPayload(
                    previous_revision=previous_revision,
                    workspace_revision=snapshot.revision,
                    change=change,
                ),
            )
        return changed, _completed_event(request, snapshot.as_payload(), snapshot.revision)

    commit = store.commit_workspace_open(
        request_id=request.request_id,
        workspace_id="ws_store",
        path="/tmp/ocean-workspace",
        expected_revision=0,
        event_factory=events,
    )

    assert commit.snapshot.revision == 1
    assert commit.changed_event is not None
    assert store.get_event(commit.changed_event.event_id) == commit.changed_event
    durable = store.get_request(request.request_id)
    assert durable is not None and durable.state == "completed"
    assert durable.terminal_event == commit.terminal_event
    assert store.workspace_snapshot("ws_store").revision == 1


def test_workspace_revision_conflict_does_not_change_workspace(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    initial = _request("req_workspace_initial", expected_workspace_revision=0)
    store.reserve(initial, principal="user:local:desktop")
    store.mark_in_progress(initial.request_id)
    store.commit_workspace_open(
        request_id=initial.request_id,
        workspace_id="ws_store",
        path="/tmp/ocean-workspace",
        expected_revision=0,
        event_factory=lambda snapshot, _previous, _change: (
            None,
            _completed_event(initial, snapshot.as_payload(), snapshot.revision),
        ),
    )

    stale = _request("req_workspace_stale", expected_workspace_revision=0)
    store.reserve(stale, principal="user:local:desktop")
    store.mark_in_progress(stale.request_id)
    with pytest.raises(WorkspaceRevisionConflict) as exc_info:
        store.commit_workspace_open(
            request_id=stale.request_id,
            workspace_id="ws_store",
            path="/tmp/other-workspace",
            expected_revision=0,
            event_factory=lambda snapshot, _previous, _change: (
                None,
                _completed_event(stale, snapshot.as_payload(), snapshot.revision),
            ),
        )

    assert exc_info.value.current_revision == 1
    assert store.workspace_snapshot("ws_store").path == "/tmp/ocean-workspace"
    assert store.get_request(stale.request_id).state == "in_progress"


def test_recovery_and_operation_checkpoints_do_not_repeat_work(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    active = _request("req_active")
    store.reserve(active, principal="user:local:desktop")
    store.mark_in_progress(active.request_id)

    store.begin_tool_call(
        operation_id="op_fixture",
        request_id=active.request_id,
        turn_id="turn_fixture",
        tool_call_id="tool_fixture",
        tool_name="analysis_work_write",
    )

    first_result = store.record_operation_result(
        operation_id="op_fixture",
        request_id=active.request_id,
        turn_id="turn_fixture",
        tool_call_id="tool_fixture",
        result={"artifact_id": "artifact_fixture"},
    )
    replayed_result = store.record_operation_result(
        operation_id="op_fixture",
        request_id=active.request_id,
        turn_id="turn_other",
        tool_call_id="tool_other",
        result={"artifact_id": "should_not_replace"},
    )
    recovered = store.recover_incomplete(_interrupted_event)

    assert first_result == replayed_result == {"artifact_id": "artifact_fixture"}
    tool_call = store.get_tool_call_record("op_fixture")
    assert tool_call is not None
    assert tool_call.state == "completed"
    assert tool_call.tool_name == "analysis_work_write"
    assert tool_call.result == {"artifact_id": "artifact_fixture"}
    assert len(recovered) == 1
    assert recovered[0].type == "request.failed"
    record = store.get_request(active.request_id)
    assert record is not None
    assert record.state == "interrupted"
    assert record.terminal_event is not None
    assert record.terminal_event.payload.error.code == "request_interrupted"


def test_recovery_completes_request_from_durable_coordinator_receipt(
    tmp_path: Path,
) -> None:
    """A restart after result submission redelivers instead of rerunning the model."""

    path = tmp_path / "state.sqlite3"
    store = RequestStore(path)
    task = store.create_research_task(
        workspace_id="ws_receipt_recovery",
        title="Receipt recovery",
        task_id="task_receipt_recovery",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_receipt_recovery",
            "type": "session.submit",
            "payload": {"text": "Return the saved conclusion."},
            "context": {
                "session_id": "ses_receipt_recovery",
                "workspace_id": "ws_receipt_recovery",
                "client_id": "client_receipt_recovery",
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
        workspace_id="ws_receipt_recovery",
    )
    result = CoordinatorResult(
        decision=CoordinatorDecision.ANSWERED,
        answer_basis=CoordinatorAnswerBasis.GENERAL_KNOWLEDGE,
        answer_markdown="This conclusion was durably accepted before the restart.",
        confidence=0.95,
    )
    assert store.record_coordinator_result(
        request_id=request.request_id,
        result=result,
    ) == result
    store.close()

    restarted = RequestStore(path)

    def completed_event(record, recovered_result: CoordinatorResult):
        return RequestCompletedEvent(
            protocol_version=2,
            event_id=new_event_id(),
            session_id=record.session_id,
            workspace_id=record.workspace_id,
            task_id=record.task_id,
            request_id=record.request_id,
            sequence=0,
            timestamp=datetime.now(timezone.utc),
            type="request.completed",
            payload=RequestCompletedPayload(
                result={
                    "assistant_text": recovered_result.answer_markdown,
                    "coordinator_result": recovered_result.model_dump(mode="json"),
                    "recovered_from_durable_receipt": True,
                }
            ),
        )

    recovered = restarted.recover_incomplete(
        _interrupted_event,
        completed_event,
    )

    assert len(recovered) == 1
    assert recovered[0].type == "request.completed"
    durable_request = restarted.get_request(request.request_id)
    assert durable_request is not None
    assert durable_request.state == "completed"
    assert restarted.get_coordinator_result(request.request_id) == result
    workflow = restarted.get_task_workflow(request.request_id)
    assert workflow is not None and workflow.state == "completed"
    snapshot = restarted.task_snapshot(task_id=task.task_id)
    assert snapshot.task.active_request_id is None
    assert snapshot.transcript[-1].role == "assistant"
    assert snapshot.transcript[-1].text == result.answer_markdown
    assert snapshot.transcript[-1].interrupted is False
    restarted.close()


def test_delete_task_removes_durable_results_and_expert_memory(tmp_path: Path) -> None:
    """A completed analysis task can be deleted with all private conversation state."""

    store = RequestStore(tmp_path / "state.sqlite3")
    task = store.create_research_task(
        workspace_id="ws_delete_complete",
        title="Completed analysis",
        task_id="task_delete_complete",
    )
    request = parse_request(
        {
            "protocol_version": 2,
            "request_id": "req_delete_complete",
            "type": "session.submit",
            "payload": {"text": "Complete an analysis before deletion."},
            "context": {
                "session_id": "ses_delete_complete",
                "workspace_id": "ws_delete_complete",
                "client_id": "client_delete_complete",
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
    result = CoordinatorResult(
        decision=CoordinatorDecision.ANSWERED,
        answer_basis=CoordinatorAnswerBasis.GENERAL_KNOWLEDGE,
        answer_markdown="A durable result that should be removed with its task.",
        confidence=0.9,
    )
    store.record_coordinator_result(request_id=request.request_id, result=result)
    store.save_expert_session_checkpoint(
        workspace_id=task.workspace_id,
        task_scope=task.task_id,
        participant_key="ocean_process_expert",
        job_key="job_delete_complete",
        work_order_id="work_delete_complete",
        messages=[{"role": "assistant", "content": "Private expert analysis"}],
        compaction_generation=0,
    )
    store.commit_task_terminal_with_checkpoint(
        request_id=request.request_id,
        task_id=task.task_id,
        terminal_event=_completed_event(request, {"assistant_text": result.answer_markdown}),
        messages=[{"role": "assistant", "content": result.answer_markdown}],
        provider_id="provider_fixture",
        model_id="model_fixture",
        runtime_profile_fingerprint="runtime_fixture",
        system_prompt_fingerprint="prompt_fixture",
        compaction_generation=0,
        usage_summary={},
    )
    completed = store.get_research_task(task.task_id)
    assert completed is not None

    store.delete_research_task(
        task_id=task.task_id,
        expected_task_revision=completed.task_revision,
    )

    assert store.get_research_task(task.task_id) is None
    assert store.get_request(request.request_id) is None
    assert store.get_coordinator_result(request.request_id) is None
    assert (
        store.get_expert_session_checkpoint(
            workspace_id=task.workspace_id,
            task_scope=task.task_id,
            participant_key="ocean_process_expert",
            job_key="job_delete_complete",
        )
        is None
    )
    assert store.get_expert_session_message_history(
        workspace_id=task.workspace_id,
        task_scope=task.task_id,
        participant_key="ocean_process_expert",
        job_key="job_delete_complete",
    ) == ()
    store.close()


def test_cancellation_commits_target_and_control_request_once(tmp_path: Path):
    store = RequestStore(tmp_path / "state.sqlite3")
    target = _request("req_target")
    cancel = _request(
        "req_cancel",
        request_type="request.cancel",
        payload={"target_request_id": target.request_id},
    )
    for request in (target, cancel):
        store.reserve(request, principal="user:local:desktop")
        store.mark_in_progress(request.request_id)

    commit = store.commit_cancellation(
        cancel_request_id=cancel.request_id,
        target_request_id=target.request_id,
        target_event=_cancelled_event(target),
        terminal_event=_completed_event(cancel, {"target_request_id": target.request_id}),
    )

    assert commit.target.state == "cancelled"
    assert commit.target_terminal_event is not None
    assert store.get_request(cancel.request_id).state == "completed"
