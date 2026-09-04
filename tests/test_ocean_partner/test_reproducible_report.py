"""A report carries the actual code execution as an openable reproducibility package."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocean_partner.artifacts.models import ArtifactRef
from ocean_partner.backend.events import BackendClient
from ocean_partner.backend.host import OceanBackendHost
from ocean_partner.task_results import TaskResultRef
from ocean_partner.team.models import ChildAuthority, EvidenceRef, WorkOrder


@pytest.mark.asyncio
async def test_expert_execution_produces_executed_notebook_and_report_package(
    tmp_path: Path,
) -> None:
    host = OceanBackendHost(tmp_path / "state", write_frame=lambda _frame: None)
    try:
        async def send(_event: object) -> None:
            return None

        client = BackendClient(
            transport="stdio", expected_client_kind="desktop", sender=send
        )
        await host.event_bus.register(client)
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_report_handshake",
                "type": "system.handshake",
                "payload": {
                    "client_kind": "desktop",
                    "client_version": "test",
                    "supported_protocol_versions": [2],
                },
            },
        )
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_report_workspace",
                "type": "workspace.open",
                "payload": {"path": str(tmp_path)},
                "context": {
                    "client_id": client.client_id,
                    "session_id": client.session_id,
                    "workspace_id": "ws_report",
                },
                "expected_workspace_revision": 0,
            },
        )
        source = tmp_path / "numbers.csv"
        source.write_text("value\n1\n2\n3\n", encoding="utf-8")
        task = host.store.create_research_task(workspace_id="ws_report", title="Report proof")
        await host.router.handle_payload(
            client,
            {
                "protocol_version": 2,
                "request_id": "req_report_dataset",
                "type": "dataset.import",
                "payload": {
                    "relative_path": "numbers.csv",
                    "materialization_level": "materialized_snapshot",
                    "materialization_acknowledged": True,
                    "title": "Pinned numeric fixture",
                    "artifact_id": "dataset_report_fixture",
                },
                "context": {
                    "client_id": client.client_id,
                    "session_id": client.session_id,
                    "workspace_id": "ws_report",
                    "task_id": task.task_id,
                },
                "expected_workspace_revision": host.store.workspace_snapshot(
                    "ws_report"
                ).revision,
            },
        )
        dataset_artifact = host.store.get_artifact(
            workspace_id="ws_report",
            ref=ArtifactRef(artifact_id="dataset_report_fixture", version=1),
        )
        assert dataset_artifact is not None
        dataset = SimpleNamespace(
            artifact=dataset_artifact,
            workspace_revision=host.store.workspace_snapshot("ws_report").revision,
        )
        order = WorkOrder(
            work_order_id="work_report_fixture",
            parent_request_id="req_report_fixture",
            task_goal="Compute a checked mean and document it.",
            profile_id="statistical_inference_expert",
            semantic_role="Statistical Inference Expert",
            authority=ChildAuthority.EXPERT,
            input_refs=(
                EvidenceRef(kind="dataset", ref=dataset.artifact.ref.key, locator="source_1"),
            ),
            outcome_intents=("report",),
            done_when="Return the value, method, checks, and reproducibility package.",
            workspace_revision=dataset.workspace_revision,
        )
        host.store.create_team_work_order(workspace_id="ws_report", work_order=order)
        host.store.mark_team_work_running(order.work_order_id)
        execution = await host.expert_code_execution.run_python(
            workspace_id="ws_report",
            task_id=task.task_id,
            work_order_id=order.work_order_id,
            child_id=order.work_order_id,
            purpose="Compute the arithmetic mean of the pinned CSV values.",
            code="""
import csv
import json
import os
from pathlib import Path

manifest = json.loads(Path(os.environ["OCEAN_INPUT_MANIFEST"]).read_text())
source = Path(manifest["inputs"][0]["path"])
with source.open(newline="") as stream:
    values = [float(row["value"]) for row in csv.DictReader(stream)]
