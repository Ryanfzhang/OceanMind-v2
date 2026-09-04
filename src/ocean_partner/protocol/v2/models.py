"""Transport-independent, discriminated Protocol v2 envelopes.

The models in this module are the sole protocol type source.  Adapters assign
per-connection event sequence numbers, but neither stdio nor WebSocket gets to
invent a second payload shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ocean_partner.artifacts.models import (
    ArtifactLinkDraft,
    ArtifactProjection,
    ArtifactRef,
    ArtifactType,
    PaperCitation,
)
from ocean_partner.desktop_contract import (
    DESKTOP_RUNTIME_CAPABILITY_SCHEMA,
    DESKTOP_RUNTIME_CONNECTIONS,
    DesktopConnectionId,
    DesktopConnectionLabel,
)
from ocean_partner.task_results import TaskResultRef

PROTOCOL_VERSION = 2

ClientKind = Literal["desktop"]
ActiveMode = Literal["literature", "exploration", "figure", "writing"]
RequestState = Literal["accepted", "in_progress", "completed", "failed", "cancelled", "interrupted"]
ResearchTaskState = Literal["active", "completed", "archived"]
ErrorCode = Literal[
    "invalid_request",
    "unsupported_protocol",
    "request_id_conflict",
    "request_interrupted",
    "workspace_revision_conflict",
    "permission_denied",
    "interaction_timeout",
    "model_error",
    "tool_error",
    "budget_exhausted",
    "expert_timeout",
    "expert_resource_limit_exceeded",
    "expert_code_exit_nonzero",
    "dependency_missing",
    "unsafe_execution",
    "source_changed_during_work",
    "expert_result_incomplete",
    "artifact_not_found",
    "artifact_version_conflict",
    "artifact_commit_failed",
    "invalid_artifact",
    "task_not_found",
    "task_revision_conflict",
    "task_active_request_conflict",
    "task_checkpoint_incompatible",
    "store_error",
    "cancelled",
]
DisclosureDecision = Literal["allow", "prompt", "deny"]
LiteratureAcquisitionMode = Literal[
    "ask_before_download",
    "auto_download_open_access",
    "search_only",
]


class StrictModel(BaseModel):
    """Reject forward-incompatible fields rather than silently dropping them."""

    model_config = ConfigDict(extra="forbid")


class RequestContext(StrictModel):
    """Client-visible routing context; authority is bound server-side."""

    session_id: str = Field(min_length=1, max_length=128)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, pattern=r"^task_[A-Za-z0-9_-]+$", max_length=128)
    active_mode: ActiveMode | None = None


class HandshakePayload(StrictModel):
    client_kind: ClientKind
    client_version: str = Field(min_length=1, max_length=128)
    supported_protocol_versions: list[int] = Field(min_length=1)
    bootstrap_token: str | None = Field(default=None, min_length=16, max_length=256)
    client_capability: str | None = Field(default=None, min_length=16, max_length=256)


class SessionOpenPayload(StrictModel):
    requested_workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    resume_session_id: str | None = Field(default=None, min_length=1, max_length=128)


class SessionSubmitPayload(StrictModel):
    """One user-authored foreground prompt for the workspace-bound Ocean agent."""

    text: str = Field(min_length=1, max_length=32_000)
    context_refs: tuple[ArtifactRef, ...] = Field(
        default=(), max_length=8, json_schema_extra={"uniqueItems": True}
    )
    literature_acquisition_mode: LiteratureAcquisitionMode = "ask_before_download"
    @model_validator(mode="after")
    def _require_non_whitespace_text(self) -> "SessionSubmitPayload":
        if not self.text.strip():
            raise ValueError("session.submit text must contain non-whitespace text")
        if len(set(self.context_refs)) != len(self.context_refs):
            raise ValueError("session.submit context_refs must be unique")
        return self


class TaskCreatePayload(StrictModel):
    title: str = Field(min_length=1, max_length=512)


class TaskListPayload(StrictModel):
    include_archived: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class TaskIdPayload(StrictModel):
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]+$", max_length=128)


class TaskRenamePayload(TaskIdPayload):
    title: str = Field(min_length=1, max_length=512)


class TaskSnapshotGetPayload(TaskIdPayload):
    transcript_limit: int = Field(default=100, ge=1, le=500)
    before_sequence: int | None = Field(default=None, ge=1)


class TaskAgentTranscriptGetPayload(TaskIdPayload):
    """Identify one visible participant in one task request."""

    parent_request_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    work_order_id: str | None = Field(default=None, max_length=128)


class TaskOutputListPayload(TaskIdPayload):
    limit: int = Field(default=100, ge=1, le=500)


class WorkspaceOpenPayload(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class EmptyPayload(StrictModel):
    """Explicit empty object payload for commands with no inputs."""


class RequestStatusGetPayload(StrictModel):
    target_request_id: str = Field(min_length=1, max_length=128)


class RequestCancelPayload(StrictModel):
    target_request_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=512)


class InteractionRespondPayload(StrictModel):
    interaction_id: str = Field(pattern=r"^int_[A-Za-z0-9_-]+$", max_length=128)
    answer: str = Field(min_length=1, max_length=16_000)

    @model_validator(mode="after")
    def _require_non_whitespace_answer(self) -> "InteractionRespondPayload":
        if not self.answer.strip():
            raise ValueError("interaction.respond answer must contain non-whitespace text")
        return self


class ShutdownPayload(StrictModel):
    reason: str | None = Field(default=None, max_length=512)


class ArtifactListPayload(StrictModel):
    artifact_type: ArtifactType | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ArtifactGetPayload(StrictModel):
    ref: ArtifactRef


class ArtifactResourceGrantPayload(StrictModel):
    """Request one bounded viewer resource from an exact immutable artifact version."""

    artifact_ref: ArtifactRef
    file_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
    purpose: Literal[
        "paper_viewer",
        "report_viewer",
        "report_notebook",
        "report_code",
        "report_environment",
        "report_inputs",
        "report_reproducibility",
        "report_image",
        "interactive_view_data",
        "interactive_view_preview",
    ]


class TaskResultResourceGrantPayload(StrictModel):
    """Request one immutable file from a result owned by the current task."""

    result_ref: TaskResultRef
    file_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,511}$")
    purpose: Literal[
        "report_viewer",
        "report_notebook",
        "report_code",
        "report_environment",
        "report_inputs",
        "report_reproducibility",
        "report_image",
        "interactive_view_data",
        "interactive_view_preview",
        "result_file",
    ]


class TaskResultInteractiveViewGetPayload(StrictModel):
    """Load one bounded interactive view from its canonical result files."""

    result_ref: TaskResultRef


class ArtifactVersionsPayload(StrictModel):
    artifact_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")


class ArtifactCreatePayload(StrictModel):
    artifact_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,127}$")
    artifact_type: ArtifactType
    title: str = Field(min_length=1, max_length=512)
    summary: str = Field(default="", max_length=8_000)
    content: dict[str, Any] = Field(default_factory=dict)
    intrinsic_links: tuple[ArtifactLinkDraft, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    supersedes_version: int | None = Field(default=None, ge=1)


class DatasetImportPayload(StrictModel):
    """Attach one local file or directory to the current Task.

    Local datasets are references by default.  The immutable artifact records the
    authorized path and provenance, while the source bytes stay where the user put
    them.  ``materialized_snapshot`` remains an explicit compatibility option.
    """

    relative_path: str | None = Field(default=None, min_length=1, max_length=1_024)
    local_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    desktop_staged: bool = False
    materialization_level: Literal["local_reference", "materialized_snapshot"] = (
        "local_reference"
    )
    materialization_acknowledged: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=512)
    artifact_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,127}$")

    @model_validator(mode="after")
    def _one_source_locator(self) -> "DatasetImportPayload":
        if (self.relative_path is None) == (self.local_path is None):
            raise ValueError("Dataset import requires exactly one local source path")
        if self.desktop_staged and self.relative_path is None:
            raise ValueError("Desktop-staged compatibility imports require relative_path")
        return self


class PaperImportPayload(StrictModel):
    """Import one workspace-local PDF without extracting or disclosing document text."""

    relative_path: str = Field(min_length=1, max_length=1_024)
    citation: PaperCitation
    materialization_acknowledged: Literal[True]
    artifact_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,127}$")


class PaperRegisterPayload(StrictModel):
    """Register citation metadata when no local document snapshot is available."""

    citation: PaperCitation
    artifact_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,127}$")


class HypothesisActivatePayload(StrictModel):
    """Explicit user action that selects one immutable hypothesis as active."""

    hypothesis_ref: ArtifactRef


class PortableExportCreatePayload(StrictModel):
    """Create one private, portable evidence bundle from exact immutable refs."""

    artifact_refs: tuple[ArtifactRef, ...] = Field(
        min_length=1, max_length=100, json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def _validate_unique_refs(self) -> "PortableExportCreatePayload":
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("Portable export artifact_refs must be unique")
        return self


class DisclosurePolicySummary(StrictModel):
    """Provider-bound decisions visible to local workspace clients, never disclosed data."""

    provider_id: str = Field(min_length=1, max_length=256)
    policy_version: int = Field(ge=1)
    metadata: DisclosureDecision
    aggregate_statistics: DisclosureDecision
    raw_bounded_sample: DisclosureDecision
    document_text: DisclosureDecision
    diagnostic_excerpt: DisclosureDecision
    confirmed: bool
    updated_at: datetime


class DisclosurePolicySetPayload(StrictModel):
    """A complete, explicitly confirmed policy replaces the active workspace policy."""

    provider_id: str = Field(min_length=1, max_length=256)
    metadata: DisclosureDecision
    aggregate_statistics: DisclosureDecision
    raw_bounded_sample: DisclosureDecision
    document_text: DisclosureDecision
    diagnostic_excerpt: DisclosureDecision
    confirmed: Literal[True]


class RequestBase(StrictModel):
    protocol_version: Literal[2]
    request_id: str = Field(pattern=r"^req_[A-Za-z0-9_-]+$", max_length=128)
    type: str
    payload: StrictModel
    context: RequestContext | None = None
    expected_workspace_revision: int | None = Field(default=None, ge=0)
    expected_task_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_context_after_handshake(self) -> "RequestBase":
        if self.type != "system.handshake" and self.context is None:
            raise ValueError("context is required after system.handshake")
        return self


class SystemHandshakeRequest(RequestBase):
    type: Literal["system.handshake"]
    payload: HandshakePayload


class SessionOpenRequest(RequestBase):
    type: Literal["session.open"]
    payload: SessionOpenPayload
    context: RequestContext


class SessionSubmitRequest(RequestBase):
    type: Literal["session.submit"]
    payload: SessionSubmitPayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class TaskCreateRequest(RequestBase):
    type: Literal["task.create"]
    payload: TaskCreatePayload
    context: RequestContext


class TaskListRequest(RequestBase):
    type: Literal["task.list"]
    payload: TaskListPayload
    context: RequestContext


class TaskGetRequest(RequestBase):
    type: Literal["task.get", "task.open"]
    payload: TaskIdPayload
    context: RequestContext


class TaskRenameRequest(RequestBase):
    type: Literal["task.rename"]
    payload: TaskRenamePayload
    context: RequestContext


class TaskArchiveRequest(RequestBase):
    type: Literal["task.archive", "task.reopen"]
    payload: TaskIdPayload
    context: RequestContext


class TaskDeleteRequest(RequestBase):
    type: Literal["task.delete"]
    payload: TaskIdPayload
    context: RequestContext


class TaskSnapshotGetRequest(RequestBase):
    type: Literal["task.snapshot.get"]
    payload: TaskSnapshotGetPayload
    context: RequestContext


class TaskAgentTranscriptGetRequest(RequestBase):
    type: Literal["task.agent_transcript.get"]
    payload: TaskAgentTranscriptGetPayload
    context: RequestContext


class TaskOutputListRequest(RequestBase):
    type: Literal["task.output.list"]
    payload: TaskOutputListPayload
    context: RequestContext


class WorkspaceOpenRequest(RequestBase):
    type: Literal["workspace.open"]
    payload: WorkspaceOpenPayload
    context: RequestContext


class WorkspaceSnapshotGetRequest(RequestBase):
    type: Literal["workspace.snapshot.get"]
    payload: EmptyPayload
    context: RequestContext


class RequestStatusGetRequest(RequestBase):
    type: Literal["request.status.get"]
    payload: RequestStatusGetPayload
    context: RequestContext


class RequestCancelRequest(RequestBase):
    type: Literal["request.cancel"]
    payload: RequestCancelPayload
    context: RequestContext


class InteractionRespondRequest(RequestBase):
    type: Literal["interaction.respond"]
    payload: InteractionRespondPayload
    context: RequestContext


class SystemShutdownRequest(RequestBase):
    type: Literal["system.shutdown"]
    payload: ShutdownPayload
    context: RequestContext


class ArtifactListRequest(RequestBase):
    type: Literal["artifact.list"]
    payload: ArtifactListPayload
    context: RequestContext


class ArtifactGetRequest(RequestBase):
    type: Literal["artifact.get"]
    payload: ArtifactGetPayload
    context: RequestContext


class ArtifactResourceGrantRequest(RequestBase):
    type: Literal["artifact.resource.grant"]
    payload: ArtifactResourceGrantPayload
    context: RequestContext


class TaskResultResourceGrantRequest(RequestBase):
    type: Literal["task_result.resource.grant"]
    payload: TaskResultResourceGrantPayload
    context: RequestContext


class TaskResultInteractiveViewGetRequest(RequestBase):
    type: Literal["task_result.interactive_view.get"]
    payload: TaskResultInteractiveViewGetPayload
    context: RequestContext


class ArtifactVersionsRequest(RequestBase):
    type: Literal["artifact.versions.get"]
    payload: ArtifactVersionsPayload
    context: RequestContext


class ArtifactCreateRequest(RequestBase):
    type: Literal["artifact.create"]
    payload: ArtifactCreatePayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class DatasetImportRequest(RequestBase):
    type: Literal["dataset.import"]
    payload: DatasetImportPayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class PaperImportRequest(RequestBase):
    type: Literal["paper.import"]
    payload: PaperImportPayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class PaperRegisterRequest(RequestBase):
    type: Literal["paper.register"]
    payload: PaperRegisterPayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class HypothesisActivateRequest(RequestBase):
    type: Literal["hypothesis.activate"]
    payload: HypothesisActivatePayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


class PortableExportCreateRequest(RequestBase):
    type: Literal["portable.export.create"]
    payload: PortableExportCreatePayload
    context: RequestContext


class DisclosurePolicyGetRequest(RequestBase):
    type: Literal["disclosure.policy.get"]
    payload: EmptyPayload
    context: RequestContext


class DisclosurePolicySetRequest(RequestBase):
    type: Literal["disclosure.policy.set"]
    payload: DisclosurePolicySetPayload
    context: RequestContext
    expected_workspace_revision: int = Field(ge=0)


RequestEnvelope = Annotated[
    Union[
        SystemHandshakeRequest,
        SessionOpenRequest,
        SessionSubmitRequest,
        TaskCreateRequest,
        TaskListRequest,
        TaskGetRequest,
        TaskRenameRequest,
        TaskArchiveRequest,
        TaskDeleteRequest,
        TaskSnapshotGetRequest,
        TaskAgentTranscriptGetRequest,
        TaskOutputListRequest,
        WorkspaceOpenRequest,
        WorkspaceSnapshotGetRequest,
        RequestStatusGetRequest,
        RequestCancelRequest,
        InteractionRespondRequest,
        SystemShutdownRequest,
        ArtifactListRequest,
        ArtifactGetRequest,
        ArtifactResourceGrantRequest,
        TaskResultResourceGrantRequest,
        TaskResultInteractiveViewGetRequest,
        ArtifactVersionsRequest,
        ArtifactCreateRequest,
        DatasetImportRequest,
        PaperImportRequest,
        PaperRegisterRequest,
        HypothesisActivateRequest,
        PortableExportCreateRequest,
        DisclosurePolicyGetRequest,
        DisclosurePolicySetRequest,
    ],
    Field(discriminator="type"),
]
REQUEST_ADAPTER: TypeAdapter[RequestEnvelope] = TypeAdapter(RequestEnvelope)


class RuntimeConnectionPayload(StrictModel):
    """One renderer-safe local capability, never an executable or endpoint."""

    id: DesktopConnectionId
    label: DesktopConnectionLabel
    available: bool


class ScientificRuntimeCapabilityPayload(StrictModel):
    """Bounded packaged-runtime identity without installation details."""

    available: bool
    schema_version: Literal["ocean-frozen-scientific-runtime/v1"] | None = None
    fingerprint_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dependency_count: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def _require_identity_only_when_available(self) -> "ScientificRuntimeCapabilityPayload":
        identity = (self.schema_version, self.fingerprint_sha256, self.dependency_count)
        if self.available and any(value is None for value in identity):
            raise ValueError("available scientific runtime requires a complete identity")
        if not self.available and any(value is not None for value in identity):
            raise ValueError("unavailable scientific runtime must not include partial identity")
        return self


class ResearchSkillMetadataPayload(StrictModel):
    """Display-safe metadata for one packaged research-process skill."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    description: str = Field(min_length=1, max_length=360)
    version: str = Field(pattern=r"^sha256:[a-f0-9]{12}$")


