"""Validated handoff contracts for OceanMind's hierarchical research team."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oceanx.artifacts.models import ArtifactRef
from oceanx.task_results import TaskResultRef

NonEmptyText = Annotated[str, Field(min_length=1, max_length=8_000)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


def expert_output_item_id(*, execution_id: str, output_name: str) -> str:
    """Return the stable identity of one immutable execution output version."""

    digest = hashlib.sha256(f"{execution_id}\0{output_name}".encode("utf-8")).hexdigest()
    return "result_" + digest[:32]


def validate_todo_graph(
    items: Iterable[tuple[str, Iterable[str]]],
) -> set[str]:
    """Validate finite todo identity and dependency structure, never readiness."""

    normalized = [(todo_id, tuple(dependencies)) for todo_id, dependencies in items]
    todo_ids = [todo_id for todo_id, _dependencies in normalized]
    if len(todo_ids) != len(set(todo_ids)):
        raise ValueError("todo ids must be unique within the Coordinator plan")
    known_todos = set(todo_ids)
    unknown_dependencies = sorted(
        {
            dependency
            for _todo_id, dependencies in normalized
            for dependency in dependencies
            if dependency not in known_todos
        }
    )
    if unknown_dependencies:
        raise ValueError(
            "todo dependencies are not present in the plan: " + ", ".join(unknown_dependencies)
        )

    dependency_map = dict(normalized)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(todo_id: str) -> None:
        if todo_id in visited:
            return
        if todo_id in visiting:
            raise ValueError("todo dependencies must form an acyclic graph")
        visiting.add(todo_id)
        for dependency in dependency_map[todo_id]:
            visit(dependency)
        visiting.remove(todo_id)
        visited.add(todo_id)

    for todo_id in todo_ids:
        visit(todo_id)
    return known_todos


class ChildAuthority(str, Enum):
    """Backend-enforced participant authority."""

    EXPERT = "expert"
    DISCUSSION = "discussion"


class WorkStatus(str, Enum):
    """Durable lifecycle states for work orders and terminal results."""

    QUEUED = "queued"
    RUNNING = "running"
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class WorkstreamPhase(str, Enum):
    """Durable progress inside one persistent Expert workstream.

    This phase is intentionally independent from ``WorkStatus``.  A child model
    may time out while the workstream still owns reusable code outputs, so a
    terminal attempt must not erase the work already completed.
    """

    ASSIGNED = "assigned"
    RUNNING = "running"
    RESULT_READY = "result_ready"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    FAILED = "failed"


class WorkFailureCode(str, Enum):
    """Stable machine-readable reason for unfinished work."""

    # Read compatibility for checkpoints created by the retired explicit
    # result-submission protocol. New workstreams do not emit this code.
    SUBMISSION_REQUIRED = "submission_required"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    NETWORK_FAILURE = "network_failure"
    PARTICIPANT_TIMEOUT = "participant_timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTRACT_FAILURE = "contract_failure"
    TOOL_VALIDATION_ERROR = "tool_validation_error"
    INVALID_APPROACH = "invalid_approach"
    ROLE_MISMATCH = "role_mismatch"
    DEPENDENCY_NOT_READY = "dependency_not_ready"
    UNSAFE_OUTPUT = "unsafe_output"
    INVALID_REQUEST = "invalid_request"
    PERMISSION_DENIED = "permission_denied"
    BACKEND_INTERRUPTED = "backend_interrupted"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ExpertResultOrigin(str, Enum):
    """Authority that finalized a terminal ExpertResult."""

    AGENT_SUBMITTED = "agent_submitted"
    BACKEND_RECOVERED = "backend_recovered"


class FindingBasis(str, Enum):
    """Kind of support behind a finding."""

    OBSERVATION = "observation"
    METHOD = "method"
    LITERATURE = "literature"
    ARTIFACT = "artifact"
    INFERENCE = "inference"


class ExpertDecision(str, Enum):
    """Expert self-assessment for one bounded assignment round.

    This value is advisory.  It never accepts the parent request; only the
    Coordinator can decide whether the returned evidence answers the user.
    """

    ACCEPTED = "accepted"
    NEEDS_REVISION = "needs_revision"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BLOCKED = "blocked"


class CoordinatorDecision(str, Enum):
    """Foreground Coordinator disposition for the user's whole request."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    GOAL_MISMATCH = "goal_mismatch"
    BLOCKED = "blocked"


