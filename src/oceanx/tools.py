"""Minimal model-visible capabilities for OceanMind's hierarchical team.

Scientific methods are written by Experts as ordinary code.  This module owns
only infrastructure boundaries: resource discovery, team assignment, bounded
Expert code execution, candidate delivery, and Coordinator-owned result
publication.
The tool surface stays limited to infrastructure needed by Coordinator and Experts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oceanx.agent_tools import (
    BaseTool,
    ToolEffect,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from oceanx.artifacts.models import ArtifactRef
from oceanx.artifacts.service import ArtifactService
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.expert_deliverables import (
    ExpertDeliverableError,
    ExpertDeliverableService,
)
from oceanx.expert_execution import (
    ExpertCodeExecutionError,
    ExpertCodeExecutionService,
)
from oceanx.jina_reader import JinaReaderTool
from oceanx.protocol.v2.models import EventEnvelope
from oceanx.research_learning import (
    ObservationKind,
    ResearchObservationDraft,
)
from oceanx.skills import (
    load_ocean_reference,
    load_ocean_skill,
    ocean_skill_metadata,
    record_ocean_resource_use,
)
from oceanx.task_results import TaskResultError, TaskResultRef
from oceanx.team.models import (
    ExpertOutput,
    expert_output_item_id,
    validate_todo_graph,
)
from oceanx.team.profiles import get_agent_profile
from oceanx.web_search import WebSearchResponse, WebSearchTool


class OceanToolInput(BaseModel):
    """Strict base model for every OceanMind model-visible call."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class OceanToolServices:
    """Server-owned bindings; model input never supplies infrastructure state."""

    workspace_id: str
    provider_id: str
    store: RequestStore
    artifacts: ArtifactService | None = None
    task_id: str | None = None
    resource_access: Literal["routing", "inspection"] = "inspection"
    skill_capabilities: tuple[str, ...] = ()
    skill_role: str | None = None
    domain_event_emitter: Callable[[EventEnvelope], Awaitable[None]] | None = None
    team_assign_sink: (
        Callable[[dict[str, Any], ToolExecutionContext], Awaitable[dict[str, Any]]] | None
    ) = None
    paper_selection_sink: (
        Callable[[dict[str, Any], ToolExecutionContext], Awaitable[dict[str, Any]]] | None
    ) = None
    expert_result_origin_request_id: str | None = None
    work_order_id: str | None = None
    expert_code_execution: ExpertCodeExecutionService | None = None
    expert_deliverables: ExpertDeliverableService | None = None
    expert_child_id: str | None = None
    # Stable LangGraph identity. Focused follow-ups reuse it; independent todos
    # receive different threads even when they use the same professional role.
    agent_thread_id: str | None = None


@dataclass(frozen=True)
class ModelToolOperation:
    request_id: str
    turn_id: str
    tool_call_id: str
    operation_id: str


@dataclass(frozen=True)
class _MaterializedResult:
    ref: TaskResultRef
    kind: Literal["interactive_view", "report"]
    title: str
    summary: str
    render_status: Literal["interactive", "preview", "file"] | None = None
    render_message: str | None = None
    output_names: tuple[str, ...] = ()


class _OceanTool(BaseTool):
    services: OceanToolServices

    def __init__(self, services: OceanToolServices) -> None:
        self.services = services

    @staticmethod
    def _json(value: Any, *, metadata: dict[str, Any] | None = None) -> ToolResult:
        return ToolResult(
            output=json.dumps(value, ensure_ascii=True, sort_keys=True),
            metadata={"display": "activity", **(metadata or {})},
        )

    @staticmethod
    def _error(message: str) -> ToolResult:
        return ToolResult(
            output=message,
            is_error=True,
            metadata={"display": "activity"},
        )

    @staticmethod
    def _model_operation(context: ToolExecutionContext) -> ModelToolOperation | None:
        values = (
            context.request_id,
            context.turn_id,
            context.tool_call_id,
            context.operation_id,
        )
        if not any(values):
            return None
        if not all(values):
            raise RequestStoreError("Model tool operation is missing durable correlation fields")
        return ModelToolOperation(
            request_id=context.request_id,
            turn_id=context.turn_id,
            tool_call_id=context.tool_call_id,
            operation_id=context.operation_id,
        )

    async def _run_mutation(
        self,
        context: ToolExecutionContext,
        action: Callable[[], Awaitable[ToolResult]],
    ) -> ToolResult:
        """Apply a model mutation once and replay its durable receipt."""

        operation: ModelToolOperation | None = None
        try:
            operation = self._model_operation(context)
            if operation is None:
                return await action()
            self.services.store.begin_tool_call(
                operation_id=operation.operation_id,
                request_id=operation.request_id,
                turn_id=operation.turn_id,
                tool_call_id=operation.tool_call_id,
                tool_name=self.name,
            )
            previous = self.services.store.operation_result(operation.operation_id)
            if previous is not None:
                return ToolResult(
                    output=str(previous["output"]),
                    is_error=bool(previous["is_error"]),
                    metadata={"display": "activity", "replayed": True},
                )
            result = await action()
            self.services.store.record_operation_result(
                operation_id=operation.operation_id,
                request_id=operation.request_id,
                turn_id=operation.turn_id,
                tool_call_id=operation.tool_call_id,
                result={"output": result.output, "is_error": result.is_error},
            )
            return result
        except (RequestStoreError, RuntimeError, ValueError) as exc:
            result = self._error(str(exc))
            # A rejected mutation is still a terminal tool result.  Persist the
            # error receipt so request replay and the UI never retain an orphan
            # ``in_progress`` operation.
            if operation is not None:
                try:
                    self.services.store.record_operation_result(
                        operation_id=operation.operation_id,
                        request_id=operation.request_id,
                        turn_id=operation.turn_id,
                        tool_call_id=operation.tool_call_id,
                        result={"output": result.output, "is_error": True},
                    )
                except RequestStoreError:
                    pass
            return result


class OceanResourcesInput(OceanToolInput):
    artifact_ref: ArtifactRef | None = None