class DesktopRuntimeCapabilitiesPayload(StrictModel):
    """The only runtime capability document permitted in ``system.ready``."""

    schema_version: Literal[DESKTOP_RUNTIME_CAPABILITY_SCHEMA]
    backend_schema: str = Field(pattern=r"^ocean-desktop-backend/v[1-9][0-9]*$")
    connections: list[RuntimeConnectionPayload] = Field(min_length=1, max_length=16)
    scientific_runtime: ScientificRuntimeCapabilityPayload
    skills: list[ResearchSkillMetadataPayload] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def _require_connection_inventory(self) -> DesktopRuntimeCapabilitiesPayload:
        if tuple((item.id, item.label) for item in self.connections) != (
            DESKTOP_RUNTIME_CONNECTIONS
        ):
            raise ValueError("desktop runtime connections must use the fixed ordered inventory")
        names = [skill.name for skill in self.skills]
        if len(set(names)) != len(names):
            raise ValueError("desktop runtime skill metadata must not repeat a name")
        return self


class SystemReadyPayload(StrictModel):
    backend_version: str
    backend_schema: str = Field(pattern=r"^ocean-desktop-backend/v[1-9][0-9]*$")
    client_id: str
    session_id: str
    client_kind: ClientKind
    capabilities: list[str]
    runtime_capabilities: DesktopRuntimeCapabilitiesPayload
    client_capability: str | None = Field(default=None, min_length=16, max_length=256)


