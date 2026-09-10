import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from oceanx_delivery import collect, execution_output_root


class Record(NS):
    def model_dump(self, **kwargs):
        return vars(self)


@pytest.fixture
def delivery(tmp_path):
    output = tmp_path / "execution" / "executions" / "execution_1" / "outputs"
    output.mkdir(parents=True)
    data = b"benchmark-derived-view"
    (output / "view.nc").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    candidate = Record(
        path="outputs/view.nc", output_name="view.nc", execution_id="execution_1",
        supporting_output_names=(), sha256=sha, source_handle=None,
        result_kind="report",
    )
    work = NS(result=NS(outputs=[candidate]), work_order=NS(input_refs=[
        Record(ref="temperature@v1"), Record(ref="salinity@v1"),
    ]))
    from oceanx.expert_recovery import execution_result_fingerprint
    bundle = {"execution_id": "execution_1", "output_root": str(output)}
    bundle_path = tmp_path / "execution/result-bundles/execution_1.json"
    bundle_path.parent.mkdir()
    bundle_path.write_text(json.dumps(bundle))
    execution = NS(execution_id="execution_1", workspace_id="workspace", task_id="task", result={
        "work_root": str(tmp_path / "execution"),
        "result_bundle_path": str(bundle_path),
        "result_fingerprint": execution_result_fingerprint(bundle),
        "outputs": [{"name": "view.nc", "bytes": len(data), "sha256": sha}],
    })
    services = NS(workspace_id="workspace", task_id="task", store=NS(
        list_task_team_work=lambda **kw: [work], get_code_execution=lambda key: execution,
    ))
    args = NS(accepted_paths=[candidate.path], review_summary="Reviewed result")
    return services, args, tmp_path / "delivery", execution, work


def test_multisource_netcdf_delivery_and_idempotence(delivery):
    services, args, dest, _, _ = delivery
    receipt = collect(services, args, dest)
    from pathlib import Path
    manifest = Path(receipt["manifest"])
    data = json.loads(manifest.read_text())
    assert data["desktop_published"] is False
    assert len(data["outputs"][0]["input_refs"]) == 2
    assert (manifest.parent / data["outputs"][0]["files"][0]["path"]).is_file()
    assert collect(services, args, dest) == receipt
    assert manifest == dest / "delivery_manifest.json"
    assert (dest / "analysis.ipynb").is_file()


def test_actual_persisted_execution_payload(delivery):
    from oceanx.expert_execution import ExpertCodeExecutionResult
    services, args, dest, execution, _ = delivery
    data = execution.result
    execution.result = ExpertCodeExecutionResult(
        execution_id=execution.execution_id, state="succeeded", returncode=0,
        stdout="", stderr="", duration_seconds=1.0,
        output_files=("view.nc",), outputs=tuple(data["outputs"]),
        output_bytes=data["outputs"][0]["bytes"], limit_trigger=None,
        code_path="analysis.py", work_root=data["work_root"],
        result_bundle_path=data["result_bundle_path"],
        result_fingerprint=data["result_fingerprint"], attempt_number=1, attempt_limit=10,
    ).as_payload()
    assert "output_root" not in execution.result
    assert collect(services, args, dest)["accepted_paths"] == args.accepted_paths


@pytest.mark.parametrize("problem", ["wrong_id", "outside_root", "missing_manifest"])
def test_manifest_validation(delivery, problem):
    from pathlib import Path
    services, args, dest, execution, _ = delivery
    path = Path(execution.result["result_bundle_path"])
    bundle = json.loads(path.read_text())
    if problem == "wrong_id":
        bundle["execution_id"] = "other"
    elif problem == "outside_root":
        bundle["output_root"] = str(dest)
    else:
        path.unlink()
    if problem != "missing_manifest":
        path.write_text(json.dumps(bundle))
    with pytest.raises((ValueError, FileNotFoundError)):
        collect(services, args, dest)
    assert not dest.exists()


@pytest.mark.parametrize("problem", ["changed", "missing", "symlink", "other_task"])
def test_invalid_files_never_receive_success_receipt(delivery, problem):
    from pathlib import Path
    services, args, dest, execution, work = delivery
    path = execution_output_root(execution) / "view.nc"
    if problem == "changed":
        path.write_bytes(b"tampered")
    elif problem == "missing":
        path.unlink()
    elif problem == "symlink":
        path.rename(path.with_suffix(".original"))
        path.symlink_to(path.with_suffix(".original"))
    elif problem == "other_task":
        execution.task_id = "other_task"
    else:
        work.result.outputs *= 2
    with pytest.raises(ValueError):
        collect(services, args, dest)
    assert not dest.exists()