class OceanResourcesTool(_OceanTool):
    name = "ocean_resources"
    description = (
        "List current workspace resources or inspect one immutable resource. The Coordinator sees "
        "only routing identity; scientific content must be assigned to an Expert."
    )
    input_model = OceanResourcesInput

    def is_read_only(self, arguments: OceanResourcesInput) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: OceanResourcesInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if arguments.artifact_ref is None:
            if self.services.resource_access == "routing":
                task_sources: list[dict[str, str]] = []
                if self.services.task_id is not None:
                    for record in self.services.store.list_task_artifacts(
                        task_id=self.services.task_id
                    ):
                        artifact = record.artifact
                        if artifact.artifact_type not in {"dataset", "paper"}:
                            continue
                        if "source" not in record.relations:
                            continue
                        task_sources.append(
                            {
                                "handle": f"source_{len(task_sources) + 1}",
                                "kind": artifact.artifact_type,
                                "title": artifact.title,
                            }
                        )
                return self._json(
                    {
                        "workspace_revision": self.services.store.workspace_snapshot(
                            self.services.workspace_id
                        ).revision,
                        "access_scope": "routing",
                        "task_sources": task_sources,
                    }
                )
            summaries = self.services.store.list_artifact_summaries(self.services.workspace_id)
            return self._json(
                {
                    "workspace_revision": self.services.store.workspace_snapshot(
                        self.services.workspace_id
                    ).revision,
                    "access_scope": self.services.resource_access,
                    "resources": summaries,
                }
            )
        artifact = self.services.store.get_artifact(
            workspace_id=self.services.workspace_id,
            ref=arguments.artifact_ref,
        )
        if self.services.resource_access == "routing":
            allowed = {
                record.artifact.ref
                for record in (
                    self.services.store.list_task_artifacts(task_id=self.services.task_id)
                    if self.services.task_id is not None
                    else ()
                )
                if "source" in record.relations
            }
            if arguments.artifact_ref not in allowed:
                return self._error("Resource is not attached to the current task")
        projection = self.services.store.get_projection(
            workspace_id=self.services.workspace_id,
            ref=arguments.artifact_ref,
        )
        if artifact is None or projection is None:
            return self._error("Resource version was not found")
        if self.services.resource_access == "routing":
            return self._json(
                {
                    "access_scope": "routing",
                    "resource": {
                        "ref": artifact.ref.model_dump(mode="json"),
                        "type": artifact.artifact_type,
                        "title": artifact.title,
                    },
                    "inspection_required_for_content_claims": True,
                }
            )
        return self._json(
            {
                "access_scope": "inspection",
                "resource": {
                    "ref": artifact.ref.model_dump(mode="json"),
                    "type": artifact.artifact_type,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "projection": projection.model_dump(mode="json"),
                    "files": [item.model_dump(mode="json") for item in artifact.files],
                },
            }
        )


class OceanListSkillsInput(OceanToolInput):
    pass


class OceanListSkillsTool(_OceanTool):
    name = "ocean_list_skills"
    description = (
        "List the compact metadata for research-process skills available to this Agent's stable "
        "professional role. Use it only when process guidance could materially improve the current "
        "WorkOrder. The current Agent, not the Coordinator, decides whether any skill is relevant."
    )
    input_model = OceanListSkillsInput

    def is_read_only(self, arguments: OceanListSkillsInput) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: OceanListSkillsInput, context: ToolExecutionContext
    ) -> ToolResult:
        del arguments, context
        role = self.services.skill_role
        if role is None:
            return self._error("This runtime has no skill-selection role")
        skills_by_name = {
            item.name: {
                "name": item.name,
                "description": item.description,
                "version": item.version,
            }
            for item in ocean_skill_metadata(
                capabilities=self.services.skill_capabilities,
                role=role,
            )
        }
        list_evolved = getattr(self.services.store, "list_evolved_skill_revisions", None)
        evolved_revisions = (
            list_evolved(workspace_id=self.services.workspace_id, active_only=True)
            if callable(list_evolved)
            else ()
        )
        for revision in evolved_revisions:
            if role not in revision.roles:
                continue
            skills_by_name[revision.skill_name] = {
                "name": revision.skill_name,
                "description": revision.description,
                "version": f"workspace:v{revision.version}:sha256:{revision.content_sha256[:12]}",
            }
        return self._json(
            {
                "role": role,
                "skills": [skills_by_name[name] for name in sorted(skills_by_name)],
                "selection_policy": (
                    "Load only guidance that materially changes the current method; loading no skill "
                    "is valid. Skills never expand the WorkOrder, authority, tools, or completion rules."
                ),
            }
        )


class OceanLoadSkillInput(OceanToolInput):
    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Exact skill name returned by ocean_list_skills.",
    )


class OceanLoadSkillTool(_OceanTool):
    name = "ocean_load_skill"
    description = (
        "Load one role-eligible research-process skill for this Agent's current WorkOrder. Call this "
        "only after this Agent decides the skill is methodologically relevant. The loaded guidance "
        "cannot change task scope, permissions, tools, sources, or completion authority."
    )
    input_model = OceanLoadSkillInput

    def effect_for(self, arguments: OceanLoadSkillInput) -> ToolEffect:
        del arguments
        return ToolEffect.MUTATION

    def concurrency_key(self, arguments: OceanLoadSkillInput) -> str:
        return (
            f"skill-load:{self.services.workspace_id}:"
            f"{self.services.work_order_id or self.services.task_id or 'coordinator'}:{arguments.name}"
        )

    async def execute(
        self, arguments: OceanLoadSkillInput, context: ToolExecutionContext
    ) -> ToolResult:
        role = self.services.skill_role
        if role is None:
            return self._error("This runtime has no skill-selection role")

        async def action() -> ToolResult:
            list_evolved = getattr(self.services.store, "list_evolved_skill_revisions", None)
            evolved_revisions = (
                list_evolved(
                    workspace_id=self.services.workspace_id,
                    skill_name=arguments.name,
                    active_only=True,
                )
                if callable(list_evolved)
                else ()
            )
            evolved = next((item for item in evolved_revisions if role in item.roles), None)
            if evolved is None:
                content, metadata = load_ocean_skill(
                    arguments.name,
                    capabilities=self.services.skill_capabilities,
                    role=role,
                )
                skill_name = metadata.name
                skill_version = metadata.version
            else:
                content = evolved.content
                skill_name = evolved.skill_name
                skill_version = (
                    f"workspace:v{evolved.version}:sha256:{evolved.content_sha256[:12]}"
                )
            usage_id = record_ocean_resource_use(
                self.services.store,
                workspace_id=self.services.workspace_id,
                resource_kind="skill",
                resource_name=skill_name,
                resource_version=skill_version,
                work_order_id=self.services.work_order_id,
                request_id=context.request_id or self.services.expert_result_origin_request_id,
                agent_id=self.services.expert_child_id or self.services.agent_thread_id or role,
            )
            references: list[dict[str, str]] = []
            if evolved is None:
                for relative_path in sorted(
                    set(re.findall(r"references/([A-Za-z0-9_.\-/]+\.md)", content))
                ):
                    reference_content, version = load_ocean_reference(relative_path)
                    record_ocean_resource_use(
                        self.services.store,
                        workspace_id=self.services.workspace_id,
                        resource_kind="reference",
                        resource_name=relative_path,
                        resource_version=version,
                        work_order_id=self.services.work_order_id,
                    )
                    references.append(
                        {
                            "path": relative_path,
                            "version": version,
                            "content": reference_content,
                        }
                    )
            return self._json(
                {
                    "name": skill_name,
                    "version": skill_version,
                    "content": content,
                    "references": references,
                    "usage_id": usage_id,
                }
            )

        return await self._run_mutation(context, action)