class RequestAcceptedPayload(StrictModel):
    request_type: str
    state: Literal["accepted", "in_progress"]


class RequestCompletedPayload(StrictModel):
    result: dict[str, Any]
    workspace_revision: int | None = Field(default=None, ge=0)


class ProtocolErrorPayload(StrictModel):
    code: ErrorCode
    message: str
    recoverable: bool
    details: dict[str, Any]


class RequestFailedPayload(StrictModel):
    error: ProtocolErrorPayload


class RequestCancelledPayload(StrictModel):
    reason: str | None = None


class PaperSelectionOptionPayload(StrictModel):
    """One Literature Expert-curated paper presented by the Coordinator."""

    paper_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    title: str = Field(min_length=1, max_length=1_000)
    topic: str = Field(min_length=1, max_length=500)
    evidence_scope: Literal["metadata_only", "abstract", "public_excerpt"] | None = None
    evidence_summary: str | None = Field(default=None, max_length=2_000)
    validation_target: str | None = Field(default=None, max_length=1_500)
    citation: str | None = Field(default=None, max_length=2_000)
    url: str | None = Field(default=None, max_length=4_096)


class InteractionRequestedPayload(StrictModel):
    interaction_id: str = Field(pattern=r"^int_[A-Za-z0-9_-]+$", max_length=128)
    kind: Literal["question", "permission", "paper_selection"]
    question: str = Field(min_length=1, max_length=4_000)
    tool_name: str | None = Field(default=None, max_length=128)
    options: tuple[PaperSelectionOptionPayload, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def _validate_options(self) -> InteractionRequestedPayload:
        if self.kind == "paper_selection":
            if not self.options:
                raise ValueError("paper_selection must contain at least one paper")
            ids = [option.paper_id for option in self.options]
            if len(ids) != len(set(ids)):
                raise ValueError("paper_selection paper_id values must be unique")
        elif self.options:
            raise ValueError("only paper_selection may contain options")
        return self


class TranscriptItemAppendedPayload(StrictModel):
    """A bounded local transcript record; it is progress, not a request terminal."""

    item_id: str = Field(pattern=r"^tr_[A-Za-z0-9_-]+$", max_length=128)
    role: Literal["user", "assistant", "tool", "system"]
    text: str = Field(max_length=64_000)
    turn_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=256)


