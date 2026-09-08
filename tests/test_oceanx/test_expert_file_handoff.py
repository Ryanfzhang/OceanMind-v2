"""Experts retrieve durable results across rounds without executing more code."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.expert_execution import (
    ExpertCodeExecutionError,
    ExpertCodeExecutionResult,
    ExpertCodeExecutionService,
)
from oceanx.tools import OceanToolServices, create_ocean_expert_tool_registry


@pytest.fixture
def session(tmp_path):
    root = tmp_path / "expert"
    root.mkdir()
    # Both rounds resolve to the same logical expert, independent of round id.
    works = {
        name: SimpleNamespace(
            workspace_id="ws",
            work_order=SimpleNamespace(
                task_id="task",
                job_key="job",
            ),
        )
        for name in ("round1", "round2")
    }
    service = object.__new__(ExpertCodeExecutionService)
    service.store = SimpleNamespace(
        get_research_task=lambda _: SimpleNamespace(workspace_id="ws"),
        get_team_work=works.get,
    )
    service.task_workspaces = SimpleNamespace(expert_session_root=lambda task, job: root)
    return service, root


def test_followup_reads_full_prior_result_without_python(session, tmp_path):
    service, root = session
    log = root / "stdout.txt"
    content = "海洋结果\n" * 3_000
    log.write_text(content)
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws",
            provider_id="fixture",
            store=service.store,
            task_id="task",
            work_order_id="round2",
            expert_code_execution=service,
        )
    )
    tool = next(t for t in registry.list_tools() if t.name == "ocean_read_file")
    recovered, offset = "", 0
    while True:
        result = asyncio.run(
            tool.execute(
                tool.input_model(path=str(log), offset=offset), ToolExecutionContext(cwd=tmp_path)
            )
        )
        assert not result.is_error
        payload = json.loads(result.output)
        recovered += payload["content"]
        if payload["eof"]:
            break
        offset = payload["next_offset"]
    assert recovered == content


def test_truncated_code_result_exposes_readable_full_log(session, tmp_path):
    service, root = session
    logs = root / "executions" / "codeexec_test" / "logs"
    logs.mkdir(parents=True)
    content = "start\n" + "x" * 3_000 + "IMPORTANT METRIC=42" + "y" * 3_000
    (logs / "stdout.txt").write_text(content)
    result = ExpertCodeExecutionResult(
        execution_id="codeexec_test",
        state="succeeded",
        returncode=0,
        stdout=content,
        stderr="",
        duration_seconds=1,
        output_files=(),
        outputs=(),
        output_bytes=0,
        limit_trigger=None,
        code_path="analysis.py",
        work_root=str(root),
        result_bundle_path=str(root / "result.json"),
        result_fingerprint="fixture",
        attempt_number=1,
        attempt_limit=10,
    )

    async def run_python(**kwargs):
        return result

    service.run_python = run_python
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws",
            provider_id="fixture",
            store=service.store,
            task_id="task",
            work_order_id="round2",
            expert_child_id="child",
            expert_code_execution=service,
        )
    )
    code_tool = next(t for t in registry.list_tools() if t.name == "ocean_expert_run_code")
    response = asyncio.run(
        code_tool.execute(
            code_tool.input_model(purpose="measure", code="print('fixture')"),
            ToolExecutionContext(cwd=tmp_path),
        )
    )
    assert not response.is_error
    payload = json.loads(response.output)
    assert payload["stdout_truncated"]
    assert "IMPORTANT METRIC" not in payload["stdout"]
    assert payload["result_bundle_path"] == str(root / "result.json")
    recovered = service.read_expert_file(
        workspace_id="ws",
        task_id="task",
        work_order_id="round2",
        path=payload["logs"]["stdout"],
        limit=12_000,
    )
    assert recovered["content"] == content


@pytest.mark.parametrize("kind", ["outside", "symlink", "binary", "missing", "wrong_task"])
def test_read_retains_expert_session_boundary(session, tmp_path, kind):
    service, root = session
    outside = tmp_path / "sibling.txt"
    outside.write_text("sibling private log")
    path = outside
    if kind == "symlink":
        path = root / "link.txt"
        path.symlink_to(outside)
    elif kind == "binary":
        path = root / "array.bin"
        path.write_bytes(b"\x00\xff")
    elif kind == "missing":
        path = root / "missing"
    with pytest.raises(ExpertCodeExecutionError):
        service.read_expert_file(
            workspace_id="ws",
            task_id="other" if kind == "wrong_task" else "task",
            work_order_id="round2",
            path=str(path),
        )