class OceanSaveExperienceInput(OceanToolInput):
    text: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "One concise lesson: applicability, problem or correction, effective approach, "
            "verification and remaining uncertainty. Distinguish preferences from untested "
            "suggestions and validated methods. Source IDs are attached automatically."
        ),
    )


class OceanSaveExperienceTool(_OceanTool):
    name = "ocean_save_experience"
    description = (
        "Optionally save one concise durable lesson for later Skill Curator review. Use this only "
        "for a reusable method, failure lesson, coordination lesson, or explicit user insight; "
        "do not save routine progress, task facts, raw logs, or the final scientific conclusion."
    )
    input_model = OceanSaveExperienceInput

    def effect_for(self, arguments: OceanSaveExperienceInput) -> ToolEffect:
        del arguments
        return ToolEffect.MUTATION

    def concurrency_key(self, arguments: OceanSaveExperienceInput) -> str:
        del arguments
        return (
            f"experience-save:{self.services.workspace_id}:"
            f"{self.services.work_order_id or self.services.task_id or 'agent'}"
        )

    async def execute(
        self, arguments: OceanSaveExperienceInput, context: ToolExecutionContext
    ) -> ToolResult:
        task_id = self.services.task_id
        role = self.services.skill_role
        if task_id is None or role is None:
            return self._error("Experience saving is unavailable outside a research task")

        async def action() -> ToolResult:
            request_id = (
                context.request_id
                or self.services.expert_result_origin_request_id
                or f"agent_saved:{task_id}"
            )
            agent_id = (
                self.services.expert_child_id
                or self.services.agent_thread_id
                or role
            )
            saved = self.services.store.save_experience(
                workspace_id=self.services.workspace_id,
                task_id=task_id,
                request_id=request_id,
                work_order_id=self.services.work_order_id,
                agent_id=agent_id,
                agent_role=role,
                text=arguments.text,
                turn_id=context.turn_id,
                tool_call_id=context.tool_call_id,
            )
            return self._json(
                {
                    "saved": True,
                    "experience_id": saved.experience_id,
                    "status": saved.status.value,
                }
            )

        return await self._run_mutation(context, action)


class OceanExpertRunCodeInput(OceanToolInput):
    purpose: str = Field(min_length=1, max_length=2_000)
    code: str = Field(min_length=1, max_length=200_000)


class OceanExpertRunCodeTool(_OceanTool):
    name = "ocean_expert_run_code"
    description = (
        "Run a Python analysis or download program inside this Expert's task sandbox with network "
        "access. Never upload local data without user authorization. Use acquisition Skills for "
        "provider methods; save reusable downloads in OCEAN_WORK_DIR/downloads. The input "
        "manifest contains server-prepared AnalysisContext, exact read-only sources, and reusable "
        "prior results. OCEAN_WORK_DIR persists intermediate arrays across focused follow-ups. "
        "ScientificFigure.save() durably declares candidate results with their supporting "
        "conclusions. The Coordinator reviews and publishes accepted candidates and owns the "
        "final report; "
        "no results schema is accepted here."
    )
    input_model = OceanExpertRunCodeInput

    def effect_for(self, arguments: OceanExpertRunCodeInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    def concurrency_key(self, arguments: OceanExpertRunCodeInput) -> str:
        del arguments
        return f"expert-code:{self.services.work_order_id or self.services.workspace_id}"

    async def execute(
        self, arguments: OceanExpertRunCodeInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        service = self.services.expert_code_execution
        if (
            service is None
            or self.services.task_id is None
            or self.services.work_order_id is None
            or self.services.expert_child_id is None
        ):
            return self._error("Expert code execution is unavailable in this session")
        try:
            result = await service.run_python(
                workspace_id=self.services.workspace_id,
                task_id=self.services.task_id,
                work_order_id=self.services.work_order_id,
                child_id=self.services.expert_child_id,
                purpose=arguments.purpose,
                code=arguments.code,
            )
            payload = result.as_payload()
            declaration_errors = list(payload.pop("invalid_candidate_results", ()))
            framework_results: list[_FrameworkResultEvent] = []
            for event in getattr(result, "discovered_results", ()):
                try:
                    framework_results.append(
                        _FrameworkResultEvent.model_validate(
                            {key: value for key, value in event.items() if key != "schema_version"}
                        )
                    )
                except ValueError as exc:
                    declaration_errors.append(
                        f"Could not validate framework-saved candidate: {exc}"
                    )
            candidates = candidate_outputs_from_execution(
                execution_id=result.execution_id,
                execution_result={
                    "outputs": [
                        dict(item)
                        for item in getattr(
                            result,
                            "outputs",
                            payload.get("outputs", ()),
                        )
                    ],
                    "discovered_results": [
                        {"schema_version": "ocean-result-event/v1", **item.model_dump(mode="json")}
                        for item in framework_results
                    ],
                },
            )
            payload["candidate_results"] = [
                {
                    "path": candidate.path,
                    "kind": candidate.result_kind,
                    "title": candidate.title,
                    "summary": candidate.summary,
                    "view_type": candidate.view_type,
                    "sha256": candidate.sha256,
                    "claims": list(candidate.claims),
                }
                for candidate in candidates
            ]
            payload["candidate_result_count"] = len(candidates)
            payload["publication_state"] = "awaiting_coordinator_review"
            if declaration_errors:
                payload["invalid_candidate_results"] = declaration_errors
                payload["publication_state"] = "candidate_declaration_error"
                payload["result_collection_message"] = (
                    "Some saved-result declarations were rejected by the runtime. "
                    "Valid candidates are retained. This is a delivery-contract error; "
                    "do not repeat scientific computation to repair registration."
                )
            # Attempt counters are a backend safety mechanism, not an invitation
            # for the model to consume every remaining slot.  The Expert decides
            # whether to hand off from saved results; the Coordinator decides any
            # subsequent focused assignment.
            payload.pop("attempt_number", None)
            payload.pop("attempt_limit", None)
            for internal_field in (
                "outputs",
                "code_path",
                "work_root",
                "result_bundle_path",
                "result_fingerprint",
                "discovered_results",
            ):
                payload.pop(internal_field, None)
            for stream_name in ("stdout", "stderr"):
                stream = str(payload[stream_name])
                payload[f"{stream_name}_total_chars"] = len(stream)
                if len(stream) > 2_400:
                    # The durable CodeExecution keeps the complete stream.  The
                    # model receives a bounded diagnostic excerpt so one noisy
                    # command cannot dominate every subsequent model turn.
                    payload[stream_name] = (
                        stream[:1_120]
                        + "\n... [middle omitted; full stream is stored in the CodeExecution] ...\n"
                        + stream[-1_120:]
                    )
                    payload[f"{stream_name}_truncated"] = True
            return self._json(payload)
        except (ExpertCodeExecutionError, RequestStoreError, RuntimeError, ValueError) as exc:
            return self._error(str(exc))


class _FrameworkResultEvent(OceanToolInput):
    """A result event emitted by the installed scientific runtime, never by the LLM."""

    kind: Literal["interactive_view", "report"]
    title: str = Field(min_length=1, max_length=512)
    summary: str = Field(default="", max_length=8_000)
    conclusions: tuple[str, ...] = Field(default=(), max_length=64)
    source_handle: str | None = Field(default=None, pattern=r"^source_[1-9][0-9]*$")
    view_kind: (
        Literal[
            "spatial_map",
            "time_series",
            "profile",
            "scatter",
            "ts_diagram",
            "section",
            "hovmoller",
        ]
        | None
    ) = None
    field_output: str | None = Field(default=None, min_length=1, max_length=512)
    data_output: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description=(
            "Self-describing NetCDF file produced by ScientificFigure.save(). "
            "The file contains arrays and its renderer contract; do not construct renderer JSON."
        ),
    )
    dataset_output: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Legacy separate NetCDF arrays; current ScientificFigure results leave this unset.",
    )
    preview_output: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Optional static PNG fallback for the same interactive result.",
    )
    variable: str | None = Field(default=None, min_length=1, max_length=256)
    longitude_coordinate: str | None = Field(default=None, min_length=1, max_length=256)
    latitude_coordinate: str | None = Field(default=None, min_length=1, max_length=256)
    units: str | None = Field(default=None, max_length=128)
    colormap: str = Field(default="viridis", min_length=1, max_length=128)
    colorbar_label: str | None = Field(default=None, max_length=256)
    field_kind: Literal["continuous", "categorical"] = "continuous"
    interaction: dict[str, Any] = Field(default_factory=dict)
    view_type: str | None = Field(default=None, min_length=1, max_length=128)
    view_spec: dict[str, Any] | None = None
    data_schema: dict[str, Any] | None = None
    report_output: str | None = Field(default=None, min_length=1, max_length=512)
    attachment_outputs: tuple[str, ...] = Field(default=(), max_length=32)
    report_checks: tuple[str, ...] = Field(default=(), max_length=64)
    report_limitations: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_contract(self) -> _FrameworkResultEvent:
        if self.kind == "report":
            if self.report_output is None:
                raise ValueError("report requires report_output")
            if self.source_handle is not None or self.view_kind is not None:
                raise ValueError("report cannot declare interactive fields")
            forbidden = {"analysis.py", "analysis.ipynb", "requirements.txt", "inputs.json"}
            if forbidden.intersection(self.attachment_outputs):
                raise ValueError("report reproducibility files are attached automatically")
            return self
        if self.view_kind is None:
            raise ValueError("interactive_view requires view_kind")
        if (self.view_spec is None) != (self.data_schema is None):
            raise ValueError("interactive_view view_spec and data_schema must be declared together")
        if self.view_kind == "spatial_map":
            if any(
                value is None
                for value in (
                    self.data_output or self.field_output,
                    self.variable,
                    self.longitude_coordinate,
                    self.latitude_coordinate,
                )
            ):
                raise ValueError(
                    "spatial_map requires data_output, variable, longitude_coordinate, and latitude_coordinate"
                )
            if self.dataset_output is not None:
                raise ValueError("spatial_map uses one NetCDF output")
        elif self.data_output is None:
            raise ValueError(f"{self.view_kind} requires data_output")
        if self.report_output is not None:
            raise ValueError("interactive_view cannot declare report fields")
        return self