class TaskSummaryPayload(StrictModel):
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]+$", max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    status: ResearchTaskState
    task_revision: int = Field(ge=0)
    active_request_id: str | None = Field(default=None, max_length=128)
    stable_checkpoint_id: str | None = Field(default=None, pattern=r"^chk_[A-Za-z0-9_-]+$")
    conversation_generation: int = Field(ge=0)
    created_at: str
    updated_at: str


class TaskTranscriptItemPayload(StrictModel):
    item_id: str = Field(pattern=r"^tr_[A-Za-z0-9_-]+$", max_length=128)
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant", "tool", "system"]
    text: str = Field(max_length=64_000)
    request_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=256)
    interrupted: bool = False
    created_at: str


class TaskInteractionPayload(StrictModel):
    interaction_id: str = Field(pattern=r"^int_[A-Za-z0-9_-]+$", max_length=128)
    kind: Literal["question", "permission", "paper_selection"]
    question: str = Field(min_length=1, max_length=4_000)
    options: tuple[PaperSelectionOptionPayload, ...] = Field(default=(), max_length=30)
    state: Literal["pending"]
    created_at: str

    @model_validator(mode="after")
    def _validate_options(self) -> TaskInteractionPayload:
        if self.kind == "paper_selection":
            if not self.options:
                raise ValueError("paper_selection must contain at least one paper")
            ids = [option.paper_id for option in self.options]
            if len(ids) != len(set(ids)):
                raise ValueError("paper_selection paper_id values must be unique")
        elif self.options:
            raise ValueError("only paper_selection may contain options")
        return self