mean = sum(values) / len(values)
output = Path(os.environ["OCEAN_OUTPUT_DIR"])
(output / "report.md").write_text(f"# Mean result\\n\\nThe arithmetic mean is {mean:.1f}.\\n")
print(json.dumps({"count": len(values), "mean": mean}))
""",
        )
        assert execution.state == "succeeded"
        assert Path(execution.result_bundle_path).is_file()
        # Relative paths are ordinary scientific outputs. The sandbox runs
        # inside its durable output root so callers need not know a special
        # environment variable merely to keep a result.
        relative = await host.expert_code_execution.run_python(
            workspace_id="ws_report",
            task_id=task.task_id,
            work_order_id=order.work_order_id,
            child_id=f"{order.work_order_id}:relative-output",
            purpose="Verify that a relative output becomes durable.",
            code=(
                "from pathlib import Path\n"
                "Path('relative-result.json').write_text('{\"ok\": true}', encoding='utf-8')\n"
            ),
        )
        assert relative.state == "succeeded"
        assert relative.output_files == ("relative-result.json",)
        relative_root = Path(relative.work_root) / "executions" / relative.execution_id / "outputs"
        assert (relative_root / "relative-result.json").is_file()
        publisher = WorkOrder(
            work_order_id="work_report_publisher",
            parent_request_id=order.parent_request_id,
            task_goal="Publish the checked result without recomputing it.",
            profile_id="visualization_communication_expert",
            semantic_role="Visualization & Communication Expert",
            authority=ChildAuthority.EXPERT,
            input_refs=(
                EvidenceRef(kind="dataset", ref=dataset.artifact.ref.key, locator="source_1"),
            ),
            outcome_intents=("report",),
            done_when="Publish the upstream execution as a reproducible report.",
            workspace_revision=dataset.workspace_revision,
        )
        host.store.create_team_work_order(
            workspace_id="ws_report", work_order=publisher
        )
        host.store.mark_team_work_running(publisher.work_order_id)
        published = await host.expert_deliverables.materialize_report(
            workspace_id="ws_report",
            task_id=task.task_id,
            work_order_id=publisher.work_order_id,
            execution_id=execution.execution_id,
            title="Mean calculation report",
            summary="Checked arithmetic mean and reproducibility material",
            report_output="report.md",
            attachment_outputs=(),
            evidence_refs=(dataset.artifact.ref,),
            data_sources_summary="Immutable numbers.csv from dataset_report_fixture@v1.",
            calculation_summary="Parsed three numeric rows and calculated sum(values) / count(values).",
            parameters_summary="Column=value; no filtering; ordinary arithmetic mean.",
            checks=("The executed notebook recorded count=3 and mean=2.0.",),
            limitations=("This fixture demonstrates packaging, not population inference.",),
            conclusion_export_allowed=True,
            fully_reproducible=True,
            origin_request_id="req_report_fixture",
            execution_output_names=("report.md",),
        )
        assert published["kind"] == "report"
        ref = TaskResultRef.model_validate(published["result_ref"])
        result = host.task_results.get(ref)
        assert result.work_order_id == publisher.work_order_id
        assert result.execution_id == execution.execution_id
        assert result.content["fully_reproducible"] is True
        files = {
            item.path: host.task_results.file_path(ref=ref, relative_path=item.path)
            for item in result.files
        }
        assert {"report.md", "analysis.py", "requirements.txt", "inputs.json", "reproducibility.json"} <= set(files)
        assert "analysis.ipynb" not in files
        report = files["report.md"].read_text(encoding="utf-8")
        assert "## Reproducibility" in report
        assert "### Calculation" in report
        manifest = json.loads(files["reproducibility.json"].read_text(encoding="utf-8"))
        assert manifest["execution_state"] == "succeeded"
        assert "analysis.ipynb" not in manifest["package_files"]
        task_roots = list((tmp_path / "OceanMind Tasks").glob("*"))
        assert len(task_roots) == 1
        persisted = task_roots[0] / "results" / ref.result_id / "v0001"
        assert persisted.is_dir()
        assert set(files).issubset({path.name for path in persisted.iterdir()})
    finally:
        await host.close()