def _assigned_dataset_ref(services: OceanToolServices, source_handle: str | None) -> TaskResultRef:
    if services.work_order_id is None:
        raise ExpertDeliverableError("Expert assignment is unavailable")
    record = services.store.get_team_work(services.work_order_id)
    if record is None or record.workspace_id != services.workspace_id:
        raise ExpertDeliverableError("Expert assignment is unavailable")
    datasets: list[tuple[str, ArtifactRef]] = []
    for index, evidence in enumerate(record.work_order.input_refs, start=1):
        match = re.fullmatch(r"([a-z][a-z0-9_]{2,127})@v(\d+)", evidence.ref)
        if match is None:
            continue
        ref = ArtifactRef(artifact_id=match.group(1), version=int(match.group(2)))
        artifact = services.store.get_artifact(workspace_id=services.workspace_id, ref=ref)
        if artifact is not None and artifact.artifact_type == "dataset":
            datasets.append((evidence.locator or f"source_{index}", ref))
    if source_handle is not None:
        selected = next((ref for handle, ref in datasets if handle == source_handle), None)
        if selected is None:
            raise ExpertDeliverableError(f"{source_handle} is not a dataset Task Source")
        return selected
    if len(datasets) == 1:
        return datasets[0][1]
    if not datasets:
        raise ExpertDeliverableError("Interactive result requires an assigned dataset source")
    raise ExpertDeliverableError("Multiple datasets are assigned; provide source_handle")


def _assigned_artifact_refs(services: OceanToolServices) -> tuple[ArtifactRef, ...]:
    """Return immutable Task Sources as report evidence without model transcription."""

    if services.work_order_id is None:
        raise ExpertDeliverableError("Expert assignment is unavailable")
    record = services.store.get_team_work(services.work_order_id)
    if record is None or record.workspace_id != services.workspace_id:
        raise ExpertDeliverableError("Expert assignment is unavailable")
    refs: list[ArtifactRef] = []
    for evidence in record.work_order.input_refs:
        match = re.fullmatch(r"([a-z][a-z0-9_]{2,127})@v(\d+)", evidence.ref)
        if match is None:
            continue
        ref = ArtifactRef(artifact_id=match.group(1), version=int(match.group(2)))
        if services.store.get_artifact(workspace_id=services.workspace_id, ref=ref) is not None:
            refs.append(ref)
    if not refs:
        raise ExpertDeliverableError("A formal report requires an immutable Task Source")
    return tuple(dict.fromkeys(refs))