class TeamAgentProfilePayload(StrictModel):
    """One trusted professional role available for sparse activation."""

    profile_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    authority: Literal["expert", "discussion"]
    category: Literal[
        "framing",
        "data",
        "science",
        "methods",
        "evidence",
        "visualization",
        "discussion",
    ]
    summary: str = Field(min_length=1, max_length=512)


class TeamAgentPayload(StrictModel):
    """One real participant in the active adaptive team topology."""

    agent_id: str = Field(min_length=1, max_length=128)
    profile_id: str | None = Field(default=None, max_length=128)
    expert_key: str | None = Field(default=None, max_length=64)
    semantic_role: str = Field(min_length=1, max_length=256)
    authority: Literal["coordinator", "expert", "discussion"]
    status: Literal[
        "planning",
        "working",
        "waiting",
        "discussing",
        "recovering",
        "completed",
        "incomplete",
        "blocked",
        "failed",
        "skipped",
    ]
    activity: str = Field(default="", max_length=512)
    work_order_id: str | None = Field(default=None, max_length=128)
    task_goal: str | None = Field(default=None, max_length=8_000)
    result_summary: str | None = Field(default=None, max_length=8_000)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)


class TeamTodoPayload(StrictModel):
    """Coordinator-authored plan item with execution progress only.

    ``state`` deliberately stops at ``result_returned``. Whether that result is
    sufficient, needs a follow-up, or completes the user's request remains a
    Coordinator decision.
    """

    todo_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=8_000)
    depends_on: tuple[str, ...] = Field(default=(), max_length=6)
    profile_id: str = Field(min_length=1, max_length=128)
    expert_key: str | None = Field(default=None, max_length=64)
    expected_outputs: tuple[str, ...] = Field(default=("answer",), max_length=16)
    state: Literal[
        "pending",
        "queued",
        "working",
        "result_returned",
        "stopped",
        "skipped",
    ]
    work_order_id: str | None = Field(default=None, max_length=128)
    session_round: int | None = Field(default=None, ge=1)
    result_summary: str | None = Field(default=None, max_length=8_000)