def test_duplicate_continuation_references_are_not_ambiguous(delivery):
    services, args, dest, _, work = delivery
    work.result.outputs *= 2
    services.store.list_task_team_work = lambda **kw: [work, work]
    result = collect(services, args, dest)
    assert len(json.loads((dest / "delivery_manifest.json").read_text())["outputs"]) == 1
    assert result["manifest"]


def test_updated_file_replaces_same_delivery_location(delivery):
    from pathlib import Path
    services, args, dest, execution, work = delivery
    collect(services, args, dest)
    data = b"revised result"
    (execution_output_root(execution) / "view.nc").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    execution.result["outputs"][0].update(bytes=len(data), sha256=sha)
    work.result.outputs[0].sha256 = sha
    collect(services, args, dest)
    manifest = json.loads((dest / "delivery_manifest.json").read_text())
    assert len(manifest["outputs"]) == 1
    assert (dest / manifest["outputs"][0]["files"][0]["path"]).read_bytes() == data


def test_expert_name_collision_has_explicit_selection(delivery):
    from copy import deepcopy
    services, args, dest, _, work = delivery
    work.work_order.job_key = "expert_a"
    other = deepcopy(work)
    other.work_order.job_key = "expert_b"
    other.result.outputs[0].execution_id = "execution_2"
    services.store.list_task_team_work = lambda **kw: [work, other]
    with pytest.raises(ValueError, match="expert_a::outputs/view.nc"):
        collect(services, args, dest)
    args.accepted_paths = ["expert_a::outputs/view.nc", "expert_b::outputs/view.nc"]
    collect(services, args, dest)
    entries = json.loads((dest / "delivery_manifest.json").read_text())["outputs"]
    assert len({e["files"][0]["path"] for e in entries}) == 2


def test_flat_delivery_is_collected_without_history(delivery, tmp_path):
    from collect_oceanx import collect_run
    services, args, _, _, _ = delivery
    attempt = tmp_path / "run/Q/attempt-1"
    collect(services, args, attempt)
    (attempt / "result.json").write_text('{"id":"Q","status":"completed"}')
    (attempt / "answer.md").write_text("Result")
    target = collect_run(tmp_path / "run") / "Q/attempt-1"
    assert (target / "delivery_manifest.json").is_file()
    assert (target / "analysis.ipynb").is_file()
    assert not (target / "delivery").exists()


def test_runs_have_independent_delivery_directories(delivery, tmp_path):
    services, args, dest, _, _ = delivery
    first = collect(services, args, dest)
    before = (dest / "delivery_manifest.json").read_bytes()
    second = collect(services, args, tmp_path / "other-run")
    assert first["manifest"] != second["manifest"]
    assert (dest / "delivery_manifest.json").read_bytes() == before


def test_import_does_not_patch_production_tool():
    from oceanx.tools import OceanPublishOutputsTool
    assert OceanPublishOutputsTool.execute.__module__ == "oceanx.tools"


def test_real_netcdf_is_rendered_with_backend_template(delivery, monkeypatch):
    import sys
    from pathlib import Path

    from oceanx.scientific_view import ScientificFigure

    monkeypatch.setenv("OCEAN_BENCH_RENDER_PYTHON", sys.executable)
    services, args, dest, execution, work = delivery
    figure = ScientificFigure(plot_kind="section", title="Temperature section")
    figure.panel(x=[20., 21., 22.], y=[0., 50.], y_reverse=True).heatmap(
        [[27., 26., 25.], [20., 19., 18.]], colorbar_label="Temperature"
    )
    path = figure.save(execution_output_root(execution) / "view.nc")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    execution.result["outputs"][0].update(bytes=len(data), sha256=sha)
    work.result.outputs[0].sha256 = sha
    work.result.outputs[0].result_kind = "interactive_view"
    result = collect(services, args, dest)
    png = Path(result["figures"][0]["png"])
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (Path(result["manifest"]).parent / "render_figures.py").is_file()


def test_paper_selection_all_and_other_interactions_unchanged():
    from run_oceanx import benchmark_interaction_answer

    from oceanx.batch import QueryCase
    case = QueryCase(id="test", query="Investigate")
    answer = benchmark_interaction_answer(case, {
        "kind": "paper_selection", "options": [{"paper_id": "p1"}, {"paper_id": "p2"}],
    })
    assert json.loads(answer) == {"selected_paper_ids": ["p1", "p2"]}
    assert benchmark_interaction_answer(case, {"kind": "question", "question": "Choose region"}) is None