def _result_materialization_key(item: _FrameworkResultEvent, *, execution_id: str) -> str:
    # User-facing prose is presentation, not the identity of an immutable
    # execution output.  A continuation may paraphrase a title or summary;
    # that must not materialize a second task result for the same bundle item.
    materialization_payload = json.dumps(
        {
            "execution_id": execution_id,
            "result": item.model_dump(mode="json", exclude={"title", "summary"}),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "materialization_" + hashlib.sha256(materialization_payload.encode("utf-8")).hexdigest()


def _declared_result_output_names(
    item: _FrameworkResultEvent,
) -> tuple[str, ...]:
    """Return the execution outputs represented by one task result.

    The task result itself is written by the materializer, while these names
    identify the already-checkpointed ResultBundle items that support it.  The
    mapping lets an interrupted Expert resume from the exact user-facing view
    or report instead of treating a raw file and its presentation result as
    unrelated records.
    """

    if item.kind == "report":
        assert item.report_output is not None
        return tuple(dict.fromkeys((item.report_output, *item.attachment_outputs)))
    if item.view_kind == "spatial_map":
        primary = item.data_output or item.field_output
        assert primary is not None
        return (primary,)
    assert item.data_output is not None
    return tuple(
        dict.fromkeys(
            (
                item.data_output,
                *((item.dataset_output,) if item.dataset_output is not None else ()),
                *((item.preview_output,) if item.preview_output is not None else ()),
            )
        )
    )


def candidate_outputs_from_execution(
    *,
    execution_id: str,
    execution_result: dict[str, Any],
) -> tuple[ExpertOutput, ...]:
    """Project framework declarations into unreviewed Expert output candidates.

    The execution directory and hashes are already durable. This projection is
    intentionally side-effect free: it lets an Expert hand evidence to the
    Coordinator without publishing a task result or copying any files.
    """

    output_records = {
        str(item.get("name")): item
        for item in execution_result.get("outputs", ())
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    candidates: dict[str, ExpertOutput] = {}
    for raw_event in execution_result.get("discovered_results", ()):
        if not isinstance(raw_event, dict):
            continue
        try:
            declared = _FrameworkResultEvent.model_validate(
                {key: value for key, value in raw_event.items() if key != "schema_version"}
            )
        except ValueError:
            continue
        output_names = _declared_result_output_names(declared)
        primary_name = output_names[0]
        primary = output_records.get(primary_name)
        if primary is None:
            continue
        size_bytes = primary.get("bytes")
        sha256 = primary.get("sha256")
        if (
            not isinstance(size_bytes, int)
            or size_bytes < 0
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            continue
        supporting_names = tuple(
            output_name for output_name in output_names[1:] if output_name in output_records
        )
        item_id = expert_output_item_id(
            execution_id=execution_id,
            output_name=primary_name,
        )
        candidates[item_id] = ExpertOutput(
            item_id=item_id,
            execution_id=execution_id,
            output_name=primary_name,
            supporting_output_names=supporting_names,
            size_bytes=size_bytes,
            sha256=sha256,
            result_kind=declared.kind,
            source_handle=declared.source_handle,
            title=declared.title,
            summary=declared.summary,
            view_type=declared.view_type or (declared.view_spec or {}).get("type"),
            view_spec=declared.view_spec,
            data_schema=declared.data_schema,
            claims=declared.conclusions,
        )
    return tuple(candidates.values())


async def _materialize_declared_result(
    services: OceanToolServices,
    item: _FrameworkResultEvent,
    *,
    execution_id: str,
) -> _MaterializedResult:
    service = services.expert_deliverables
    if service is None or services.task_id is None or services.work_order_id is None:
        raise ExpertDeliverableError("Expert result materialization is unavailable")

    def canonical_name(output_name: str) -> str:
        return service.resolve_execution_output_name(
            workspace_id=services.workspace_id,
            task_id=services.task_id,
            work_order_id=services.work_order_id,
            execution_id=execution_id,
            output_name=output_name,
        )

    canonical_updates: dict[str, Any]
    if item.kind == "report":
        assert item.report_output is not None
        canonical_updates = {
            "report_output": canonical_name(item.report_output),
            "attachment_outputs": tuple(canonical_name(name) for name in item.attachment_outputs),
        }
    elif item.view_kind == "spatial_map":
        primary = item.data_output or item.field_output
        assert primary is not None
        canonical_updates = {
            "data_output": canonical_name(primary),
            "field_output": None,
        }
    else:
        assert item.data_output is not None
        canonical_dataset: str | None = None
        if item.dataset_output is not None:
            canonical_dataset = canonical_name(item.dataset_output)
        canonical_preview: str | None = None
        if item.preview_output is not None:
            try:
                canonical_preview = canonical_name(item.preview_output)
            except ExpertDeliverableError:
                # The primary data file is still a durable result. An optional
                # preview may be absent without turning persistence into a
                # second delivery failure.
                canonical_preview = None
        canonical_updates = {
            "data_output": canonical_name(item.data_output),
            "dataset_output": canonical_dataset,
            "preview_output": canonical_preview,
        }
    canonical_item = item.model_copy(update=canonical_updates)
    materialization_key = _result_materialization_key(
        canonical_item,
        execution_id=execution_id,
    )
    existing = service.results.find_by_materialization_key(
        task_id=services.task_id,
        materialization_key=materialization_key,
    )
    if existing is not None:
        return _MaterializedResult(
            ref=existing.ref,
            kind=existing.kind,
            title=existing.title,
            summary=existing.summary,
            render_status=(
                existing.content.get("render_status")
                if existing.content.get("render_status") in {"interactive", "preview", "file"}
                else None
            ),
            render_message=(
                str(existing.content["render_message"])
                if existing.content.get("render_message")
                else None
            ),
            output_names=existing.execution_output_names,
        )

    item = canonical_item

    presentation: dict[str, Any] = {}
    if item.conclusions:
        presentation = {
            "claim_key": "claim_"
            + hashlib.sha256(item.conclusions[0].encode("utf-8")).hexdigest()[:16],
            "claim_summary": item.conclusions[0],
        }

    common = {
        "workspace_id": services.workspace_id,
        "task_id": services.task_id,
        "work_order_id": services.work_order_id,
        "execution_id": execution_id,
        "execution_output_names": _declared_result_output_names(item),
        "title": item.title,
        "summary": item.summary,
        "origin_request_id": services.expert_result_origin_request_id,
        "materialization_key": materialization_key,
        "presentation": presentation,
    }
    if item.kind == "report":
        assert item.report_output is not None
        result = await service.materialize_report(
            **common,
            report_output=item.report_output,
            attachment_outputs=item.attachment_outputs,
            evidence_refs=_assigned_artifact_refs(services),
            data_sources_summary="Immutable Task Sources assigned by the Coordinator.",
            calculation_summary="Reproducible analysis captured in the attached execution notebook.",
            parameters_summary="Parameters are recorded in the execution input manifest and notebook.",
            checks=item.report_checks
            or ("The report file was captured from a successful execution.",),
            limitations=item.report_limitations
            or ("See the report text for scientific limitations.",),
            conclusion_export_allowed=False,
            fully_reproducible=True,
        )
    else:
        dataset_ref = _assigned_dataset_ref(services, item.source_handle)
        if item.view_kind == "spatial_map":
            assert item.data_output is not None
            assert item.variable is not None
            assert item.longitude_coordinate is not None
            assert item.latitude_coordinate is not None
            result = await service.materialize_spatial_view(
                **common,
                field_output=item.data_output,
                dataset_ref=dataset_ref,
                variable=item.variable,
                longitude_coordinate=item.longitude_coordinate,
                latitude_coordinate=item.latitude_coordinate,
                units=item.units,
                colormap=item.colormap,
                colorbar_label=item.colorbar_label,
                field_kind=item.field_kind,
            )
        else:
            assert item.view_kind is not None
            assert item.data_output is not None
            result = await service.materialize_structured_view(
                **common,
                data_output=item.data_output,
                dataset_output=item.dataset_output,
                preview_output=item.preview_output,
                dataset_ref=dataset_ref,
                view_kind=item.view_kind,
                interaction=item.interaction,
            )
    persisted = result.get("result")
    if not isinstance(persisted, dict):
        raise ExpertDeliverableError("Task result metadata is unavailable")
    kind = persisted.get("kind")
    if kind not in {"interactive_view", "report"}:
        raise ExpertDeliverableError("Task result kind is unavailable")
    return _MaterializedResult(
        ref=TaskResultRef.model_validate(result["result_ref"]),
        kind=kind,
        title=str(persisted.get("title", "")),
        summary=str(persisted.get("summary", "")),
        render_status=(
            result.get("render_status")
            if result.get("render_status") in {"interactive", "preview", "file"}
            else None
        ),
        render_message=(str(result["render_message"]) if result.get("render_message") else None),
        output_names=_declared_result_output_names(item),
    )


class OceanPublishOutputsInput(OceanToolInput):
    accepted_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = (
        Field(min_length=1, max_length=64)
    )
    review_summary: str = Field(
        min_length=1,
        max_length=4_000,
        description=(
            "Why these candidate outputs are sufficient and scientifically consistent with the "
            "Coordinator's answer. This is stored as acceptance provenance."
        ),
    )

    @model_validator(mode="after")
    def unique_output_paths(self) -> OceanPublishOutputsInput:
        if len(self.accepted_paths) != len(set(self.accepted_paths)):
            raise ValueError("accepted_paths must be unique")
        return self


class OceanPublishOutputsTool(_OceanTool):
    """Coordinator-only promotion of immutable candidates to published results."""

    name = "ocean_publish_outputs"
    description = (
        "Publish selected Expert candidate outputs after reviewing their conclusions, evidence, "
        "limitations, and any focused refinement. Files are already durable; this call only "
        "promotes accepted candidates to task results. Never publish a candidate merely because "
        "it exists."
    )
    input_model = OceanPublishOutputsInput

    def effect_for(self, arguments: OceanPublishOutputsInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    def concurrency_key(self, arguments: OceanPublishOutputsInput) -> str:
        del arguments
        return f"publish-results:{self.services.task_id or self.services.workspace_id}"

    async def execute(
        self,
        arguments: OceanPublishOutputsInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        async def action() -> ToolResult:
            if self.services.task_id is None or self.services.expert_deliverables is None:
                return self._error("Coordinator result publication is unavailable")
            records = self.services.store.list_task_team_work(
                workspace_id=self.services.workspace_id,
                task_id=self.services.task_id,
            )
            candidates: dict[str, tuple[Any, ExpertOutput]] = {}
            for record in records:
                if context.request_id and record.work_order.parent_request_id != context.request_id:
                    continue
                if record.result is None:
                    continue
                for output in record.result.outputs:
                    if output.result_ref is None:
                        candidates[output.path] = (record, output)
            unknown = sorted(set(arguments.accepted_paths) - set(candidates))
            if unknown:
                return self._error(
                    "Accepted output paths are unavailable or already published: "
                    + ", ".join(unknown)
                )

            published: list[dict[str, Any]] = []
            for output_path in arguments.accepted_paths:
                work_record, candidate = candidates[output_path]
                execution = self.services.store.get_code_execution(candidate.execution_id)
                if execution is None or execution.result is None:
                    return self._error(f"Candidate execution is not a durable record: {output_path}")
                declared: _FrameworkResultEvent | None = None
                for raw_event in execution.result.get("discovered_results", ()):
                    if not isinstance(raw_event, dict):
                        continue
                    try:
                        event = _FrameworkResultEvent.model_validate(
                            {
                                key: value
                                for key, value in raw_event.items()
                                if key != "schema_version"
                            }
                        )
                    except ValueError:
                        continue
                    projected = candidate_outputs_from_execution(
                        execution_id=execution.execution_id,
                        execution_result={
                            "outputs": execution.result.get("outputs", ()),
                            "discovered_results": (raw_event,),
                        },
                    )
                    if any(item.path == output_path for item in projected):
                        declared = event
                        break
                if declared is None:
                    return self._error(
                        f"Candidate declaration is no longer reproducible: {output_path}"
                    )

                scoped_services = replace(
                    self.services,
                    work_order_id=work_record.work_order.work_order_id,
                    expert_result_origin_request_id=context.request_id or None,
                )
                materialized = await _materialize_declared_result(
                    scoped_services,
                    declared,
                    execution_id=execution.execution_id,
                )
                output_names = materialized.output_names or _declared_result_output_names(declared)
                self.services.store.record_workstream_results(
                    work_record.work_order.work_order_id,
                    (materialized.ref,),
                    output_bindings={
                        output_name: (execution.execution_id, materialized.ref)
                        for output_name in output_names
                    },
                    result_metadata={
                        "kind": materialized.kind,
                        "title": materialized.title,
                        "summary": materialized.summary,
                        "claims": declared.conclusions,
                        "source_handle": declared.source_handle,
                        "view_type": declared.view_type,
                        "view_spec": declared.view_spec,
                        "data_schema": declared.data_schema,
                        "coordinator_review": arguments.review_summary,
                    },
                )
                published.append(
                    {
                        "path": output_path,
                        "result_ref": materialized.ref.model_dump(mode="json"),
                        "kind": materialized.kind,
                        "title": materialized.title,
                        "render_status": materialized.render_status,
                    }
                )
            try:
                self.services.store.record_research_observation(
                    ResearchObservationDraft(
                        workspace_id=self.services.workspace_id,
                        task_id=self.services.task_id,
                        request_id=context.request_id or "coordinator_publication",
                        kind=ObservationKind.EVALUATION,
                        statement=arguments.review_summary,
                        evidence_refs=tuple(
                            TaskResultRef.model_validate(item["result_ref"]).key
                            for item in published
                        ),
                        outcome="supported",
                        confidence=1.0,
                        reusable=False,
                    )
                )
            except RequestStoreError:
                # Publication is canonical; optional learning telemetry must
                # never turn a committed result into an apparent failure.
                pass
            return self._json(
                {
                    "publication_state": "coordinator_accepted",
                    "review_summary": arguments.review_summary,
                    "published": published,
                }
            )

        try:
            return await self._run_mutation(context, action)
        except (
            ExpertDeliverableError,
            TaskResultError,
            RequestStoreError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            return self._error(str(exc))


class OceanTodoInput(OceanToolInput):
    todo_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$",
        description=(
            "Stable identifier for one Coordinator-owned scientific sub-question. Reuse it only "
            "for a focused follow-up to that same todo; use a different todo_id for an independent "
            "question. Todo identity does not create an Expert; expert_key selects which stable "
            "task-scoped instance of the requested professional profile owns it."
        ),
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        max_length=8,
        description=(
            "Prerequisite todo ids in the Coordinator's plan. The Coordinator decides when those "
            "prerequisites are sufficient and dispatches only the current ready wave."
        ),
    )
    question: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "The bounded scientific question or decision delegated to this Expert. State what must "
            "be answered, not a method, reader, procedural checklist, or expansion of the profile."
        ),
    )
    why_this_expert: str = Field(min_length=1, max_length=2_000)
    context: str = Field(default="", max_length=16_000)
    source_handles: tuple[str, ...] = Field(default=(), max_length=32)
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    expert_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$",
        description=(
            "Stable identity for one concrete Expert instance within profile_id. Reuse the same "
            "expert_key for follow-up or refinement so that session memory and workspace continue. "
            "Use distinct keys only when independent tasks should run concurrently as separate "
            "Experts of the same type. Omit it for the profile's default singleton Expert."
        ),
    )
    expected_outputs: tuple[str, ...] = Field(
        min_length=1,
        max_length=16,
        description=(
            "Answer-level outcomes needed by the Coordinator, normally 'answer' plus only explicitly "
            "needed durable deliverables such as interactive_view, report, dataset, or notebook. "
            "Use interactive_view for requested maps/plots and for generated figures that carry "
            "material evidence in a quantitative analysis or report; it may accompany report. "
            "Do not enumerate domain properties, checks, or Manual questions here."
        ),
    )
    constraints: tuple[str, ...] = Field(default=(), max_length=32)
    done_when: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "Evidence-sufficiency stopping condition for the delegated question. Do not turn the "
            "Expert profile or Manual into an exhaustive completion checklist."
        ),
    )
    budget_tier: Literal["quick", "standard", "deep"] = Field(
        default="standard",
        description=(
            "Model-reasoning scope: quick for one bounded inspection or lookup, standard for "
            "ordinary analysis, deep only for explicitly multi-stage reproduction/research."
        ),
    )

    @model_validator(mode="after")
    def validate_todo(self) -> OceanTodoInput:
        for field_name in ("expected_outputs", "source_handles", "depends_on"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if self.todo_id in self.depends_on:
            raise ValueError("a todo cannot depend on itself")
        get_agent_profile(self.profile_id)
        return self


class OceanAssignmentInput(OceanToolInput):
    plan_goal: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "The unchanged user-level question this finite todo plan must answer. Keep it stable "
            "across waves."
        ),
    )
    todos: tuple[OceanTodoInput, ...] = Field(
        min_length=1,
        max_length=6,
        description=(
            "The Coordinator's complete finite scientific plan, repeated on every call. Split only "
            "at natural evidence seams. Independently answerable scientific questions may run in "
            "parallel even when they read the same dataset. Keep work together only when it shares "
            "an intermediate calculation and one integrated conclusion; never create one todo per plot."
        ),
    )
    dispatch: tuple[str, ...] = Field(
        min_length=1,
        max_length=4,
        description=(
            "Todo ids selected by the Coordinator for this wave. Independent ids in this list run "
            "in parallel. Each dispatched todo must target a distinct (profile_id, expert_key) "
            "Expert instance. Distinct expert_key values allow several Experts of the same type to "
            "run concurrently; reuse a key to continue that instance in a later round. The backend "
            "does not infer readiness or completion from todo state."
        ),
    )

    @model_validator(mode="after")
    def validate_assignment(self) -> OceanAssignmentInput:
        known_todos = validate_todo_graph((todo.todo_id, todo.depends_on) for todo in self.todos)
        if len(self.dispatch) != len(set(self.dispatch)):
            raise ValueError("dispatch must not contain duplicate todo ids")
        unknown_dispatch = sorted(set(self.dispatch) - known_todos)
        if unknown_dispatch:
            raise ValueError("dispatch references unknown todo ids: " + ", ".join(unknown_dispatch))
        todos_by_id = {todo.todo_id: todo for todo in self.todos}
        dispatched_experts = [
            (
                todos_by_id[todo_id].profile_id,
                todos_by_id[todo_id].expert_key or "default",
            )
            for todo_id in self.dispatch
        ]
        duplicate_experts = sorted(
            expert
            for expert in set(dispatched_experts)
            if dispatched_experts.count(expert) > 1
        )
        if duplicate_experts:
            raise ValueError(
                "dispatch may contain at most one todo per Expert instance; sequence additional "
                "work through that stable instance or use distinct expert_key values: "
                + ", ".join(f"{profile_id}/{expert_key}" for profile_id, expert_key in duplicate_experts)
            )
        return self


