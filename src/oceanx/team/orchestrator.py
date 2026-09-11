"""OceanMind Coordinator-to-participant orchestration.

One WorkOrder owns one persistent participant identity.  An Expert may write,
run, inspect, and repair code inside that session.  The backend schedules work,
checks assignment/result envelope completeness, and persists the returned
result; it does not create a separate code-writing participant or an automatic rerun loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from oceanx.agent import (
    OceanAgentBudget,
    OceanAgentRuntime,
    OceanAgentRuntimeFactory,
    build_default_ocean_discussion_runtime,
    build_default_ocean_expert_runtime,
    configured_provider_id,
)
from oceanx.agent_contract import (
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from oceanx.artifacts.models import ArtifactRef
from oceanx.artifacts.service import ArtifactService
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.expert_deliverables import ExpertDeliverableService
from oceanx.expert_execution import ExpertCodeExecutionService
from oceanx.model_recovery import RETRY_DELAYS, model_events
from oceanx.protocol.v2.models import EventEnvelope
from oceanx.skills import (
    JINA_READER_CAPABILITY,
    LITERATURE_CAPABILITY,
    WEB_SEARCH_CAPABILITY,
)
from oceanx.task_results import TaskResultError, TaskResultStore
from oceanx.team.models import (
    ChildAuthority,
    EvidenceRef,
    ExpertConclusion,
    ExpertDecision,
    ExpertReport,
    ExpertResult,
    ExpertResultOrigin,
    FindingBasis,
    ResourceVersion,
    UsageRecord,
    WorkFailureCode,
    WorkOrder,
    WorkPlan,
    WorkStatus,
    WorkstreamCheckpoint,
)
from oceanx.team.profiles import (
    EXPERT_PROFILE_IDS,
    get_agent_profile,
    profile_system_prompt,
)
from oceanx.tools import OceanToolServices, candidate_outputs_from_execution

log = logging.getLogger(__name__)
TeamProgressSink = Callable[[], Awaitable[None]]


class OceanTeamError(RuntimeError):
    """A team assignment violated an authority or contract boundary."""


class OceanTeamDisabledError(OceanTeamError):
    """Team delegation is disabled by deployment policy."""


EXPERT_CAPABILITIES = frozenset(
    {
        "artifact.read",
        "dataset.read",
        "analysis.read",
        "manual.read",
        "reference.read",
        "code.write",
        "code.execute",
    }
)
DISCUSSION_CAPABILITIES = frozenset(
    capability
    for capability in EXPERT_CAPABILITIES
    if capability not in {"code.write", "code.execute"}
)
LEAD_CHILD_CAPABILITIES = frozenset({*EXPERT_CAPABILITIES, *DISCUSSION_CAPABILITIES})

# Only transport failures are safe to retry without another Coordinator
# decision. Scientific, contract, permission, and budget failures are useful
# results in their own right: return them to the Coordinator together with any
# durable partial work instead of silently starting another model loop.
TRANSIENT_SESSION_FAILURES = frozenset(
    {
        WorkFailureCode.PROVIDER_TIMEOUT,
        WorkFailureCode.PROVIDER_RATE_LIMIT,
        WorkFailureCode.NETWORK_FAILURE,
        WorkFailureCode.PARTICIPANT_TIMEOUT,
        WorkFailureCode.BACKEND_INTERRUPTED,
    }
)


def capabilities_for_authority(authority: ChildAuthority) -> frozenset[str]:
    if authority is ChildAuthority.EXPERT:
        return EXPERT_CAPABILITIES
    if authority is ChildAuthority.DISCUSSION:
        return DISCUSSION_CAPABILITIES
    raise OceanTeamError("Unknown participant authority")


@dataclass(frozen=True)
class OceanTeamSettings:
    enabled: bool = True
    max_active_experts: int = 4
    max_jobs_per_request: int = 12
    # Only transient provider/transport failures consume this allowance. A
    # budget, contract, method, or permission failure is immediately returned
    # to the Coordinator. This is shared across one model-delivery stream.
    max_transient_retries: int = 4

    def __post_init__(self) -> None:
        if not 1 <= self.max_active_experts <= 4:
            raise ValueError("Active Expert limit must be between one and four")
        if not self.max_active_experts <= self.max_jobs_per_request <= 16:
            raise ValueError("Request job limit must be between active limit and sixteen")
        if not 0 <= self.max_transient_retries <= 5:
            raise ValueError("Transient retry limit must be between zero and five")

    @classmethod
    def from_environment(cls) -> OceanTeamSettings:
        enabled = os.getenv("OCEAN_TEAM_RUNTIME_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        max_transient_retries = int(os.getenv("OCEAN_TEAM_MAX_TRANSIENT_RETRIES", "4"))
        return cls(enabled=enabled, max_transient_retries=max_transient_retries)


class _ParticipantState(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class _ParticipantUsage:
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_seconds: float = 0.0


@dataclass(frozen=True)
class _ParticipantRunResult:
    child_id: str
    state: _ParticipantState
    last_assistant_text: str = ""
    usage: _ParticipantUsage = field(default_factory=_ParticipantUsage)
    failure_code: WorkFailureCode | None = None
    error: str | None = None


@dataclass(frozen=True)
class _ParticipantSpec:
    child_id: str
    prompt: str


@dataclass
class _ParticipantBinding:
    workspace_id: str
    workspace_path: Path
    provider_id: str
    task_id: str | None
    work_order: WorkOrder
    # Tests and a few receiver-only paths construct bindings without launching
    # an Expert runtime.  Runtime paths always provide the stable key; the
    # empty default keeps those validation-only call sites independent of
    # conversation lifecycle state.
    session_key: tuple[str, str, str, str] = field(default_factory=tuple)
    child_id: str | None = None
    checkpoint: WorkstreamCheckpoint = field(default_factory=WorkstreamCheckpoint)
    prior_result: ExpertResult | None = None
    progress_sink: TeamProgressSink | None = None
    analysis_context: dict[str, object] = field(default_factory=dict)


class OceanTeamOrchestrator:
    """Run validated assignment rounds for durable logical participants."""

    def __init__(
        self,
        *,
        store: RequestStore,
        artifacts: ArtifactService,
        task_results: TaskResultStore,
        expert_code_execution: ExpertCodeExecutionService | None = None,
        expert_deliverables: ExpertDeliverableService | None = None,
        domain_event_emitter: Callable[[EventEnvelope], Awaitable[None]] | None = None,
        settings: OceanTeamSettings | None = None,
        expert_runtime_factory: OceanAgentRuntimeFactory = build_default_ocean_expert_runtime,
        discussion_runtime_factory: OceanAgentRuntimeFactory = build_default_ocean_discussion_runtime,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.task_results = task_results
        self.expert_code_execution = expert_code_execution
        self.expert_deliverables = expert_deliverables
        self.domain_event_emitter = domain_event_emitter
        self.settings = settings or OceanTeamSettings.from_environment()
        self.expert_runtime_factory = expert_runtime_factory
        self.discussion_runtime_factory = discussion_runtime_factory
        self._bindings: dict[str, _ParticipantBinding] = {}
        self._plans: dict[str, WorkPlan] = {}
        self._participant_locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}
        self._active_participants: dict[str, asyncio.Task[_ParticipantRunResult]] = {}

    async def delegate(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        task_id: str | None,
        work_order: WorkOrder,
        progress_sink: TeamProgressSink | None = None,
    ) -> ExpertResult:
        """Run one assignment until the Expert returns its ordinary final answer.

        Provider/transport interruptions are retried automatically. Runtime-owned
        outputs survive every interruption and are attached without another model
        formatting turn.
        """

        session_key = self._expert_session_key(
            workspace_id=workspace_id,
            task_id=task_id,
            work_order=work_order,
        )
        # One LangGraph thread represents one logical Expert. Independent todo
        # threads run concurrently; focused follow-ups serialize on this lock.
        lock = self._participant_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            for retry_index in range(self.settings.max_transient_retries + 1):
                result = await self._delegate_once(
                    workspace_id=workspace_id,
                    workspace_path=workspace_path,
                    provider_id=provider_id,
                    task_id=task_id,
                    work_order=work_order,
                    session_key=session_key,
                    progress_sink=progress_sink,
                )
                if not self._should_continue_workstream(result):
                    return result
                if retry_index >= self.settings.max_transient_retries:
                    # The Coordinator sees the last transient failure and can
                    # continue, change provider, or stop. Do not raise a second
                    # foreground error after useful partial work was persisted.
                    return result
                # Fallback for custom engines without checkpoint-resume support. Production
                # streams exhaust their allowance inside model_events and do not reach here.
                await asyncio.sleep(RETRY_DELAYS[min(retry_index, len(RETRY_DELAYS) - 1)])
        raise AssertionError("unreachable Expert delegation state")

    @staticmethod
    def _should_continue_workstream(result: ExpertResult) -> bool:
        return (
            result.result_origin is ExpertResultOrigin.BACKEND_RECOVERED
            and result.failure_code in TRANSIENT_SESSION_FAILURES
        )

    @staticmethod
    def _expert_session_key(
        *, workspace_id: str, task_id: str | None, work_order: WorkOrder
    ) -> tuple[str, str, str, str]:
        task_scope = task_id or f"request:{work_order.parent_request_id}"
        logical_expert = work_order.job_key or work_order.work_order_id
        participant_key = work_order.profile_id or (
            f"{work_order.authority.value}:{work_order.semantic_role}"
        )
        return (workspace_id, task_scope, participant_key, logical_expert)

    async def _delegate_once(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        task_id: str | None,
        work_order: WorkOrder,
        session_key: tuple[str, str, str, str],
        progress_sink: TeamProgressSink | None = None,
    ) -> ExpertResult:
        if not self.settings.enabled:
            raise OceanTeamDisabledError("OceanMind team delegation is disabled")
        if frozenset(work_order.allowed_capabilities) != capabilities_for_authority(
            work_order.authority
        ):
            raise OceanTeamError("Assignment capabilities do not match participant authority")

        existing = self.store.get_team_work(work_order.work_order_id)
        if existing is not None:
            if existing.state is WorkStatus.COMPLETED and existing.result is not None:
                if existing.result.result_origin is ExpertResultOrigin.AGENT_SUBMITTED:
                    return existing.result
                if not self._can_resume(existing):
                    return existing.result
            if existing.state is WorkStatus.RUNNING:
                raise OceanTeamError("Participant is already working on this assignment")
            if existing.state in {
                WorkStatus.COMPLETED,
                WorkStatus.INCOMPLETE,
                WorkStatus.FAILED,
                WorkStatus.CANCELLED,
            }:
                if not self._can_resume(existing):
                    assert existing.result is not None
                    return existing.result
                existing = self.store.resume_team_work(
                    workspace_id=workspace_id,
                    work_order=work_order,
                    max_resumes=None,
                )
            elif existing.work_order != work_order:
                raise OceanTeamError("Queued WorkOrder identity has different durable content")

        # Count durable logical sessions, not WorkOrder rounds. A focused
        # Coordinator follow-up keeps the same job_key and therefore does not
        # pay for a second participant merely because its assignment changed.
        delegated_jobs = {
            record.work_order.job_key or record.work_order.work_order_id
            for record in self.store.list_team_work(
                workspace_id=workspace_id,
                parent_request_id=work_order.parent_request_id,
            )
        }
        logical_job = work_order.job_key or work_order.work_order_id
        if (
            existing is None
            and logical_job not in delegated_jobs
            and len(delegated_jobs) >= self.settings.max_jobs_per_request
        ):
            raise OceanTeamError("Coordinator participant limit reached")
        if existing is None:
            existing = self.store.create_team_work_order(
                workspace_id=workspace_id, work_order=work_order
            )

        saved_submission = existing.checkpoint.draft_result
        if (
            saved_submission is not None
            and saved_submission.result_origin is ExpertResultOrigin.AGENT_SUBMITTED
        ):
            # The receiver committed the unified ExpertResult before the
            # provider connection stopped. Re-deliver that exact candidate;
            # launching another model call could only waste tokens or recompute
            # outputs that are already formally attached.
            prior_attempt = existing.result
            terminal = ExpertResult.model_validate(
                {
                    **saved_submission.model_dump(mode="python"),
                    "status": WorkStatus.COMPLETED,
                    "outputs": existing.checkpoint.result_bundle.items,
                    "usage": (
                        prior_attempt.usage if prior_attempt is not None else saved_submission.usage
                    ),
                    "resource_versions": (
                        prior_attempt.resource_versions
                        if prior_attempt is not None
                        else saved_submission.resource_versions
                    ),
                    "failure_code": None,
                    "error": None,
                }
            )
            self._validate_result_refs(task_id=task_id, result=terminal)
            self.store.complete_team_work(terminal)
            await self._notify_progress(progress_sink)
            return terminal

        # Receiver-side envelope validation happens before any model call.  It
        # checks identity and availability, not scientific method or file type.
        analysis_context: dict[str, object] = {}
        if work_order.authority is ChildAuthority.EXPERT:
            if self.expert_code_execution is None:
                raise OceanTeamError("Expert code capability is unavailable")
            try:
                sources = self.expert_code_execution.resolve_work_order_sources(
                    workspace_id=workspace_id,
                    work_order_id=work_order.work_order_id,
                )
                # Prepare the shared metadata before the model's first turn, not
                # inside its first analysis call. Text-only jobs need no Python.
                if sources:
                    context_task_id = work_order.task_id or task_id
                    if context_task_id is not None:
                        analysis_context = await self.expert_code_execution.get_task_dataset_context(
                            workspace_id=workspace_id,
                            task_id=context_task_id,
                            work_order_id=work_order.work_order_id,
                            sources=sources,
                        )
                    else:
                        analysis_context = {"sources": [source.manifest for source in sources]}
            except (RequestStoreError, RuntimeError, ValueError) as exc:
                terminal = ExpertResult(
                    work_order_id=work_order.work_order_id,
                    status=WorkStatus.FAILED,
                    failure_code=WorkFailureCode.CONTRACT_FAILURE,
                    error=f"Assignment sources are unavailable: {exc}",
                )
                self.store.complete_team_work(terminal)
                await self._notify_progress(progress_sink)
                return terminal

        prior_result = existing.result
        checkpoint = existing.checkpoint
        running_record = self.store.mark_team_work_running(work_order.work_order_id)
        await self._notify_progress(progress_sink)
        child_id = f"{work_order.work_order_id}:run:{running_record.resume_count + 1}"
        binding = _ParticipantBinding(
            workspace_id=workspace_id,
            workspace_path=workspace_path.resolve(),
            provider_id=provider_id,
            task_id=task_id,
            child_id=child_id,
            work_order=running_record.work_order,
            session_key=session_key,
            checkpoint=checkpoint,
            prior_result=prior_result,
            progress_sink=progress_sink,
            analysis_context=analysis_context,
        )
        self._bindings[child_id] = binding
        participant_task: asyncio.Task[_ParticipantRunResult] | None = None
        try:
            participant_task = asyncio.create_task(
                self._run_participant(binding),
                name=f"ocean-participant-{child_id}",
            )
            self._active_participants[child_id] = participant_task
            child_result = await participant_task
            terminal = self._terminal_result(binding, child_result)
        except asyncio.CancelledError:
            if participant_task is not None:
                participant_task.cancel()
                child_result = await asyncio.shield(participant_task)
                terminal = self._terminal_result(binding, child_result)
                self.store.complete_team_work(terminal)
                await self._notify_progress(progress_sink)
            raise
        # Runtime factories and provider adapters expose third-party exception
        # types. Normalize them into the durable participant result boundary.
        except Exception as exc:  # noqa: BLE001
            terminal = ExpertResult(
                work_order_id=work_order.work_order_id,
                status=WorkStatus.FAILED,
                failure_code=WorkFailureCode.UNKNOWN,
                error=f"Participant could not start: {exc}",
            )
        finally:
            self._active_participants.pop(child_id, None)
            self._bindings.pop(child_id, None)
        self.store.complete_team_work(terminal)
        await self._notify_progress(progress_sink)
        return terminal

    @staticmethod
    def _can_resume(record) -> bool:
        if record.result is None:
            return False
        if record.result.result_origin is not ExpertResultOrigin.BACKEND_RECOVERED:
            return False
        return record.result.failure_code in TRANSIENT_SESSION_FAILURES

    async def execute_plan(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        task_id: str | None,
        plan: WorkPlan,
        progress_sink: TeamProgressSink | None = None,
    ) -> tuple[ExpertResult, ...]:
        self._plans[plan.parent_request_id] = plan
        await self._notify_progress(progress_sink)
        completed = await asyncio.gather(
            *(
                self.delegate(
                    workspace_id=workspace_id,
                    workspace_path=workspace_path,
                    provider_id=provider_id,
                    task_id=task_id,
                    work_order=order,
                    progress_sink=progress_sink,
                )
                for order in plan.work_orders
            )
        )
        return tuple(completed)

    async def settle_request(
        self,
        parent_request_id: str,
        *,
        reason: str = "Coordinator concluded the request while this round was still active.",
    ) -> tuple[_ParticipantRunResult, ...]:
        """Stop leftover work without vetoing the Coordinator's final decision.

        Normal ``ocean_assign`` calls await every dispatched Expert, so this path
        is primarily for interrupted providers and stale queued/running records.
        Completed outputs and drafts are preserved as an incomplete ExpertResult;
        an empty assignment is simply cancelled.  The Coordinator can therefore
        finish honestly from the evidence it already reviewed.
        """

        active = [
            (child_id, task)
            for child_id, task in self._active_participants.items()
            if (binding := self._bindings.get(child_id)) is not None
            and binding.work_order.parent_request_id == parent_request_id
        ]
        for _child_id, task in active:
            task.cancel()
        gathered = (
            await asyncio.gather(*(task for _child_id, task in active), return_exceptions=True)
            if active
            else ()
        )
        results = tuple(result for result in gathered if isinstance(result, _ParticipantRunResult))
        for child_result in results:
            binding = self._bindings.pop(child_result.child_id, None)
            if binding is None:
                continue
            terminal = self._terminal_result(binding, child_result)
            try:
                self.store.complete_team_work(terminal)
            except RequestStoreError:
                pass
        request = self.store.get_request(parent_request_id)
        durable_records = (
            self.store.list_team_work(
                workspace_id=request.workspace_id,
                parent_request_id=parent_request_id,
            )
            if request is not None
            else ()
        )
        for record in durable_records:
            # Active bindings were handled above. This loop only catches a
            # durable row whose in-memory child disappeared unexpectedly.
            if record.state not in {WorkStatus.QUEUED, WorkStatus.RUNNING}:
                continue
            checkpoint = record.checkpoint
            evidence = tuple(
                EvidenceRef(kind="code_execution", ref=execution_id)
                for execution_id in checkpoint.successful_execution_ids
            )
            if checkpoint.draft_result is not None:
                draft = checkpoint.draft_result
                terminal = ExpertResult.model_validate(
                    {
                        **draft.model_dump(mode="python"),
                        "status": WorkStatus.INCOMPLETE,
                        "result_origin": ExpertResultOrigin.BACKEND_RECOVERED,
                        "outputs": checkpoint.result_bundle.items,
                        "evidence_refs": tuple(dict.fromkeys((*draft.evidence_refs, *evidence))),
                        "failure_code": WorkFailureCode.CANCELLED,
                        "error": reason,
                    }
                )
            elif checkpoint.result_bundle.items or evidence:
                terminal = ExpertResult(
                    work_order_id=record.work_order.work_order_id,
                    status=WorkStatus.INCOMPLETE,
                    result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                    text="Durable partial evidence was preserved when the request was settled.",
                    outputs=checkpoint.result_bundle.items,
                    evidence_refs=evidence,
                    failure_code=WorkFailureCode.CANCELLED,
                    error=reason,
                )
            else:
                terminal = ExpertResult(
                    work_order_id=record.work_order.work_order_id,
                    status=WorkStatus.CANCELLED,
                    result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                    failure_code=WorkFailureCode.CANCELLED,
                    error=reason,
                )
            try:
                self.store.complete_team_work(terminal)
            except RequestStoreError:
                pass
        return results

    async def close_request(self, parent_request_id: str) -> tuple[_ParticipantRunResult, ...]:
        results = await self.settle_request(
            parent_request_id,
            reason="The foreground request closed before this assignment returned a result.",
        )
        self._plans.pop(parent_request_id, None)
        request_scope = f"request:{parent_request_id}"
        for session_key in tuple(self._participant_locks):
            if session_key[1] != request_scope:
                continue
            self._participant_locks.pop(session_key, None)
        return results

    def plan_for(self, parent_request_id: str) -> WorkPlan | None:
        return self._plans.get(parent_request_id)

    async def close(self) -> None:
        parent_request_ids = {
            binding.work_order.parent_request_id for binding in self._bindings.values()
        }
        for parent_request_id in parent_request_ids:
            await self.close_request(parent_request_id)
        self._participant_locks.clear()

    def _participant_spec(self, binding: _ParticipantBinding) -> _ParticipantSpec:
        order = binding.work_order
        payload = order.scientific_assignment()
        payload["sources"] = self._source_contract(
            workspace_id=binding.workspace_id, input_refs=order.input_refs
        )
        payload["workstream_checkpoint"] = self._checkpoint_contract(binding)
        payload["analysis_context"] = self._participant_analysis_context(binding.analysis_context)
        if order.review and self.expert_code_execution is not None:
            payload["review_evidence"] = self.expert_code_execution.review_evidence(order.work_order_id)
        prompt = (
            "Own this bounded scientific question. Python is required only if you choose to run code. "
            "task_goal is the question; answer_standard states the requested evidence level and "
            "what it must resolve. required_outputs explains what is necessary. suggested_path and "
            "hints are optional and replaceable, never a mandatory method checklist. Choose and "
            "adjust analyses within this question, its authorized target_node/alternative_nodes, "
            "sources and budget. Return new mechanisms or research objectives as optional leads "
            "for the Coordinator to consider; do not expand the authorized objective yourself. "
            "Assigned source paths and available metadata are supplied below before your first turn. "
            "Do not run code merely to locate an input manifest or inspect the environment. "
            "Inside Python, read the full manifest directly with "
            "json.load(open(os.environ['OCEAN_INPUT_MANIFEST'])) after importing json and os; "
            "never guess its directory or search for inputs.json. "
            "When present, analysis_context is the server-prepared, "
            "task-scoped DatasetContext shared by all queries and Experts. It is the authoritative "
            "description of input paths, dimensions, coordinates, variables, and "
            "units. An input with inspection=ready is conclusive: use its declared member paths "
            "directly and never call code merely to list the source directory, test path existence, "
            "re-open the schema, or inspect helper signatures. If computation is needed, default "
            "to one complete Python program that reads the declared data, performs the necessary "
            "checks and calculations, and saves all requested user-facing figures with "
            "ScientificFigure, which is already available as a global in every code run. Use it "
            "directly and follow the exact API contract in the input manifest; never search for, "
            "import, or inspect its implementation. It creates output candidates in your "
            "isolated workspace; do not write a result schema or publish files yourself. After a "
            "save, ocean_expert_run_code returns each candidate path. Cite material statements "
            "in your ordinary final Markdown as [[output:relative/path.nc|short label]]; do not construct a "
            "second conclusions payload. Saving the same path again replaces the current candidate; "
            "old execution versions stay in backend audit history. The Coordinator reviews and publishes "
            "only accepted candidates and owns any final report requested by the user. A report intent "
            "therefore asks you for report-ready conclusions and evidence, not a report file. "
            "Include the scientific conclusion "
            "supported by each saved view in that API's conclusions argument. Reuse preserved "
            "executions, working data, and shared accepted results instead of recomputing them. Use "
            "another code call when it adds useful discriminating evidence within the authorized "
            "question, checks a consequential limitation, repairs an execution error, performs an "
            "assigned independent spot-check, or scientifically corrects prior results. No separate "
            "Test registration or method approval is required before analysis. When finished, "
            "return one ordinary concise answer to the Coordinator. That final answer ends this "
            "round; the runtime attaches saved outputs and evidence automatically. If evidence is "
            "insufficient, say exactly what remains unsupported. Ordinary Markdown is sufficient. "
            "If structured scientific information helps, you may instead return a JSON report "
            "with answer.statement and optional answer.direction, answer.level, answer.evidence_refs, "
            "answer.open_gaps, tests (saved Test IDs), limitations, leads, path_deviations, "
            "open_conflicts, and required_outputs_status. Do not repeat the same answer outside the "
            "report. Empty optional sections are valid; never invent entries to fill them.\n\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        professional_section = ""
        if order.profile_id is not None:
            professional_section = profile_system_prompt(get_agent_profile(order.profile_id))
        if professional_section:
            prompt = professional_section + "\n\n" + prompt
        return _ParticipantSpec(
            child_id=binding.child_id or order.work_order_id,
            prompt=prompt,
        )

    @staticmethod
    def _participant_analysis_context(context: dict[str, object]) -> dict[str, object]:
        """Keep the task DatasetContext useful without replaying bulky metadata.

        Full metadata stays in the immutable execution ``inputs.json``.  The
        participant needs paths, array layout, dimensions, coordinate extents,
        variable names, shapes, and units—not arbitrary global/variable attrs.
        """

        def compact_array(value: object) -> dict[str, object] | None:
            if not isinstance(value, dict):
                return None
            result = {
                key: value[key]
                for key in (
                    "name",
                    "dims",
                    "shape",
                    "dtype",
                    "chunks",
                    "coordinate_role",
                    "extent",
                )
                if key in value
            }
            attrs = value.get("attrs")
            if isinstance(attrs, dict):
                for key in ("units", "standard_name", "long_name", "positive"):
                    if key in attrs:
                        result[key] = attrs[key]
            return result

        def compact_arrays(value: object) -> list[dict[str, object]]:
            if not isinstance(value, list):
                return []
            return [item for raw in value if (item := compact_array(raw)) is not None]

        sources: list[dict[str, object]] = []
        raw_sources = context.get("sources")
        if isinstance(raw_sources, list):
            for raw_source in raw_sources:
                if not isinstance(raw_source, dict):
                    continue
                source = {
                    key: raw_source[key]
                    for key in (
                        "handle",
                        "kind",
                        "title",
                        "path",
                        "format",
                        "inspection",
                        "error",
                        "dataset_layout",
                        "member_count",
                        "dimensions",
                        "spatial_context",
                        "time_range",
                    )
                    if key in raw_source
                }
                source["coordinates"] = compact_arrays(raw_source.get("coordinates"))
                source["data_variables"] = compact_arrays(raw_source.get("data_variables"))
                members: list[dict[str, object]] = []
                raw_members = raw_source.get("members")
                if isinstance(raw_members, list):
                    for raw_member in raw_members:
                        if not isinstance(raw_member, dict):
                            continue
                        member = {
                            key: raw_member[key]
                            for key in (
                                "path",
                                "format",
                                "inspection",
                                "error",
                                "dimensions",
                            )
                            if key in raw_member
                        }
                        member["coordinates"] = compact_arrays(raw_member.get("coordinates"))
                        member["data_variables"] = compact_arrays(raw_member.get("data_variables"))
                        members.append(member)
                if members:
                    source["members"] = members
                sources.append(source)
        return {
            key: context[key]
            for key in ("schema_version", "context_id", "scope", "task_id")
            if key in context
        } | {
            "sources": sources,
            "instruction": (
                "This compact task-level DatasetContext is authoritative. Full metadata is "
                "already mounted in inputs.json for code execution; do not rebuild it."
            ),
        }

    def _checkpoint_contract(self, binding: _ParticipantBinding) -> dict[str, Any]:
        executions: list[dict[str, Any]] = []
        shared_results: list[dict[str, Any]] = []
        order = binding.work_order
        if order.job_key is None:
            job_executions = self.store.list_code_executions(order.work_order_id)
        else:
            job_executions = self.store.list_agent_job_code_executions(
                workspace_id=binding.workspace_id,
                task_id=order.task_id or binding.task_id,
                parent_request_id=order.parent_request_id,
                job_key=order.job_key,
            )
        job_execution_ids = {record.execution_id for record in job_executions}

        def saved_output_names(record: Any) -> tuple[str, ...]:
            origin = self.store.get_team_work(record.work_order_id)
            return tuple(
                dict.fromkeys(
                    name
                    for item in (origin.checkpoint.result_bundle.items if origin else ())
                    if item.execution_id == record.execution_id
                    for name in (item.output_name, *item.supporting_output_names)
                )
            )

        def validated_output_names(record: Any) -> tuple[str, ...]:
            """Recover framework-saved outputs without trusting a failed envelope."""

            saved = saved_output_names(record)
            if record.result is None:
                return saved
            candidates = candidate_outputs_from_execution(
                execution_id=record.execution_id,
                execution_result=record.result,
            )
            return tuple(
                dict.fromkeys(
                    (
                        *saved,
                        *(
                            name
                            for candidate in candidates
                            for name in (
                                candidate.output_name,
                                *candidate.supporting_output_names,
                            )
                        ),
                    )
                )
            )

        # The prompt gets a compact view; full stdout/stderr and generated files
        # remain directly readable without starting another code execution.
        recent_executions = list(job_executions[-3:])
        latest_failure = next(
            (record for record in reversed(job_executions)
             if record.result is not None and record.state not in {"running", "succeeded"}), None,
        )
        if latest_failure is not None and latest_failure not in recent_executions:
            recent_executions.insert(0, latest_failure)
        for record in recent_executions:
            saved_names = validated_output_names(record)
            if record.result is None:
                continue
            origin = self.store.get_team_work(record.work_order_id)
            ready = (
                record.execution_id in origin.checkpoint.output_ready_execution_ids
                or bool(saved_names)
                if origin is not None
                else False
            )
            stdout = str(record.result.get("stdout", ""))
            if len(stdout) > 1_500:
                stdout = (
                    stdout[:718]
                    + "\n... [middle omitted; use ocean_read_file on logs.stdout] ...\n"
                    + stdout[-718:]
                )
            executions.append(
                {
                    "execution_id": record.execution_id,
                    "origin_work_order_id": record.work_order_id,
                    "session_round": (
                        origin.work_order.session_round if origin is not None else None
                    ),
                    "purpose": record.request.get("purpose"),
                    "execution_state": record.state,
                    "output_ready": ready,
                    "output_files": (
                        list(saved_names)
                        if record.state != "succeeded"
                        else list(record.result.get("output_files", ()))
                    ),
                    "result_bundle_path": record.result.get("result_bundle_path"),
                    "result_fingerprint": record.result.get("result_fingerprint"),
                    "stdout_excerpt": stdout,
                    "stderr_excerpt": str(record.result.get("stderr", ""))[-1_500:],
                    "returncode": record.result.get("returncode"),
                    "saved_code_path": str(
                        Path(str(record.result["work_root"])) / "executions"
                        / record.execution_id / "code" / "analysis.py"
                    ) if record.result.get("work_root") else None,
                    "logs": record.result.get("logs") or ({
                        stream: str(
                            Path(str(record.result["work_root"]))
                            / "executions"
                            / record.execution_id
                            / "logs"
                            / f"{stream}.txt"
                        )
                        for stream in ("stdout", "stderr")
                    } if record.result.get("work_root") else {}),
                    "duration_seconds": record.result.get("duration_seconds"),
                }
            )
        for record in self.store.list_request_code_executions(
            workspace_id=binding.workspace_id,
            task_id=binding.task_id,
            parent_request_id=order.parent_request_id,
        ):
            saved_names = validated_output_names(record)
            origin = self.store.get_team_work(record.work_order_id)
            if record.execution_id in job_execution_ids or record.result is None or not saved_names:
                continue
            if order.todo_id is not None and (
                origin is None or origin.work_order.todo_id not in order.depends_on
            ):
                continue
            shared_results.append(
                {
                    "execution_id": record.execution_id,
                    "origin_work_order_id": record.work_order_id,
                    "origin_profile_id": (
                        origin.work_order.profile_id if origin is not None else None
                    ),
                    "origin_role": (
                        origin.work_order.semantic_role if origin is not None else None
                    ),
                    "purpose": record.request.get("purpose"),
                    "execution_state": record.state,
                    "output_files": list(saved_names),
                    "result_bundle_path": record.result.get("result_bundle_path"),
                    "result_fingerprint": record.result.get("result_fingerprint"),
                }
            )
        return {
            **binding.checkpoint.model_dump(mode="json", exclude={"draft_result"}),
            "resume_count": self.store.get_team_work(binding.work_order.work_order_id).resume_count,
            "executions": executions,
            "shared_results": shared_results[-8:],
            "execution_scope": ("agent_job" if order.job_key is not None else "work_order"),
            "instruction": (
                "This is compact durable memory for the logical Expert session. First answer from "
                "the prior-round summary and existing execution evidence. Use the full log/output "
                "paths or result_bundle_path with ocean_read_file when an exact omitted detail "
                "is needed; do not run Python just to search for your own logs. Run "
                "new code only when the incremental outcome was never computed or prior evidence "
                "must be scientifically corrected. A publication, formatting, or response-envelope "
                "failure never justifies recomputation. shared_results are immutable outputs from "
                "sibling Experts in this request: consume them as task evidence without copying, "
                "republishing, inheriting private transcripts, or repeating their computation. "
                "End with one "
                "concise final answer."
            ),
        }

    async def _build_participant_runtime(self, spec: _ParticipantSpec) -> OceanAgentRuntime:
        binding = self._bindings.get(spec.child_id)
        if binding is None:
            raise OceanTeamError(f"Missing participant binding: {spec.child_id}")
        order = binding.work_order
        participant_provider_id = configured_provider_id("coordinator" if order.review else "expert")
        services = OceanToolServices(
            workspace_id=binding.workspace_id,
            provider_id=participant_provider_id,
            store=self.store,
            artifacts=self.artifacts,
            task_id=binding.task_id,
            resource_access="inspection",
            skill_capabilities=(
                LITERATURE_CAPABILITY,
                *(
                    (WEB_SEARCH_CAPABILITY, JINA_READER_CAPABILITY)
                    if order.profile_id
                    in {"literature_reproduction_expert", "data_reproducibility_expert"}
                    else ()
                ),
            ),
            skill_role=order.profile_id,
            expert_result_origin_request_id=order.parent_request_id,
            expert_code_execution=(
                self.expert_code_execution
                if order.profile_id in EXPERT_PROFILE_IDS
                and order.authority is ChildAuthority.EXPERT
                else None
            ),
            expert_deliverables=(
                self.expert_deliverables
                if order.profile_id in EXPERT_PROFILE_IDS
                and order.authority is ChildAuthority.EXPERT
                else None
            ),
            domain_event_emitter=self.domain_event_emitter,
            expert_child_id=spec.child_id,
            work_order_id=order.work_order_id,
            agent_thread_id="expert:"
            + hashlib.sha256(
                "\0".join(
                    binding.session_key
                    or self._expert_session_key(
                        workspace_id=binding.workspace_id,
                        task_id=binding.task_id,
                        work_order=binding.work_order,
                    )
                ).encode("utf-8")
            ).hexdigest()[:40],
        )
        factory = {
            ChildAuthority.EXPERT: self.expert_runtime_factory,
            ChildAuthority.DISCUSSION: self.discussion_runtime_factory,
        }[order.authority]
        runtime = await factory(
            services,
            binding.workspace_path,
            OceanAgentBudget(
                max_turns=order.budget.max_turns,
                max_tool_calls=order.budget.max_tool_calls,
                max_wall_seconds=order.budget.max_wall_seconds,
                max_input_tokens=order.budget.max_input_tokens,
                max_output_tokens=order.budget.max_output_tokens,
            ),
            lambda request_id, turn_id, tool_call_id: self._operation_id(
                order.work_order_id, request_id, turn_id, tool_call_id
            ),
        )
        if runtime.provider_id != participant_provider_id:
            await runtime.close()
            raise OceanTeamError("Participant runtime differs from its assigned model-role API")
        return runtime

    async def _run_participant(self, binding: _ParticipantBinding) -> _ParticipantRunResult:
        """Run one Expert graph until its ordinary final answer returns control."""

        spec = self._participant_spec(binding)
        started = asyncio.get_running_loop().time()
        turns = tool_calls = input_tokens = output_tokens = 0
        final_text = ""
        failure_code: WorkFailureCode | None = None
        error: str | None = None
        runtime: OceanAgentRuntime | None = None
        try:
            runtime = await self._build_participant_runtime(spec)
            async with asyncio.timeout(binding.work_order.budget.max_wall_seconds):
                async for event in model_events(
                    runtime.engine,
                    spec.prompt,
                    binding.work_order.parent_request_id,
                    max_retries=self.settings.max_transient_retries,
                ):
                    event_name = type(event).__name__
                    if (
                        isinstance(event, AssistantTurnComplete)
                        or event_name == "AssistantTurnComplete"
                    ):
                        turns += 1
                        input_tokens += event.usage.input_tokens
                        output_tokens += event.usage.output_tokens
                        if event.message.text.strip() and not event.message.tool_uses:
                            final_text = event.message.text.strip()
                    elif (
                        isinstance(event, ToolExecutionStarted)
                        or event_name == "ToolExecutionStarted"
                    ):
                        tool_calls += 1
                        final_text = ""
                    elif (
                        isinstance(event, ToolExecutionCompleted)
                        or event_name == "ToolExecutionCompleted"
                    ):
                        await self._notify_progress(binding.progress_sink)
                    elif isinstance(event, ErrorEvent) or event_name == "ErrorEvent":
                        error = event.message
                        failure_code = WorkFailureCode.PROVIDER_UNAVAILABLE if getattr(event, "retries_exhausted", False) else {
                            "network_failure": WorkFailureCode.NETWORK_FAILURE,
                            "provider_timeout": WorkFailureCode.PROVIDER_TIMEOUT,
                            "provider_rate_limit": WorkFailureCode.PROVIDER_RATE_LIMIT,
                        }.get(event.code, WorkFailureCode.UNKNOWN)
                    elif isinstance(event, StatusEvent):
                        log.info("Expert %s: %s", binding.work_order.work_order_id, event.message)
                        await self._notify_progress(binding.progress_sink)
            state = _ParticipantState.COMPLETED if final_text and error is None else _ParticipantState.FAILED
            if state is _ParticipantState.FAILED and error is None:
                error = "Participant ended without a final answer"
        except TimeoutError:
            state = _ParticipantState.FAILED
            failure_code = WorkFailureCode.PARTICIPANT_TIMEOUT
            error = "Participant reached its wall-time limit"
        except asyncio.CancelledError:
            return _ParticipantRunResult(
                child_id=spec.child_id,
                state=_ParticipantState.CANCELLED,
                last_assistant_text=final_text,
                usage=_ParticipantUsage(
                    turns=turns,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    wall_seconds=max(0.0, asyncio.get_running_loop().time() - started),
                ),
                failure_code=WorkFailureCode.CANCELLED,
                error="Participant was cancelled",
            )
        except Exception as exc:  # noqa: BLE001 - provider adapters have no common error base
            state = _ParticipantState.FAILED
            failure_code = WorkFailureCode.UNKNOWN
            error = str(exc)
        finally:
            if runtime is not None:
                # LangGraph is the source of truth. This compact projection is
                # written only for the existing Agent History UI and crash
                # recovery report; it is never loaded back into the graph.
                messages = getattr(runtime.engine, "messages", ())
                if messages:
                    session_key = binding.session_key or self._expert_session_key(
                        workspace_id=binding.workspace_id,
                        task_id=binding.task_id,
                        work_order=binding.work_order,
                    )
                    try:
                        self.store.save_expert_session_checkpoint(
                            workspace_id=session_key[0],
                            task_scope=session_key[1],
                            participant_key=session_key[2],
                            job_key=session_key[3],
                            work_order_id=binding.work_order.work_order_id,
                            messages=[message.model_dump(mode="json") for message in messages],
                            compaction_generation=0,
                        )
                    except RequestStoreError:
                        # The graph checkpoint is authoritative. A display-only
                        # transcript projection must never turn a valid Expert
                        # answer into a failed workstream.
                        log.exception(
                            "Could not update Expert transcript projection for %s",
                            binding.work_order.work_order_id,
                        )
                await runtime.close()
        return _ParticipantRunResult(
            child_id=spec.child_id,
            state=state,
            last_assistant_text=final_text,
            usage=_ParticipantUsage(
                turns=turns,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                wall_seconds=max(0.0, asyncio.get_running_loop().time() - started),
            ),
            failure_code=failure_code,
            error=error,
        )

    def _source_contract(
        self, *, workspace_id: str, input_refs: tuple[Any, ...]
    ) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        for index, evidence in enumerate(input_refs, start=1):
            match = re.fullmatch(r"([a-z][a-z0-9_]{2,127})@v(\d+)", evidence.ref)
            title = evidence.kind
            if match is not None:
                ref = ArtifactRef(artifact_id=match.group(1), version=int(match.group(2)))
                artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
                if artifact is not None:
                    title = artifact.title
            sources.append(
                {
                    "handle": evidence.locator or f"source_{index}",
                    "kind": evidence.kind,
                    "title": title,
                }
            )
        return sources

    def _validate_result_refs(self, *, task_id: str | None, result: ExpertResult) -> None:
        for ref in result.result_refs:
            if task_id is None or ref.task_id != task_id:
                raise OceanTeamError(f"Task result is not owned by the current task: {ref.key}")
            try:
                self.task_results.get(ref)
            except TaskResultError as exc:
                raise OceanTeamError(f"Unavailable task result: {ref.key}") from exc

    @staticmethod
    def _runtime_conclusions(outputs: tuple[Any, ...]) -> tuple[ExpertConclusion, ...]:
        """Bind code-declared claims to backend-owned output ids."""

        conclusions: list[ExpertConclusion] = []
        for output in outputs:
            if output.result_kind != "interactive_view":
                continue
            statements = (
                output.claims
                or tuple(item for item in (output.summary, output.title) if item.strip())[:1]
            )
            for statement in statements:
                digest = hashlib.sha256(f"{output.item_id}\0{statement}".encode()).hexdigest()[:24]
                conclusions.append(
                    ExpertConclusion(
                        conclusion_id=f"conclusion_{digest}",
                        statement=statement,
                        basis=FindingBasis.ARTIFACT,
                        output_ids=(output.item_id,),
                    )
                )
        return tuple(conclusions)

    @staticmethod
    def _candidate_outputs(
        execution_records: tuple[Any, ...],
        accepted_outputs: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        """Return immutable candidates, preserving accepted versions as authoritative."""

        outputs: dict[str, Any] = {}
        for record in execution_records:
            if record.result is None:
                continue
            for output in candidate_outputs_from_execution(
                execution_id=record.execution_id,
                execution_result=record.result,
            ):
                # A corrected save to the same relative path replaces the
                # previous candidate for Coordinator review. Execution history
                # remains in the backend audit log, not in ExpertResult.
                outputs[output.output_name] = output
        # Coordinator-accepted checkpoint items carry result_ref and therefore
        # replace the otherwise identical unreviewed projection.
        for output in accepted_outputs:
            outputs[output.output_name] = output
        return tuple(outputs.values())

    @staticmethod
    def _missing_requested_outputs(
        order: WorkOrder,
        outputs: tuple[Any, ...],
    ) -> tuple[str, ...]:
        """Return structural delivery gaps without judging scientific quality."""

        # A report is a Coordinator-owned synthesis of accepted ExpertResults.
        # Experts deliver report-ready conclusions and evidence, never a report
        # artifact of their own.
        requested = {intent for intent in order.outcome_intents if intent == "interactive_view"}
        delivered = {output.result_kind for output in outputs if output.result_kind is not None}
        return tuple(sorted(requested - delivered))

    @staticmethod
    def _missing_output_next_step(missing: tuple[str, ...]) -> str:
        return (
            "Resume this same Expert session and produce only the missing requested "
            f"output type(s): {', '.join(missing)}. Reuse completed computation and "
            "do not restart the analysis."
        )

    def _terminal_result(
        self, binding: _ParticipantBinding, child: _ParticipantRunResult
    ) -> ExpertResult:
        usage = UsageRecord(
            turns=child.usage.turns,
            tool_calls=child.usage.tool_calls,
            input_tokens=child.usage.input_tokens,
            output_tokens=child.usage.output_tokens,
            wall_seconds=child.usage.wall_seconds,
        )
        resources = self._resource_versions(binding)
        cancelled = self._participant_state_is(child, _ParticipantState.CANCELLED)
        completed = self._participant_state_is(child, _ParticipantState.COMPLETED)
        failure_code = self._failure_code(child, cancelled=cancelled)
        current = self.store.get_team_work(binding.work_order.work_order_id)
        checkpoint = current.checkpoint if current is not None else binding.checkpoint
        final_text = child.last_assistant_text.strip()
        if (
            completed
            and checkpoint.draft_result is not None
            and checkpoint.draft_result.result_origin is ExpertResultOrigin.AGENT_SUBMITTED
        ):
            missing_outputs = self._missing_requested_outputs(
                binding.work_order,
                checkpoint.result_bundle.items,
            )
            submitted_draft = ExpertResult.model_validate(
                {
                    **checkpoint.draft_result.model_dump(mode="python"),
                    "status": (
                        WorkStatus.INCOMPLETE if missing_outputs else checkpoint.draft_result.status
                    ),
                    "expert_decision": (
                        ExpertDecision.INSUFFICIENT_EVIDENCE
                        if missing_outputs
                        else checkpoint.draft_result.expert_decision
                    ),
                    "outputs": checkpoint.result_bundle.items,
                    "usage": usage,
                    "resource_versions": resources,
                    "failure_code": None,
                    "error": None,
                    "suggested_next_step": (
                        self._missing_output_next_step(missing_outputs)
                        if missing_outputs
                        else checkpoint.draft_result.suggested_next_step
                    ),
                }
            )
            self._validate_result_refs(
                task_id=binding.task_id,
                result=submitted_draft,
            )
            return submitted_draft
        if not cancelled and checkpoint.draft_result is not None:
            draft = checkpoint.draft_result
            recovered_draft = ExpertResult.model_validate(
                {
                    **draft.model_dump(mode="python"),
                    "status": WorkStatus.INCOMPLETE,
                    "result_origin": ExpertResultOrigin.BACKEND_RECOVERED,
                    "usage": usage,
                    "resource_versions": resources,
                    "outputs": checkpoint.result_bundle.items,
                    "failure_code": failure_code,
                    "error": child.error or "Participant stopped during result delivery",
                    "suggested_next_step": (
                        draft.suggested_next_step
                        or "Resume delivery from the saved result draft; do not recompute."
                    ),
                }
            )
            return recovered_draft
        # Job-scoped executions survive assignment rounds. This is evidence
        # memory, not conversation replay.
        order = binding.work_order
        execution_records = (
            self.store.list_code_executions(order.work_order_id)
            if order.job_key is None
            else self.store.list_agent_job_code_executions(
                workspace_id=binding.workspace_id,
                task_id=order.task_id or binding.task_id,
                parent_request_id=order.parent_request_id,
                job_key=order.job_key,
            )
        )
        outputs = self._candidate_outputs(
            execution_records,
            checkpoint.result_bundle.items,
        )
        output_execution_ids = {output.execution_id for output in outputs}
        # A later exception cannot erase a result event that was already
        # written and whose files/hashes validate. Keep successful computation
        # as evidence, plus any failed execution that produced such a result.
        executions = tuple(
            record
            for record in execution_records
            if record.state == "succeeded" or record.execution_id in output_execution_ids
        )
        execution_evidence = tuple(
            EvidenceRef(kind="code_execution", ref=record.execution_id) for record in executions
        )
        runtime_conclusions = self._runtime_conclusions(outputs)
        if completed:
            completed_text = final_text
            if not completed_text and binding.prior_result is not None:
                completed_text = binding.prior_result.text.strip()
            if not completed_text and outputs:
                titles = [item.title or item.output_name for item in outputs]
                completed_text = "Completed the assigned analysis: " + "; ".join(titles)
            if completed_text:
                missing_outputs = self._missing_requested_outputs(
                    binding.work_order,
                    outputs,
                )
                completed_report = (
                    binding.prior_result.report
                    if not final_text and binding.prior_result is not None
                    and binding.prior_result.report is not None
                    else ExpertReport.from_response(completed_text, evidence_refs=execution_evidence)
                )
                return ExpertResult(
                    work_order_id=binding.work_order.work_order_id,
                    status=(WorkStatus.INCOMPLETE if missing_outputs else WorkStatus.COMPLETED),
                    result_origin=ExpertResultOrigin.AGENT_SUBMITTED,
                    expert_decision=(
                        ExpertDecision.INSUFFICIENT_EVIDENCE if missing_outputs else None
                    ),
                    text=completed_text,
                    report=completed_report,
                    outputs=outputs,
                    conclusions=runtime_conclusions,
                    evidence_refs=execution_evidence,
                    method_summary=(
                        "The runtime preserved the Expert's complete analysis program, notebook, "
                        "execution evidence, and every framework-saved result."
                        if executions
                        else "Scientific discussion returned without code execution."
                    ),
                    suggested_next_step=(
                        self._missing_output_next_step(missing_outputs) if missing_outputs else ""
                    ),
                    usage=usage,
                    resource_versions=resources,
                )
        # A provider or budget boundary does not erase completed computation.
        # Return a partial product so the Coordinator can ask only for the
        # missing delta instead of replaying the workstream.
        if not cancelled and (executions or outputs or final_text):
            partial_candidates = [
                final_text,
            ]
            if binding.prior_result is not None:
                partial_candidates.append(binding.prior_result.text.strip())
            partial_summary = max(partial_candidates, key=len).strip()
            prior_report = (
                binding.prior_result.report
                if binding.prior_result is not None
                and partial_summary == binding.prior_result.text.strip()
                else None
            )
            if len(partial_summary) > 8_000:
                partial_summary = (
                    partial_summary[:4_000]
                    + "\n... [middle omitted] ...\n"
                    + partial_summary[-4_000:]
                )
            if not partial_summary:
                partial_summary = (
                    f"Completed {len(executions)} durable code execution(s) before the "
                    "participant stopped."
                )
            return ExpertResult(
                work_order_id=binding.work_order.work_order_id,
                status=WorkStatus.INCOMPLETE,
                result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                text=partial_summary,
                report=prior_report or ExpertReport.from_response(
                    partial_summary, evidence_refs=execution_evidence
                ),
                outputs=outputs,
                conclusions=runtime_conclusions,
                evidence_refs=execution_evidence,
                method_summary=(
                    "Preserved every validated result and its code-execution evidence from "
                    "the interrupted AgentJob; no computation was repeated by the backend."
                ),
                suggested_next_step=(
                    "Continue only the unmet outcome identifiers and reuse the preserved evidence."
                ),
                usage=usage,
                resource_versions=resources,
                failure_code=failure_code,
                error=child.error or "Participant stopped before returning its final answer",
            )
        if cancelled:
            return ExpertResult(
                work_order_id=binding.work_order.work_order_id,
                status=WorkStatus.CANCELLED,
                result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                report=(
                    ExpertReport.from_response(final_text, evidence_refs=execution_evidence)
                    if final_text else (
                        checkpoint.draft_result.report if checkpoint.draft_result is not None else None
                    )
                ),
                outputs=outputs,
                conclusions=runtime_conclusions,
                evidence_refs=execution_evidence,
                method_summary=(
                    "The runtime preserved every validated result event emitted before "
                    "the participant was cancelled."
                    if outputs
                    else ""
                ),
                usage=usage,
                resource_versions=resources,
                failure_code=WorkFailureCode.CANCELLED,
                error=child.error or "Participant was explicitly cancelled",
            )
        return ExpertResult(
            work_order_id=binding.work_order.work_order_id,
            status=WorkStatus.INCOMPLETE,
            result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
            text=(
                "The Expert workstream stopped before returning a candidate answer. "
                "Resume the same WorkOrder without repeating saved computation."
            ),
            usage=usage,
            resource_versions=resources,
            outputs=outputs,
            failure_code=failure_code,
            error=child.error or "Participant ended without a candidate answer",
            suggested_next_step=("Continue the same Expert session from its saved evidence."),
        )

    @staticmethod
    def _participant_state_is(child: _ParticipantRunResult, expected: _ParticipantState) -> bool:
        value = getattr(child.state, "value", child.state)
        return value == expected.value

    @staticmethod
    def _failure_code(child: _ParticipantRunResult, *, cancelled: bool) -> WorkFailureCode:
        direct = getattr(child, "failure_code", None)
        if direct is not None:
            return WorkFailureCode(getattr(direct, "value", direct))
        legacy = getattr(child, "failure", None)
        if legacy is None:
            return WorkFailureCode.CANCELLED if cancelled else WorkFailureCode.UNKNOWN
        code = getattr(getattr(legacy, "code", None), "value", None)
        try:
            return WorkFailureCode(code)
        except ValueError:
            return WorkFailureCode.UNKNOWN

    @staticmethod
    async def _notify_progress(sink: TeamProgressSink | None) -> None:
        if sink is None:
            return
        try:
            await sink()
        except Exception:
            log.exception("Could not publish team progress")

    def _resource_versions(self, binding: _ParticipantBinding) -> tuple[ResourceVersion, ...]:
        records = self.store.list_resource_usage(
            workspace_id=binding.workspace_id,
            work_order_id=binding.work_order.work_order_id,
            limit=100,
        )
        unique: dict[tuple[str, str, str], ResourceVersion] = {}
        for record in reversed(records):
            key = (
                str(record["resource_kind"]),
                str(record["resource_name"]),
                str(record["resource_version"]),
            )
            unique[key] = ResourceVersion(kind=key[0], name=key[1], version=key[2])
        return tuple(unique.values())

    @staticmethod
    def _operation_id(child_id: str, request_id: str, turn_id: str, tool_call_id: str) -> str:
        digest = hashlib.sha256(
            f"{child_id}\0{request_id}\0{turn_id}\0{tool_call_id}".encode()
        ).hexdigest()
        return f"op_child_{digest[:40]}"


__all__ = [
    "DISCUSSION_CAPABILITIES",
    "EXPERT_CAPABILITIES",
    "LEAD_CHILD_CAPABILITIES",
    "OceanTeamDisabledError",
    "OceanTeamError",
    "OceanTeamOrchestrator",
    "OceanTeamSettings",
    "capabilities_for_authority",
]
