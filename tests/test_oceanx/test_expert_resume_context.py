"""Recovery keeps the last error and archive paths without replaying old logs."""

from types import SimpleNamespace

from oceanx.expert_execution import ExpertCodeExecutionService
from oceanx.team.models import WorkstreamCheckpoint
from oceanx.team.orchestrator import OceanTeamOrchestrator, _ParticipantBinding


def test_probe_successes_do_not_hide_last_failure_from_resume(tmp_path):
    checkpoint = WorkstreamCheckpoint()
    origin = SimpleNamespace(
        checkpoint=checkpoint,
        resume_count=0,
        work_order=SimpleNamespace(session_round=1),
    )
    records = [
        SimpleNamespace(
            execution_id=f"codeexec_{i}",
            work_order_id="work",
            state="failed" if i == 0 else "succeeded",
            request={"purpose": "failed calculation" if i == 0 else "diagnostic"},
            result={
                "stdout": "metadata",
                "stderr": "ValueError: invalid shape" if i == 0 else "",
                "returncode": 1 if i == 0 else 0,
                "output_files": [],
                "logs": {
                    "stdout": f"/archive/{i}/stdout.txt",
                    "stderr": f"/archive/{i}/stderr.txt",
                },
            },
        )
        for i in range(5)
    ]
    orchestrator = object.__new__(OceanTeamOrchestrator)
    orchestrator.store = SimpleNamespace(
        list_code_executions=lambda _: records,
        get_team_work=lambda _: origin,
        list_request_code_executions=lambda **_: [],
    )
    order = SimpleNamespace(
        job_key=None, work_order_id="work", parent_request_id="request", todo_id=None
    )
    binding = _ParticipantBinding(
        workspace_id="ws",
        workspace_path=tmp_path,
        provider_id="fixture",
        task_id="task",
        work_order=order,
        checkpoint=checkpoint,
    )
    result = orchestrator._checkpoint_contract(binding)
    assert len(result["executions"]) == 4
    error = result["executions"][0]
    assert error["execution_id"] == "codeexec_0"
    assert error["execution_state"] == "failed"
    assert error["returncode"] == 1
    assert "invalid shape" in error["stderr_excerpt"]
    # Supplied durable paths must survive even in legacy rows without work_root.
    assert error["logs"]["stderr"] == "/archive/0/stderr.txt"


def test_older_manifest_retains_readable_logs_without_inline_replay(tmp_path):
    work_root = tmp_path / "expert"
    logs = work_root / "executions" / "codeexec_old" / "logs"
    logs.mkdir(parents=True)
    (logs / "stdout.txt").write_text("full scientific evidence" * 1000)
    origin = SimpleNamespace(
        checkpoint=WorkstreamCheckpoint(),
        work_order=SimpleNamespace(profile_id="expert", semantic_role="expert", session_round=1),
    )
    service = object.__new__(ExpertCodeExecutionService)
    service.store = SimpleNamespace(get_team_work=lambda _: origin)
    record = SimpleNamespace(
        execution_id="codeexec_old",
        work_order_id="work",
        state="succeeded",
        request={"purpose": "old measurement"},
        result={"work_root": str(work_root), "output_files": [], "stdout": "large evidence" * 1000},
    )
    allowed = []
    result = service._execution_manifest_entry(
        record,
        task_root=tmp_path,
        read_only_roots=allowed,
        include_private_logs=True,
        include_log_excerpts=False,
    )
    assert "stdout_excerpt" not in result
    assert result["logs"]["stdout"] == str(logs / "stdout.txt")
    assert logs in allowed
    assert (logs / "stdout.txt").read_text() == "full scientific evidence" * 1000