class OceanAssignmentTool(_OceanTool):
    name = "ocean_assign"
    description = (
        "Submit the Coordinator's complete finite scientific TodoPlan and dispatch one selected "
        "wave from it. Include every todo in todos on every call; list only the ids selected for "
        "this wave in dispatch. Independent dispatched todos run in parallel. After ExpertResults "
        "return, the Coordinator alone decides whether to accept, follow up, or revisit a todo. "
        "profile_id selects the capability type and expert_key selects a stable task-scoped Expert "
        "instance. Distinct expert_key values may run independent tasks concurrently under the same "
        "profile; reusing a key continues that Expert's session and workspace. Changing todo_id alone "
        "does not create another Expert. Dispatch no more than one todo per Expert instance in a wave. "
        "The backend does not judge scientific sufficiency. Split "
        "by independently reviewable scientific questions, never by file or delivery format alone. "
        "Experts own any code they need. Request interactive_view for user-facing maps/plots or "
        "material visual evidence; one round may publish multiple distinct views. Pass semantic "
        "source handles, never paths or internal refs."
    )
    input_model = OceanAssignmentInput

    def effect_for(self, arguments: OceanAssignmentInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    def concurrency_key(self, arguments: OceanAssignmentInput) -> str:
        del arguments
        return f"team-assign:{self.services.workspace_id}"

    async def execute(
        self, arguments: OceanAssignmentInput, context: ToolExecutionContext
    ) -> ToolResult:
        if self.services.team_assign_sink is None:
            return self._error("Team assignment is unavailable")

        async def action() -> ToolResult:
            value = await self.services.team_assign_sink(arguments.model_dump(mode="json"), context)
            return self._json(value, metadata={"team_work": value})

        return await self._run_mutation(context, action)


class OceanPaperCandidateInput(OceanToolInput):
    """One stable item in a Literature Expert-curated shortlist."""

    paper_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
        description="Stable short identifier used to return the researcher's selection.",
    )
    title: str = Field(min_length=1, max_length=1_000)
    topic: str = Field(
        min_length=1,
        max_length=500,
        description="One concise phrase stating why this paper is relevant to the research task.",
    )
    evidence_scope: Literal["metadata_only", "abstract", "public_excerpt"] = Field(
        description=(
            "The deepest source material actually inspected during discovery. This prevents a "
            "candidate assessment from being presented as a full-text conclusion."
        ),
    )
    evidence_summary: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "Concrete methods, variables, reported findings, or boundaries this paper can "
            "contribute, based only on the declared evidence_scope; not a generic abstract recap."
        ),
    )
    validation_target: str = Field(
        min_length=1,
        max_length=1_500,
        description=(
            "The specific observation, diagnostic, threshold, mechanism, or comparison in the "
            "current research task that this paper could support, challenge, or help reproduce."
        ),
    )
    citation: str | None = Field(
        default=None,
        max_length=2_000,
        description="Compact authors, venue, and year metadata when known.",
    )
    url: str | None = Field(
        default=None,
        max_length=4_096,
        description="Canonical landing-page or full-text URL when known.",
    )


