"""Exercise the real save -> collect -> candidate -> publish -> hydrate boundary."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.artifacts.models import ArtifactRef
from oceanx.expert_deliverables import ExpertDeliverableService, hydrate_ocean_view_netcdf
from oceanx.expert_execution import ExpertCodeExecutionService
from oceanx.scientific_view import ScientificFigure
from oceanx.task_results import TaskResultRef, TaskResultStore
from oceanx.team.models import EvidenceRef
from oceanx.tools import (
    OceanExpertRunCodeTool,
    OceanPublishOutputsTool,
    OceanToolServices,
    candidate_outputs_from_execution,
)


@pytest.mark.parametrize("kind", [
    "spatial_map", "section", "hovmoller", "profile", "time_series", "scatter", "ts_diagram",
])
def test_saved_view_reaches_publication_with_evidence(tmp_path, monkeypatch, kind):
    task_id, workspace_id, work_id = "task_pipeline", "ws_pipeline", "work_pipeline"
    execution_id = "codeexec_pipeline"
    work_root = tmp_path / "analysis" / "expert"
    output_root = work_root / "executions" / execution_id / "outputs"
    manifest = output_root.parent / "result-events.jsonl"
    monkeypatch.setenv("OCEAN_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("OCEAN_RESULT_MANIFEST", str(manifest))
    monkeypatch.delenv("OCEAN_INPUT_MANIFEST", raising=False)
    claim = "The plotted values support this conclusion."
    figure = ScientificFigure(plot_kind=kind, title="Saved view", conclusions=(claim,))
    panel = figure.panel(x=[-91, -90], y=[24, 25], x_label="X", y_label="Y")
    if kind in {"spatial_map", "section", "hovmoller"}:
        panel.field2d([[27, 28], [26, 27.5]], variable="temperature", units="degC")
    elif kind in {"scatter", "ts_diagram"}:
        panel.scatter()
    else:
        panel.line()
    saved = figure.save("view.nc")
    errors = []
    events = ExpertCodeExecutionService._read_result_events(
        manifest, output_files=("view.nc",), errors=errors,
    )
    assert not errors
    assert len(events) == 1
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    execution_result = {
        "work_root": str(work_root), "output_files": ["view.nc"],
        "outputs": [{"name": "view.nc", "bytes": saved.stat().st_size, "sha256": digest}],
        "discovered_results": events,
    }
    candidates = candidate_outputs_from_execution(
        execution_id=execution_id, execution_result=execution_result,
    )
    assert len(candidates) == 1
    assert candidates[0].claims == (claim,)
    dataset = ArtifactRef(artifact_id="dataset_pipeline", version=1)
    work = SimpleNamespace(
        workspace_id=workspace_id,
        work_order=SimpleNamespace(
            work_order_id=work_id, task_id=task_id, parent_request_id="req_pipeline",
            input_refs=(EvidenceRef(kind="dataset", ref=dataset.key),),
        ),
        result=SimpleNamespace(outputs=candidates),
    )
    execution = SimpleNamespace(
        execution_id=execution_id, workspace_id=workspace_id, task_id=task_id,
        work_order_id=work_id, state="succeeded", result=execution_result,
    )
    bindings = []

    class Store:
        def list_task_team_work(self, **_kwargs):
            return [work]

        def get_code_execution(self, key):
            return execution if key == execution_id else None

        def get_team_work(self, key):
            return work if key == work_id else None

        def get_artifact(self, *, workspace_id, ref):
            return SimpleNamespace(artifact_type="dataset") if ref == dataset else None

        def record_workstream_results(self, _work_id, refs, *, output_bindings, result_metadata):
            bindings.append((refs, output_bindings, result_metadata))

        def record_research_observation(self, _draft):
            pass

    workspaces = SimpleNamespace(
        ensure_task_root=lambda _task: tmp_path,
        paths=SimpleNamespace(cache=tmp_path / "cache"),
    )
    store = Store()
    results = TaskResultStore(task_workspaces=workspaces)
    deliverables = ExpertDeliverableService(
        store=store, task_workspaces=workspaces, task_results=results,
    )
    tool = OceanPublishOutputsTool(OceanToolServices(
        workspace_id=workspace_id, task_id=task_id, provider_id="fixture", store=store,
        expert_deliverables=deliverables,
    ))
    response = asyncio.run(tool.execute(
        tool.input_model(accepted_paths=("outputs/view.nc",), review_summary=claim),
        ToolExecutionContext(cwd=tmp_path),
    ))
    assert not response.is_error, response.output
    published = json.loads(response.output)["published"][0]
    assert published["render_status"] == "interactive"
    ref = TaskResultRef.model_validate(published["result_ref"])
    record = results.get(ref)
    assert record.content["output_path"] == "outputs/view.nc"
    assert record.execution_output_names == ("view.nc",)
    assert [f.path for f in record.files] == ["data.nc"]
    published_path = results.file_path(ref=ref, relative_path="data.nc")
    assert hashlib.sha256(published_path.read_bytes()).hexdigest() == digest
    assert hydrate_ocean_view_netcdf(published_path)
    assert bindings[0][1] == {"view.nc": (execution_id, ref)}
    assert bindings[0][2]["claims"] == (claim,)


def test_legacy_map_alias_and_latest_save_are_normalized(tmp_path):
    manifest = tmp_path / "events.jsonl"
    base = {"schema_version": "ocean-result-event/v1", "kind": "interactive_view",
            "view_kind": "spatial_map", "title": "Old", "field_output": "map.nc"}
    latest = {**base, "title": "Revised", "conclusions": ["Updated evidence."]}
    manifest.write_text("\n".join(json.dumps(e) for e in [base, latest]))
    events = ExpertCodeExecutionService._read_result_events(manifest, output_files=("map.nc",))
    assert len(events) == 1
    assert events[0]["data_output"] == "map.nc"
    assert "field_output" not in events[0]
    assert events[0]["title"] == "Revised"


def test_rejected_declaration_is_reported_without_losing_valid_sibling(tmp_path):
    manifest = tmp_path / "events.jsonl"
    valid = {"schema_version": "ocean-result-event/v1", "kind": "interactive_view",
             "view_kind": "spatial_map", "title": "Valid", "data_output": "map.nc"}
    invalid = {**valid, "field_output": "another.nc"}
    missing = {**valid, "data_output": "missing.nc"}
    manifest.write_text("\n".join([json.dumps(valid), json.dumps(invalid),
                                    json.dumps(missing), "{broken"]))
    errors = []
    events = ExpertCodeExecutionService._read_result_events(
        manifest, output_files=("map.nc", "another.nc"), errors=errors,
    )
    assert len(events) == 1
    assert len(errors) == 3
    assert "conflicting" in errors[0]
    assert "missing.nc" in errors[1]
    assert "invalid JSON" in errors[2]


def test_collection_failure_reaches_expert_without_discarding_valid_candidates(tmp_path):
    event = {"schema_version": "ocean-result-event/v1", "kind": "interactive_view",
             "view_kind": "profile", "title": "Valid", "data_output": "profile.nc"}

    class ExecutionService:
        async def run_python(self, **_kwargs):
            return SimpleNamespace(
                execution_id="codeexec_diagnostics", discovered_results=(event,),
                outputs=({"name": "profile.nc", "bytes": 64, "sha256": "a" * 64},),
                as_payload=lambda: {
                    "state": "succeeded", "stdout": "saved", "stderr": "",
                    "invalid_candidate_results": ["Saved-result event 2: missing data_output"],
                },
            )

    tool = OceanExpertRunCodeTool(OceanToolServices(
        workspace_id="ws_diag", task_id="task_diag", provider_id="fixture", store=None,
        work_order_id="work_diag", expert_child_id="child_diag",
        expert_code_execution=ExecutionService(),
    ))
    response = asyncio.run(tool.execute(
        tool.input_model(purpose="Read completed results", code="pass"),
        ToolExecutionContext(cwd=tmp_path),
    ))
    payload = json.loads(response.output)
    assert payload["candidate_result_count"] == 1
    assert payload["publication_state"] == "candidate_declaration_error"
    assert payload["invalid_candidate_results"]
    assert "do not repeat scientific computation" in payload["result_collection_message"]