class TeamDependencyPayload(StrictModel):
    from_agent_id: str = Field(min_length=1, max_length=128)
    to_agent_id: str = Field(min_length=1, max_length=128)
    kind: Literal["delegation", "discussion", "feedback"]


class TeamInteractionPayload(StrictModel):
    interaction_id: str = Field(min_length=1, max_length=128)
    from_agent_id: str = Field(min_length=1, max_length=128)
    to_agent_id: str = Field(min_length=1, max_length=128)
    kind: Literal["delegation", "handoff", "discussion", "feedback", "continuation"]
    summary: str = Field(default="", max_length=512)
    state: Literal["active", "completed"]


class TeamSnapshotPayload(StrictModel):
    """A renderer-safe, event-derived view of the current adaptive team."""

    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    revision: int = Field(ge=1)
    status: Literal["working", "completed", "incomplete", "blocked", "failed"] = (
        "working"
    )
    strategy: Literal[
        "direct",
        "single_delegate",
        "parallel_team",
        "team_with_discussion",
    ]
    role_pool: tuple[TeamAgentProfilePayload, ...] = Field(default=(), max_length=16)
    agents: tuple[TeamAgentPayload, ...] = Field(min_length=1, max_length=13)
    todos: tuple[TeamTodoPayload, ...] = Field(default=(), max_length=6)
    dependencies: tuple[TeamDependencyPayload, ...] = Field(default=(), max_length=16)
    interactions: tuple[TeamInteractionPayload, ...] = Field(default=(), max_length=16)


class TaskSnapshotPayload(StrictModel):
    task: TaskSummaryPayload
    transcript: tuple[TaskTranscriptItemPayload, ...] = ()
    next_transcript_cursor: int | None = Field(default=None, ge=1)
    sources: tuple[dict[str, Any], ...] = ()
    outputs: tuple[dict[str, Any], ...] = ()
    delivery_manifests: tuple[dict[str, Any], ...] = ()
    task_results: tuple[dict[str, Any], ...] = ()
    interactions: tuple[TaskInteractionPayload, ...] = ()
    team_snapshot: TeamSnapshotPayload | None = None
    team_snapshots: tuple[TeamSnapshotPayload, ...] = Field(default=(), max_length=100)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    workflow: dict[str, Any] | None = None