class OceanRequestPaperSelectionInput(OceanToolInput):
    question: str = Field(
        default="Which papers should OceanMind use for the next stage?",
        min_length=1,
        max_length=4_000,
        description="A short decision prompt shown above the paper table.",
    )
    papers: tuple[OceanPaperCandidateInput, ...] = Field(
        min_length=1,
        max_length=30,
        description=(
            "The complete Literature Expert shortlist already reviewed by the Coordinator and "
            "explained to the user paper by paper. Each entry must preserve the Expert's evidence scope, concrete "
            "candidate evidence, and task-specific validation target in addition to identity."
        ),
    )

    @model_validator(mode="after")
    def validate_papers(self) -> OceanRequestPaperSelectionInput:
        ids = [paper.paper_id for paper in self.papers]
        if len(ids) != len(set(ids)):
            raise ValueError("paper_id values must be unique")
        return self


class OceanRequestPaperSelectionTool(_OceanTool):
    name = "ocean_request_paper_selection"
    description = (
        "Pause the current Coordinator turn and let the researcher choose papers from an interactive "
        "shortlist returned by the Literature Expert. Before calling this tool, write a complete "
        "paper-by-paper explanation in the assistant response: exact citation, inspected evidence scope, "
        "concrete source-grounded contribution, task-specific validation target, and the boundary caused "
        "by not yet reading the full text. Do not collapse the candidates into one-line summaries. Then "
        "call this tool with the same candidates without silently replacing or re-searching them. The "
        "interface deliberately shows only paper titles and selection checkboxes, then returns "
        "the selected stable paper_ids. Continue through the task's existing Literature "
        "Expert role to read the selected papers and produce conclusions; changing the planned todo "
        "does not create another instance of that role. Use this instead of asking the user to type titles or "
        "numbers. Do not acquire full text before the selection returns."
    )
    input_model = OceanRequestPaperSelectionInput

    def effect_for(self, arguments: OceanRequestPaperSelectionInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    def concurrency_key(self, arguments: OceanRequestPaperSelectionInput) -> str:
        del arguments
        return f"paper-selection:{self.services.workspace_id}"

    async def execute(
        self,
        arguments: OceanRequestPaperSelectionInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        if self.services.paper_selection_sink is None:
            return self._error("Interactive paper selection is unavailable")

        async def action() -> ToolResult:
            value = await self.services.paper_selection_sink(
                arguments.model_dump(mode="json"), context
            )
            return self._json(value, metadata={"paper_selection": value})

        return await self._run_mutation(context, action)


def create_ocean_tool_registry(services: OceanToolServices) -> ToolRegistry:
    return create_ocean_lead_tool_registry(services)


def _create_web_search_tool(services: OceanToolServices) -> WebSearchTool:
    def record_search(response: WebSearchResponse, context: ToolExecutionContext) -> None:
        if services.task_id is None:
            return
        request_id = context.request_id or response.request_id or "external_search"
        try:
            services.store.record_research_observation(
                ResearchObservationDraft(
                    workspace_id=services.workspace_id,
                    task_id=services.task_id,
                    request_id=request_id,
                    work_order_id=services.work_order_id,
                    kind=ObservationKind.SEARCH,
                    statement=response.query,
                    evidence_refs=tuple(item.url for item in response.results),
                    outcome="retrieved",
                    confidence=1.0,
                )
            )
        except RequestStoreError:
            # Search evidence remains usable even if optional learning telemetry
            # cannot be attached (for example during a legacy request replay).
            return

    return WebSearchTool(on_response=record_search)


def _register_agent_skill_tools(
    registry: ToolRegistry, services: OceanToolServices
) -> None:
    if services.skill_role is None:
        return
    registry.register(OceanListSkillsTool(services))
    registry.register(OceanLoadSkillTool(services))
    if services.task_id is not None:
        registry.register(OceanSaveExperienceTool(services))


def create_ocean_lead_tool_registry(services: OceanToolServices) -> ToolRegistry:
    registry = ToolRegistry()
    _register_agent_skill_tools(registry, services)
    for tool in (
        OceanResourcesTool(services),
        _create_web_search_tool(services),
        *((OceanAssignmentTool(services),) if services.team_assign_sink else ()),
        *(
            (OceanRequestPaperSelectionTool(services),)
            if services.paper_selection_sink
            else ()
        ),
        *((OceanPublishOutputsTool(services),) if services.expert_deliverables else ()),
    ):
        registry.register(tool)
    return registry


def create_ocean_expert_tool_registry(services: OceanToolServices) -> ToolRegistry:
    registry = ToolRegistry()
    _register_agent_skill_tools(registry, services)
    if "web.search" in services.skill_capabilities:
        registry.register(_create_web_search_tool(services))
    if "jina.reader" in services.skill_capabilities:
        registry.register(JinaReaderTool())
    if services.expert_code_execution is not None:
        registry.register(OceanExpertRunCodeTool(services))
    return registry


def create_ocean_discussion_tool_registry(services: OceanToolServices) -> ToolRegistry:
    registry = ToolRegistry()
    _register_agent_skill_tools(registry, services)
    return registry


__all__ = [
    "OceanToolServices",
    "candidate_outputs_from_execution",
    "create_ocean_discussion_tool_registry",
    "create_ocean_expert_tool_registry",
    "create_ocean_lead_tool_registry",
    "create_ocean_tool_registry",
]