class CoordinatorAnswerBasis(str, Enum):
    """Authority supporting the Coordinator's user-facing answer.

    The distinction is deliberately coarse.  It prevents a routing catalog
    from silently becoming scientific evidence while keeping ordinary
    knowledge questions and literal resource listings lightweight.
    """

    GENERAL_KNOWLEDGE = "general_knowledge"
    WORKSPACE_CATALOG = "workspace_catalog"
    EXPERT_EVIDENCE = "expert_evidence"


class FrozenModel(BaseModel):
    """Immutable, strict base for durable team records."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class InteractiveViewSpec(FrozenModel):
    """Compact scientific rendering semantics carried to the Coordinator.

    Numeric arrays are deliberately excluded. ``data`` contains only the
    renderer's variable bindings; the corresponding values live in the
    immutable NetCDF output described by :class:`ViewDataSchema`.
    """

    schema_version: Literal["ocean-interactive-view/v1"] = "ocean-interactive-view/v1"
    type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
    plot_kind: Annotated[str, Field(min_length=1, max_length=64)]
    figure_schema: Annotated[str, Field(min_length=1, max_length=128)]
    data: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=256)
    layout: dict[str, Any] = Field(default_factory=dict)
    panels: tuple[dict[str, Any], ...] = Field(min_length=1, max_length=32)
    interaction: dict[str, Any] = Field(default_factory=dict)
    spatial_context: dict[str, Any] | None = None


class ViewDataVariable(FrozenModel):
    """One named NetCDF variable and the channels it drives in the view."""

    name: Annotated[str, Field(min_length=1, max_length=256)]
    dims: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        min_length=1,
        max_length=8,
    )
    shape: tuple[int, ...] = Field(min_length=1, max_length=8)
    units: Annotated[str, Field(max_length=128)] = ""
    roles: tuple[Literal["x", "y", "z", "color", "category", "u", "v", "y0", "y1"], ...] = ()

    @model_validator(mode="after")
    def validate_dimensions(self) -> ViewDataVariable:
        if len(self.dims) != len(self.shape):
            raise ValueError("view data variable dims and shape must have equal rank")
        if any(size < 0 for size in self.shape):
            raise ValueError("view data variable shape cannot contain negative sizes")
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("view data variable roles must be unique")
        return self


class ViewDataSchema(FrozenModel):
    """External array contract required to hydrate one interactive view."""

    schema_version: Literal["ocean-view-data/v1"] = "ocean-view-data/v1"
    format: Literal["netcdf"] = "netcdf"
    dataset_output: Annotated[str, Field(min_length=1, max_length=512)]
    variables: tuple[ViewDataVariable, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_variables(self) -> ViewDataSchema:
        names = [variable.name for variable in self.variables]
        if len(names) != len(set(names)):
            raise ValueError("view data variable names must be unique")
        return self


class ExpertOutput(FrozenModel):
    """One runtime-collected result backed by an immutable execution output.

    ``result_ref`` is deliberately optional for a newly returned Expert
    result.  An Expert owns computation and may nominate an execution output
    as a candidate delivery, but only the Coordinator may promote that
    candidate to a task-local published result.
    """

    item_id: Identifier
    execution_id: Identifier
    output_name: Annotated[str, Field(min_length=1, max_length=512)]
    supporting_output_names: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = ()
    size_bytes: int = Field(ge=0)
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    # ``None`` means that the immutable execution output is a candidate still
    # awaiting Coordinator review.  A non-null ref means the Coordinator has
    # accepted and published it as a task-local result.
    result_ref: TaskResultRef | None = None
    result_kind: Literal["interactive_view", "report"] | None = None
    source_handle: Annotated[str, Field(pattern=r"^source_[1-9][0-9]*$")] | None = None
    title: Annotated[str, Field(max_length=512)] = ""
    summary: Annotated[str, Field(max_length=8_000)] = ""
    view_type: Annotated[str, Field(max_length=128)] | None = None
    # These two compact manifests tell the Coordinator exactly what the view
    # is and how its external NetCDF variables bind to visual channels. They
    # contain no numeric arrays and therefore remain safe to return through the
    # model protocol.
    view_spec: InteractiveViewSpec | None = None
    data_schema: ViewDataSchema | None = None
    # Scientific code records the conclusions supported by this output when it
    # saves the view.  The backend later turns these statements into typed
    # ExpertConclusion records with the correct output id; the model never has
    # to copy ids into a second JSON contract.
    claims: tuple[NonEmptyText, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_supporting_outputs(self) -> ExpertOutput:
        if len(self.supporting_output_names) != len(set(self.supporting_output_names)):
            raise ValueError("supporting ResultBundle outputs must be unique")
        if self.output_name in self.supporting_output_names:
            raise ValueError("primary ResultBundle output cannot also be supporting")
        if (self.view_spec is None) != (self.data_schema is None):
            raise ValueError("interactive view_spec and data_schema must be declared together")
        if self.result_kind == "report" and self.view_spec is not None:
            raise ValueError("report outputs cannot declare interactive view semantics")
        return self

    def coordinator_payload(self) -> dict[str, Any]:
        """Return the small path-based handoff consumed by the Coordinator."""

        payload: dict[str, Any] = {
            "path": self.path,
            "kind": self.result_kind,
            "title": self.title,
            "summary": self.summary,
            "sha256": self.sha256,
        }
        if self.view_type:
            payload["view_type"] = self.view_type
        return payload

    @property
    def path(self) -> str:
        """Path spelling used by Experts, the Coordinator, and the frontend."""

        return self.output_name if self.output_name.startswith("outputs/") else f"outputs/{self.output_name}"


class ResultBundle(FrozenModel):
    """Backend-owned incremental output ledger for one Expert workstream.

    This is checkpoint state, not a second Coordinator-facing contract.
    Raw and intermediate files remain CodeExecution evidence. Only
    Coordinator-accepted user-facing results enter this checkpoint ledger;
    unreviewed candidates travel in ``ExpertResult.outputs`` with no
    ``result_ref``.
    """

    schema_version: Literal["ocean-result-bundle/v1"] = "ocean-result-bundle/v1"
    items: tuple[ExpertOutput, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def unique_items(self) -> ResultBundle:
        item_ids = [item.item_id for item in self.items]
        output_names = [item.output_name for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("result bundle item_id values must be unique")
        if len(output_names) != len(set(output_names)):
            raise ValueError("result bundle output_name values must be unique")
        return self


class WorkstreamCheckpoint(FrozenModel):
    """Small durable resume packet owned by the backend, not by model prose."""

    schema_version: Literal["ocean-workstream-checkpoint/v1"] = "ocean-workstream-checkpoint/v1"
    phase: WorkstreamPhase = WorkstreamPhase.ASSIGNED
    latest_execution_id: Identifier | None = None
    successful_execution_ids: tuple[Identifier, ...] = ()
    output_ready_execution_ids: tuple[Identifier, ...] = ()
    result_bundle: ResultBundle = Field(default_factory=ResultBundle)
    result_refs: tuple[TaskResultRef, ...] = ()
    # Historical read compatibility only.  New Expert outputs use result_refs.
    deliverable_refs: tuple[ArtifactRef, ...] = ()
    # A validated, receiver-facing draft is persisted independently of result
    # materialization. It is intentionally generic: every Expert role uses the same
    # ExpertResult envelope and no scientific method is encoded here.
    draft_result: ExpertResult | None = None

    @model_validator(mode="after")
    def unique_refs(self) -> WorkstreamCheckpoint:
        for field_name in (
            "successful_execution_ids",
            "output_ready_execution_ids",
            "result_refs",
            "deliverable_refs",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        if not set(self.output_ready_execution_ids).issubset(self.successful_execution_ids):
            raise ValueError("output-ready executions must also be successful")
        return self


class EvidenceRef(FrozenModel):
    """Typed pointer to evidence visible in the current disclosure snapshot."""

    kind: Literal[
        "artifact",
        "code_execution",
        "dataset",
        "paper",
        "external",
    ]
    ref: Annotated[str, Field(min_length=1, max_length=1_024)]
    checksum: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    locator: Annotated[str, Field(min_length=1, max_length=2_048)] | None = None


class CoordinatorResult(FrozenModel):
    """Typed final boundary between the Coordinator and request lifecycle.

    A model stopping without this value has only stopped generating; it has not
    proved that the user's request reached a terminal outcome.
    """

    answer_markdown: Annotated[str, Field(min_length=1, max_length=64_000)]
    decision: CoordinatorDecision = CoordinatorDecision.ANSWERED
    answer_basis: CoordinatorAnswerBasis = CoordinatorAnswerBasis.GENERAL_KNOWLEDGE
    evidence_refs: tuple[EvidenceRef, ...] = Field(default=(), max_length=256)
    result_refs: tuple[TaskResultRef, ...] = Field(default=(), max_length=64)
    # Historical payload compatibility only. Runtime delivery and completion
    # decisions ignore this field; new work must return ``result_refs``.
    deliverable_refs: tuple[ArtifactRef, ...] = Field(
        default=(),
        max_length=64,
        json_schema_extra={"deprecated": True},
    )
    limitations: tuple[NonEmptyText, ...] = Field(default=(), max_length=64)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def unique_evidence(self) -> CoordinatorResult:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Coordinator result evidence refs must be unique")
        if len(self.deliverable_refs) != len(set(self.deliverable_refs)):
            raise ValueError("Coordinator result deliverable refs must be unique")
        if len(self.result_refs) != len(set(self.result_refs)):
            raise ValueError("Coordinator result task result refs must be unique")
        return self


class WorkBudget(FrozenModel):
    """Hard child limits owned by the backend, not prompt instructions."""

    max_turns: int = Field(default=48, ge=1, le=200)
    max_tool_calls: int = Field(default=64, ge=0, le=256)
    max_input_tokens: int = Field(default=120_000, ge=1, le=1_000_000)
    max_output_tokens: int = Field(default=32_000, ge=1, le=200_000)
    max_wall_seconds: float = Field(default=300.0, gt=0, le=3_600)


class UsageRecord(FrozenModel):
    """Measured child usage stored with a terminal result."""

    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0.0, ge=0)


class ResourceVersion(FrozenModel):
    """Backend-audited Manual or Reference identity used by one child."""

    kind: Literal["skill", "reference"]
    name: Annotated[str, Field(min_length=1, max_length=512)]
    version: Annotated[str, Field(min_length=1, max_length=128)]


class Finding(FrozenModel):
    """Evidence-bound statement returned by an Expert."""

    finding_id: Identifier
    claim: NonEmptyText
    evidence_refs: tuple[EvidenceRef, ...] = ()
    basis: FindingBasis
    confidence: float = Field(ge=0.0, le=1.0)
    counterevidence: tuple[NonEmptyText, ...] = ()
    prerequisites: tuple[NonEmptyText, ...] = ()
    uncertainty: NonEmptyText | None = None

    @model_validator(mode="after")
    def evidence_is_required_for_confident_non_inference(self) -> Finding:
        if (
            self.confidence >= 0.8
            and self.basis is not FindingBasis.INFERENCE
            and not self.evidence_refs
        ):
            raise ValueError("high-confidence non-inference findings require evidence_refs")
        return self


class CoordinatorTodo(FrozenModel):
    """One scientific question in the Coordinator's finite plan.

    This is planning structure, not backend-owned task state. The Coordinator
    decides which todos to dispatch, accept, continue, or revisit after reading
    ExpertResults.
    """

    todo_id: Identifier
    question: NonEmptyText
    depends_on: tuple[Identifier, ...] = ()
    profile_id: Identifier
    # A profile names a capability type; expert_key names one concrete,
    # task-scoped participant of that type.  Omitting it preserves the default
    # singleton participant used by older plans.
    expert_key: Identifier | None = None
    expected_outputs: tuple[Identifier, ...] = ("answer",)

    @model_validator(mode="after")
    def validate_structure(self) -> CoordinatorTodo:
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on must not contain duplicates")
        if self.todo_id in self.depends_on:
            raise ValueError("a todo cannot depend on itself")
        if len(self.expected_outputs) != len(set(self.expected_outputs)):
            raise ValueError("expected_outputs must not contain duplicates")
        return self


class WorkOrder(FrozenModel):
    """One bounded Coordinator round in an Expert or Discussion session."""

    work_order_id: Identifier
    # The research task owns the persistent participant session. Older stored
    # WorkOrders may not carry this field; the store recovers it from the
    # durable parent request when reading legacy records.
    task_id: Identifier | None = None
    # Backend-derived identity for one task-scoped Expert instance. It is
    # independent from todo ids, assignment wording, sources, and retries.
    job_key: Identifier | None = None
    # Coordinator-selected instance identity within a professional profile.
    # Several Experts may share one profile when their expert_key values differ.
    # Reusing the same value continues the same participant session.
    expert_key: Identifier | None = None
    # Each incremental Coordinator question is a new round.  Receiving a
    # ExpertResult ends this round, not the logical session identified by job_key.
    session_round: int = Field(default=1, ge=1)
    # Stable Coordinator-owned identity for one scientific sub-question. The
    # task-scoped Expert session may serve several todo ids sequentially.
    todo_id: Identifier | None = None
    depends_on: tuple[Identifier, ...] = ()
    parent_request_id: Identifier
    task_goal: NonEmptyText
    context_summary: Annotated[str, Field(max_length=16_000)] = ""
    profile_id: Identifier | None = None
    semantic_role: Annotated[str, Field(min_length=1, max_length=160)]
    authority: ChildAuthority
    input_refs: tuple[EvidenceRef, ...] = ()
    allowed_capabilities: tuple[Identifier, ...] = ()
    outcome_intents: tuple[Identifier, ...] = ("answer",)
    constraints: tuple[NonEmptyText, ...] = ()
    done_when: NonEmptyText = "Return an evidence-backed answer to the assigned question."
    budget_tier: Literal["quick", "standard", "deep"] = "standard"
    budget: WorkBudget = Field(default_factory=WorkBudget)
    deadline: datetime | None = None
    workspace_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_sets_and_authority(self) -> WorkOrder:
        for field_name in (
            "allowed_capabilities",
            "outcome_intents",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on must not contain duplicates")
        if self.todo_id is not None and self.todo_id in self.depends_on:
            raise ValueError("a todo cannot depend on itself")
        return self


class WorkPlan(FrozenModel):
    """One Coordinator-owned todo plan and the wave dispatched from it."""

    plan_id: Identifier
    parent_request_id: Identifier
    workspace_revision: int = Field(ge=0)
    reason_codes: tuple[Identifier, ...]
    plan_goal: Annotated[str, Field(max_length=8_000)] = ""
    todos: tuple[CoordinatorTodo, ...]
    dispatch: tuple[Identifier, ...]
    work_orders: tuple[WorkOrder, ...] = ()
    preserve_disagreements: bool = True

    @model_validator(mode="after")
    def validate_topology(self) -> WorkPlan:
        if not self.reason_codes:
            raise ValueError("routing requires at least one reason code")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must not contain duplicates")
        if not 1 <= len(self.todos) <= 6:
            raise ValueError("a Coordinator plan requires one to six scientific todos")
        if not 1 <= len(self.dispatch) <= 4:
            raise ValueError("a Coordinator wave dispatches one to four todos")
        if len(self.dispatch) != len(set(self.dispatch)):
            raise ValueError("dispatch must not contain duplicate todo ids")
        if not self.preserve_disagreements:
            raise ValueError("Coordinator plans must preserve unresolved disagreements")

        known_todos = validate_todo_graph((todo.todo_id, todo.depends_on) for todo in self.todos)
        unknown_dispatch = sorted(set(self.dispatch) - known_todos)
        if unknown_dispatch:
            raise ValueError(
                "dispatched todos are not present in the plan: " + ", ".join(unknown_dispatch)
            )

        order_ids = [order.work_order_id for order in self.work_orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("work_order_id values must be unique within a plan")
        order_todo_ids = [order.todo_id for order in self.work_orders]
        if any(todo_id is None for todo_id in order_todo_ids):
            raise ValueError("every dispatched work order requires a todo_id")
        if len(order_todo_ids) != len(set(order_todo_ids)):
            raise ValueError("a Coordinator wave may dispatch each todo only once")
        if set(order_todo_ids) != set(self.dispatch):
            raise ValueError("work orders must match the Coordinator dispatch list")
        for order in self.work_orders:
            if order.parent_request_id != self.parent_request_id:
                raise ValueError("all work orders must share the plan parent_request_id")
            if order.workspace_revision != self.workspace_revision:
                raise ValueError("all work orders must use the plan workspace_revision")
        return self


class ExpertResult(FrozenModel):
    """One unified Expert delivery returned to the Coordinator.

    Text-only work uses ``text`` alone. Analysis work adds durable ``outputs``
    and evidence-bound ``conclusions``. ``status`` describes only how this
    Expert round stopped; the Coordinator alone decides whether the user's
    request is complete.
    """

    work_order_id: Identifier
    status: Literal[
        WorkStatus.INCOMPLETE,
        WorkStatus.COMPLETED,
        WorkStatus.FAILED,
        WorkStatus.CANCELLED,
        WorkStatus.SKIPPED,
    ]
    result_origin: ExpertResultOrigin | None = None
    # Optional Expert self-assessment.  The Coordinator, not the transport
    # receiver, decides whether the returned content is sufficient.
    expert_decision: ExpertDecision | None = None
    text: Annotated[str, Field(max_length=8_000)] = ""
    outputs: tuple[ExpertOutput, ...] = Field(default=(), max_length=256)
    conclusions: tuple[ExpertConclusion, ...] = Field(default=(), max_length=128)
    findings: tuple[Finding, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    method_summary: Annotated[str, Field(max_length=8_000)] = ""
    checks_performed: tuple[NonEmptyText, ...] = ()
    suggested_next_step: Annotated[str, Field(max_length=4_000)] = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: tuple[NonEmptyText, ...] = ()
    limitations: tuple[NonEmptyText, ...] = ()
    unresolved_questions: tuple[NonEmptyText, ...] = ()
    resource_versions: tuple[ResourceVersion, ...] = ()
    usage: UsageRecord = Field(default_factory=UsageRecord)
    failure_code: WorkFailureCode | None = None
    error: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None

    @model_validator(mode="before")
    @classmethod
    def read_legacy_delivery(cls, value: Any) -> Any:
        """Read pre-ExpertResult records at the storage boundary only."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("text", payload.pop("summary", ""))
        if "outputs" not in payload:
            bundle = payload.pop("result_bundle", None)
            payload["outputs"] = bundle.get("items", []) if isinstance(bundle, dict) else []
        payload.pop("completed_outcomes", None)
        payload.pop("result_refs", None)
        payload.pop("deliverable_refs", None)
        return payload

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> ExpertResult:
        output_ids = [output.item_id for output in self.outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("ExpertResult output identifiers must be unique")
        known_outputs = set(output_ids)
        conclusion_ids = [conclusion.conclusion_id for conclusion in self.conclusions]
        if len(conclusion_ids) != len(set(conclusion_ids)):
            raise ValueError("ExpertResult conclusion identifiers must be unique")
        unknown_outputs = sorted(
            {
                output_id
                for conclusion in self.conclusions
                for output_id in conclusion.output_ids
                if output_id not in known_outputs
            }
        )
        if unknown_outputs:
            raise ValueError(
                "ExpertResult conclusions reference unavailable outputs: "
                + ", ".join(unknown_outputs)
            )
        interactive_output_ids = {
            output.item_id for output in self.outputs if output.result_kind == "interactive_view"
        }
        unbound_conclusions = [
            conclusion.conclusion_id
            for conclusion in self.conclusions
            if interactive_output_ids
            and not interactive_output_ids.intersection(conclusion.output_ids)
        ]
        if unbound_conclusions:
            raise ValueError(
                "Scientific conclusions must cite a supporting interactive view: "
                + ", ".join(unbound_conclusions)
            )
        if self.status is WorkStatus.COMPLETED:
            if not self.text:
                raise ValueError("a returned ExpertResult requires text")
            if self.error is not None:
                raise ValueError("a returned ExpertResult cannot include an error")
            if self.failure_code is not None:
                raise ValueError("a returned ExpertResult cannot include failure details")
        elif self.status is WorkStatus.INCOMPLETE:
            if not self.text and not self.outputs and self.error is None:
                raise ValueError("an interrupted ExpertResult requires text, outputs, or an error")
        elif self.error is None:
            raise ValueError("unsuccessful work requires an error")
        elif self.failure_code is None:
            raise ValueError("unsuccessful work requires a failure_code")
        return self

    @property
    def result_refs(self) -> tuple[TaskResultRef, ...]:
        """Return immutable output refs without storing a duplicate list."""

        return tuple(
            dict.fromkeys(
                output.result_ref for output in self.outputs if output.result_ref is not None
            )
        )

    def coordinator_payload(self) -> dict[str, Any]:
        """Return the two-field scientific handoff consumed by the Coordinator.

        Work status, budgets and failures remain in backend-owned work records.
        The semantic result itself contains only prose and fully described
        outputs, avoiding duplicated conclusions and cumulative execution state.
        """

        return {
            "text": self.text,
            "outputs": [output.coordinator_payload() for output in self.outputs],
        }


class ExpertConclusion(FrozenModel):
    """One scientific conclusion linked to the views that support it."""

    conclusion_id: Identifier
    statement: NonEmptyText
    basis: FindingBasis
    output_ids: tuple[Identifier, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: tuple[NonEmptyText, ...] = ()

    @model_validator(mode="after")
    def unique_support(self) -> ExpertConclusion:
        if len(self.output_ids) != len(set(self.output_ids)):
            raise ValueError("ExpertConclusion output_ids must be unique")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("ExpertConclusion evidence_refs must be unique")
        if not self.output_ids and not self.evidence_refs:
            raise ValueError("ExpertConclusion must cite at least one output or evidence reference")
        return self


WorkOrder.model_rebuild()
ExpertResult.model_rebuild()
WorkstreamCheckpoint.model_rebuild()


__all__ = [
    "ChildAuthority",
    "CoordinatorAnswerBasis",
    "CoordinatorDecision",
    "CoordinatorResult",
    "CoordinatorTodo",
    "EvidenceRef",
    "ExpertConclusion",
    "ExpertDecision",
    "ExpertOutput",
    "ExpertResult",
    "Finding",
    "FindingBasis",
    "InteractiveViewSpec",
    "ResourceVersion",
    "ResultBundle",
    "UsageRecord",
    "ViewDataSchema",
    "ViewDataVariable",
    "WorkBudget",
    "WorkFailureCode",
    "WorkOrder",
    "WorkPlan",
    "WorkStatus",
    "WorkstreamCheckpoint",
    "WorkstreamPhase",
    "expert_output_item_id",
    "validate_todo_graph",
]