class AssistantDeltaPayload(StrictModel):
    turn_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=16_000)


class AssistantTurnCompletedPayload(StrictModel):
    turn_id: str = Field(min_length=1, max_length=256)
    text: str = Field(default="", max_length=64_000)
    tool_call_ids: tuple[str, ...] = Field(default=(), max_length=128)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ToolCallStartedPayload(StrictModel):
    turn_id: str = Field(min_length=1, max_length=256)
    tool_call_id: str = Field(min_length=1, max_length=256)
    operation_id: str | None = Field(default=None, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    input: dict[str, Any]


class ToolCallCompletedPayload(StrictModel):
    turn_id: str = Field(min_length=1, max_length=256)
    tool_call_id: str = Field(min_length=1, max_length=256)
    operation_id: str | None = Field(default=None, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    output: str = Field(max_length=64_000)
    is_error: bool
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None


class ContextCompactionProgressPayload(StrictModel):
    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ]
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = Field(default=None, max_length=4_000)
    attempt: int | None = Field(default=None, ge=0)
    checkpoint: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None


class WorkspaceSnapshotPayload(StrictModel):
    workspace_id: str
    path: str | None = None
    revision: int = Field(ge=0)
    artifacts: list[dict[str, Any]]
    active_refs: dict[str, ArtifactRef] = Field(default_factory=dict)
    disclosure_policy: DisclosurePolicySummary | None = None


class WorkspaceChangedPayload(StrictModel):
    previous_revision: int = Field(ge=0)
    workspace_revision: int = Field(ge=0)
    change: Literal["opened", "updated"]


class DisclosurePolicyUpdatedPayload(StrictModel):
    policy: DisclosurePolicySummary
    previous_revision: int = Field(ge=0)
    workspace_revision: int = Field(ge=0)


class ArtifactSummaryPayload(StrictModel):
    ref: ArtifactRef
    artifact_type: ArtifactType
    title: str
    summary: str
    projection: ArtifactProjection


class ArtifactCreatedPayload(StrictModel):
    artifact: ArtifactSummaryPayload
    manifest_uri: str
    workspace_revision: int = Field(ge=0)


class ArtifactStatusChangedPayload(StrictModel):
    ref: ArtifactRef
    projection: ArtifactProjection


class SystemShutdownPayload(StrictModel):
    reason: str | None = None


class SystemErrorPayload(StrictModel):
    error: ProtocolErrorPayload


class EventBase(StrictModel):
    protocol_version: Literal[2]
    event_id: str = Field(pattern=r"^evt_[A-Za-z0-9_-]+$", max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    workspace_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, pattern=r"^task_[A-Za-z0-9_-]+$", max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    sequence: int = Field(ge=0)
    timestamp: datetime
    type: str
    payload: StrictModel


class SystemReadyEvent(EventBase):
    type: Literal["system.ready"]
    payload: SystemReadyPayload


class RequestAcceptedEvent(EventBase):
    type: Literal["request.accepted"]
    payload: RequestAcceptedPayload


class RequestCompletedEvent(EventBase):
    type: Literal["request.completed"]
    payload: RequestCompletedPayload


class RequestFailedEvent(EventBase):
    type: Literal["request.failed"]
    payload: RequestFailedPayload


class RequestCancelledEvent(EventBase):
    type: Literal["request.cancelled"]
    payload: RequestCancelledPayload


class InteractionRequestedEvent(EventBase):
    type: Literal["interaction.requested"]
    payload: InteractionRequestedPayload


class TranscriptItemAppendedEvent(EventBase):
    type: Literal["transcript.item.appended"]
    payload: TranscriptItemAppendedPayload


class AssistantDeltaEvent(EventBase):
    type: Literal["assistant.delta"]
    payload: AssistantDeltaPayload


class AssistantTurnCompletedEvent(EventBase):
    type: Literal["assistant.turn.completed"]
    payload: AssistantTurnCompletedPayload


class ToolCallStartedEvent(EventBase):
    type: Literal["tool.call.started"]
    payload: ToolCallStartedPayload


class ToolCallCompletedEvent(EventBase):
    type: Literal["tool.call.completed"]
    payload: ToolCallCompletedPayload


class ContextCompactionProgressEvent(EventBase):
    type: Literal["context.compaction.progress"]
    payload: ContextCompactionProgressPayload


class TaskSnapshotEvent(EventBase):
    type: Literal["task.snapshot"]
    payload: TaskSnapshotPayload


class TeamSnapshotEvent(EventBase):
    type: Literal["team.snapshot"]
    payload: TeamSnapshotPayload


class WorkspaceSnapshotEvent(EventBase):
    type: Literal["workspace.snapshot"]
    payload: WorkspaceSnapshotPayload


class WorkspaceChangedEvent(EventBase):
    type: Literal["workspace.changed"]
    payload: WorkspaceChangedPayload


class DisclosurePolicyUpdatedEvent(EventBase):
    type: Literal["disclosure.policy.updated"]
    payload: DisclosurePolicyUpdatedPayload


class SystemShutdownEvent(EventBase):
    type: Literal["system.shutdown"]
    payload: SystemShutdownPayload


class SystemErrorEvent(EventBase):
    type: Literal["system.error"]
    payload: SystemErrorPayload


class ArtifactCreatedEvent(EventBase):
    type: Literal["artifact.created"]
    payload: ArtifactCreatedPayload


class ArtifactVersionCreatedEvent(EventBase):
    type: Literal["artifact.version.created"]
    payload: ArtifactCreatedPayload


class ArtifactStatusChangedEvent(EventBase):
    type: Literal["artifact.status.changed"]
    payload: ArtifactStatusChangedPayload


EventEnvelope = Annotated[
    Union[
        SystemReadyEvent,
        RequestAcceptedEvent,
        RequestCompletedEvent,
        RequestFailedEvent,
        RequestCancelledEvent,
        InteractionRequestedEvent,
        TranscriptItemAppendedEvent,
        AssistantDeltaEvent,
        AssistantTurnCompletedEvent,
        ToolCallStartedEvent,
        ToolCallCompletedEvent,
        ContextCompactionProgressEvent,
        TaskSnapshotEvent,
        TeamSnapshotEvent,
        WorkspaceSnapshotEvent,
        WorkspaceChangedEvent,
        DisclosurePolicyUpdatedEvent,
        SystemShutdownEvent,
        SystemErrorEvent,
        ArtifactCreatedEvent,
        ArtifactVersionCreatedEvent,
        ArtifactStatusChangedEvent,
    ],
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[EventEnvelope] = TypeAdapter(EventEnvelope)

MUTATING_REQUEST_TYPES = frozenset(
    {
        "session.open",
        "session.submit",
        "task.create",
        "task.rename",
        "task.archive",
        "task.reopen",
        "task.delete",
        "workspace.open",
        "request.cancel",
        "system.shutdown",
        "artifact.create",
        "dataset.import",
        "paper.import",
        "paper.register",
        "hypothesis.activate",
        "disclosure.policy.set",
    }
)


def new_request_id() -> str:
    """Return an opaque request identifier suitable for Protocol v2."""

    return f"req_{uuid4().hex}"


def new_event_id() -> str:
    """Return an opaque event identifier stable across transport adapters."""

    return f"evt_{uuid4().hex}"


def parse_request(payload: object) -> RequestEnvelope:
    """Validate one decoded request object against the discriminated union."""

    return REQUEST_ADAPTER.validate_python(payload)


def parse_event(payload: object) -> EventEnvelope:
    """Validate one decoded event object against the discriminated union."""

    return EVENT_ADAPTER.validate_python(payload)


def canonical_request_fields(request: RequestEnvelope) -> dict[str, Any]:
    """Return the exact semantic fields used for durable idempotency hashing."""

    return {
        "protocol_version": request.protocol_version,
        "type": request.type,
        "payload": request.payload.model_dump(mode="json"),
        "workspace_id": request.context.workspace_id if request.context else None,
        "task_id": request.context.task_id if request.context else None,
        "expected_workspace_revision": request.expected_workspace_revision,
        "expected_task_revision": request.expected_task_revision,
    }


def is_mutating_request(request: RequestEnvelope) -> bool:
    """Identify requests that must reserve a durable idempotency record."""

    return request.type in MUTATING_REQUEST_TYPES


__all__ = [
    "ActiveMode",
    "ClientKind",
    "ErrorCode",
    "EVENT_ADAPTER",
    "EventEnvelope",
    "MUTATING_REQUEST_TYPES",
    "PROTOCOL_VERSION",
    "REQUEST_ADAPTER",
    "RequestContext",
    "RequestEnvelope",
    "ResearchTaskState",
    "canonical_request_fields",
    "is_mutating_request",
    "new_event_id",
    "new_request_id",
    "parse_event",
    "parse_request",
]
