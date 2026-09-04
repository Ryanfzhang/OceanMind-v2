from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ocean_partner.agent_tools import ToolExecutionContext
from ocean_partner.artifacts.models import ArtifactRef
from ocean_partner.expert_deliverables import ExpertDeliverableService
from ocean_partner.task_results import TaskResultRef, TaskResultStore
from ocean_partner.team.models import EvidenceRef
from ocean_partner.tools import (
    OceanToolServices,
    create_ocean_expert_tool_registry,
)


class _TaskWorkspaces:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_task_root(self, _task_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


def test_failed_execution_keeps_valid_declared_file_with_preview_fallback(tmp_path) -> None:
    task_root = tmp_path / "task"
    work_root = task_root / "analysis" / "expert"
    execution_id = "codeexec_fallback"
    output_root = work_root / "executions" / execution_id / "outputs"
    output_root.mkdir(parents=True)
    code_root = output_root.parent / "code"
    code_root.mkdir()
    (code_root / "analysis.ipynb").write_text("{}", encoding="utf-8")
    (output_root / "figure.json").write_text(
        json.dumps(
            {
                "schema_version": "ocean-scientific-figure/v2",
                "plot_kind": "section",
                # Nested values intentionally fail the interactive renderer.
                "data": {"z": [[1.0, 2.0]]},
                "panels": [],
            }
        ),
        encoding="utf-8",
    )
    (output_root / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\npreview")

    dataset_ref = ArtifactRef(artifact_id="dataset_fallback", version=1)
    execution = SimpleNamespace(
        workspace_id="ws_fallback",
        task_id="task_fallback",
        work_order_id="work_fallback",
        state="failed",
        result={
            "work_root": str(work_root),
            "output_files": ["figure.json", "preview.png"],
        },
    )
    work = SimpleNamespace(
        work_order=SimpleNamespace(
            input_refs=(EvidenceRef(kind="dataset", ref=dataset_ref.key),),
        )
    )

    class Store:
        def get_code_execution(self, candidate: str):
            return execution if candidate == execution_id else None

        def get_team_work(self, candidate: str):
            return work if candidate == "work_fallback" else None

        def get_artifact(self, *, workspace_id: str, ref: ArtifactRef):
            if workspace_id == "ws_fallback" and ref == dataset_ref:
                return SimpleNamespace(artifact_type="dataset")
            return None

    workspaces = _TaskWorkspaces(task_root)
    results = TaskResultStore(task_workspaces=workspaces)
    service = ExpertDeliverableService(
        store=Store(),
        task_workspaces=workspaces,
        task_results=results,
    )

    published = asyncio.run(
        service.materialize_structured_view(
            workspace_id="ws_fallback",
            task_id="task_fallback",
            work_order_id="work_fallback",
            execution_id=execution_id,
            title="Saved section",
            summary="The computation completed even though the renderer contract did not.",
            # Both spellings resolve to the manifest-relative output name.
            data_output="outputs/figure.json",
            preview_output="outputs/preview.png",
            dataset_ref=dataset_ref,
            view_kind="section",
            interaction={"hover": True},
            origin_request_id="req_fallback",
            execution_output_names=("figure.json", "preview.png"),
        )
    )

    assert published["render_status"] == "preview"
    assert "values must be numbers" in published["render_message"]
    record = results.get(TaskResultRef.model_validate(published["result_ref"]))
    assert record.kind == "interactive_view"
    assert record.content["render_status"] == "preview"
    assert record.content["data_file"] == "data.json"
    assert record.content["preview_file"] == "preview.png"
    assert {item.path for item in record.files} == {
        "data.json",
        "preview.png",
    }


def test_invalid_interactive_payload_without_preview_remains_downloadable(tmp_path) -> None:
    task_root = tmp_path / "task"
    work_root = task_root / "analysis" / "expert"
    execution_id = "codeexec_file_fallback"
    output_root = work_root / "executions" / execution_id / "outputs"
    output_root.mkdir(parents=True)
    (output_root / "result.json").write_text("not-json", encoding="utf-8")

    dataset_ref = ArtifactRef(artifact_id="dataset_file_fallback", version=1)
    execution = SimpleNamespace(
        workspace_id="ws_file_fallback",
        task_id="task_file_fallback",
        work_order_id="work_file_fallback",
        state="succeeded",
        result={"work_root": str(work_root), "output_files": ["result.json"]},
    )
    work = SimpleNamespace(
        work_order=SimpleNamespace(
            input_refs=(EvidenceRef(kind="dataset", ref=dataset_ref.key),),
        )
    )

    class Store:
        def get_code_execution(self, candidate: str):
            return execution if candidate == execution_id else None

        def get_team_work(self, candidate: str):
            return work if candidate == "work_file_fallback" else None

        def get_artifact(self, *, workspace_id: str, ref: ArtifactRef):
            if workspace_id == "ws_file_fallback" and ref == dataset_ref:
                return SimpleNamespace(artifact_type="dataset")
            return None

    workspaces = _TaskWorkspaces(task_root)
    results = TaskResultStore(task_workspaces=workspaces)
    service = ExpertDeliverableService(
        store=Store(),
        task_workspaces=workspaces,
        task_results=results,
    )

    published = asyncio.run(
        service.materialize_structured_view(
            workspace_id="ws_file_fallback",
            task_id="task_file_fallback",
            work_order_id="work_file_fallback",
            execution_id=execution_id,
            title="Saved raw result",
            summary="The original result remains available.",
            data_output="result.json",
            preview_output=None,
            dataset_ref=dataset_ref,
            view_kind="profile",
            interaction={},
            origin_request_id="req_file_fallback",
            execution_output_names=("result.json",),
        )
    )

    assert published["render_status"] == "file"
    record = results.get(TaskResultRef.model_validate(published["result_ref"]))
    assert record.content["render_status"] == "file"
    assert {item.path for item in record.files} == {"data.json"}


def test_run_code_atomically_binds_output_alias_to_fallback_result(tmp_path) -> None:
    task_root = tmp_path / "task"
    work_root = task_root / "analysis" / "expert"
    execution_id = "codeexec_atomic_fallback"
    output_root = work_root / "executions" / execution_id / "outputs"
    output_root.mkdir(parents=True)
    code_root = output_root.parent / "code"
    code_root.mkdir()
    (code_root / "analysis.ipynb").write_text("{}", encoding="utf-8")
    (output_root / "figure.json").write_text("not-json", encoding="utf-8")
    (output_root / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\npreview")

    dataset_ref = ArtifactRef(artifact_id="dataset_atomic_fallback", version=1)
    execution = SimpleNamespace(
        workspace_id="ws_atomic_fallback",
        task_id="task_atomic_fallback",
        work_order_id="work_atomic_fallback",
        state="succeeded",
        result={
            "work_root": str(work_root),
            "output_files": ["figure.json", "preview.png"],
        },
    )
    work = SimpleNamespace(
        workspace_id="ws_atomic_fallback",
        work_order=SimpleNamespace(
            input_refs=(
                EvidenceRef(kind="dataset", ref=dataset_ref.key, locator="source_1"),
            ),
        )
    )
    bindings: list[dict[str, tuple[str, TaskResultRef]]] = []

    class Store:
        def get_code_execution(self, candidate: str):
            return execution if candidate == execution_id else None

        def get_team_work(self, candidate: str):
            return work if candidate == "work_atomic_fallback" else None

        def get_artifact(self, *, workspace_id: str, ref: ArtifactRef):
            if workspace_id == "ws_atomic_fallback" and ref == dataset_ref:
                return SimpleNamespace(artifact_type="dataset")
            return None

        def record_workstream_results(
            self, _work_order_id, _refs, *, output_bindings, result_metadata
        ) -> None:
            assert result_metadata["kind"] == "interactive_view"
            bindings.append(output_bindings)

    class CodeExecution:
        async def run_python(self, **_kwargs):
            return SimpleNamespace(
                execution_id=execution_id,
                state="succeeded",
                attempt_number=1,
                discovered_results=(
                    {
                        "schema_version": "ocean-result-event/v1",
                        "kind": "interactive_view",
                        "title": "Atomic fallback",
                        "source_handle": "source_1",
                        "view_kind": "section",
                        "data_output": "figure.json",
                        "preview_output": "preview.png",
                    },
                ),
                outputs=(
                    {"name": "figure.json", "bytes": 8, "sha256": "a" * 64},
                    {"name": "preview.png", "bytes": 15, "sha256": "b" * 64},
                ),
                as_payload=lambda: {
                    "execution_id": execution_id,
                    "state": "succeeded",
                    "stdout": "",
                    "stderr": "",
                    "output_files": ["figure.json", "preview.png"],
                },
            )

    store = Store()
    workspaces = _TaskWorkspaces(task_root)
    task_results = TaskResultStore(task_workspaces=workspaces)
    deliverables = ExpertDeliverableService(
        store=store,
        task_workspaces=workspaces,
        task_results=task_results,
    )
    registry = create_ocean_expert_tool_registry(
        OceanToolServices(
            workspace_id="ws_atomic_fallback",
            provider_id="provider_fixture",
            store=store,
            task_id="task_atomic_fallback",
            work_order_id="work_atomic_fallback",
            expert_child_id="work_atomic_fallback:run:1",
            expert_code_execution=CodeExecution(),
            expert_deliverables=deliverables,
        )
    )
    tool = next(item for item in registry.list_tools() if item.name == "ocean_expert_run_code")

    response = asyncio.run(
        tool.execute(
            tool.input_model(
                purpose="Create and deliver one bounded result.",
                code="print('already executed by fixture')",
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
    )

    payload = json.loads(response.output)
    assert "result_materialization_errors" not in payload
    assert payload["candidate_result_count"] == 1
    assert payload["publication_state"] == "awaiting_coordinator_review"
    assert bindings == []
    assert task_results.list(task_id="task_atomic_fallback") == ()
