"""Protocol v2 request router with durable terminal-event semantics."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from langgraph.errors import GraphRecursionError

from ocean_partner.agent_contract import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ConversationMessage,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ocean_partner import __version__
from ocean_partner.desktop_contract import DESKTOP_BACKEND_SCHEMA
from ocean_partner.agent import (
    OceanAgentBudget,
    OceanAgentRuntime,
    OceanAgentRuntimeError,
    OceanAgentRuntimeFactory,
    build_default_ocean_agent_runtime,
    configured_model_id,
    configured_provider_id,
)
from ocean_partner.artifacts.files import ArtifactFileError
from ocean_partner.artifacts.models import (
    ArtifactRef,
    ArtifactVersionDraft,
    PaperContent,
)
from ocean_partner.artifacts.service import ArtifactService
from ocean_partner.backend.auth import principal_for_client_kind, required_capability
from ocean_partner.backend.events import BackendClient, EventBus
from ocean_partner.backend.store import (
    ArtifactVersionConflict,
    RequestIdConflict,
    RequestNotFound,
    RequestRecord,
    RequestStore,
    RequestStoreError,
    TaskCheckpointIncompatible,
    TaskNotFound,
    TaskRevisionConflict,
    TaskWorkflowState,
    TeamWorkRecord,
    WorkspaceRevisionConflict,
    WorkspaceSnapshot,
)
from ocean_partner.context import ContextPolicyError, ModelDataDisclosurePolicy, OceanContextBuilder
from ocean_partner.doctor import desktop_runtime_capabilities
from ocean_partner.exports import PortableExportError, PortableExportService
from ocean_partner.expert_deliverables import (
    ExpertDeliverableError,
    interactive_view_cache,
)
from ocean_partner.figure_reproduction import (
    FigureReproductionSource,
    build_figure_reproduction_notebook,
)
from ocean_partner.permissions import OceanExecutionPermissionChecker
from ocean_partner.skills import LITERATURE_CAPABILITY
from ocean_partner.skill_curator import SkillCurator
from ocean_partner.storage import is_safe_cross_platform_relative_path
from ocean_partner.team.models import (
    ChildAuthority,
    CoordinatorAnswerBasis,
    CoordinatorDecision,
    CoordinatorResult,
    CoordinatorTodo,
    EvidenceRef,
    ExpertDecision,
    WorkBudget,
    WorkOrder,
    WorkPlan,
    ExpertResultOrigin,
    WorkStatus,
    WorkstreamPhase,
)
from ocean_partner.team.profiles import AGENT_PROFILES, bind_agent_profile
from ocean_partner.team.orchestrator import (
    OceanTeamOrchestrator,
    capabilities_for_authority,
)
from ocean_partner.task_workspace import TaskWorkspaceProjector
from ocean_partner.task_results import TaskResultError, TaskResultRef, TaskResultStore
from ocean_partner.tools import OceanToolServices
from ocean_partner.runtime import OCEAN_RUNTIME_PROFILE_VERSION
from ocean_partner.protocol.v2.models import (
    AssistantDeltaEvent,
    AssistantDeltaPayload,
    AssistantTurnCompletedEvent,
    AssistantTurnCompletedPayload,
    ArtifactCreateRequest,
    DatasetImportRequest,
    DisclosurePolicyGetRequest,
    DisclosurePolicySetRequest,
    DisclosurePolicySummary,
    DisclosurePolicyUpdatedEvent,
    DisclosurePolicyUpdatedPayload,
    HypothesisActivateRequest,
    ArtifactCreatedEvent,
    ArtifactCreatedPayload,
    ArtifactGetRequest,
    ArtifactResourceGrantRequest,
    TaskResultResourceGrantRequest,
    TaskResultInteractiveViewGetRequest,
    ArtifactListRequest,
    ArtifactVersionsRequest,
    ArtifactSummaryPayload,
    ArtifactVersionCreatedEvent,
    ContextCompactionProgressEvent,
    ContextCompactionProgressPayload,
    ErrorCode,
    EventEnvelope,
    InteractionRequestedEvent,
    InteractionRequestedPayload,
    InteractionRespondRequest,
    PaperImportRequest,
    PaperRegisterRequest,
    ProtocolErrorPayload,
    RequestAcceptedEvent,
    RequestAcceptedPayload,
    RequestCancelRequest,
    RequestCancelledEvent,
    RequestCancelledPayload,
    RequestCompletedEvent,
    RequestCompletedPayload,
    RequestEnvelope,
    RequestFailedEvent,
    RequestFailedPayload,
    RequestStatusGetRequest,
    PortableExportCreateRequest,
    SessionOpenRequest,
    SessionSubmitRequest,
    SystemErrorEvent,
    SystemErrorPayload,
    SystemHandshakeRequest,
    SystemReadyEvent,
    SystemReadyPayload,
    SystemShutdownEvent,
    SystemShutdownPayload,
    SystemShutdownRequest,
    TaskArchiveRequest,
    TaskAgentTranscriptGetRequest,
    TaskCreateRequest,
    TaskDeleteRequest,
    TaskGetRequest,
    TaskListRequest,
    TaskOutputListRequest,
    TaskRenameRequest,
    TaskSnapshotEvent,
    TaskSnapshotGetRequest,
    TaskSnapshotPayload,
    TeamAgentPayload,
    TeamAgentProfilePayload,
    TeamDependencyPayload,
    TeamInteractionPayload,
    TeamSnapshotEvent,
    TeamSnapshotPayload,
    TeamTodoPayload,
    ToolCallCompletedEvent,
    ToolCallCompletedPayload,
    ToolCallStartedEvent,
    ToolCallStartedPayload,
    TranscriptItemAppendedEvent,
    TranscriptItemAppendedPayload,
    WorkspaceChangedEvent,
    WorkspaceChangedPayload,
    WorkspaceOpenRequest,
    WorkspaceSnapshotEvent,
    WorkspaceSnapshotGetRequest,
    WorkspaceSnapshotPayload,
    new_event_id,
    parse_request,
)


def _local_dataset_format(path: Path) -> str:
    """Return a non-authoritative format hint; unknown formats remain importable."""

    if path.is_dir():
        if any(
            (path / marker).is_file()
            for marker in (".zgroup", ".zarray", ".zmetadata", "zarr.json")
        ):
            return "zarr"
        return "directory"
    suffix = path.suffix.lower()
    aliases = {
        ".nc": "netcdf",
        ".nc4": "netcdf",
        ".cdf": "netcdf",
        ".tif": "geotiff",
        ".tiff": "geotiff",
        ".grb": "grib",
        ".grb2": "grib",
        ".grib": "grib",
        ".grib2": "grib",
        ".h5": "hdf5",
        ".hdf": "hdf5",
        ".hdf5": "hdf5",
    }
    return aliases.get(suffix, suffix.lstrip(".") or "unknown")


_LOGGER = logging.getLogger(__name__)


class CrashAfterTerminalCommit(RuntimeError):
    """Test-only crash seam for the commit-before-broadcast recovery contract."""


TerminalCommitHook = Callable[[EventEnvelope], Awaitable[None] | None]


@dataclass
class _AgentSession:
    """One cached model conversation tied to one authenticated Ocean session."""

    session_id: str
    runtime_key: str
    workspace_id: str
    task_id: str | None
    runtime: OceanAgentRuntime


class _AgentBudgetExceeded(RuntimeError):
    """A backend-owned foreground agent budget reached a hard limit."""


class _AgentToolWaitExceeded(RuntimeError):
    """An independently budgeted tool exceeded its wait safety ceiling."""


class OceanRequestRouter:
    """Route validated envelopes while keeping authority and durability server-side."""

    def __init__(
        self,
        *,
        store: RequestStore,
        event_bus: EventBus,
        artifact_service: ArtifactService | None = None,
        portable_export_service: PortableExportService | None = None,
        team_orchestrator: OceanTeamOrchestrator | None = None,
        agent_runtime_factory: OceanAgentRuntimeFactory | None = None,
        agent_budget: OceanAgentBudget | None = None,
        provider_id_resolver: Callable[[], str] = configured_provider_id,
        model_id_resolver: Callable[[], str] | None = None,
        after_terminal_commit: TerminalCommitHook | None = None,
        task_workspace_projector: TaskWorkspaceProjector | None = None,
        task_results: TaskResultStore | None = None,
        skill_curator: SkillCurator | None = None,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.artifact_service = artifact_service
        self.portable_export_service = portable_export_service
        self.team_orchestrator = team_orchestrator
        self.agent_runtime_factory = agent_runtime_factory or build_default_ocean_agent_runtime
        self.agent_budget = agent_budget or OceanAgentBudget()
        self.provider_id_resolver = provider_id_resolver
        # Production sessions must be rebuilt when the user changes only the
        # Coordinator model under the same provider. Injected runtimes keep
        # their own lifecycle contract and therefore opt in explicitly.
        self.model_id_resolver = model_id_resolver
        if (
            model_id_resolver is None
            and agent_runtime_factory is None
            and provider_id_resolver is configured_provider_id
        ):
            self.model_id_resolver = configured_model_id
        self.after_terminal_commit = after_terminal_commit
        self.task_workspace_projector = task_workspace_projector
        self.task_results = task_results
        self.skill_curator = skill_curator
        self.shutdown_requested = False
        self._agent_sessions: dict[str, _AgentSession] = {}
        self._agent_tasks: dict[str, asyncio.Task[None]] = {}
        self._agent_request_sessions: dict[str, str] = {}
        self._agent_request_clients: dict[str, BackendClient] = {}
        self._team_snapshot_revisions: dict[str, int] = {}
        # Internal callers such as the frozen evaluator can lower a single
        # follow-up request's remaining budget without changing the user-facing
        # Protocol v2 payload or the router-wide default.
        self._agent_request_budgets: dict[str, OceanAgentBudget] = {}
        self._cancelling_agent_requests: set[str] = set()
        self._pending_questions: dict[str, tuple[str, str, asyncio.Future[str]]] = {}
        self._closing = False
        self.recovered_events = self.store.recover_incomplete(
            self._interrupted_event,
            self._recovered_completed_event,
        )

    async def handle_payload(self, client: BackendClient, payload: object) -> None:
        """Validate a decoded payload and send a typed error on rejection."""

        try:
            request = parse_request(payload)
        except ValidationError as exc:
            await self._emit_system_error(
                client,
                code="invalid_request",
                message="Request does not satisfy Protocol v2 schema",
                recoverable=True,
                details={
                    "validation": exc.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    )
                },
            )
            return
        await self.handle(client, request)

    def interrupt_active_requests(self) -> list[EventEnvelope]:
        """Durably terminate work left active when this backend is shutting down."""

        return self.store.recover_incomplete(
            self._interrupted_event,
            self._recovered_completed_event,
        )

    async def shutdown_active_analysis(self) -> None:
        """Stop every foreground operation before request recovery closes the backend."""

        self._closing = True
        for request_id, task in list(self._agent_tasks.items()):
            self._cancelling_agent_requests.add(request_id)
            if not task.done():
                task.cancel()
        tasks = [task for task in self._agent_tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        sessions = list(self._agent_sessions.values())
        self._agent_sessions.clear()
        for session in sessions:
            await session.runtime.close()
        if self.team_orchestrator is not None:
            await self.team_orchestrator.close()

    async def handle(self, client: BackendClient, request: RequestEnvelope) -> None:
        """Process one typed request from a registered transport connection."""

        if request.type == "system.handshake":
            await self._handle_handshake(client, request)
            return
        if not client.authenticated:
            await self._emit_system_error(
                client,
                code="permission_denied",
                message="system.handshake must complete before other requests",
                recoverable=True,
                details={},
                request_id=request.request_id,
            )
            return
        if (
            request.context is None
            or request.context.client_id != client.client_id
            or request.context.session_id != client.session_id
        ):
            await self._emit_system_error(
                client,
                code="permission_denied",
                message="Request context does not belong to this authenticated connection",
                recoverable=True,
                details={},
                request_id=request.request_id,
            )
            return
        client.workspace_id = request.context.workspace_id
        capability = required_capability(request.type)
        if (
            capability is None
            or client.principal is None
            or not client.principal.allows(capability)
        ):
            await self._emit_system_error(
                client,
                code="permission_denied",
                message=f"Authenticated client cannot submit {request.type}",
                recoverable=False,
                details={"required_capability": capability},
                request_id=request.request_id,
            )
            return

        try:
            reservation = self.store.reserve(request, principal=client.principal.key)
        except RequestIdConflict:
            await self._emit_system_error(
                client,
                code="request_id_conflict",
                message="request_id was already used for a different semantic request",
                recoverable=False,
                details={},
                request_id=request.request_id,
            )
            return

        if not reservation.created:
            if reservation.record.principal != client.principal.key:
                await self._emit_system_error(
                    client,
                    code="permission_denied",
                    message="Request replay is not available to this principal",
                    recoverable=False,
                    details={},
                    request_id=request.request_id,
                )
                return
            if reservation.record.terminal_event is not None:
                await self.event_bus.emit_local(client, reservation.record.terminal_event)
            else:
                await self.event_bus.emit_local(
                    client,
                    self._accepted_event(client, request, state="in_progress"),
                )
            return

        await self.event_bus.emit_local(
            client, self._accepted_event(client, request, state="accepted")
        )
        self.store.mark_in_progress(request.request_id)
        try:
            await self._dispatch(client, request)
        except CrashAfterTerminalCommit:
            raise
        except WorkspaceRevisionConflict as exc:
            await self._fail_request(
                client,
                request,
                code="workspace_revision_conflict",
                message="Workspace changed since this request was composed",
                recoverable=True,
                details={"current_workspace_revision": exc.current_revision},
            )
        except TaskRevisionConflict as exc:
            await self._fail_request(
                client,
                request,
                code="task_revision_conflict",
                message="Research task changed since this request was composed",
                recoverable=True,
                details={"current_task_revision": exc.current_revision},
            )
        except TaskCheckpointIncompatible as exc:
            await self._fail_request(
                client,
                request,
                code="task_checkpoint_incompatible",
                message="The saved task conversation cannot be restored safely",
                recoverable=True,
                details={"reason": str(exc)},
            )
        except TaskNotFound:
            await self._fail_request(
                client,
                request,
                code="task_not_found",
                message="Requested research task was not found",
                recoverable=True,
                details={},
            )
        except ArtifactVersionConflict as exc:
            await self._fail_request(
                client,
                request,
                code="artifact_version_conflict",
                message="Artifact version or dependency relation conflicts with durable workspace state",
                recoverable=True,
                details={"reason": str(exc)},
            )
        except ArtifactFileError as exc:
            await self._fail_request(
                client,
                request,
                code="artifact_commit_failed",
                message="Artifact files could not be staged or finalized safely",
                recoverable=True,
                details={"reason": str(exc)},
            )
        except RequestNotFound:
            await self._fail_request(
                client,
                request,
                code="artifact_not_found",
                message="Requested durable record was not found",
                recoverable=True,
                details={},
            )
        except ValidationError as exc:
            await self._fail_request(
                client,
                request,
                code="invalid_artifact",
                message="Artifact content does not satisfy its declared schema",
                recoverable=True,
                details={
                    "validation": exc.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    )
                },
            )
        except RequestStoreError as exc:
            await self._fail_request(
                client,
                request,
                code="store_error",
                message="Could not persist the request result",
                recoverable=True,
                details={"reason": str(exc)},
            )
        except Exception as exc:  # pragma: no cover - defensive router boundary
            await self._fail_request(
                client,
                request,
                code="store_error",
                message="Backend request handler failed",
                recoverable=True,
                details={"reason": str(exc)},
            )

    def set_request_agent_budget(self, request_id: str, budget: OceanAgentBudget) -> None:
        """Lower one not-yet-submitted agent request's backend-owned limits.

        This is intentionally an internal router control rather than a Protocol
        v2 field: a client cannot raise its own limits. The frozen evaluator
        uses it to make a bounded continuation consume only the originating
        task's remaining budget.
        """

        if request_id in self._agent_tasks or self.store.get_request(request_id) is not None:
            raise RequestStoreError("Agent request budget must be set before request submission")
        self._agent_request_budgets[request_id] = budget

    async def _handle_handshake(
        self,
        client: BackendClient,
        request: SystemHandshakeRequest,
    ) -> None:
        if client.authenticated:
            await self._emit_system_error(
                client,
                code="invalid_request",
                message="This connection has already completed system.handshake",
                recoverable=False,
                details={},
                request_id=request.request_id,
            )
            return
        if 2 not in request.payload.supported_protocol_versions:
            await self._emit_system_error(
                client,
                code="unsupported_protocol",
                message="Client does not support Protocol v2",
                recoverable=False,
                details={"supported_by_backend": [2]},
                request_id=request.request_id,
            )
            return
        if request.payload.client_kind != client.expected_client_kind:
            await self._emit_system_error(
                client,
                code="permission_denied",
                message="Client kind does not match this transport",
                recoverable=False,
                details={},
                request_id=request.request_id,
            )
            return
        if not await client.validate_handshake(
            request.payload.bootstrap_token,
            request.payload.client_capability,
        ):
            await self._emit_system_error(
                client,
                code="permission_denied",
                message="Desktop bootstrap token is missing, expired, or already used",
                recoverable=False,
                details={},
                request_id=request.request_id,
            )
            return

        client.client_id = (
            client.resume_client_id or f"client_{client.expected_client_kind}_{uuid4().hex}"
        )
        client.session_id = client.resume_session_id or f"ses_{uuid4().hex}"
        client.workspace_id = client.resume_workspace_id
        client.principal = client.resume_principal or principal_for_client_kind(
            client.expected_client_kind
        )
        event = SystemReadyEvent(
            **self._event_fields(
                client,
                request_id=request.request_id,
                workspace_id=request.context.workspace_id if request.context else None,
            ),
            type="system.ready",
            payload=SystemReadyPayload(
                backend_version=__version__,
                backend_schema=DESKTOP_BACKEND_SCHEMA,
                client_id=client.client_id,
                session_id=client.session_id,
                client_kind=client.expected_client_kind,
                capabilities=sorted(client.principal.capabilities),
                runtime_capabilities=desktop_runtime_capabilities(
                    skill_capabilities=(LITERATURE_CAPABILITY,)
                ),
                client_capability=client.client_capability,
            ),
        )
        await self.event_bus.emit_local(client, event)

    async def _dispatch(self, client: BackendClient, request: RequestEnvelope) -> None:
        if isinstance(request, InteractionRespondRequest):
            await self._interaction_respond(client, request)
            return
        if isinstance(request, TaskCreateRequest):
            await self._task_create(client, request)
            return
        if isinstance(request, TaskListRequest):
            await self._task_list(client, request)
            return
        if isinstance(request, TaskGetRequest):
            await self._task_get_or_open(client, request)
            return
        if isinstance(request, TaskRenameRequest):
            await self._task_rename(client, request)
            return
        if isinstance(request, TaskArchiveRequest):
            await self._task_archive_or_reopen(client, request)
            return
        if isinstance(request, TaskDeleteRequest):
            await self._task_delete(client, request)
            return
        if isinstance(request, TaskSnapshotGetRequest):
            await self._task_snapshot(client, request)
            return
        if isinstance(request, TaskAgentTranscriptGetRequest):
            await self._task_agent_transcript(client, request)
            return
        if isinstance(request, TaskOutputListRequest):
            await self._task_output_list(client, request)
            return
        if isinstance(request, SessionOpenRequest):
            await self._session_open(client, request)
            return
        if isinstance(request, SessionSubmitRequest):
            await self._session_submit(client, request)
            return
        if isinstance(request, WorkspaceOpenRequest):
            await self._workspace_open(client, request)
            return
        if isinstance(request, WorkspaceSnapshotGetRequest):
            await self._workspace_snapshot(client, request)
            return
        if isinstance(request, ArtifactListRequest):
            await self._artifact_list(client, request)
            return
        if isinstance(request, ArtifactGetRequest):
            await self._artifact_get(client, request)
            return
        if isinstance(request, ArtifactResourceGrantRequest):
            await self._artifact_resource_grant(client, request)
            return
        if isinstance(request, TaskResultResourceGrantRequest):
            await self._task_result_resource_grant(client, request)
            return
        if isinstance(request, TaskResultInteractiveViewGetRequest):
            await self._task_result_interactive_view_get(client, request)
            return
        if isinstance(request, ArtifactVersionsRequest):
            await self._artifact_versions(client, request)
            return
        if isinstance(request, ArtifactCreateRequest):
            await self._artifact_create(client, request)
            return
        if isinstance(request, DatasetImportRequest):
            await self._dataset_import(client, request)
            return
        if isinstance(request, PaperImportRequest):
            await self._paper_import(client, request)
            return
        if isinstance(request, PaperRegisterRequest):
            await self._paper_register(client, request)
            return
        if isinstance(request, HypothesisActivateRequest):
            await self._hypothesis_activate(client, request)
            return
        if isinstance(request, PortableExportCreateRequest):
            await self._portable_export_create(client, request)
            return
        if isinstance(request, DisclosurePolicyGetRequest):
            await self._disclosure_policy_get(client, request)
            return
        if isinstance(request, DisclosurePolicySetRequest):
            await self._disclosure_policy_set(client, request)
            return
        if isinstance(request, RequestStatusGetRequest):
            await self._request_status(client, request)
            return
        if isinstance(request, RequestCancelRequest):
            await self._request_cancel(client, request)
            return
        if isinstance(request, SystemShutdownRequest):
            await self._system_shutdown(client, request)
            return
        raise RequestStoreError(f"No handler for request type {request.type}")

    async def _artifact_list(self, client: BackendClient, request: ArtifactListRequest) -> None:
        workspace_id = self._workspace_id(request)
        artifacts = self.store.list_artifact_summaries(
            workspace_id,
            artifact_type=request.payload.artifact_type,
            limit=request.payload.limit,
        )
        terminal = self._completed_event(client, request, result={"artifacts": artifacts})
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _artifact_get(self, client: BackendClient, request: ArtifactGetRequest) -> None:
        workspace_id = self._workspace_id(request)
        artifact = self.store.get_artifact(workspace_id=workspace_id, ref=request.payload.ref)
        projection = self.store.get_projection(workspace_id=workspace_id, ref=request.payload.ref)
        if artifact is None or projection is None:
            raise RequestNotFound(request.payload.ref.key)
        terminal = self._completed_event(
            client,
            request,
            result={
                "artifact": artifact.model_dump(mode="json"),
                "projection": projection.model_dump(mode="json"),
                "links": {
                    "outgoing": self.store.list_artifact_links(
                        workspace_id=workspace_id,
                        ref=request.payload.ref,
                        direction="outgoing",
                    ),
                    "incoming": self.store.list_artifact_links(
                        workspace_id=workspace_id,
                        ref=request.payload.ref,
                        direction="incoming",
                    ),
                },
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _artifact_resource_grant(
        self, client: BackendClient, request: ArtifactResourceGrantRequest
    ) -> None:
        """Approve one bounded immutable result file for the Desktop viewer path."""

        workspace_id = self._workspace_id(request)
        artifact = self.store.get_artifact(
            workspace_id=workspace_id, ref=request.payload.artifact_ref
        )
        if artifact is None:
            raise RequestNotFound(request.payload.artifact_ref.key)
        file = next(
            (
                item
                for item in artifact.files
                if item.uri.rsplit("/", maxsplit=1)[-1] == request.payload.file_name
            ),
            None,
        )
        if file is None:
            raise RequestStoreError(
                "Requested viewer file is not declared by the artifact manifest"
            )
        policy = {
            "paper_viewer": {
                "artifact_type": "paper",
                "mime_type": "application/pdf",
                "maximum_bytes": 25 * 1024 * 1024,
            },
            "report_viewer": {
                "artifact_type": "report",
                "mime_type": "text/markdown",
                "maximum_bytes": 2 * 1024 * 1024,
            },
            "report_notebook": {
                "artifact_type": "report",
                "mime_type": "application/x-ipynb+json",
                "maximum_bytes": 5 * 1024 * 1024,
            },
            "report_code": {
                "artifact_type": "report",
                "mime_type": "text/x-python",
                "maximum_bytes": 2 * 1024 * 1024,
            },
            "report_environment": {
                "artifact_type": "report",
                "mime_type": "text/plain",
                "maximum_bytes": 512 * 1024,
            },
            "report_inputs": {
                "artifact_type": "report",
                "mime_type": "application/json",
                "maximum_bytes": 2 * 1024 * 1024,
            },
            "report_reproducibility": {
                "artifact_type": "report",
                "mime_type": "application/json",
                "maximum_bytes": 2 * 1024 * 1024,
            },
            "report_image": {
                "artifact_type": "report",
                "mime_type": "image/png",
                "maximum_bytes": 8 * 1024 * 1024,
            },
            "interactive_view_data": {
                "artifact_type": "interactive_view",
                "mime_type": "application/json",
                "maximum_bytes": 8 * 1024 * 1024,
            },
            "interactive_view_preview": {
                "artifact_type": "interactive_view",
                "mime_type": "image/png",
                "maximum_bytes": 8 * 1024 * 1024,
            },
        }[request.payload.purpose]
        if (
            artifact.artifact_type != policy["artifact_type"]
            or file.mime_type != policy["mime_type"]
        ):
            raise RequestStoreError("Requested file is not permitted for this viewer purpose")
        if file.size_bytes > policy["maximum_bytes"]:
            raise RequestStoreError("Requested viewer file exceeds the bounded resource limit")
        terminal = self._completed_event(
            client,
            request,
            result={
                "resource_token": f"res_{uuid4().hex}",
                "resource_uri": file.uri,
                "artifact_ref": artifact.ref.model_dump(mode="json"),
                "file_name": request.payload.file_name,
                "mime_type": file.mime_type,
                "size_bytes": file.size_bytes,
                "sha256": file.sha256,
                "purpose": request.payload.purpose,
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _artifact_versions(
        self, client: BackendClient, request: ArtifactVersionsRequest
    ) -> None:
        """Return a version timeline without loading mutable local files into the protocol."""

        workspace_id = self._workspace_id(request)
        versions = self.store.list_artifact_versions(
            workspace_id=workspace_id,
            artifact_id=request.payload.artifact_id,
        )
        terminal = self._completed_event(
            client,
            request,
            result={"artifact_id": request.payload.artifact_id, "versions": versions},
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_result_resource_grant(
        self, client: BackendClient, request: TaskResultResourceGrantRequest
    ) -> None:
        """Grant one declared file directly from a result owned by this task."""

        if self.task_results is None:
            raise RequestStoreError("Task result storage is unavailable")
        ref = request.payload.result_ref
        if request.context.task_id != ref.task_id:
            raise RequestStoreError("Task result does not belong to the current task")
        self._task_in_workspace(ref.task_id, request)
        try:
            result = self.task_results.get(ref)
            path = self.task_results.file_path(ref=ref, relative_path=request.payload.file_name)
        except TaskResultError as exc:
            raise RequestNotFound(ref.key) from exc
        if result.workspace_id != self._workspace_id(request):
            raise RequestStoreError("Task result does not belong to the current workspace")
        declared = next(
            (item for item in result.files if item.path == request.payload.file_name),
            None,
        )
        if declared is None:
            raise RequestStoreError("Requested file is not declared by the task result")
        workspace_files = result.content.get("workspace_files")
        mutable_workspace_file = (
            isinstance(workspace_files, dict)
            and request.payload.file_name in workspace_files
        )
        current_size = path.stat().st_size if mutable_workspace_file else declared.size
        if mutable_workspace_file:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            current_sha256 = digest.hexdigest()
        else:
            current_sha256 = declared.sha256
        # The manifest declares which file may be opened. Mutable supplementary
        # files use their current size/hash because the user is expected to edit
        # them; accepted scientific payloads retain the manifest checksum.
        if current_size > 25 * 1024 * 1024:
            raise RequestStoreError("Requested task result file exceeds the bounded resource limit")
        terminal = self._completed_event(
            client,
            request,
            result={
                "resource_token": f"res_{uuid4().hex}",
                "resource_uri": path.as_uri(),
                "result_ref": ref.model_dump(mode="json"),
                "file_name": request.payload.file_name,
                "mime_type": declared.mime_type,
                "size_bytes": current_size,
                "sha256": current_sha256,
                "purpose": request.payload.purpose,
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_result_interactive_view_get(
        self, client: BackendClient, request: TaskResultInteractiveViewGetRequest
    ) -> None:
        """Grant a cached hydrated view without putting arrays in an event frame."""

        if self.task_results is None:
            raise RequestStoreError("Task result storage is unavailable")
        ref = request.payload.result_ref
        if request.context.task_id != ref.task_id:
            raise RequestStoreError("Task result does not belong to the current task")
        self._task_in_workspace(ref.task_id, request)
        try:
            result = self.task_results.get(ref)
        except TaskResultError as exc:
            raise RequestNotFound(ref.key) from exc
        if result.workspace_id != self._workspace_id(request) or result.kind != "interactive_view":
            raise RequestStoreError("Interactive task result is unavailable")
        data_file = result.content.get("data_file")
        if not isinstance(data_file, str):
            raise RequestStoreError("Interactive result manifest is missing")
        try:
            cache_path, raw = interactive_view_cache(
                record=result,
                results=self.task_results,
            )
        except (OSError, ValueError, TaskResultError, ExpertDeliverableError) as exc:
            raise RequestStoreError(str(exc) or "Interactive result could not be loaded") from exc
        terminal = self._completed_event(
            client,
            request,
            result={
                "resource_token": f"res_{uuid4().hex}",
                "resource_uri": cache_path.as_uri(),
                "result_ref": ref.model_dump(mode="json"),
                "file_name": cache_path.name,
                "mime_type": "application/json",
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "purpose": "interactive_view_data",
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _artifact_create(self, client: BackendClient, request: ArtifactCreateRequest) -> None:
        if self.artifact_service is None:
            raise RequestStoreError("Artifact service is not configured")
        if request.payload.artifact_type == "paper":
            raise RequestStoreError(
                "Use paper.import for a local PDF snapshot or paper.register for citation metadata"
            )
        if request.payload.artifact_type == "report":
            raise RequestStoreError(
                "Reports are published by the Expert that performed the computation"
            )
        workspace_id = self._workspace_id(request)
        artifact_id = request.payload.artifact_id or self.artifact_service.new_artifact_id(
            request.payload.artifact_type
        )
        draft = ArtifactVersionDraft(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            artifact_type=request.payload.artifact_type,
            title=request.payload.title,
            summary=request.payload.summary,
            created_by="user",
            content=request.payload.content,
            intrinsic_links=request.payload.intrinsic_links,
            provenance=request.payload.provenance,
            supersedes_version=request.payload.supersedes_version,
        )

        commit = self.artifact_service.commit(
            draft,
            request_id=request.request_id,
            task_id=self._task_id(request),
            task_relation="source" if self._task_id(request) is not None else None,
            expected_workspace_revision=request.expected_workspace_revision,
            files=None,
            event_factory=self._artifact_event_factory(
                client=client,
                request=request,
                workspace_id=workspace_id,
            ),
        )
        if commit.terminal_event is None:
            raise RequestStoreError("Request-backed artifact commit has no terminal event")
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _dataset_import(self, client: BackendClient, request: DatasetImportRequest) -> None:
        """Attach one local dataset without copying its bytes by default."""

        if self.artifact_service is None:
            raise RequestStoreError("Artifact service is not configured")
        workspace_id = self._workspace_id(request)
        workspace = self.store.workspace_snapshot(workspace_id)
        if not workspace.path:
            raise RequestStoreError("Open a project-local workspace before importing a dataset")
        source, relative_path, dataset_format, source_kind, source_scope = (
            self._local_dataset_source(
                workspace_path=workspace.path,
                relative_path=request.payload.relative_path,
                local_path=request.payload.local_path,
                allow_external=(
                    client.transport == "stdio" and client.expected_client_kind == "desktop"
                ),
            )
        )
        if request.payload.desktop_staged:
            if relative_path is None:
                raise RequestStoreError("Desktop-staged imports require a workspace path")
            self._assert_desktop_staged_dataset_path(relative_path)
        snapshot = request.payload.materialization_level == "materialized_snapshot"
        if snapshot:
            files, store_root = self._dataset_snapshot_files(
                source=source,
                source_kind=source_kind,
            )
        else:
            files, store_root = {}, None
        source_stat = source.stat(follow_symlinks=False)
        registered_fingerprint = {
            "size_bytes": source_stat.st_size,
            "modified_ns": source_stat.st_mtime_ns,
        }
        if (
            not snapshot
            and not request.payload.desktop_staged
            and request.payload.artifact_id is None
        ):
            source_locator = relative_path if relative_path is not None else str(source)
            existing = self.store.find_local_dataset_reference(
                workspace_id=workspace_id,
                source_scope=source_scope,
                source_locator=source_locator,
                registered_fingerprint=registered_fingerprint,
            )
            if existing is not None:
                task_id = self._task_id(request)
                if task_id is not None and not any(
                    record.artifact.ref == existing.ref and "source" in record.relations
                    for record in self.store.list_task_artifacts(task_id=task_id)
                ):
                    self.store.link_task_artifact(
                        task_id=task_id,
                        ref=existing.ref,
                        relation="source",
                        origin_request_id=request.request_id,
                    )
                projection = self.store.get_projection(
                    workspace_id=workspace_id,
                    ref=existing.ref,
                )
                if projection is None:
                    raise RequestStoreError("Reusable dataset reference has no projection")
                summary = ArtifactSummaryPayload(
                    ref=existing.ref,
                    artifact_type=existing.artifact_type,
                    title=existing.title,
                    summary=existing.summary,
                    projection=projection,
                )
                terminal = self._completed_event(
                    client,
                    request,
                    result={
                        "artifact": summary.model_dump(mode="json"),
                        "manifest_uri": existing.manifest_uri,
                        "reused": True,
                    },
                    workspace_revision=workspace.revision,
                )
                self.store.commit_terminal(request.request_id, terminal)
                await self._broadcast_committed_terminal(client, terminal)
                return
        artifact_id = request.payload.artifact_id or self.artifact_service.new_artifact_id(
            "dataset"
        )
        draft = ArtifactVersionDraft(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            artifact_type="dataset",
            schema_version="ocean-dataset/v2" if not snapshot else "ocean-dataset/v1",
            title=request.payload.title or source.stem,
            summary=(
                f"Local read-only {dataset_format.upper()} reference"
                if not snapshot
                else f"Materialized local {dataset_format.upper()} snapshot"
            ),
            created_by="user",
            content={
                "schema_version": "ocean-dataset/v2" if not snapshot else "ocean-dataset/v1",
                "materialization_level": request.payload.materialization_level,
                **(
                    {"source_relative_path": relative_path}
                    if relative_path is not None
                    else {"source_path": str(source)}
                ),
                "source_scope": source_scope,
                "format": dataset_format,
                "source_kind": source_kind,
                "registered_fingerprint": registered_fingerprint,
                **({"store_root": store_root} if store_root is not None else {}),
            },
            provenance={
                "schema_version": "ocean-local-dataset-import/v2",
                "source_scope": source_scope,
                **(
                    {"source_relative_path": relative_path}
                    if relative_path is not None
                    else {"source_path": str(source)}
                ),
                "materialization_acknowledged": request.payload.materialization_acknowledged,
            },
        )
        commit = self.artifact_service.commit(
            draft,
            request_id=request.request_id,
            task_id=self._task_id(request),
            task_relation="source" if self._task_id(request) is not None else None,
            expected_workspace_revision=request.expected_workspace_revision,
            files=files,
            event_factory=self._artifact_event_factory(
                client=client,
                request=request,
                workspace_id=workspace_id,
            ),
        )
        if commit.terminal_event is None:
            raise RequestStoreError("Request-backed dataset import has no terminal event")
        if request.payload.desktop_staged:
            assert relative_path is not None
            self._discard_desktop_staged_dataset(workspace.path, relative_path)
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _paper_import(self, client: BackendClient, request: PaperImportRequest) -> None:
        """Materialize a local PDF without parsing or disclosing its untrusted document text."""

        if self.artifact_service is None:
            raise RequestStoreError("Artifact service is not configured")
        workspace_id = self._workspace_id(request)
        workspace = self.store.workspace_snapshot(workspace_id)
        if not workspace.path:
            raise RequestStoreError("Open a project-local workspace before importing a paper")
        source, relative_path = self._workspace_pdf_source(
            workspace_path=workspace.path,
            requested_path=request.payload.relative_path,
        )
        artifact_id = request.payload.artifact_id or self.artifact_service.new_artifact_id("paper")
        content = PaperContent(
            citation=request.payload.citation,
            source_kind="local_pdf",
            materialization_level="materialized_snapshot",
        )
        draft = ArtifactVersionDraft(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            artifact_type="paper",
            title=content.citation.title,
            summary="User-imported local PDF; document text remains opaque to the model",
            created_by="import",
            content=content.model_dump(mode="json"),
            provenance={
                "schema_version": "ocean-local-paper-import/v1",
                "source_relative_path": relative_path,
                "materialization_acknowledged": request.payload.materialization_acknowledged,
                "document_text_exposed_to_model": False,
            },
        )
        commit = self.artifact_service.commit(
            draft,
            request_id=request.request_id,
            task_id=self._task_id(request),
            task_relation="source" if self._task_id(request) is not None else None,
            expected_workspace_revision=request.expected_workspace_revision,
            files={source.name: source},
            event_factory=self._artifact_event_factory(
                client=client,
                request=request,
                workspace_id=workspace_id,
            ),
        )
        if commit.terminal_event is None:
            raise RequestStoreError("Request-backed paper import has no terminal event")
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _paper_register(self, client: BackendClient, request: PaperRegisterRequest) -> None:
        """Persist user-supplied bibliographic metadata without pretending to own paper bytes."""

        if self.artifact_service is None:
            raise RequestStoreError("Artifact service is not configured")
        workspace_id = self._workspace_id(request)
        artifact_id = request.payload.artifact_id or self.artifact_service.new_artifact_id("paper")
        content = PaperContent(
            citation=request.payload.citation,
            source_kind="metadata_only",
            materialization_level="metadata_only",
        )
        draft = ArtifactVersionDraft(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            artifact_type="paper",
            title=content.citation.title,
            summary="User-registered citation metadata; no document text or PDF is stored",
            created_by="user",
            content=content.model_dump(mode="json"),
            provenance={
                "schema_version": "ocean-paper-metadata-registration/v1",
                "document_text_exposed_to_model": False,
            },
        )
        commit = self.artifact_service.commit(
            draft,
            request_id=request.request_id,
            task_id=self._task_id(request),
            task_relation="source" if self._task_id(request) is not None else None,
            expected_workspace_revision=request.expected_workspace_revision,
            files=None,
            event_factory=self._artifact_event_factory(
                client=client,
                request=request,
                workspace_id=workspace_id,
            ),
        )
        if commit.terminal_event is None:
            raise RequestStoreError("Request-backed paper registration has no terminal event")
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _hypothesis_activate(
        self,
        client: BackendClient,
        request: HypothesisActivateRequest,
    ) -> None:
        """Move the active-hypothesis pointer only after an explicit Desktop request."""

        workspace_id = self._workspace_id(request)
        artifact = self.store.get_artifact(
            workspace_id=workspace_id, ref=request.payload.hypothesis_ref
        )
        if artifact is None:
            raise RequestNotFound(request.payload.hypothesis_ref.key)
        if artifact.artifact_type != "hypothesis":
            raise RequestStoreError("hypothesis.activate requires a HypothesisArtifact reference")

        def event_factory(snapshot, previous_revision, workspace_revision):
            changed = WorkspaceChangedEvent(
                **self._event_fields(
                    client, request_id=request.request_id, workspace_id=workspace_id
                ),
                type="workspace.changed",
                payload=WorkspaceChangedPayload(
                    previous_revision=previous_revision,
                    workspace_revision=workspace_revision,
                    change="updated",
                ),
            )
            terminal = self._completed_event(
                client,
                request,
                result={
                    "active_hypothesis": request.payload.hypothesis_ref.model_dump(mode="json"),
                    "workspace": snapshot.as_payload(),
                },
                workspace_revision=workspace_revision,
            )
            return changed, terminal

        commit = self.store.commit_active_ref(
            request_id=request.request_id,
            workspace_id=workspace_id,
            slot="active_hypothesis",
            ref=request.payload.hypothesis_ref,
            expected_workspace_revision=request.expected_workspace_revision,
            event_factory=event_factory,
        )
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _portable_export_create(
        self, client: BackendClient, request: PortableExportCreateRequest
    ) -> None:
        """Materialize an audited bundle without exposing renderer-visible local paths."""

        if self.portable_export_service is None:
            raise RequestStoreError("Portable export service is not configured")
        workspace_id = self._workspace_id(request)
        try:
            exported = self.portable_export_service.export(
                workspace_id=workspace_id,
                refs=request.payload.artifact_refs,
            )
        except PortableExportError as exc:
            await self._fail_request(
                client,
                request,
                code="tool_error",
                message="Portable export could not be created",
                recoverable=True,
                details={"reason": str(exc)},
            )
            return
        terminal = self._completed_event(
            client,
            request,
            result={
                "export_id": exported.bundle_directory.name,
                "artifact_refs": [ref.model_dump(mode="json") for ref in exported.refs],
                "format": "ocean-portable-export/v1",
                "raw_logs_included": False,
                "local_paper_pdfs_included": False,
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _disclosure_policy_get(
        self,
        client: BackendClient,
        request: DisclosurePolicyGetRequest,
    ) -> None:
        """Return the current policy summary without disclosing any protected content."""

        summary = self.store.get_disclosure_policy_summary(self._workspace_id(request))
        policy = DisclosurePolicySummary.model_validate(summary) if summary is not None else None
        terminal = self._completed_event(
            client,
            request,
            result={
                "policy": policy.model_dump(mode="json") if policy is not None else None,
                "versions": self.store.list_disclosure_policy_versions(
                    workspace_id=self._workspace_id(request)
                ),
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _disclosure_policy_set(
        self,
        client: BackendClient,
        request: DisclosurePolicySetRequest,
    ) -> None:
        """Commit a fully specified user confirmation as a versioned workspace policy."""

        workspace_id = self._workspace_id(request)
        policy = ModelDataDisclosurePolicy(
            provider_id=request.payload.provider_id,
            policy_version=1,
            metadata=request.payload.metadata,
            aggregate_statistics=request.payload.aggregate_statistics,
            raw_bounded_sample=request.payload.raw_bounded_sample,
            document_text=request.payload.document_text,
            diagnostic_excerpt=request.payload.diagnostic_excerpt,
        )

        def event_factory(summary, previous_revision, workspace_revision, event_id):
            visible_policy = DisclosurePolicySummary.model_validate(summary)
            domain_event = DisclosurePolicyUpdatedEvent(
                **self._event_fields(
                    client,
                    request_id=request.request_id,
                    workspace_id=workspace_id,
                    event_id=event_id,
                ),
                type="disclosure.policy.updated",
                payload=DisclosurePolicyUpdatedPayload(
                    policy=visible_policy,
                    previous_revision=previous_revision,
                    workspace_revision=workspace_revision,
                ),
            )
            terminal_event = self._completed_event(
                client,
                request,
                result={"policy": visible_policy.model_dump(mode="json"), "confirmed": True},
                workspace_revision=workspace_revision,
            )
            return domain_event, terminal_event

        commit = self.store.commit_disclosure_policy(
            request_id=request.request_id,
            workspace_id=workspace_id,
            provider_id=policy.provider_id,
            policy=policy.as_storage_payload(),
            expected_workspace_revision=request.expected_workspace_revision,
            event_factory=event_factory,
        )
        await self._run_terminal_commit_hook(commit.terminal_event)
        await self.event_bus.emit_workspace(commit.domain_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    @staticmethod
    def _workspace_dataset_source(
        *, workspace_path: str, requested_path: str
    ) -> tuple[Path, str, str, Literal["file", "directory"]]:
        """Resolve one locally authorized dataset without imposing a format whitelist."""

        if not is_safe_cross_platform_relative_path(requested_path):
            raise RequestStoreError("Dataset import path must be a safe workspace-relative path")
        relative = PurePosixPath(requested_path)
        try:
            root = Path(workspace_path).resolve(strict=True)
        except OSError as exc:
            raise RequestStoreError("Workspace root is unavailable for dataset import") from exc
        if not root.is_dir():
            raise RequestStoreError("Workspace root is not a directory")
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RequestStoreError("Dataset import does not permit symlinked paths")
        try:
            source = candidate.resolve(strict=True)
            source.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RequestStoreError(
                "Dataset import path is unavailable or escapes the workspace"
            ) from exc
        if source.is_file() and not source.is_symlink():
            return source, relative.as_posix(), _local_dataset_format(source), "file"
        if source.is_dir() and not source.is_symlink():
            return source, relative.as_posix(), _local_dataset_format(source), "directory"
        raise RequestStoreError("Dataset import source must be a regular file or directory")

    @staticmethod
    def _local_dataset_source(
        *,
        workspace_path: str,
        relative_path: str | None,
        local_path: str | None,
        allow_external: bool,
    ) -> tuple[
        Path,
        str | None,
        str,
        Literal["file", "directory"],
        Literal["workspace", "desktop_authorized"],
    ]:
        """Resolve a user-selected local source without walking or copying it."""

        if local_path is None:
            if relative_path is None:
                raise RequestStoreError("Dataset import has no local source path")
            source, normalized, dataset_format, source_kind = (
                OceanRequestRouter._workspace_dataset_source(
                    workspace_path=workspace_path,
                    requested_path=relative_path,
                )
            )
            return source, normalized, dataset_format, source_kind, "workspace"
        if relative_path is not None:
            raise RequestStoreError("Dataset import must use one source path")
        if not allow_external:
            raise RequestStoreError(
                "External local paths may only be attached by the local Desktop client"
            )
        candidate = Path(local_path).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink():
            raise RequestStoreError("Desktop dataset path must be an absolute non-link path")
        try:
            source = candidate.resolve(strict=True)
        except OSError as exc:
            raise RequestStoreError("Desktop dataset path is unavailable") from exc
        if source.is_file() and not source.is_symlink():
            source_kind: Literal["file", "directory"] = "file"
        elif source.is_dir() and not source.is_symlink():
            source_kind = "directory"
        else:
            raise RequestStoreError("Desktop dataset source must be a regular file or directory")
        return (
            source,
            None,
            _local_dataset_format(source),
            source_kind,
            "desktop_authorized",
        )

    @staticmethod
    def _dataset_snapshot_files(
        *, source: Path, source_kind: Literal["file", "directory"]
    ) -> tuple[dict[str, Path], str | None]:
        if source_kind == "file":
            return {f"data/{source.name}": source}, None
        files: dict[str, Path] = {}
        stack = [source]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise RequestStoreError("Zarr store could not be enumerated safely") from exc
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(source).as_posix()
                is_junction = getattr(path, "is_junction", None)
                file_attributes = getattr(path.lstat(), "st_file_attributes", 0)
                reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if (
                    path.is_symlink()
                    or (callable(is_junction) and is_junction())
                    or bool(reparse_flag and file_attributes & reparse_flag)
                ):
                    raise RequestStoreError(
                        f"Dataset directory link-like entry is forbidden: {relative}"
                    )
                mode = path.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    stack.append(path)
                elif stat.S_ISREG(mode):
                    files[f"data/{source.name}/{relative}"] = path
                else:
                    raise RequestStoreError(
                        f"Dataset directory special file is forbidden: {relative}"
                    )
        if not files:
            raise RequestStoreError("Dataset directory contains no regular files")
        return files, f"data/{source.name}"

    @staticmethod
    def _assert_desktop_staged_dataset_path(relative_path: str) -> None:
        parts = PurePosixPath(relative_path).parts
        if parts[:3] == (".oceanmind", "staging", "desktop-imports"):
            import_index = 3
        else:
            raise RequestStoreError("Desktop-staged dataset path is invalid")
        if (
            len(parts) <= import_index + 1
            or not re.fullmatch(r"desktop_import_[a-f0-9]{32}", parts[import_index])
            or parts[import_index + 1] != "source"
        ):
            raise RequestStoreError("Desktop-staged dataset path is invalid")

    @staticmethod
    def _discard_desktop_staged_dataset(workspace_path: str, relative_path: str) -> None:
        parts = PurePosixPath(relative_path).parts
        if parts[:3] == (".oceanmind", "staging", "desktop-imports"):
            import_index = 3
        else:
            return
        if len(parts) <= import_index or not re.fullmatch(
            r"desktop_import_[a-f0-9]{32}", parts[import_index]
        ):
            return
        container = Path(workspace_path).joinpath(*parts[: import_index + 1])
        shutil.rmtree(container, ignore_errors=True)

    @staticmethod
    def _workspace_pdf_source(*, workspace_path: str, requested_path: str) -> tuple[Path, str]:
        """Resolve one local PDF without letting paper import become a file API."""

        if not is_safe_cross_platform_relative_path(requested_path):
            raise RequestStoreError("Paper import path must be a safe workspace-relative path")
        relative = PurePosixPath(requested_path)
        try:
            root = Path(workspace_path).resolve(strict=True)
        except OSError as exc:
            raise RequestStoreError("Workspace root is unavailable for paper import") from exc
        if not root.is_dir():
            raise RequestStoreError("Workspace root is not a directory")
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RequestStoreError("Paper import does not permit symlinked paths")
        try:
            source = candidate.resolve(strict=True)
            source.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RequestStoreError(
                "Paper import path is unavailable or escapes the workspace"
            ) from exc
        if not source.is_file() or source.is_symlink():
            raise RequestStoreError("Paper import source must be a regular file")
        if source.suffix.lower() != ".pdf":
            raise RequestStoreError("Paper import accepts PDF files only")
        return source, relative.as_posix()

    def _artifact_event_factory(
        self,
        *,
        client: BackendClient,
        request: RequestEnvelope,
        workspace_id: str,
    ):
        def event_factory(
            artifact,
            projection,
            _previous_revision: int,
            workspace_revision: int,
            event_id: str,
        ):
            summary = ArtifactSummaryPayload(
                ref=artifact.ref,
                artifact_type=artifact.artifact_type,
                title=artifact.title,
                summary=artifact.summary,
                projection=projection,
            )
            event_type = (
                ArtifactCreatedEvent if artifact.ref.version == 1 else ArtifactVersionCreatedEvent
            )
            domain_event = event_type(
                **self._event_fields(
                    client,
                    request_id=request.request_id,
                    workspace_id=workspace_id,
                    event_id=event_id,
                ),
                type="artifact.created"
                if artifact.ref.version == 1
                else "artifact.version.created",
                payload=ArtifactCreatedPayload(
                    artifact=summary,
                    manifest_uri=artifact.manifest_uri,
                    workspace_revision=workspace_revision,
                ),
            )
            terminal_event = self._completed_event(
                client,
                request,
                result={
                    "artifact": summary.model_dump(mode="json"),
                    "manifest_uri": artifact.manifest_uri,
                },
                workspace_revision=workspace_revision,
            )
            return domain_event, terminal_event

        return event_factory

    async def _session_submit(self, client: BackendClient, request: SessionSubmitRequest) -> None:
        """Start one durable, cancellable agent request without blocking control frames."""

        if self.artifact_service is None:
            raise RequestStoreError("Ocean agent services are not configured")
        workspace_id = self._workspace_id(request)
        task_id = self._task_id(request)
        workspace = self.store.workspace_snapshot(workspace_id)
        effective_task_revision = request.expected_task_revision
        revision_refreshed = False
        if workspace.revision != request.expected_workspace_revision:
            # session.submit has not produced an external effect yet.  Rebase
            # this read/plan boundary exactly once when the task has no active
            # writer; later mutating tools still carry normal revision guards.
            task = self.store.get_research_task(task_id) if task_id is not None else None
            if (
                task is None
                or task.workspace_id != workspace_id
                or task.status != "active"
                or task.active_request_id is not None
            ):
                raise WorkspaceRevisionConflict(workspace.revision)
            effective_task_revision = task.task_revision
            revision_refreshed = True
        if not workspace.path:
            await self._fail_request(
                client,
                request,
                code="tool_error",
                message="Open a project-local workspace before submitting an Ocean agent request",
                recoverable=True,
                details={},
            )
            return
        runtime_key = task_id or client.session_id
        if runtime_key is None:
            raise OceanAgentRuntimeError("Authenticated client has no session ID")
        # A terminal event is committed before the request task releases its
        # in-memory runtime slot.  A fast follow-up must wait for that bounded
        # cleanup instead of being misclassified as concurrent work.
        stale_request_ids = [
            request_id
            for request_id, session_key in self._agent_request_sessions.items()
            if session_key == runtime_key
            and (record := self.store.get_request(request_id)) is not None
            and record.terminal
        ]
        stale_tasks = [
            self._agent_tasks[request_id]
            for request_id in stale_request_ids
            if request_id in self._agent_tasks
            and self._agent_tasks[request_id] is not asyncio.current_task()
        ]
        if stale_tasks:
            await asyncio.gather(*stale_tasks, return_exceptions=True)
        if runtime_key in self._agent_sessions and any(
            session_key == runtime_key for session_key in self._agent_request_sessions.values()
        ):
            await self._fail_request(
                client,
                request,
                code="tool_error",
                message="This session already has an active foreground agent request",
                recoverable=True,
                details={},
            )
            return

        budget = self._agent_request_budgets.get(request.request_id, self.agent_budget)

        try:
            provider_id = self.provider_id_resolver()
            expected_model_id = (
                self.model_id_resolver() if self.model_id_resolver is not None else None
            )
        except OceanAgentRuntimeError as exc:
            await self._fail_request(
                client,
                request,
                code="model_error",
                message="Configured Ocean model provider is unavailable",
                recoverable=True,
                details={"reason": str(exc)},
            )
            return

        try:
            submitted_text = self._submitted_text_with_context_refs(
                workspace_id=workspace_id,
                task_id=task_id,
                text=request.payload.text,
                context_refs=request.payload.context_refs,
            )
        except RequestNotFound as exc:
            await self._fail_request(
                client,
                request,
                code="artifact_not_found",
                message="A selected immutable context reference is unavailable in this workspace",
                recoverable=True,
                details={"reason": str(exc)},
            )
            return

        checkpoint = None
        if task_id is not None:
            current_task_record = self._task_in_workspace(task_id, request)
            if effective_task_revision != current_task_record.task_revision:
                if (
                    current_task_record.status != "active"
                    or current_task_record.active_request_id is not None
                ):
                    raise TaskRevisionConflict(current_task_record.task_revision)
                effective_task_revision = current_task_record.task_revision
                revision_refreshed = True
            self.store.begin_task_request(
                task_id=task_id,
                request_id=request.request_id,
                expected_task_revision=effective_task_revision,
            )
            self.store.start_task_workflow(
                request_id=request.request_id,
                task_id=task_id,
                workspace_id=workspace_id,
            )
            checkpoint = self.store.get_task_checkpoint(task_id)

        try:
            agent_session = await self._agent_session_for(
                client=client,
                workspace_id=workspace_id,
                workspace_path=Path(workspace.path),
                provider_id=provider_id,
                expected_model_id=expected_model_id,
                budget=budget,
                task_id=task_id,
                checkpoint_messages=list(checkpoint.messages) if checkpoint is not None else None,
                checkpoint_compaction_generation=(
                    checkpoint.compaction_generation if checkpoint is not None else 0
                ),
                checkpoint_runtime_profile_fingerprint=(
                    checkpoint.runtime_profile_fingerprint if checkpoint is not None else None
                ),
                checkpoint_system_prompt_fingerprint=(
                    checkpoint.system_prompt_fingerprint if checkpoint is not None else None
                ),
            )
            context_snapshot = OceanContextBuilder(store=self.store).build(
                workspace_id=workspace_id,
                provider_id=provider_id,
                task_id=task_id,
                routing_only=True,
            )
        except (ContextPolicyError, OceanAgentRuntimeError, OSError, ValueError) as exc:
            await self._fail_request(
                client,
                request,
                code="model_error",
                message="Ocean agent runtime could not prepare a provider-approved workspace context",
                recoverable=True,
                details={"reason": str(exc)},
            )
            return

        agent_session.runtime.engine.set_system_prompt(
            self._system_prompt_with_workspace_context(
                agent_session.runtime.base_system_prompt,
                context_snapshot.payload,
                literature_acquisition_mode=request.payload.literature_acquisition_mode,
            )
        )
        agent_session.runtime.engine.set_ask_user_prompt(
            lambda question: self._ask_agent_interaction(client, request, question, kind="question")
        )
        agent_session.runtime.engine.set_permission_checker(OceanExecutionPermissionChecker())
        agent_session.runtime.engine.set_permission_prompt(
            lambda tool_name, reason: self._ask_execution_permission(
                client, request, tool_name, reason
            )
        )
        self._agent_request_clients[request.request_id] = client
        if revision_refreshed:
            # This is concurrency bookkeeping, not part of the research
            # conversation.  Keep it in backend diagnostics instead of
            # presenting it as if the user or Coordinator said it.
            _LOGGER.info(
                "Refreshed a stale task revision before model execution",
                extra={"task_id": task_id, "request_id": request.request_id},
            )
        self._team_snapshot_revisions[request.request_id] = 0
        await self._emit_team_snapshot(
            workspace_id=workspace_id,
            parent_request_id=request.request_id,
            task_id=task_id,
        )
        task = asyncio.create_task(
            self._execute_agent_request(
                client=client,
                request=request,
                agent_session=agent_session,
                context_audit_ids=context_snapshot.audit_ids,
                budget=budget,
                submitted_text=submitted_text,
                visible_text=request.payload.text,
            ),
            name=f"ocean-agent-{request.request_id}",
        )
        self._agent_tasks[request.request_id] = task
        self._agent_request_sessions[request.request_id] = agent_session.runtime_key

    async def _ask_agent_interaction(
        self,
        client: BackendClient,
        request: SessionSubmitRequest,
        question: str,
        *,
        kind: Literal["question", "permission"],
        tool_name: str | None = None,
    ) -> str:
        """Route one model question only to its owning local Desktop session."""

        return await self._ask_interaction_for_request(
            client,
            request_id=request.request_id,
            task_id=self._task_id(request),
            workspace_id=self._workspace_id(request),
            question=question,
            kind=kind,
            tool_name=tool_name,
        )

    async def _ask_interaction_for_request(
        self,
        client: BackendClient,
        *,
        request_id: str,
        task_id: str | None,
        workspace_id: str,
        question: str,
        kind: Literal["question", "permission", "paper_selection"],
        tool_name: str | None = None,
        options: tuple[dict[str, Any], ...] = (),
    ) -> str:
        """Pause one active model request for a typed researcher interaction."""

        if client.session_id is None:
            raise OceanAgentRuntimeError("Question interaction requires an authenticated session")
        normalized = question.strip()
        if not normalized:
            raise OceanAgentRuntimeError("Question interaction cannot be empty")
        interaction_id = f"int_{uuid4().hex}"
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.store.create_interaction(
            interaction_id=interaction_id,
            request_id=request_id,
            task_id=task_id,
            workspace_id=workspace_id,
            session_id=client.session_id,
            principal=client.principal.key if client.principal is not None else "",
            question=normalized,
            kind=kind,
            options=options,
        )
        self._pending_questions[interaction_id] = (request_id, client.session_id, future)
        await self.event_bus.emit_local(
            client,
            InteractionRequestedEvent(
                **self._event_fields(
                    client,
                    request_id=request_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                ),
                type="interaction.requested",
                payload=InteractionRequestedPayload(
                    interaction_id=interaction_id,
                    kind=kind,
                    question=self._bounded_text(normalized, 4_000),
                    tool_name=tool_name,
                    options=options,
                ),
            ),
        )
        try:
            return await future
        finally:
            self._pending_questions.pop(interaction_id, None)

    async def _ask_execution_permission(
        self, client: BackendClient, request: SessionSubmitRequest, tool_name: str, reason: str
    ) -> bool:
        response = await self._ask_agent_interaction(
            client,
            request,
            f"Allow {tool_name} to start this sandboxed analysis execution? {reason}",
            kind="permission",
            tool_name=tool_name,
        )
        return response.strip().lower() in {"allow", "approve", "approved", "yes"}

    async def _request_paper_selection(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        payload: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        """Return an explicit paper shortlist selection to the active Coordinator tool call."""

        request_id = str(context.request_id or "").strip()
        if not request_id:
            raise OceanAgentRuntimeError("Paper selection requires an active request")
        client = self._agent_request_clients.get(request_id)
        if client is None:
            raise OceanAgentRuntimeError("Paper selection request is no longer active")
        raw_papers = payload.get("papers")
        if not isinstance(raw_papers, list) or not raw_papers:
            raise OceanAgentRuntimeError("Paper selection requires a non-empty shortlist")
        papers = tuple(dict(item) for item in raw_papers if isinstance(item, dict))
        if len(papers) != len(raw_papers):
            raise OceanAgentRuntimeError("Paper selection shortlist is malformed")
        question = str(payload.get("question") or "").strip()
        answer = await self._ask_interaction_for_request(
            client,
            request_id=request_id,
            task_id=task_id,
            workspace_id=workspace_id,
            question=question,
            kind="paper_selection",
            tool_name="ocean_request_paper_selection",
            options=papers,
        )
        try:
            response = json.loads(answer)
            selected_ids = response["selected_paper_ids"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OceanAgentRuntimeError("Paper selection response is malformed") from exc
        if (
            not isinstance(selected_ids, list)
            or not selected_ids
            or any(not isinstance(value, str) for value in selected_ids)
            or len(selected_ids) != len(set(selected_ids))
        ):
            raise OceanAgentRuntimeError("Select at least one unique paper")
        known_by_id = {str(paper.get("paper_id")): paper for paper in papers}
        unknown = sorted(set(selected_ids) - set(known_by_id))
        if unknown:
            raise OceanAgentRuntimeError(
                "Paper selection contains unknown ids: " + ", ".join(unknown)
            )
        selected_set = set(selected_ids)
        selected_papers = [
            known_by_id[str(paper["paper_id"])]
            for paper in papers
            if str(paper["paper_id"]) in selected_set
        ]
        return {
            "selected_paper_ids": selected_ids,
            "selected_papers": selected_papers,
        }

    async def _interaction_respond(
        self, client: BackendClient, request: InteractionRespondRequest
    ) -> None:
        pending = self._pending_questions.get(request.payload.interaction_id)
        if pending is None:
            raise RequestNotFound("Question interaction is no longer pending")
        target_request_id, session_id, future = pending
        if client.session_id != session_id:
            raise RequestStoreError("Question interaction belongs to a different session")
        answer = request.payload.answer.strip()
        terminal = self._completed_event(
            client,
            request,
            result={
                "interaction_id": request.payload.interaction_id,
                "target_request_id": target_request_id,
            },
        )
        self.store.commit_interaction_response(
            interaction_id=request.payload.interaction_id,
            session_id=client.session_id,
            principal=client.principal.key if client.principal is not None else "",
            terminal_event=terminal,
        )
        if not future.done():
            future.set_result(answer)
        await self._broadcast_committed_terminal(client, terminal)

    async def _agent_session_for(
        self,
        *,
        client: BackendClient,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        expected_model_id: str | None,
        budget: OceanAgentBudget,
        task_id: str | None,
        checkpoint_messages: list[dict[str, Any]] | None,
        checkpoint_compaction_generation: int,
        checkpoint_runtime_profile_fingerprint: str | None,
        checkpoint_system_prompt_fingerprint: str | None,
    ) -> _AgentSession:
        """Reuse a conversation only while its workspace/provider binding remains valid."""

        if client.session_id is None:
            raise OceanAgentRuntimeError("Authenticated client has no session ID")
        if self.artifact_service is None:
            raise OceanAgentRuntimeError("Ocean agent services are not configured")
        runtime_key = task_id or client.session_id
        existing = self._agent_sessions.get(runtime_key)
        if (
            existing is not None
            and existing.workspace_id == workspace_id
            and existing.runtime.provider_id == provider_id
            and (
                expected_model_id is None
                or existing.runtime.model_id == expected_model_id
            )
        ):
            return existing
        if existing is not None:
            await existing.runtime.close()
            self._agent_sessions.pop(runtime_key, None)
        if task_id is not None:
            active_runtime_keys = set(self._agent_request_sessions.values())
            for cached_key, cached in list(self._agent_sessions.items()):
                if cached_key == runtime_key or cached_key in active_runtime_keys:
                    continue
                if cached.task_id is not None:
                    await cached.runtime.close()
                    self._agent_sessions.pop(cached_key, None)
        if not workspace_path.is_dir():
            raise OceanAgentRuntimeError("Workspace directory is unavailable")
        services = OceanToolServices(
            workspace_id=workspace_id,
            provider_id=provider_id,
            store=self.store,
            artifacts=self.artifact_service,
            task_id=task_id,
            resource_access="routing",
            skill_capabilities=(LITERATURE_CAPABILITY,),
            skill_role="coordinator",
            domain_event_emitter=self.event_bus.emit_workspace,
            paper_selection_sink=(
                lambda payload, context: self._request_paper_selection(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    payload=payload,
                    context=context,
                )
            ),
            team_assign_sink=(
                (
                    lambda payload, context: self._assign_team_work(
                        workspace_id=workspace_id,
                        workspace_path=workspace_path,
                        provider_id=provider_id,
                        task_id=task_id,
                        payload=payload,
                        context=context,
                    )
                )
                if self.team_orchestrator is not None
                else None
            ),
            # Experts persist immutable candidate files. The Coordinator alone
            # receives the publication service used to promote reviewed
            # candidates into task-local results.
            expert_deliverables=(
                self.team_orchestrator.expert_deliverables
                if self.team_orchestrator is not None
                else None
            ),
            agent_thread_id=(
                f"task:{workspace_id}:{task_id}"
                if task_id is not None
                else f"session:{workspace_id}:{client.session_id}"
            ),
        )
        runtime = await self.agent_runtime_factory(
            services,
            workspace_path.resolve(),
            budget,
            self._operation_id,
        )
        if runtime.provider_id != provider_id:
            await runtime.close()
            raise OceanAgentRuntimeError(
                "Ocean runtime provider does not match the confirmed disclosure policy"
            )
        current_runtime_fingerprint = hashlib.sha256(
            OCEAN_RUNTIME_PROFILE_VERSION.encode("utf-8")
        ).hexdigest()
        current_prompt_fingerprint = hashlib.sha256(
            runtime.base_system_prompt.encode("utf-8")
        ).hexdigest()
        checkpoint_is_current = (
            checkpoint_messages is not None
            and checkpoint_runtime_profile_fingerprint == current_runtime_fingerprint
            and checkpoint_system_prompt_fingerprint == current_prompt_fingerprint
        )
        if checkpoint_messages is not None and not checkpoint_is_current:
            _LOGGER.info(
                "Ignoring stale task model checkpoint after Ocean runtime policy change",
                extra={"task_id": task_id, "runtime_profile": OCEAN_RUNTIME_PROFILE_VERSION},
            )
        if checkpoint_is_current:
            try:
                runtime.engine.load_messages(
                    [
                        ConversationMessage.model_validate(message)
                        for message in checkpoint_messages
                    ],
                    compaction_generation=checkpoint_compaction_generation,
                )
            except ValidationError as exc:
                await runtime.close()
                raise TaskCheckpointIncompatible(
                    "Task checkpoint messages no longer match the OceanMind message schema"
                ) from exc
        session = _AgentSession(
            session_id=client.session_id,
            runtime_key=runtime_key,
            workspace_id=workspace_id,
            task_id=task_id,
            runtime=runtime,
        )
        self._agent_sessions[runtime_key] = session
        return session

    @staticmethod
    def _agent_job_key(payload: dict[str, Any], *, task_scope: str) -> str:
        """Derive one persistent participant identity per task and Expert instance.

        Goals, source selections, expected outputs, retries, and foreground
        requests are assignment state. ``todo_id`` belongs to the Coordinator's
        scientific plan; it never creates another instance. ``expert_key`` is
        the only Coordinator-authored discriminator within one profile.
        """

        raw_authority = payload.get("authority", "")
        try:
            authority = ChildAuthority(raw_authority).value
        except ValueError:
            authority = str(raw_authority)
        normalized = {
            "task_scope": task_scope,
            "profile_id": payload.get("profile_id"),
            "authority": authority,
        }
        expert_key = payload.get("expert_key")
        if expert_key:
            # Keep the legacy/default singleton hash unchanged for existing
            # tasks while giving explicitly parallel siblings isolated jobs.
            normalized["expert_key"] = str(expert_key)
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"job_{digest[:40]}"

    def _task_source_refs(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
    ) -> tuple[EvidenceRef, ...]:
        """Freeze every user-visible task source without involving an Agent."""

        if task_id is None:
            return ()
        refs: list[EvidenceRef] = []
        for record in self.store.list_task_artifacts(task_id=task_id):
            artifact = record.artifact
            if artifact.workspace_id != workspace_id or artifact.artifact_type not in {
                "dataset",
                "paper",
            }:
                continue
            if "source" not in record.relations:
                continue
            kind = "dataset" if artifact.artifact_type == "dataset" else "paper"
            evidence = EvidenceRef(
                kind=kind,
                ref=artifact.ref.key,
                locator=f"source_{len(refs) + 1}",
            )
            if evidence not in refs:
                refs.append(evidence)
        return tuple(refs)

    def _agent_job_records(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        parent_request_id: str,
        job_key: str,
        profile_id: str,
        authority: str,
        expert_key: str | None,
    ) -> list[TeamWorkRecord]:
        records = (
            self.store.list_task_team_work(
                workspace_id=workspace_id,
                task_id=task_id,
            )
            if task_id is not None
            else self.store.list_team_work(
                workspace_id=workspace_id,
                parent_request_id=parent_request_id,
            )
        )
        return [
            record
            for record in records
            if record.work_order.job_key == job_key
            or (
                # Legacy releases created several todo-derived job keys for
                # the default singleton profile. Recover only those unkeyed
                # records; never absorb an explicitly named sibling Expert.
                expert_key is None
                and record.work_order.expert_key is None
                and record.work_order.profile_id == profile_id
                and record.work_order.authority.value == authority
            )
        ]

    @staticmethod
    def _agent_round_id(*, parent_request_id: str, job_key: str, operation_suffix: str) -> str:
        """Derive one replay-stable WorkOrder id for a single session round."""

        digest = hashlib.sha256(
            f"{parent_request_id}\0{job_key}\0{operation_suffix}".encode()
        ).hexdigest()
        return f"work_{digest[:32]}"

    @classmethod
    def _agent_session_capsule(cls, records: list[TeamWorkRecord]) -> str:
        """Build bounded durable memory for a new round in one Expert session.

        The child model process is disposable. Continuity lives in accepted or
        partial round results keyed by ``job_key`` so a follow-up can reuse prior
        evidence without replaying the previous model loop.
        """

        # Backend-recovered partial results also become durable context when an
        # exhausted interrupted WorkOrder must be followed by a fresh bounded
        # assignment. This preserves useful prose and outputs without replaying
        # the failed provider transcript.
        terminal_records = [record for record in records if record.result is not None]
        rounds: list[dict[str, Any]] = []
        # Expert continuity is a compact product history, not a replay of the
        # prior ReAct transcript, checkpoints, methods, and diagnostics. The
        # latter already live in durable execution records and the manifest.
        numbered_records = list(enumerate(terminal_records, start=1))[-3:]
        for round_number, record in reversed(numbered_records):
            result = record.result
            rounds.append(
                {
                    "round": round_number,
                    "goal": record.work_order.task_goal,
                    "requested_outcomes": list(record.work_order.outcome_intents),
                    "status": record.state.value,
                    "result": (
                        {
                            "text": cls._bounded_text(result.text, 6_000),
                            "result_origin": (
                                result.result_origin.value
                                if result.result_origin is not None
                                else None
                            ),
                            "outputs": [item.model_dump(mode="json") for item in result.outputs],
                            "conclusions": [
                                item.model_dump(mode="json") for item in result.conclusions
                            ],
                            "evidence_refs": [
                                item.model_dump(mode="json") for item in result.evidence_refs
                            ],
                            "limitations": list(result.limitations),
                            "unresolved_questions": list(result.unresolved_questions),
                            "failure_code": (
                                result.failure_code.value
                                if result.failure_code is not None
                                else None
                            ),
                            "error": result.error,
                        }
                        if result is not None
                        else None
                    ),
                }
            )
        return cls._bounded_text(
            json.dumps(
                {
                    "round_count": len(terminal_records),
                    "ordering": "most_recent_first",
                    "rounds": rounds,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            8_000,
        )

    @staticmethod
    def _team_work_token_usage(records: list[TeamWorkRecord]) -> int:
        """Return measured tokens for terminal participant rounds in one user turn."""

        return sum(
            record.result.usage.input_tokens + record.result.usage.output_tokens
            for record in records
            if record.result is not None
        )

    def _participant_work_budget(
        self,
        request_id: str,
        *,
        authority: ChildAuthority,
        sibling_count: int = 1,
        budget_tier: Literal["quick", "standard", "deep"] = "standard",
    ) -> WorkBudget:
        """Return the ceiling for one bounded Coordinator-to-Expert assignment.

        Scientific completion remains a Coordinator decision. These limits only
        bound one round. A focused Coordinator follow-up is a new assignment and
        receives a fresh bounded envelope while the shared request budget remains
        the hard upper limit across the team.
        """

        del authority, sibling_count
        budget = self._agent_request_budgets.get(request_id, self.agent_budget)
        tier_limits = {
            "quick": (24, 12, 160_000, 32_000, 300.0),
            "standard": (64, 32, 320_000, 64_000, 600.0),
            "deep": (
                budget.max_turns,
                budget.max_tool_calls,
                budget.max_input_tokens,
                budget.max_output_tokens,
                budget.max_wall_seconds,
            ),
        }
        turns, tool_calls, input_tokens, output_tokens, wall_seconds = tier_limits[budget_tier]
        return WorkBudget(
            max_turns=min(budget.max_turns, turns),
            max_tool_calls=min(budget.max_tool_calls, tool_calls),
            max_input_tokens=min(budget.max_input_tokens, input_tokens),
            max_output_tokens=min(budget.max_output_tokens, output_tokens),
            max_wall_seconds=min(budget.max_wall_seconds, wall_seconds),
        )

    @staticmethod
    def _remaining_participant_work_budget(
        total: WorkBudget,
        records: list[TeamWorkRecord],
    ) -> WorkBudget | None:
        """Return the unused envelope when resuming an interrupted assignment.

        This does not judge whether the science is complete. It prevents a
        backend-recovered continuation of the same WorkOrder from resetting the
        resources already spent before transport failed.
        """

        used_turns = 0
        used_tool_calls = 0
        used_input_tokens = 0
        used_output_tokens = 0
        used_wall_seconds = 0.0
        for record in records:
            if record.result is None:
                continue
            usage = record.result.usage
            used_turns += usage.turns
            used_tool_calls += usage.tool_calls
            used_input_tokens += usage.input_tokens
            used_output_tokens += usage.output_tokens
            used_wall_seconds += usage.wall_seconds

        remaining = {
            "max_turns": total.max_turns - used_turns,
            "max_tool_calls": total.max_tool_calls - used_tool_calls,
            "max_input_tokens": total.max_input_tokens - used_input_tokens,
            "max_output_tokens": total.max_output_tokens - used_output_tokens,
            "max_wall_seconds": total.max_wall_seconds - used_wall_seconds,
        }
        # A continuation needs enough room to read its compact assignment and
        # return once. Below these transport minima another model call cannot
        # produce a useful delivery.
        if (
            remaining["max_turns"] < 1
            or remaining["max_tool_calls"] < 1
            or remaining["max_input_tokens"] < 8_000
            or remaining["max_output_tokens"] < 2_000
            or remaining["max_wall_seconds"] < 30.0
        ):
            return None
        return WorkBudget(**remaining)

    async def _assign_team_work(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        task_id: str | None,
        payload: dict[str, Any],
        context,
    ) -> dict[str, Any]:
        """Route the Coordinator's single scheduling contract to backend orchestration."""

        return await self._execute_team_plan(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            provider_id=provider_id,
            task_id=task_id,
            payload=payload,
            context=context,
        )

    async def _execute_team_plan(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        provider_id: str,
        task_id: str | None,
        payload: dict[str, Any],
        context,
    ) -> dict[str, Any]:
        """Bind the Coordinator's dispatched todos to Expert work orders."""

        orchestrator = self.team_orchestrator
        if orchestrator is None:
            raise RequestStoreError("Sparse Ocean team delegation is not configured")
        if context.request_id is None or context.operation_id is None:
            raise RequestStoreError("Team WorkPlan requires durable request correlation")
        request_record = self.store.get_request(context.request_id)
        if request_record is None or request_record.request_type != "session.submit":
            raise RequestStoreError("Team WorkPlan parent is not a foreground Ocean request")
        if request_record.workspace_id != workspace_id or request_record.task_id != task_id:
            raise RequestStoreError("Team WorkPlan does not belong to this workspace task")
        expected_revision = self.store.workspace_snapshot(workspace_id).revision

        operation_suffix = hashlib.sha256(context.operation_id.encode("utf-8")).hexdigest()[:24]
        plan_goal = str(payload.pop("plan_goal")).strip()
        todos = list(payload.pop("todos"))
        dispatch = tuple(str(todo_id) for todo_id in payload.pop("dispatch"))
        if payload:
            raise RequestStoreError(
                "Team assignment contains unsupported fields: " + ", ".join(sorted(payload))
            )
        todo_by_id = {str(todo["todo_id"]): todo for todo in todos}
        dispatched_todos = [dict(todo_by_id[todo_id]) for todo_id in dispatch]
        dispatched_experts = [
            (str(todo.get("profile_id")), str(todo.get("expert_key") or "default"))
            for todo in dispatched_todos
        ]
        duplicate_experts = sorted(
            expert
            for expert in set(dispatched_experts)
            if dispatched_experts.count(expert) > 1
        )
        if duplicate_experts:
            raise RequestStoreError(
                "Dispatch at most one todo per stable Expert instance in a wave. Sequence more "
                "work through that instance or use distinct expert_key values: "
                + ", ".join(
                    f"{profile_id}/{expert_key}"
                    for profile_id, expert_key in duplicate_experts
                )
            )
        todo_plan = tuple(
            CoordinatorTodo(
                todo_id=str(todo["todo_id"]),
                question=str(todo["question"]),
                depends_on=tuple(todo.get("depends_on", ())),
                profile_id=str(todo["profile_id"]),
                expert_key=(str(todo["expert_key"]) if todo.get("expert_key") else None),
                expected_outputs=tuple(todo.get("expected_outputs", ("answer",))),
            )
            for todo in todos
        )
        task_sources = self._task_source_refs(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        for todo in dispatched_todos:
            bind_agent_profile(todo)
            authority = ChildAuthority(todo["authority"])
            requested_handles = tuple(todo.pop("source_handles", ()))
            selected_sources: tuple[EvidenceRef, ...]
            if authority is not ChildAuthority.EXPERT:
                if requested_handles:
                    raise RequestStoreError(
                        "Scientific Discussion Partner does not receive Task Sources; pass frozen "
                        "Expert results through context in a later Coordinator wave"
                    )
                selected_sources = ()
            elif requested_handles:
                selected: list[EvidenceRef] = []
                for handle in requested_handles:
                    match = re.fullmatch(r"source_([1-9][0-9]*)", str(handle))
                    if match is None or int(match.group(1)) > len(task_sources):
                        raise RequestStoreError(f"Unknown Task Source handle: {handle}")
                    source = task_sources[int(match.group(1)) - 1]
                    if source not in selected:
                        selected.append(source)
                selected_sources = tuple(selected)
            else:
                # A task's source memory is the default assignment envelope. The
                # Coordinator may narrow it with source_handles, but omitting that
                # optional optimization must never dispatch an empty Expert job or
                # trigger a response retry.
                selected_sources = task_sources
            todo["task_goal"] = todo.pop("question")
            todo["context_summary"] = "\n\n".join(
                part
                for part in (
                    f"Why this Expert: {todo.pop('why_this_expert')}",
                    str(todo.pop("context", "")).strip(),
                )
                if part
            )
            todo["outcome_intents"] = tuple(todo.pop("expected_outputs"))
            # Experts receive backend-mounted Task Sources. The Scientific
            # Discussion Partner receives only the Coordinator's question,
            # hypotheses, and explicitly supplied context.
            todo["input_refs"] = tuple(item.model_dump(mode="json") for item in selected_sources)
        job_keys = {
            todo["todo_id"]: self._agent_job_key(
                todo,
                task_scope=task_id or context.request_id,
            )
            for todo in dispatched_todos
        }
        dispatched_by_id = {str(todo["todo_id"]): todo for todo in dispatched_todos}
        job_records = {
            todo_id: self._agent_job_records(
                workspace_id=workspace_id,
                task_id=task_id,
                parent_request_id=context.request_id,
                job_key=job_key,
                profile_id=str(dispatched_by_id[todo_id]["profile_id"]),
                authority=ChildAuthority(dispatched_by_id[todo_id]["authority"]).value,
                expert_key=(
                    str(dispatched_by_id[todo_id]["expert_key"])
                    if dispatched_by_id[todo_id].get("expert_key")
                    else None
                ),
            )
            for todo_id, job_key in job_keys.items()
        }
        # Existing tasks may contain pre-instance-key records whose job_key
        # included todo_id. The default unkeyed Expert adopts the latest stored
        # key so its session workspace continues instead of being reinitialized.
        for todo_id, records in job_records.items():
            if records and records[-1].work_order.job_key is not None:
                job_keys[todo_id] = records[-1].work_order.job_key
        existing_jobs = {
            todo_id: (records[-1] if records else None) for todo_id, records in job_records.items()
        }
        for latest in existing_jobs.values():
            if latest is not None and latest.state in {
                WorkStatus.QUEUED,
                WorkStatus.RUNNING,
            }:
                raise RequestStoreError(
                    "The selected Expert session already has an active round; wait for its result "
                    "before assigning the next question"
                )
        todos_by_id = dispatched_by_id
        continuations: dict[str, TeamWorkRecord] = {}
        for todo_id, latest in existing_jobs.items():
            if (
                latest is None
                or latest.work_order.parent_request_id != context.request_id
                or latest.work_order.todo_id != todo_id
                or latest.result is None
                or latest.result.result_origin is not ExpertResultOrigin.BACKEND_RECOVERED
                or latest.state
                not in {
                    WorkStatus.COMPLETED,
                    WorkStatus.INCOMPLETE,
                    WorkStatus.FAILED,
                    WorkStatus.CANCELLED,
                }
            ):
                continue
            todo = todos_by_id[todo_id]
            continuation_budget = self._remaining_participant_work_budget(
                self._participant_work_budget(
                    context.request_id,
                    authority=ChildAuthority(todo["authority"]),
                    budget_tier=todo.get("budget_tier", "standard"),
                ),
                [latest],
            )
            if continuation_budget is not None:
                continuations[todo_id] = latest
        request_work_records = self.store.list_team_work(
            workspace_id=workspace_id,
            parent_request_id=context.request_id,
        )
        agent_budget = self._agent_request_budgets.get(context.request_id, self.agent_budget)
        team_tokens_before_wave = self._team_work_token_usage(request_work_records)
        new_round_cutoff = max(
            0,
            agent_budget.max_team_tokens - agent_budget.delivery_reserve_tokens,
        )
        starts_new_round = any(
            str(todo["todo_id"]) not in continuations for todo in dispatched_todos
        )
        if starts_new_round and team_tokens_before_wave >= new_round_cutoff:
            raise RequestStoreError(
                "The shared team budget is now reserved for Coordinator delivery. "
                "Do not start another Expert round; synthesize the answer from the "
                "durable results already returned in this user turn."
            )
        key_to_id = {
            todo["todo_id"]: (
                continuations[str(todo["todo_id"])].work_order.work_order_id
                if str(todo["todo_id"]) in continuations
                else self._agent_round_id(
                    parent_request_id=context.request_id,
                    job_key=job_keys[todo["todo_id"]],
                    operation_suffix=operation_suffix,
                )
            )
            for todo in dispatched_todos
        }
        bound_revision = expected_revision
        orders: list[WorkOrder] = []
        for dispatched_todo in dispatched_todos:
            todo = dict(dispatched_todo)
            todo_id = str(todo["todo_id"])
            authority = ChildAuthority(todo["authority"])
            prior_records = job_records[todo_id]
            continuation = continuations.get(todo_id)
            total_job_budget = self._participant_work_budget(
                context.request_id,
                authority=authority,
                budget_tier=todo.get("budget_tier", "standard"),
            )
            remaining_job_budget = self._remaining_participant_work_budget(
                total_job_budget,
                [continuation] if continuation is not None else [],
            )
            if remaining_job_budget is None:
                raise RequestStoreError(
                    "This interrupted assignment has no delivery capacity left. Review its "
                    "durable outputs and either issue a new focused follow-up assignment or "
                    "synthesize the supported answer with its remaining limitation."
                )
            if continuation is not None:
                # The Expert still owns this assignment. Coordinator prose,
                # regenerated parameters, and a new operation id cannot create
                # another round until the Expert explicitly submits. Refresh
                # only the revision required by WorkPlan validation.
                orders.append(
                    continuation.work_order.model_copy(
                        update={
                            "workspace_revision": bound_revision,
                            "budget": remaining_job_budget,
                        }
                    )
                )
                continue
            prior_terminal_records = [
                record for record in prior_records if record.result is not None
            ]
            if prior_terminal_records:
                todo["context_summary"] = self._bounded_text(
                    "\n\n".join(
                        part
                        for part in (
                            str(todo.get("context_summary", "")).strip(),
                            "This is a new Coordinator follow-up round in the same logical Expert "
                            "session. Reuse the durable results and execution evidence below. "
                            "Answer the new incremental question; do not repeat or recompute "
                            "already supported outcomes unless the new goal requires correction.\n"
                            + self._agent_session_capsule(prior_terminal_records),
                        )
                        if part
                    ),
                    16_000,
                )
            candidate = WorkOrder(
                work_order_id=key_to_id[todo_id],
                task_id=task_id,
                job_key=job_keys[todo_id],
                session_round=len(prior_terminal_records) + 1,
                parent_request_id=context.request_id,
                workspace_revision=bound_revision,
                allowed_capabilities=tuple(sorted(capabilities_for_authority(authority))),
                budget=remaining_job_budget,
                **todo,
            )
            orders.append(candidate)
        plan = WorkPlan(
            plan_id=f"plan_{operation_suffix}",
            parent_request_id=context.request_id,
            workspace_revision=bound_revision,
            reason_codes=("coordinator_assignment",),
            plan_goal=plan_goal,
            todos=todo_plan,
            dispatch=dispatch,
            work_orders=tuple(orders),
            preserve_disagreements=True,
        )
        results = await orchestrator.execute_plan(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            provider_id=provider_id,
            task_id=task_id,
            plan=plan,
            progress_sink=lambda: self._emit_team_snapshot(
                workspace_id=workspace_id,
                parent_request_id=context.request_id,
                task_id=task_id,
            ),
        )
        records_by_id = {
            record.work_order.work_order_id: record
            for record in self.store.list_team_work(
                workspace_id=workspace_id,
                parent_request_id=context.request_id,
            )
        }
        cumulative_team_tokens = self._team_work_token_usage(list(records_by_id.values()))
        results_by_id = {result.work_order_id: result for result in results}
        return {
            # The Coordinator already authored the WorkPlan. Do not echo it or
            # return backend bookkeeping inside the semantic ExpertResult.
            # Work state remains in work_records/todo_progress; scientific
            # handoff is deliberately only text plus fully described outputs.
            "expert_results": [result.coordinator_payload() for result in results],
            "work_records": [
                (
                    self._team_work_activity_summary(
                        records_by_id[order.work_order_id],
                        todo_id=str(todo["todo_id"]),
                    )
                    if order.work_order_id in records_by_id
                    else {
                        "todo_id": str(todo["todo_id"]),
                        "work_order_id": order.work_order_id,
                        "semantic_role": order.semantic_role,
                        "authority": order.authority.value,
                        "state": results_by_id[order.work_order_id].status.value,
                        "result": {
                            "status": results_by_id[order.work_order_id].status.value,
                            "failure_code": (
                                results_by_id[order.work_order_id].failure_code.value
                                if results_by_id[order.work_order_id].failure_code is not None
                                else None
                            ),
                            "error": results_by_id[order.work_order_id].error,
                        },
                    }
                )
                for order, todo in zip(orders, dispatched_todos, strict=True)
            ],
            "estimated_limits": {
                "max_children": len(orders),
                "shared_team_tokens": agent_budget.max_team_tokens,
                "delivery_reserve_tokens": agent_budget.delivery_reserve_tokens,
                "participant_max_input_tokens": agent_budget.max_input_tokens,
                "participant_max_output_tokens": agent_budget.max_output_tokens,
                "participant_max_tool_calls": agent_budget.max_tool_calls,
            },
            "actual_usage": {
                "input_tokens": sum(result.usage.input_tokens for result in results),
                "output_tokens": sum(result.usage.output_tokens for result in results),
                "tool_calls": sum(result.usage.tool_calls for result in results),
                "wall_seconds_sum": sum(result.usage.wall_seconds for result in results),
                "team_tokens_before_wave": team_tokens_before_wave,
                "cumulative_team_tokens": cumulative_team_tokens,
                "shared_team_tokens_remaining": max(
                    0, agent_budget.max_team_tokens - cumulative_team_tokens
                ),
            },
            "workspace_revision": self.store.workspace_snapshot(workspace_id).revision,
            "unresolved_disagreements_must_be_preserved": True,
            # New work becomes visible only through task-local results. Legacy
            # Artifact deliverables remain readable, but cannot turn a current
            # Team round into a newly published result.
            "canonical_mutation": any(result.result_refs for result in results),
            "todo_progress": {
                str(todo["todo_id"]): {
                    "job_key": job_keys[str(todo["todo_id"])],
                    "session_round": order.session_round,
                    "prior_rounds": sum(
                        1
                        for record in job_records[str(todo["todo_id"])]
                        if record.result is not None
                        and record.result.result_origin is ExpertResultOrigin.AGENT_SUBMITTED
                    ),
                    "current_work_order_id": key_to_id[str(todo["todo_id"])],
                    "depends_on": list(order.depends_on),
                    "state": results_by_id[order.work_order_id].status.value,
                    "round_result_returned_to_coordinator": (
                        results_by_id[order.work_order_id].result_origin
                        is ExpertResultOrigin.AGENT_SUBMITTED
                    ),
                }
                for todo, order in zip(dispatched_todos, orders, strict=True)
            },
        }

    @staticmethod
    def _durable_team_result_refs(
        records: list[TeamWorkRecord],
    ) -> tuple[TaskResultRef, ...]:
        """Return task-local results from the latest durable Expert round.

        TaskResult creation and ResultBundle binding are backend-owned commits,
        so their validity does not depend on the Expert managing to submit its
        final prose.  Only the latest round for each persistent Expert session
        is visible; older rounds remain durable history.
        """

        latest_durable_by_job: dict[str, TeamWorkRecord] = {}
        for record in records:
            if (
                record.work_order.authority is not ChildAuthority.EXPERT
                or record.result is None
                or not record.result.result_refs
            ):
                continue
            job_key = record.work_order.job_key or record.work_order.work_order_id
            current = latest_durable_by_job.get(job_key)
            if (
                current is None
                or record.work_order.session_round > current.work_order.session_round
            ):
                latest_durable_by_job[job_key] = record

        return tuple(
            dict.fromkeys(
                ref
                for record in latest_durable_by_job.values()
                for ref in record.result.result_refs
                if record.result is not None
            )
        )

    def _task_result_records(
        self,
        refs: tuple[TaskResultRef, ...],
    ) -> tuple[Any, ...]:
        """Resolve only result refs that still belong to their immutable task."""

        if self.task_results is None:
            return ()
        records: list[Any] = []
        for ref in refs:
            try:
                records.append(self.task_results.get(ref))
            except TaskResultError:
                continue
        return tuple(records)

    @staticmethod
    def _coordinator_report_requested(records: list[TeamWorkRecord]) -> bool:
        """Return whether the user-facing team plan requested a final report."""

        return any(
            record.work_order.authority is ChildAuthority.EXPERT
            and "report" in record.work_order.outcome_intents
            for record in records
        )

    def _materialize_coordinator_report(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        request_id: str,
        answer_markdown: str,
        result_refs: tuple[TaskResultRef, ...],
    ) -> TaskResultRef | None:
        """Persist the Coordinator synthesis without asking an Expert to publish it."""

        if task_id is None or self.task_results is None or not answer_markdown.strip():
            return None
        existing_reports = [
            record for record in self._task_result_records(result_refs) if record.kind == "report"
        ]
        if existing_reports:
            return max(
                existing_reports,
                key=lambda item: (item.created_at, item.ref.result_id),
            ).ref

        source_refs = tuple(ref.model_dump(mode="json") for ref in result_refs)
        markdown = answer_markdown.strip() + "\n"
        result_records = {record.ref.key: record for record in self._task_result_records(result_refs)}
        uncited_refs = []
        for ref in result_refs:
            record = result_records.get(ref.key)
            output_path = record.content.get("output_path") if record is not None else None
            if ref.result_id not in markdown and not (
                isinstance(output_path, str) and f"[[output:{output_path}" in markdown
            ):
                uncited_refs.append(ref)
        if uncited_refs:
            markdown += "\n## Supporting results\n\n"
            for ref in uncited_refs:
                key = f"{ref.task_id}/{ref.result_id}@v{ref.version}"
                markdown += f"- [[result:{key}|{ref.result_id}]]\n"

        task = self.store.get_research_task(task_id, workspace_id=workspace_id)
        task_title = task.title if task is not None else "OceanMind analysis"
        record = self.task_results.put(
            workspace_id=workspace_id,
            task_id=task_id,
            kind="report",
            title=f"{task_title} — research report",
            summary="Coordinator synthesis of accepted Expert conclusions and evidence.",
            content={
                "role": "coordinator_report",
                "markdown_file": "report.md",
                "source_result_refs": list(source_refs),
                "conclusion_export_allowed": True,
            },
            files={"report.md": markdown.encode("utf-8")},
            source_refs=source_refs,
            origin_request_id=request_id,
            materialization_key=f"coordinator-report:{request_id}",
        )
        return record.ref

    def _materialize_figure_reproduction_notebook(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        request_id: str,
        result_refs: tuple[TaskResultRef, ...],
    ) -> TaskResultRef | None:
        """Create a notebook that re-renders accepted, already-saved result data."""

        if task_id is None or self.task_results is None:
            return None
        materialization_key = f"supplementary-analysis-v4:{request_id}"
        existing = self.task_results.find_by_materialization_key(
            task_id=task_id,
            materialization_key=materialization_key,
        )
        if existing is not None:
            return existing.ref
        projector = self.task_workspace_projector or self.task_results.task_workspaces
        supplementary_root = projector.supplementary_notebook_path(task_id, request_id).parent
        sources: list[FigureReproductionSource] = []
        provenance_refs: list[dict[str, Any]] = []
        for record in self._task_result_records(result_refs):
            if record.kind != "interactive_view":
                continue
            data_file = record.content.get("dataset_file") or record.content.get(
                "data_file"
            )
            if not isinstance(data_file, str) or not data_file.strip():
                continue
            declared = next(
                (item for item in record.files if item.path == data_file),
                None,
            )
            if declared is None or Path(declared.path).suffix.lower() != ".nc":
                continue
            try:
                path = self.task_results.file_path(
                    ref=record.ref,
                    relative_path=data_file,
                )
            except (OSError, ValueError, TaskResultError):
                continue
            view_kind = record.content.get("view_kind")
            sources.append(
                FigureReproductionSource(
                    result_ref=record.ref.model_dump(mode="json"),
                    title=record.title,
                    summary=record.summary,
                    view_kind=view_kind if isinstance(view_kind, str) else "interactive_view",
                    data_reference=Path(
                        os.path.relpath(path, supplementary_root)
                    ).as_posix(),
                )
            )
            for source_ref in record.source_refs:
                if source_ref not in provenance_refs:
                    provenance_refs.append(source_ref)
        if not sources:
            return None

        notebook, data_index = build_figure_reproduction_notebook(
            request_id=request_id,
            sources=tuple(sources),
        )
        notebook_path = projector.write_supplementary_notebook(
            task_id=task_id,
            request_id=request_id,
            content=(
                json.dumps(notebook, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8"),
        )
        record = self.task_results.put(
            workspace_id=workspace_id,
            task_id=task_id,
            kind="file",
            title="Analysis notebook",
            summary="One editable notebook for this analysis, linked to its accepted result data.",
            content={
                "role": "supplementary_figure_notebook",
                "file": "analysis.ipynb",
                "source_result_refs": [source.result_ref for source in sources],
                "data_files": data_index,
                "renderer": "nature-python-templates/v3",
            },
            workspace_files={"analysis.ipynb": notebook_path},
            source_refs=tuple(provenance_refs),
            origin_request_id=request_id,
            materialization_key=materialization_key,
        )
        return record.ref

    def _canonical_user_answer(
        self,
        *,
        candidate: str,
        decision: CoordinatorDecision,
        result_refs: tuple[TaskResultRef, ...],
    ) -> str:
        """Prefer the published report when final prose is not result-bound.

        A Coordinator's ordinary prose remains authoritative when it actually
        cites durable results.  If it does not, a published report is a safer
        user-facing boundary than the latest repair round's filenames, API
        diagnostics, or advisory self-assessment.
        """

        records = self._task_result_records(result_refs)
        reports = [record for record in records if record.kind == "report"]
        has_result_binding = "[[result:" in candidate or "[[output:" in candidate
        answer = candidate.strip()
        if reports and (not has_result_binding or decision is not CoordinatorDecision.ANSWERED):
            report = max(reports, key=lambda item: (item.created_at, item.ref.result_id))
            markdown_file = report.content.get("markdown_file")
            if isinstance(markdown_file, str):
                try:
                    answer = (
                        self.task_results.file_path(
                            ref=report.ref,
                            relative_path=markdown_file,
                        )
                        .read_text(encoding="utf-8")
                        .strip()
                    )
                except (OSError, TaskResultError):
                    pass
        if not answer:
            answer = "The available results could not be presented as a complete answer."
        if reports:
            report = max(reports, key=lambda item: (item.created_at, item.ref.result_id))
            report_key = f"{report.ref.task_id}/{report.ref.result_id}@v{report.ref.version}"
            if report_key not in answer and report.ref.result_id not in answer:
                answer = (
                    f"{answer.rstrip()}\n\n## Research report\n\n"
                    f"- [[result:{report_key}|Open the complete research report]]"
                )
        return answer

    async def _execute_agent_request(
        self,
        *,
        client: BackendClient,
        request: SessionSubmitRequest,
        agent_session: _AgentSession,
        context_audit_ids: tuple[str, ...],
        budget: OceanAgentBudget,
        submitted_text: str,
        visible_text: str,
    ) -> None:
        """Map one Ocean agent stream into Protocol v2 and commit one terminal result."""

        usage_input_tokens = 0
        usage_output_tokens = 0
        tool_call_count = 0
        turn_count = 0
        last_assistant_text = ""
        model_error: str | None = None
        model_active_seconds = 0.0
        model_call_started = 0.0
        model_call_in_flight = False
        active_budget_reached = False
        active_tool_calls = 0
        prior_max_turns = agent_session.runtime.engine.max_turns
        agent_session.runtime.engine.set_max_turns(budget.max_turns)

        def model_call_state_hook(in_flight: bool) -> None:
            nonlocal model_active_seconds, model_call_started, model_call_in_flight
            now = time.monotonic()
            if in_flight and not model_call_in_flight:
                model_call_started = now
                model_call_in_flight = True
            elif not in_flight and model_call_in_flight:
                model_active_seconds += max(0.0, now - model_call_started)
                model_call_in_flight = False

        current_task = asyncio.current_task()
        assert current_task is not None

        async def monitor_model_active_budget() -> None:
            nonlocal active_budget_reached
            interval = min(0.5, max(0.02, budget.max_wall_seconds / 100.0))
            while True:
                await asyncio.sleep(interval)
                elapsed = model_active_seconds
                if model_call_in_flight:
                    elapsed += max(0.0, time.monotonic() - model_call_started)
                if elapsed >= budget.max_wall_seconds:
                    active_budget_reached = True
                    current_task.cancel()
                    return

        set_state_hook = getattr(agent_session.runtime.engine, "set_model_call_state_hook", None)
        if callable(set_state_hook):
            set_state_hook(model_call_state_hook)
        budget_monitor = asyncio.create_task(
            monitor_model_active_budget(),
            name=f"ocean-agent-active-budget-{request.request_id}",
        )
        try:
            await self._append_transcript_item(
                client,
                request,
                role="user",
                # ``submitted_text`` contains server-owned routing identities
                # used by the model.  The durable, user-visible transcript must
                # contain exactly what the researcher submitted.
                text=visible_text,
            )
            stream = agent_session.runtime.engine.submit_message(
                submitted_text,
                request_id=request.request_id,
            ).__aiter__()
            while True:
                try:
                    if active_tool_calls:
                        async with asyncio.timeout(budget.max_tool_wait_seconds):
                            event = await anext(stream)
                    else:
                        event = await anext(stream)
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    # Expert products are candidates, not a Coordinator
                    # decision. Never synthesize or accept them in backend
                    # recovery when Coordinator reasoning is interrupted.
                    raise
                except TimeoutError as exc:
                    raise _AgentToolWaitExceeded(
                        "tool execution did not report completion within "
                        f"{budget.max_tool_wait_seconds:g} seconds"
                    ) from exc

                if isinstance(event, ToolExecutionStarted):
                    active_tool_calls += 1
                elif isinstance(event, ToolExecutionCompleted):
                    active_tool_calls = max(0, active_tool_calls - 1)
                    if event.tool_name == "ocean_assign":
                        # Text before the assignment is progress narration, not
                        # a conclusion. The next assistant answer is synthesis.
                        last_assistant_text = ""

                if isinstance(event, AssistantTurnComplete):
                    turn_count += 1
                    usage_input_tokens += event.usage.input_tokens
                    usage_output_tokens += event.usage.output_tokens
                    if event.message.text and not event.message.tool_uses:
                        last_assistant_text = event.message.text
                    over_budget = (
                        usage_input_tokens > budget.max_input_tokens
                        or usage_output_tokens > budget.max_output_tokens
                    )
                    if over_budget:
                        raise _AgentBudgetExceeded("request token budget reached")
                elif isinstance(event, ToolExecutionStarted):
                    tool_call_count += 1
                    if tool_call_count > budget.max_tool_calls:
                        raise _AgentBudgetExceeded("tool call budget reached")
                elif isinstance(event, ErrorEvent):
                    model_error = event.message

                self._update_task_workflow_for_event(request, event)
                await self._emit_agent_stream_event(client, request, event)

            if request.request_id in self._cancelling_agent_requests or self._closing:
                return
            if model_error is not None:
                await self._fail_request(
                    client,
                    request,
                    code="model_error",
                    message="Coordinator stream ended before it made a final decision",
                    recoverable=True,
                    details={"reason": model_error},
                )
                return
            if (
                agent_session.task_id is not None
                and agent_session.runtime.engine.has_pending_continuation()
            ):
                await self._fail_request(
                    client,
                    request,
                    code="model_error",
                    message="Model stream ended before completing its pending tool continuation",
                    recoverable=True,
                    details={},
                )
                return
            team_plan = (
                self.team_orchestrator.plan_for(request.request_id)
                if self.team_orchestrator is not None
                else None
            )
            if self.team_orchestrator is not None:
                # Coordinator prose ends scheduling. Any leftover round is
                # first settled to a durable partial/cancelled ExpertResult so
                # the fallback request status reflects actual returned evidence
                # rather than generation stopping alone.
                await self.team_orchestrator.settle_request(request.request_id)
            team_records = (
                self.store.list_team_work(
                    workspace_id=agent_session.workspace_id,
                    parent_request_id=request.request_id,
                )
                if self.team_orchestrator is not None
                else []
            )
            coordinator_result = self.store.get_coordinator_result(request.request_id)
            if coordinator_result is None:
                # The ordinary final assistant answer is the Coordinator's
                # only handoff boundary. Durable Expert results are attached
                # by the backend without another formatting turn.
                fallback_text = last_assistant_text.strip()
                if fallback_text:
                    fallback_evidence = tuple(
                        dict.fromkeys(
                            evidence
                            for record in team_records
                            if record.result is not None
                            for evidence in record.result.evidence_refs
                        )
                    )
                    fallback_results = self._durable_team_result_refs(team_records)
                    if self._coordinator_report_requested(team_records):
                        try:
                            report_ref = self._materialize_coordinator_report(
                                workspace_id=agent_session.workspace_id,
                                task_id=agent_session.task_id,
                                request_id=request.request_id,
                                answer_markdown=fallback_text,
                                result_refs=fallback_results,
                            )
                        except (OSError, TaskResultError, ValueError) as exc:
                            _LOGGER.warning(
                                "Could not materialize Coordinator report for %s: %s",
                                request.request_id,
                                exc,
                            )
                            report_ref = None
                        if report_ref is not None and report_ref not in fallback_results:
                            fallback_results = (*fallback_results, report_ref)
                    try:
                        supplementary_notebook = self._materialize_figure_reproduction_notebook(
                            workspace_id=agent_session.workspace_id,
                            task_id=agent_session.task_id,
                            request_id=request.request_id,
                            result_refs=fallback_results,
                        )
                    except (OSError, TaskResultError, ValueError) as exc:
                        _LOGGER.warning(
                            "Could not materialize supplementary figure notebook for %s: %s",
                            request.request_id,
                            exc,
                        )
                        supplementary_notebook = None
                    if (
                        supplementary_notebook is not None
                        and supplementary_notebook not in fallback_results
                    ):
                        fallback_results = (*fallback_results, supplementary_notebook)
                    # Reaching an ordinary no-tool answer is the Coordinator's
                    # own stop decision. The backend attaches durable evidence,
                    # but never reclassifies that decision from Expert states.
                    fallback_decision = CoordinatorDecision.ANSWERED
                    coordinator_result = CoordinatorResult(
                        answer_markdown=self._canonical_user_answer(
                            candidate=fallback_text,
                            decision=fallback_decision,
                            result_refs=fallback_results,
                        ),
                        decision=fallback_decision,
                        answer_basis=(
                            CoordinatorAnswerBasis.EXPERT_EVIDENCE
                            if team_records
                            else CoordinatorAnswerBasis.GENERAL_KNOWLEDGE
                        ),
                        evidence_refs=fallback_evidence,
                        result_refs=fallback_results,
                    )
                    coordinator_result = self.store.record_coordinator_result(
                        request_id=request.request_id,
                        result=coordinator_result,
                    )
            if coordinator_result is None:
                await self._fail_request(
                    client,
                    request,
                    code="model_error",
                    message="OceanMind stopped without a user-facing conclusion",
                    recoverable=True,
                    details={},
                )
                return
            final_assistant_text = (
                coordinator_result.answer_markdown
                if coordinator_result is not None
                else last_assistant_text
            )
            outcome_status = (
                {
                    CoordinatorDecision.ANSWERED: "completed",
                    CoordinatorDecision.INSUFFICIENT_EVIDENCE: "incomplete",
                    CoordinatorDecision.GOAL_MISMATCH: "incomplete",
                    CoordinatorDecision.BLOCKED: "blocked",
                }[coordinator_result.decision]
                if coordinator_result is not None
                else "completed"
            )
            if agent_session.task_id is not None:
                self.store.update_task_workflow_progress(
                    request_id=request.request_id,
                    activity="Coordinator conclusion ready",
                    checkpoint={
                        "phase": "coordinator_result",
                        "outcome_status": outcome_status,
                        "decision": (
                            coordinator_result.decision.value
                            if coordinator_result is not None
                            else CoordinatorDecision.ANSWERED.value
                        ),
                    },
                )
            await self._append_transcript_item(
                client,
                request,
                role="assistant",
                text=final_assistant_text,
            )
            team_provenance = {
                "architecture": "coordinator_owned_expert_runtime",
                "activation_count": len(team_records),
                "routing_decision": {
                    "strategy": (
                        "direct"
                        if not team_records
                        else "single_delegate"
                        if len(team_records) == 1
                        else "team_with_discussion"
                        if any(
                            record.work_order.authority is ChildAuthority.DISCUSSION
                            for record in team_records
                        )
                        else "parallel_team"
                    ),
                    "worker_count": len(team_records),
                    "reason_codes": (
                        list(team_plan.reason_codes)
                        if team_plan is not None
                        else ["lead_direct" if not team_records else "single_bounded_delegation"]
                    ),
                },
                "work_plan": team_plan.model_dump(mode="json") if team_plan is not None else None,
                "actual_usage": {
                    "turns": sum(
                        record.result.usage.turns
                        for record in team_records
                        if record.result is not None
                    ),
                    "tool_calls": sum(
                        record.result.usage.tool_calls
                        for record in team_records
                        if record.result is not None
                    ),
                    "input_tokens": sum(
                        record.result.usage.input_tokens
                        for record in team_records
                        if record.result is not None
                    ),
                    "output_tokens": sum(
                        record.result.usage.output_tokens
                        for record in team_records
                        if record.result is not None
                    ),
                    "wall_seconds_sum": sum(
                        record.result.usage.wall_seconds
                        for record in team_records
                        if record.result is not None
                    ),
                },
                "budget_limits": {
                    "max_input_tokens": sum(
                        record.work_order.budget.max_input_tokens for record in team_records
                    ),
                    "max_output_tokens": sum(
                        record.work_order.budget.max_output_tokens for record in team_records
                    ),
                    "max_tool_calls": sum(
                        record.work_order.budget.max_tool_calls for record in team_records
                    ),
                },
                "code_executions": [
                    {
                        "execution_id": execution.execution_id,
                        "work_order_id": execution.work_order_id,
                        "state": execution.state,
                        "result": execution.result,
                    }
                    for record in team_records
                    for execution in self.store.list_code_executions(
                        record.work_order.work_order_id
                    )
                ],
                "work": [
                    {
                        "work_order": record.work_order.model_dump(mode="json"),
                        "state": record.state.value,
                        "result": record.result.model_dump(mode="json")
                        if record.result is not None
                        else None,
                    }
                    for record in team_records
                ],
                # Scientific discussion is advisory. Human approval is a
                # separate user decision and is never inferred from consulting
                # the Discussion Partner.
                "human_approval_required": False,
            }

            terminal = self._completed_event(
                client,
                request,
                result={
                    "provider_id": agent_session.runtime.provider_id,
                    "model_id": agent_session.runtime.model_id,
                    "assistant_text": self._bounded_text(final_assistant_text, 64_000),
                    "turn_count": turn_count,
                    "tool_call_count": tool_call_count,
                    "usage": {
                        "input_tokens": usage_input_tokens,
                        "output_tokens": usage_output_tokens,
                    },
                    "context_audit_ids": list(context_audit_ids),
                    "team_provenance": team_provenance,
                },
                workspace_revision=self.store.workspace_snapshot(
                    agent_session.workspace_id
                ).revision,
            )
            if agent_session.task_id is None:
                self.store.commit_terminal(request.request_id, terminal)
            else:
                self.store.commit_task_terminal_with_checkpoint(
                    request_id=request.request_id,
                    task_id=agent_session.task_id,
                    terminal_event=terminal,
                    messages=[
                        message.model_dump(mode="json")
                        for message in agent_session.runtime.engine.messages
                    ],
                    provider_id=agent_session.runtime.provider_id,
                    model_id=agent_session.runtime.model_id,
                    runtime_profile_fingerprint=hashlib.sha256(
                        OCEAN_RUNTIME_PROFILE_VERSION.encode("utf-8")
                    ).hexdigest(),
                    system_prompt_fingerprint=hashlib.sha256(
                        agent_session.runtime.base_system_prompt.encode("utf-8")
                    ).hexdigest(),
                    compaction_generation=agent_session.runtime.engine.compaction_generation,
                    usage_summary={
                        "input_tokens": usage_input_tokens,
                        "output_tokens": usage_output_tokens,
                        "turn_count": turn_count,
                        "tool_call_count": tool_call_count,
                    },
                )
            await self._emit_team_snapshot(
                workspace_id=agent_session.workspace_id,
                parent_request_id=request.request_id,
                task_id=agent_session.task_id,
                heartbeat=False,
            )
            await self._broadcast_committed_terminal(client, terminal)
        except _AgentBudgetExceeded as exc:
            if request.request_id not in self._cancelling_agent_requests and not self._closing:
                await self._fail_request(
                    client,
                    request,
                    code="budget_exhausted",
                    message="Ocean agent request reached a configured hard budget",
                    recoverable=True,
                    details={"reason": str(exc)},
                )
        except _AgentToolWaitExceeded as exc:
            if request.request_id not in self._cancelling_agent_requests and not self._closing:
                await self._fail_request(
                    client,
                    request,
                    code="tool_error",
                    message="A background operation stopped reporting progress",
                    recoverable=True,
                    details={
                        "reason": str(exc),
                        "max_tool_wait_seconds": budget.max_tool_wait_seconds,
                        "recovery_state": "incomplete",
                    },
                )
        except GraphRecursionError as exc:
            if request.request_id not in self._cancelling_agent_requests and not self._closing:
                await self._fail_request(
                    client,
                    request,
                    code="budget_exhausted",
                    message="Ocean agent request reached its model-turn budget",
                    recoverable=True,
                    details={"max_turns": exc.max_turns},
                )
        except asyncio.CancelledError:
            if active_budget_reached:
                if request.request_id not in self._cancelling_agent_requests and not self._closing:
                    await self._fail_request(
                        client,
                        request,
                        code="budget_exhausted",
                        message="OceanMind reached its model-active reasoning budget",
                        recoverable=True,
                        details={
                            "max_model_active_seconds": budget.max_wall_seconds,
                            "excluded_time": "tools, code execution, downloads, and delegated agents",
                            "recovery_state": "incomplete",
                        },
                    )
                return
            if request.request_id in self._cancelling_agent_requests or self._closing:
                return
            raise
        except CrashAfterTerminalCommit:
            raise
        except Exception as exc:  # pragma: no cover - final foreground-task boundary
            if request.request_id not in self._cancelling_agent_requests and not self._closing:
                await self._fail_request(
                    client,
                    request,
                    code="model_error",
                    message="Ocean agent foreground task failed",
                    recoverable=True,
                    details={"reason": str(exc)},
                )
        finally:
            budget_monitor.cancel()
            await asyncio.gather(budget_monitor, return_exceptions=True)
            if callable(set_state_hook):
                set_state_hook(None)
            if self.team_orchestrator is not None:
                await self.team_orchestrator.close_request(request.request_id)
            agent_session.runtime.engine.set_max_turns(prior_max_turns)
            self._agent_tasks.pop(request.request_id, None)
            self._agent_request_sessions.pop(request.request_id, None)
            self._agent_request_budgets.pop(request.request_id, None)
            self._agent_request_clients.pop(request.request_id, None)
            self._team_snapshot_revisions.pop(request.request_id, None)

    def _update_task_workflow_for_event(
        self,
        request: SessionSubmitRequest,
        event: StreamEvent,
    ) -> None:
        """Project generic model/tool events onto the durable task lifecycle."""

        if self._task_id(request) is None:
            return
        state: TaskWorkflowState | None = None
        activity: str | None = None
        checkpoint: dict[str, Any] | None = None
        failure_fingerprint: str | None = None
        if isinstance(event, ToolExecutionStarted):
            checkpoint = {
                "tool_name": event.tool_name,
                "tool_call_id": event.tool_call_id,
                "operation_id": event.operation_id,
            }
            if event.tool_name == "ocean_expert_run_code":
                state, activity = "working", "Expert running code"
            elif event.tool_name == "web_search":
                state, activity = "working", "Searching external scientific evidence"
            elif event.tool_name == "jina_reader":
                state, activity = "working", "Reading a selected scientific source"
            elif event.tool_name == "ocean_assign":
                state, activity = "working", "Waiting for Expert results"
        elif isinstance(event, ToolExecutionCompleted):
            checkpoint = {
                "tool_name": event.tool_name,
                "tool_call_id": event.tool_call_id,
                "operation_id": event.operation_id,
                "is_error": event.is_error,
                "duration_seconds": event.duration_seconds,
            }
            if event.is_error:
                state = "working"
                activity = "Expert adjusting its method after a tool failure"
                failure_fingerprint = hashlib.sha256(
                    f"{event.tool_name}\0{event.output}".encode()
                ).hexdigest()
            elif event.tool_name == "ocean_expert_run_code":
                state, activity = "working", "Expert interpreting the code result"
            elif event.tool_name == "web_search":
                state, activity = "working", "Reviewing external search evidence"
            elif event.tool_name == "jina_reader":
                state, activity = "working", "Interpreting the selected paper"
            elif event.tool_name == "ocean_assign":
                state, activity = "working", "Coordinator integrating Expert results"
        elif isinstance(event, AssistantTurnComplete) and not event.message.tool_uses:
            state, activity = "working", "Preparing the final answer"
        if state is not None and activity is not None:
            self.store.transition_task_workflow(
                request_id=request.request_id,
                state=state,
                activity=activity,
                checkpoint=checkpoint,
                failure_fingerprint=failure_fingerprint,
            )

    async def _emit_agent_stream_event(
        self,
        client: BackendClient,
        request: SessionSubmitRequest,
        event: StreamEvent,
    ) -> None:
        """Translate core events without making assistant turn completion terminal."""

        workspace_id = self._workspace_id(request)
        task_id = self._task_id(request)
        if isinstance(event, AssistantTextDelta):
            if event.turn_id is None:
                return
            await self.event_bus.emit_local(
                client,
                AssistantDeltaEvent(
                    **self._event_fields(
                        client,
                        request_id=request.request_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    type="assistant.delta",
                    payload=AssistantDeltaPayload(
                        turn_id=event.turn_id,
                        text=self._bounded_text(event.text, 16_000),
                    ),
                ),
            )
            return
        if isinstance(event, AssistantTurnComplete):
            if event.turn_id is None:
                return
            if event.message.text.strip():
                await self._append_transcript_item(
                    client,
                    request,
                    role="assistant",
                    text=event.message.text,
                    turn_id=event.turn_id,
                )
            await self.event_bus.emit_local(
                client,
                AssistantTurnCompletedEvent(
                    **self._event_fields(
                        client,
                        request_id=request.request_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    type="assistant.turn.completed",
                    payload=AssistantTurnCompletedPayload(
                        turn_id=event.turn_id,
                        text=self._bounded_text(event.message.text, 64_000),
                        tool_call_ids=tuple(item.id for item in event.message.tool_uses),
                        input_tokens=event.usage.input_tokens,
                        output_tokens=event.usage.output_tokens,
                    ),
                ),
            )
            return
        if isinstance(event, ToolExecutionStarted):
            if event.turn_id is None or event.tool_call_id is None:
                return
            await self.event_bus.emit_local(
                client,
                ToolCallStartedEvent(
                    **self._event_fields(
                        client,
                        request_id=request.request_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    type="tool.call.started",
                    payload=ToolCallStartedPayload(
                        turn_id=event.turn_id,
                        tool_call_id=event.tool_call_id,
                        operation_id=event.operation_id,
                        tool_name=event.tool_name,
                        input=event.tool_input,
                    ),
                ),
            )
            return
        if isinstance(event, ToolExecutionCompleted):
            if event.turn_id is None or event.tool_call_id is None:
                return
            output = self._bounded_text(event.output, 64_000)
            activity_only = bool(
                event.metadata is not None and event.metadata.get("display") == "activity"
            )
            await self.event_bus.emit_local(
                client,
                ToolCallCompletedEvent(
                    **self._event_fields(
                        client,
                        request_id=request.request_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    type="tool.call.completed",
                    payload=ToolCallCompletedPayload(
                        turn_id=event.turn_id,
                        tool_call_id=event.tool_call_id,
                        operation_id=event.operation_id,
                        tool_name=event.tool_name,
                        # Tool responses remain available to the model but are not user-chat
                        # content. Ocean tools explicitly mark their detailed contracts private.
                        output="" if activity_only else output,
                        is_error=event.is_error,
                        duration_seconds=event.duration_seconds,
                        metadata=event.metadata,
                    ),
                ),
            )
            return
        if isinstance(event, CompactProgressEvent):
            await self.event_bus.emit_local(
                client,
                ContextCompactionProgressEvent(
                    **self._event_fields(
                        client,
                        request_id=request.request_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    type="context.compaction.progress",
                    payload=ContextCompactionProgressPayload(
                        phase=event.phase,
                        trigger=event.trigger,
                        message=event.message,
                        attempt=event.attempt,
                        checkpoint=event.checkpoint,
                        metadata=event.metadata,
                    ),
                ),
            )
            return
        if isinstance(event, (StatusEvent, ErrorEvent)):
            await self._append_transcript_item(
                client,
                request,
                role="system",
                text=self._bounded_text(event.message, 64_000),
            )

    async def _append_transcript_item(
        self,
        client: BackendClient,
        request: SessionSubmitRequest,
        *,
        role: Literal["user", "assistant", "tool", "system"],
        text: str,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        item_id = f"tr_{uuid4().hex}"
        task_id = self._task_id(request)
        if task_id is not None:
            self.store.append_task_transcript_item(
                task_id=task_id,
                item_id=item_id,
                role=role,
                text=self._bounded_text(text, 64_000),
                request_id=request.request_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            )
        event = TranscriptItemAppendedEvent(
            **self._event_fields(
                client,
                request_id=request.request_id,
                workspace_id=self._workspace_id(request),
                task_id=task_id,
            ),
            type="transcript.item.appended",
            payload=TranscriptItemAppendedPayload(
                item_id=item_id,
                role=role,
                text=self._bounded_text(text, 64_000),
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            ),
        )
        await self.event_bus.emit_local(client, event)

    async def _cancel_agent_request(
        self,
        client: BackendClient,
        *,
        cancel_request: RequestEnvelope,
        target_request_id: str,
        reason: str,
    ) -> None:
        """Stop the model loop before committing both durable cancellation terminals."""

        target = self.store.get_request(target_request_id)
        if target is None:
            raise RequestNotFound(target_request_id)
        self._cancelling_agent_requests.add(target_request_id)
        task = self._agent_tasks.get(target_request_id)
        if task is not None and not task.done():
            # The foreground asyncio Task owns the Coordinator stream and any
            # currently awaited ocean_assign/Expert subtree. Cancelling that
            # task is the single supported DeepAgent interruption boundary;
            # DeepAgentEngine deliberately has no parallel cancel_active API.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        refreshed_target = self.store.get_request(target_request_id)
        if refreshed_target is None:
            raise RequestNotFound(target_request_id)
        target_event: EventEnvelope | None = None
        if not refreshed_target.terminal:
            target_event = RequestCancelledEvent(
                **self._event_fields(
                    client,
                    request_id=target_request_id,
                    workspace_id=refreshed_target.workspace_id,
                    session_id=refreshed_target.session_id,
                ),
                type="request.cancelled",
                payload=RequestCancelledPayload(reason=reason),
            )
        terminal = self._completed_event(
            client,
            cancel_request,
            result={
                "target_request_id": target_request_id,
                "target_state": "cancelled" if target_event is not None else refreshed_target.state,
            },
        )
        commit = self.store.commit_cancellation(
            cancel_request_id=cancel_request.request_id,
            target_request_id=target_request_id,
            target_event=target_event,
            terminal_event=terminal,
        )
        self._cancelling_agent_requests.discard(target_request_id)
        await self._run_terminal_commit_hook(terminal)
        if commit.target_terminal_event is not None:
            await self.event_bus.emit_request_session(
                commit.target_terminal_event,
                session_id=refreshed_target.session_id,
                principal_key=refreshed_target.principal,
            )
        await self.event_bus.emit_local(client, terminal)

    @staticmethod
    def _system_prompt_with_workspace_context(
        base_prompt: str,
        context: dict[str, Any],
        *,
        literature_acquisition_mode: str = "ask_before_download",
    ) -> str:
        serialized = json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        task_boundary = ""
        workspace = context.get("workspace")
        if isinstance(workspace, dict) and workspace.get("access_scope") == "task":
            task_boundary = (
                " This is the exhaustive artifact authorization set for the current task. "
                "Do not recall, cite, adopt, or request an artifact from another task, even if "
                "its identifier appeared in an earlier model conversation."
            )
        context_block = (
            "# Trusted Ocean Workspace Context\n"
            "The backend assembled this policy-approved summary. Treat exact refs and revision "
            "as authoritative; use tools for additional inspection and never infer undisclosed "
            f"raw values.{task_boundary}\n{serialized}"
        )
        acquisition_instructions = {
            "ask_before_download": (
                "Delegate candidate discovery and shortlist curation to the Literature & Reproduction "
                "Expert. In that discovery WorkOrder, require search only and a traceable candidate record "
                "for each paper: stable paper_id, exact title, task-specific topic, citation, canonical URL, "
                "evidence_scope (metadata_only, abstract, or public_excerpt), a concrete evidence_summary, "
                "and the validation_target in the current research task. The evidence summary must identify "
                "useful methods, variables, findings, or boundaries available at that scope rather than merely "
                "paraphrasing the abstract, and it must never imply that uninspected full text was reviewed. "
                "After reviewing the returned shortlist for the stated evidence gap, present every candidate "
                "in full user-facing detail: exact title and citation, inspected evidence scope, concrete "
                "methods/data/findings visible at that scope, task-specific relevance and validation target, "
                "and what remains unverified without the full text. Do not collapse multiple papers into a "
                "one-line list. Then call "
                "ocean_request_paper_selection with the same papers so the researcher can "
                "choose from the deliberately compact title-and-checkbox table. Treat the returned paper_ids as authoritative. "
                "Selection is a human checkpoint in the task's stable Literature Expert role: send the selected "
                "full texts to that role's next round so it can continue from its durable search context and "
                "produce the requested viewpoint. Coordinator web "
                "lookup may orient the task but must not replace scholarly "
                "discovery, silently replace the Expert's shortlist, or replace the selection table with a "
                "free-form question."
            ),
            "auto_download_open_access": (
                "Delegate literature discovery, shortlist curation, and review to the Literature & "
                "Reproduction Expert. Open-access full texts may be acquired automatically. Ask the user "
                "before any source that requires credentials, payment, or a user-provided file."
            ),
            "search_only": (
                "Delegate search and candidate curation to the Literature & Reproduction Expert, then "
                "present its traceable shortlist. Do not download, import, or read full texts."
            ),
        }
        acquisition_block = (
            "# Literature Acquisition Preference\n"
            f"Mode: {literature_acquisition_mode}. "
            f"{acquisition_instructions.get(literature_acquisition_mode, acquisition_instructions['ask_before_download'])} "
            "This preference controls acquisition only; it does not lower evidence standards. "
            "Always distinguish discovered metadata, abstract-only evidence, and reviewed full text."
        )
        return "\n\n".join(
            (
                base_prompt,
                context_block,
                acquisition_block,
            )
        )

    @staticmethod
    def _operation_id(request_id: str, turn_id: str, tool_call_id: str) -> str:
        material = "\x1f".join((request_id, turn_id, tool_call_id)).encode("utf-8")
        return f"op_{hashlib.sha256(material).hexdigest()}"

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 3)] + "..."

    def _team_snapshot_payload(
        self,
        *,
        workspace_id: str,
        parent_request_id: str,
        revision: int,
    ) -> TeamSnapshotPayload:
        """Project durable WorkOrders into a transport-safe collaboration topology."""

        records = self.store.list_team_work(
            workspace_id=workspace_id,
            parent_request_id=parent_request_id,
        )
        request_record = self.store.get_request(parent_request_id)
        workflow = self.store.get_task_workflow(parent_request_id)
        coordinator_result = self.store.get_coordinator_result(parent_request_id)
        active_plan = (
            self.team_orchestrator.plan_for(parent_request_id)
            if self.team_orchestrator is not None
            else None
        )
        # Durable records contain every successive Coordinator wave.  The
        # in-memory plan is only the most recent wave and must not hide agents
        # consulted earlier in the same request.
        raw_orders = [record.work_order for record in records]
        records_by_order_id = {record.work_order.work_order_id: record for record in records}

        canonical_order_ids: dict[str, str] = {}
        visible_orders: list[WorkOrder] = []
        visible_records: dict[str, TeamWorkRecord | None] = {}
        visible_index: dict[str, int] = {}

        def logical_key(order: WorkOrder) -> str:
            if order.profile_id is not None and order.expert_key is not None:
                return (
                    f"instance:{order.authority.value}:{order.profile_id}:"
                    f"{order.expert_key}"
                )
            if order.profile_id is not None:
                # Preserve the legacy/default singleton visual identity.
                return f"role:{order.authority.value}:{order.profile_id}"
            if order.job_key is not None:
                return f"job:{order.job_key}"
            return f"work:{order.work_order_id}"

        for order in raw_orders:
            key = logical_key(order)
            record = records_by_order_id.get(order.work_order_id)
            index = visible_index.get(key)
            if index is None:
                visible_index[key] = len(visible_orders)
                visible_orders.append(order)
                canonical_id = order.job_key or order.work_order_id
                canonical_order_ids[order.work_order_id] = canonical_id
                visible_records[canonical_id] = record
                continue
            canonical_id = canonical_order_ids[visible_orders[index].work_order_id]
            canonical_order_ids[order.work_order_id] = canonical_id
            # Records are durable round history in creation order. One explicit
            # Expert instance has one Canvas node; unkeyed legacy role rounds are
            # still folded into their default singleton. The newest round wins.
            visible_orders[index] = order
            visible_records[canonical_id] = record

        orders = visible_orders
        strategy: Literal["direct", "single_delegate", "parallel_team", "team_with_discussion"]
        if not orders:
            strategy = "direct"
        elif len(orders) == 1:
            strategy = "single_delegate"
        elif any(order.authority is ChildAuthority.DISCUSSION for order in orders):
            strategy = "team_with_discussion"
        else:
            strategy = "parallel_team"

        active_states = {
            WorkStatus.QUEUED,
            WorkStatus.RUNNING,
        }
        projected_records = [record for record in visible_records.values() if record is not None]
        checkpoint_status = (
            str(workflow.checkpoint.get("outcome_status", "")) if workflow is not None else ""
        )
        # The request record remains the commit point for user-visible terminal
        # state. A durable Coordinator receipt can finish an interrupted commit
        # during startup, but it must not make the canvas claim completion before
        # that request settlement occurs.
        if request_record is not None and request_record.state == "completed":
            if checkpoint_status in {
                "completed",
                "incomplete",
                "blocked",
                "failed",
            }:
                team_status = checkpoint_status
            elif coordinator_result is not None:
                team_status = {
                    CoordinatorDecision.ANSWERED: "completed",
                    CoordinatorDecision.INSUFFICIENT_EVIDENCE: "incomplete",
                    CoordinatorDecision.GOAL_MISMATCH: "incomplete",
                    CoordinatorDecision.BLOCKED: "blocked",
                }[coordinator_result.decision]
            else:
                team_status = "completed"
        elif request_record is not None and request_record.state in {
            "failed",
            "cancelled",
            "interrupted",
        }:
            if workflow is not None and workflow.state == "incomplete":
                team_status = "incomplete"
            else:
                team_status = "failed"
        elif request_record is None:
            # Defensive fallback for imported legacy snapshots that predate
            # durable request records.  New requests always take the branch
            # above and therefore have one authoritative terminal source.
            if workflow is not None and workflow.state == "completed":
                team_status = "completed"
            elif workflow is not None and workflow.state == "incomplete":
                team_status = "incomplete"
            elif workflow is not None and workflow.state in {"failed", "cancelled"}:
                team_status = "failed"
            else:
                team_status = "working"
        else:
            team_status = "working"

        if team_status != "working":
            coordinator_status = team_status
            coordinator_activity = {
                "completed": "Task completed",
                "incomplete": "Concluded with explicitly limited evidence",
                "blocked": "Concluded that progress is blocked",
                "failed": "Task stopped before a reliable conclusion",
            }[team_status]
        elif workflow is not None and workflow.state == "completed":
            coordinator_status = "completed"
            coordinator_activity = workflow.activity or "Request completed"
        elif not orders:
            coordinator_status = "working"
            coordinator_activity = "Assessing the task and selecting the smallest sufficient team"
        elif any(record.state in active_states for record in projected_records):
            active_count = sum(record.state in active_states for record in projected_records)
            coordinator_status = "working"
            coordinator_activity = f"Coordinating {active_count} active team member(s)"
        else:
            coordinator_status = "working"
            coordinator_activity = "Synthesizing Expert results and deciding the next step"

        agents: list[TeamAgentPayload] = [
            TeamAgentPayload(
                agent_id="coordinator",
                profile_id=None,
                semantic_role="Coordinator",
                authority="coordinator",
                status=coordinator_status,
                activity=coordinator_activity,
            )
        ]
        dependencies: list[TeamDependencyPayload] = []
        interactions: list[TeamInteractionPayload] = []
        dependency_keys: set[tuple[str, str, str]] = set()
        for order in orders:
            agent_id = canonical_order_ids[order.work_order_id]
            record = visible_records.get(agent_id)
            if record is None:
                status = "planning"
                activity = "Queued by Coordinator"
            elif record.state is WorkStatus.RUNNING:
                status = "discussing" if order.authority is ChildAuthority.DISCUSSION else "working"
                if order.authority is ChildAuthority.DISCUSSION:
                    activity = "Discussing hypotheses, mechanisms, and alternatives"
                elif record.checkpoint.phase in {
                    WorkstreamPhase.RESULT_READY,
                    WorkstreamPhase.DELIVERING,
                }:
                    activity = "Delivering the saved result"
                else:
                    activity = "Investigating the assigned question"
            elif record.state is WorkStatus.INCOMPLETE:
                if (
                    record.result is not None
                    and record.result.expert_decision is ExpertDecision.BLOCKED
                ):
                    status = "blocked"
                    activity = "Reported a material blocker to the Coordinator"
                else:
                    status = "incomplete"
                    activity = "Returned a useful but incomplete result"
            elif record.state is WorkStatus.COMPLETED:
                # The returned candidate closes this assignment round.  The
                # logical Expert session remains available to the Coordinator
                # until the foreground request itself reaches a terminal state.
                status = "waiting" if team_status == "working" else "completed"
                activity = (
                    "Waiting for Coordinator feedback"
                    if team_status == "working"
                    else (
                        record.result.text
                        if record.result is not None and record.result.text
                        else "Expert session completed"
                    )
                )
            elif record.state is WorkStatus.SKIPPED:
                status = "skipped"
                activity = "Skipped because a dependency did not complete"
            elif record.state in {WorkStatus.FAILED, WorkStatus.CANCELLED}:
                status = "failed"
                activity = "Expert workstream stopped"
            else:
                status = "waiting"
                activity = "Waiting"

            authority = order.authority.value
            agents.append(
                TeamAgentPayload(
                    agent_id=agent_id,
                    profile_id=order.profile_id,
                    expert_key=order.expert_key,
                    semantic_role=order.semantic_role,
                    authority=authority,
                    status=status,
                    activity=self._bounded_text(activity, 512),
                    work_order_id=(
                        record.work_order.work_order_id
                        if record is not None
                        else order.work_order_id
                    ),
                    task_goal=order.task_goal,
                    result_summary=(
                        record.result.text
                        if record is not None and record.result is not None and record.result.text
                        else None
                    ),
                    limitations=(
                        record.result.limitations
                        if record is not None and record.result is not None
                        else ()
                    ),
                )
            )
            from_agent_id = "coordinator"
            key = (from_agent_id, agent_id, "delegation")
            if key not in dependency_keys:
                dependency_keys.add(key)
                dependencies.append(
                    TeamDependencyPayload(
                        from_agent_id=from_agent_id,
                        to_agent_id=agent_id,
                        kind="delegation",
                    )
                )
            if status in {"working", "discussing"}:
                interaction_from = "coordinator"
                interactions.append(
                    TeamInteractionPayload(
                        interaction_id=f"interaction_{agent_id}_{revision}",
                        from_agent_id=interaction_from,
                        to_agent_id=agent_id,
                        kind=(
                            "handoff"
                            if order.authority is ChildAuthority.DISCUSSION
                            else "delegation"
                        ),
                        summary=activity,
                        state="active",
                    )
                )

        latest_record_by_todo: dict[str, TeamWorkRecord] = {}
        for record in records:
            todo_id = record.work_order.todo_id
            if todo_id is not None:
                latest_record_by_todo[todo_id] = record

        if active_plan is not None:
            todo_definitions = active_plan.todos
        else:
            # Terminal and legacy snapshots may outlive the request-scoped
            # in-memory plan. They can still show every durable dispatched item,
            # but only an active plan knows about not-yet-dispatched todos.
            todo_definitions = tuple(
                CoordinatorTodo(
                    todo_id=todo_id,
                    question=record.work_order.task_goal,
                    depends_on=record.work_order.depends_on,
                    profile_id=record.work_order.profile_id or "legacy_expert",
                    expert_key=record.work_order.expert_key,
                    expected_outputs=record.work_order.outcome_intents,
                )
                for todo_id, record in latest_record_by_todo.items()
            )

        todos: list[TeamTodoPayload] = []
        for todo in todo_definitions:
            record = latest_record_by_todo.get(todo.todo_id)
            if record is None:
                progress_state = (
                    "queued"
                    if active_plan is not None and todo.todo_id in active_plan.dispatch
                    else "pending"
                )
            elif record.state is WorkStatus.QUEUED:
                progress_state = "queued"
            elif record.state is WorkStatus.RUNNING:
                progress_state = "working"
            elif record.state is WorkStatus.SKIPPED:
                progress_state = "skipped"
            elif (
                record.result is not None
                and record.result.result_origin is ExpertResultOrigin.AGENT_SUBMITTED
            ):
                progress_state = "result_returned"
            else:
                progress_state = "stopped"
            todos.append(
                TeamTodoPayload(
                    todo_id=todo.todo_id,
                    question=todo.question,
                    depends_on=todo.depends_on,
                    profile_id=todo.profile_id,
                    expert_key=todo.expert_key,
                    expected_outputs=todo.expected_outputs,
                    state=progress_state,
                    work_order_id=(record.work_order.work_order_id if record is not None else None),
                    session_round=(record.work_order.session_round if record is not None else None),
                    result_summary=(
                        self._bounded_text(record.result.text, 8_000)
                        if record is not None and record.result is not None and record.result.text
                        else None
                    ),
                )
            )

        return TeamSnapshotPayload(
            request_id=parent_request_id,
            revision=max(1, revision),
            status=team_status,
            strategy=strategy,
            role_pool=tuple(
                TeamAgentProfilePayload(
                    profile_id=profile.profile_id,
                    display_name=profile.display_name,
                    authority=profile.authority.value,
                    category=profile.category,
                    summary=profile.summary,
                )
                for profile in AGENT_PROFILES
            ),
            agents=tuple(agents),
            todos=tuple(todos),
            dependencies=tuple(dependencies),
            interactions=tuple(interactions),
        )

    async def _emit_team_snapshot(
        self,
        *,
        workspace_id: str,
        parent_request_id: str,
        task_id: str | None,
        heartbeat: bool = True,
    ) -> None:
        client = self._agent_request_clients.get(parent_request_id)
        if client is None:
            return
        if heartbeat:
            self.store.update_task_workflow_progress(
                request_id=parent_request_id,
                activity="Coordinating active team members",
                checkpoint={"phase": "team_work", "task_id": task_id},
            )
        revision = self._team_snapshot_revisions.get(parent_request_id, 0) + 1
        self._team_snapshot_revisions[parent_request_id] = revision
        await self.event_bus.emit_local(
            client,
            TeamSnapshotEvent(
                **self._event_fields(
                    client,
                    request_id=parent_request_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                ),
                type="team.snapshot",
                payload=self._team_snapshot_payload(
                    workspace_id=workspace_id,
                    parent_request_id=parent_request_id,
                    revision=revision,
                ),
            ),
        )

    @classmethod
    def _team_work_activity_summary(
        cls,
        record: TeamWorkRecord,
        *,
        todo_id: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded execution state for UI activity, not model-facing evidence."""

        return {
            "todo_id": todo_id,
            "work_order_id": record.work_order.work_order_id,
            "semantic_role": record.work_order.semantic_role,
            "authority": record.work_order.authority.value,
            "state": record.state.value,
            "result": (
                {
                    "status": record.result.status.value,
                    "failure_code": (
                        record.result.failure_code.value
                        if record.result.failure_code is not None
                        else None
                    ),
                    "error": (
                        cls._bounded_text(record.result.error, 320)
                        if record.result.error is not None
                        else None
                    ),
                }
                if record.result is not None
                else None
            ),
        }

    async def _session_open(self, client: BackendClient, request: SessionOpenRequest) -> None:
        terminal = self._completed_event(
            client,
            request,
            result={
                "session_id": client.session_id,
                "workspace_id": request.payload.requested_workspace_id
                or (request.context.workspace_id if request.context else None),
            },
        )
        self.store.commit_session_open(
            request_id=request.request_id,
            session_id=client.session_id or "",
            principal=client.principal.key if client.principal else "",
            workspace_id=request.payload.requested_workspace_id
            or (request.context.workspace_id if request.context else None),
            terminal_event=terminal,
        )
        await self._broadcast_committed_terminal(client, terminal)

    async def _workspace_open(self, client: BackendClient, request: WorkspaceOpenRequest) -> None:
        workspace_id = self._workspace_id(request)

        def event_factory(
            snapshot: WorkspaceSnapshot,
            previous_revision: int,
            change: str | None,
        ) -> tuple[EventEnvelope | None, EventEnvelope]:
            changed_event: EventEnvelope | None = None
            if change is not None:
                changed_event = WorkspaceChangedEvent(
                    **self._event_fields(
                        client, request_id=request.request_id, workspace_id=workspace_id
                    ),
                    type="workspace.changed",
                    payload=WorkspaceChangedPayload(
                        previous_revision=previous_revision,
                        workspace_revision=snapshot.revision,
                        change=change,
                    ),
                )
            return changed_event, self._completed_event(
                client,
                request,
                result=snapshot.as_payload(),
                workspace_revision=snapshot.revision,
            )

        commit = self.store.commit_workspace_open(
            request_id=request.request_id,
            workspace_id=workspace_id,
            path=request.payload.path,
            expected_revision=request.expected_workspace_revision,
            event_factory=event_factory,
        )
        await self._run_terminal_commit_hook(commit.terminal_event)
        if commit.changed_event is not None:
            await self.event_bus.emit_workspace(commit.changed_event)
        await self.event_bus.emit_local(client, commit.terminal_event)

    async def _workspace_snapshot(
        self,
        client: BackendClient,
        request: WorkspaceSnapshotGetRequest,
    ) -> None:
        snapshot = self.store.workspace_snapshot(self._workspace_id(request))
        snapshot_event = WorkspaceSnapshotEvent(
            **self._event_fields(
                client, request_id=request.request_id, workspace_id=snapshot.workspace_id
            ),
            type="workspace.snapshot",
            payload=WorkspaceSnapshotPayload(**snapshot.as_payload()),
        )
        terminal = self._completed_event(
            client,
            request,
            result=snapshot.as_payload(),
            workspace_revision=snapshot.revision,
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._run_terminal_commit_hook(terminal)
        await self.event_bus.emit_local(client, snapshot_event)
        await self.event_bus.emit_local(client, terminal)

    async def _task_create(self, client: BackendClient, request: TaskCreateRequest) -> None:
        task = self.store.create_research_task(
            workspace_id=self._workspace_id(request), title=request.payload.title
        )
        await self._sync_task_workspace(task.task_id)
        terminal = self._completed_event(client, request, result={"task": task.as_summary()})
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_list(self, client: BackendClient, request: TaskListRequest) -> None:
        tasks = self.store.list_research_tasks(
            workspace_id=self._workspace_id(request),
            include_archived=request.payload.include_archived,
            limit=request.payload.limit,
        )
        terminal = self._completed_event(
            client, request, result={"tasks": [task.as_summary() for task in tasks]}
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_get_or_open(self, client: BackendClient, request: TaskGetRequest) -> None:
        task = self._task_in_workspace(request.payload.task_id, request)
        if request.type == "task.open":
            # The canonical Task can predate user-visible task folders.  Reconcile
            # that lightweight projection before returning the snapshot; this creates
            # the stable folder/manifest only and never inspects source payloads.
            await self._ensure_task_workspace_root(task.task_id)
            await self._emit_task_snapshot(client, request, task_id=task.task_id)
            return
        terminal = self._completed_event(client, request, result={"task": task.as_summary()})
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_rename(self, client: BackendClient, request: TaskRenameRequest) -> None:
        self._task_in_workspace(request.payload.task_id, request)
        task = self.store.rename_research_task(
            task_id=request.payload.task_id,
            title=request.payload.title,
            expected_task_revision=request.expected_task_revision,
        )
        await self._sync_task_workspace(task.task_id)
        terminal = self._completed_event(client, request, result={"task": task.as_summary()})
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_archive_or_reopen(
        self, client: BackendClient, request: TaskArchiveRequest
    ) -> None:
        self._task_in_workspace(request.payload.task_id, request)
        task = self.store.set_research_task_status(
            task_id=request.payload.task_id,
            status="archived" if request.type == "task.archive" else "active",
            expected_task_revision=request.expected_task_revision,
        )
        await self._sync_task_workspace(task.task_id)
        terminal = self._completed_event(client, request, result={"task": task.as_summary()})
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_delete(self, client: BackendClient, request: TaskDeleteRequest) -> None:
        self._task_in_workspace(request.payload.task_id, request)
        self.store.delete_research_task(
            task_id=request.payload.task_id,
            expected_task_revision=request.expected_task_revision,
            in_flight_request_id=request.request_id,
        )
        terminal = self._completed_event(
            client, request, result={"task_id": request.payload.task_id}
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _task_snapshot(self, client: BackendClient, request: TaskSnapshotGetRequest) -> None:
        self._task_in_workspace(request.payload.task_id, request)
        await self._emit_task_snapshot(client, request, task_id=request.payload.task_id)

    async def _task_agent_transcript(
        self,
        client: BackendClient,
        request: TaskAgentTranscriptGetRequest,
    ) -> None:
        """Return one selected participant's durable conversation on demand."""

        workspace_id = self._workspace_id(request)
        task_id = request.payload.task_id
        self._task_in_workspace(task_id, request)
        parent = self.store.get_request(request.payload.parent_request_id)
        if (
            parent is None
            or parent.workspace_id != workspace_id
            or parent.task_id != task_id
            or parent.request_type != "session.submit"
        ):
            raise RequestStoreError("Agent transcript parent request is not part of this task")

        if request.payload.agent_id == "coordinator":
            coordinator_items = self.store.list_task_transcript_for_request(
                task_id=task_id,
                request_id=request.payload.parent_request_id,
            )
            messages = [
                {
                    "message_id": item.item_id,
                    "role": "coordinator" if item.role == "assistant" else item.role,
                    "blocks": [{"type": "text", "text": item.text}],
                    "created_at": item.created_at,
                    "interrupted": item.interrupted,
                }
                for item in coordinator_items
            ]
            result = {
                "agent_id": "coordinator",
                "work_order_id": None,
                "messages": messages,
                "compaction_generation": 0,
                "updated_at": messages[-1]["created_at"] if messages else None,
            }
        else:
            work_order_id = request.payload.work_order_id
            if not work_order_id:
                raise RequestStoreError("Expert transcript requires a work_order_id")
            record = self.store.get_team_work(work_order_id)
            if record is None:
                raise RequestStoreError("Selected Expert work order was not found")
            order = record.work_order
            visible_agent_id = order.job_key or order.work_order_id
            if (
                record.workspace_id != workspace_id
                or order.task_id != task_id
                or order.parent_request_id != request.payload.parent_request_id
                or visible_agent_id != request.payload.agent_id
            ):
                raise RequestStoreError("Selected Expert does not belong to this task request")
            participant_key = order.profile_id or (f"{order.authority.value}:{order.semantic_role}")
            checkpoint = self.store.get_expert_session_checkpoint(
                workspace_id=workspace_id,
                task_scope=task_id,
                participant_key=participant_key,
                job_key=order.job_key or order.work_order_id,
            )
            history = self.store.get_expert_session_message_history(
                workspace_id=workspace_id,
                task_scope=task_id,
                participant_key=participant_key,
                job_key=order.job_key or order.work_order_id,
            )
            result = {
                "agent_id": visible_agent_id,
                "work_order_id": work_order_id,
                "messages": self._renderer_agent_messages(
                    history if history else checkpoint.messages if checkpoint is not None else ()
                ),
                "compaction_generation": (
                    checkpoint.compaction_generation if checkpoint is not None else 0
                ),
                "updated_at": checkpoint.updated_at if checkpoint is not None else None,
            }

        terminal = self._completed_event(client, request, result=result)
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    @staticmethod
    def _renderer_agent_messages(
        messages: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        """Remove binary image bodies while preserving every conversational block."""

        rendered: list[dict[str, Any]] = []
        for message_index, message in enumerate(messages):
            role = "coordinator" if message.get("role") == "user" else "expert"
            blocks: list[dict[str, Any]] = []
            raw_blocks = message.get("content")
            if not isinstance(raw_blocks, list):
                raw_blocks = []
            for block in raw_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))})
                elif block_type == "tool_use":
                    blocks.append(
                        {
                            "type": "tool_call",
                            "tool_call_id": str(block.get("id", "")),
                            "tool_name": str(block.get("name", "Tool")),
                            "input": block.get("input")
                            if isinstance(block.get("input"), dict)
                            else {},
                        }
                    )
                elif block_type == "tool_result":
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_call_id": str(block.get("tool_use_id", "")),
                            "text": str(block.get("content", "")),
                            "is_error": bool(block.get("is_error", False)),
                        }
                    )
                elif block_type == "image":
                    blocks.append(
                        {
                            "type": "attachment",
                            "media_type": str(block.get("media_type", "image")),
                            "source_path": str(block.get("source_path", "")),
                        }
                    )
            rendered.append(
                {
                    "message_id": f"agent-message-{message_index + 1}",
                    "role": role,
                    "blocks": blocks,
                }
            )
        return rendered

    async def _task_output_list(
        self, client: BackendClient, request: TaskOutputListRequest
    ) -> None:
        self._task_in_workspace(request.payload.task_id, request)
        outputs = self.store.list_task_output_summaries(
            task_id=request.payload.task_id, limit=request.payload.limit
        )
        from ocean_partner.delivery import build_delivery_manifests

        terminal = self._completed_event(
            client,
            request,
            result={
                "outputs": outputs,
                "delivery_manifests": [
                    manifest.model_dump(mode="json")
                    for manifest in build_delivery_manifests(outputs)
                ],
                "task_results": (
                    list(self.task_results.list_payloads(task_id=request.payload.task_id))
                    if self.task_results is not None
                    else []
                ),
            },
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _emit_task_snapshot(
        self, client: BackendClient, request: RequestEnvelope, *, task_id: str
    ) -> None:
        # Snapshot reads do not project the filesystem.  ``task.open`` performs one
        # bounded reconciliation immediately before entering this method so legacy
        # canonical Tasks still acquire their stable user-visible folder.
        if isinstance(request, TaskSnapshotGetRequest):
            limit = request.payload.transcript_limit
            before_sequence = request.payload.before_sequence
        else:
            limit = 100
            before_sequence = None
        snapshot = self.store.task_snapshot(
            task_id=task_id,
            transcript_limit=limit,
            before_sequence=before_sequence,
        )
        payload_data = snapshot.as_payload()
        # Keep interactive navigation latency independent of the number of
        # durable result manifests. The desktop asks for ``task.output.list``
        # immediately after rendering ``task.open``; explicit snapshot reads
        # retain their complete payload for protocol compatibility.
        payload_data["task_results"] = (
            list(self.task_results.list_payloads(task_id=task_id))
            if isinstance(request, TaskSnapshotGetRequest) and self.task_results is not None
            else []
        )
        team_request_id = snapshot.task.active_request_id or (
            snapshot.workflow.request_id if snapshot.workflow is not None else None
        )
        visible_request_ids = list(
            dict.fromkeys(
                item.request_id
                for item in snapshot.transcript
                if item.request_id is not None
            )
        )
        team_request_ids = {
            record.work_order.parent_request_id
            for record in self.store.list_task_team_work(
                workspace_id=snapshot.task.workspace_id,
                task_id=task_id,
            )
        }
        payload_data["team_snapshots"] = [
            self._team_snapshot_payload(
                workspace_id=snapshot.task.workspace_id,
                parent_request_id=request_id,
                revision=self._team_snapshot_revisions.get(request_id, 1),
            ).model_dump(mode="json")
            for request_id in visible_request_ids
            if request_id in team_request_ids
        ]
        if team_request_id is not None:
            payload_data["team_snapshot"] = self._team_snapshot_payload(
                workspace_id=snapshot.task.workspace_id,
                parent_request_id=team_request_id,
                revision=self._team_snapshot_revisions.get(team_request_id, 1),
            ).model_dump(mode="json")
        payload = TaskSnapshotPayload.model_validate(payload_data)
        snapshot_event = TaskSnapshotEvent(
            **self._event_fields(
                client,
                request_id=request.request_id,
                workspace_id=snapshot.task.workspace_id,
                task_id=task_id,
            ),
            type="task.snapshot",
            payload=payload,
        )
        terminal = self._completed_event(client, request, result=payload.model_dump(mode="json"))
        self.store.commit_terminal(request.request_id, terminal)
        await self._run_terminal_commit_hook(terminal)
        await self.event_bus.emit_local(client, snapshot_event)
        await self.event_bus.emit_local(client, terminal)

    async def _request_status(
        self, client: BackendClient, request: RequestStatusGetRequest
    ) -> None:
        target = self.store.get_request(request.payload.target_request_id)
        if target is None or client.principal is None or target.principal != client.principal.key:
            raise RequestNotFound(request.payload.target_request_id)
        result: dict[str, Any] = {
            "request_id": target.request_id,
            "request_type": target.request_type,
            "state": target.state,
        }
        if target.terminal_event is not None:
            result["terminal_event"] = target.terminal_event.model_dump(mode="json")
        terminal = self._completed_event(client, request, result=result)
        self.store.commit_terminal(request.request_id, terminal)
        await self._broadcast_committed_terminal(client, terminal)

    async def _request_cancel(self, client: BackendClient, request: RequestCancelRequest) -> None:
        target = self.store.get_request(request.payload.target_request_id)
        if target is None or client.principal is None or target.principal != client.principal.key:
            raise RequestNotFound(request.payload.target_request_id)
        if request.payload.target_request_id in self._agent_tasks:
            await self._cancel_agent_request(
                client,
                cancel_request=request,
                target_request_id=request.payload.target_request_id,
                reason=request.payload.reason or "Cancelled by user",
            )
            return
        target_event: EventEnvelope | None = None
        if not target.terminal:
            target_event = RequestCancelledEvent(
                **self._event_fields(
                    client,
                    request_id=target.request_id,
                    workspace_id=target.workspace_id,
                    session_id=target.session_id,
                    task_id=target.task_id,
                ),
                type="request.cancelled",
                payload=RequestCancelledPayload(
                    reason=request.payload.reason or "Cancelled by user"
                ),
            )
        terminal = self._completed_event(
            client,
            request,
            result={"target_request_id": target.request_id, "target_state": "cancelled"},
        )
        commit = self.store.commit_cancellation(
            cancel_request_id=request.request_id,
            target_request_id=target.request_id,
            target_event=target_event,
            terminal_event=terminal,
        )
        await self._run_terminal_commit_hook(terminal)
        if commit.target_terminal_event is not None:
            await self.event_bus.emit_request_session(
                commit.target_terminal_event,
                session_id=target.session_id,
                principal_key=target.principal,
            )
        await self.event_bus.emit_local(client, terminal)

    async def _system_shutdown(self, client: BackendClient, request: SystemShutdownRequest) -> None:
        terminal = self._completed_event(client, request, result={"shutdown": "requested"})
        shutdown = SystemShutdownEvent(
            **self._event_fields(client, request_id=request.request_id),
            type="system.shutdown",
            payload=SystemShutdownPayload(reason=request.payload.reason),
        )
        self.store.commit_terminal(request.request_id, terminal)
        await self._run_terminal_commit_hook(terminal)
        await self.event_bus.emit_local(client, terminal)
        await self.event_bus.emit_system(shutdown)
        self.shutdown_requested = True

    async def _fail_request(
        self,
        client: BackendClient,
        request: RequestEnvelope,
        *,
        code: ErrorCode,
        message: str,
        recoverable: bool,
        details: dict[str, Any],
    ) -> None:
        event = self._failed_event(
            client,
            request,
            code=code,
            message=message,
            recoverable=recoverable,
            details=details,
        )
        record = self.store.get_request(request.request_id)
        if record is not None and not record.terminal:
            if isinstance(request, SessionSubmitRequest) and record.task_id is not None:
                self.store.commit_task_terminal_without_checkpoint(
                    request_id=request.request_id, terminal_event=event
                )
            else:
                self.store.commit_terminal(request.request_id, event)
            if isinstance(request, SessionSubmitRequest):
                await self._emit_team_snapshot(
                    workspace_id=record.workspace_id,
                    parent_request_id=request.request_id,
                    task_id=record.task_id,
                    heartbeat=False,
                )
            await self._broadcast_committed_terminal(client, event)
        if isinstance(request, SessionSubmitRequest):
            self._agent_request_budgets.pop(request.request_id, None)

    async def _broadcast_committed_terminal(
        self,
        client: BackendClient,
        event: EventEnvelope,
    ) -> None:
        await self._run_terminal_commit_hook(event)
        await self.event_bus.emit_local(client, event)

    async def _run_terminal_commit_hook(self, event: EventEnvelope) -> None:
        if event.task_id is not None:
            await self._sync_task_workspace(event.task_id)
        if self.after_terminal_commit is None:
            return
        result = self.after_terminal_commit(event)
        if inspect.isawaitable(result):
            await result

    async def _sync_task_workspace(self, task_id: str) -> None:
        """Refresh a derived task folder without weakening a durable committed request."""

        if self.task_workspace_projector is None:
            return
        try:
            await asyncio.to_thread(self.task_workspace_projector.sync, task_id)
        except Exception:
            # Canonical SQLite/artifact/run state has already committed at each call site.
            # A recoverable user-facing projection must never turn that success into a
            # misleading request failure; task.open retries this idempotently.
            _LOGGER.exception("Could not refresh OceanMind task folder for %s", task_id)

    async def _ensure_task_workspace_root(self, task_id: str) -> None:
        """Create only the stable task-folder shell required for navigation."""

        if self.task_workspace_projector is None:
            return
        try:
            await asyncio.to_thread(self.task_workspace_projector.ensure_task_root, task_id)
        except Exception:
            _LOGGER.exception("Could not ensure OceanMind task folder for %s", task_id)

    async def _emit_system_error(
        self,
        client: BackendClient,
        *,
        code: ErrorCode,
        message: str,
        recoverable: bool,
        details: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        event = SystemErrorEvent(
            **self._event_fields(client, request_id=request_id),
            type="system.error",
            payload=SystemErrorPayload(
                error=ProtocolErrorPayload(
                    code=code,
                    message=message,
                    recoverable=recoverable,
                    details=details,
                )
            ),
        )
        await self.event_bus.emit_local(client, event)

    def _accepted_event(
        self,
        client: BackendClient,
        request: RequestEnvelope,
        *,
        state: str,
    ) -> RequestAcceptedEvent:
        return RequestAcceptedEvent(
            **self._event_fields(
                client, request_id=request.request_id, task_id=self._task_id(request)
            ),
            type="request.accepted",
            payload=RequestAcceptedPayload(request_type=request.type, state=state),
        )

    def _completed_event(
        self,
        client: BackendClient,
        request: RequestEnvelope,
        *,
        result: dict[str, Any],
        workspace_revision: int | None = None,
    ) -> RequestCompletedEvent:
        return RequestCompletedEvent(
            **self._event_fields(
                client, request_id=request.request_id, task_id=self._task_id(request)
            ),
            type="request.completed",
            payload=RequestCompletedPayload(
                result=result,
                workspace_revision=workspace_revision,
            ),
        )

    def _failed_event(
        self,
        client: BackendClient,
        request: RequestEnvelope,
        *,
        code: ErrorCode,
        message: str,
        recoverable: bool,
        details: dict[str, Any],
    ) -> RequestFailedEvent:
        return RequestFailedEvent(
            **self._event_fields(
                client, request_id=request.request_id, task_id=self._task_id(request)
            ),
            type="request.failed",
            payload=RequestFailedPayload(
                error=ProtocolErrorPayload(
                    code=code,
                    message=message,
                    recoverable=recoverable,
                    details=details,
                )
            ),
        )

    def _interrupted_event(self, record: RequestRecord) -> RequestFailedEvent:
        return RequestFailedEvent(
            protocol_version=2,
            event_id=new_event_id(),
            session_id=record.session_id,
            workspace_id=record.workspace_id,
            task_id=record.task_id,
            request_id=record.request_id,
            sequence=0,
            timestamp=datetime.now(timezone.utc),
            type="request.failed",
            payload=RequestFailedPayload(
                error=ProtocolErrorPayload(
                    code="request_interrupted",
                    message="Backend restarted before this request finished",
                    recoverable=True,
                    details={},
                )
            ),
        )

    def _recovered_completed_event(
        self,
        record: RequestRecord,
        result: CoordinatorResult,
    ) -> RequestCompletedEvent:
        """Close a crashed request from its already-accepted durable conclusion."""

        return RequestCompletedEvent(
            protocol_version=2,
            event_id=new_event_id(),
            session_id=record.session_id,
            workspace_id=record.workspace_id,
            task_id=record.task_id,
            request_id=record.request_id,
            sequence=0,
            timestamp=datetime.now(timezone.utc),
            type="request.completed",
            payload=RequestCompletedPayload(
                result={
                    "assistant_text": self._bounded_text(result.answer_markdown, 64_000),
                    "coordinator_result": result.model_dump(mode="json"),
                    "recovered_from_durable_receipt": True,
                }
            ),
        )

    def _event_fields(
        self,
        client: BackendClient,
        *,
        request_id: str | None,
        workspace_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol_version": 2,
            "event_id": event_id or new_event_id(),
            "session_id": session_id if session_id is not None else client.session_id,
            "workspace_id": workspace_id if workspace_id is not None else client.workspace_id,
            "task_id": task_id,
            "request_id": request_id,
            "sequence": 0,
            "timestamp": datetime.now(timezone.utc),
        }

    @staticmethod
    def _workspace_id(request: RequestEnvelope) -> str:
        if request.context is None or request.context.workspace_id is None:
            raise RequestStoreError("This request requires context.workspace_id")
        return request.context.workspace_id

    @staticmethod
    def _task_id(request: RequestEnvelope) -> str | None:
        return request.context.task_id if request.context is not None else None

    def _submitted_text_with_context_refs(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        text: str,
        context_refs: tuple[ArtifactRef, ...],
    ) -> str:
        """Attach the task's durable source memory to every Coordinator turn.

        The renderer may add exact immutable references for a single turn, but the task's
        source links are server-owned and automatically inherited. This prevents the
        conversation checkpoint, sidebar, and Expert WorkOrder from disagreeing about
        which inputs belong to the task.
        """

        refs: list[ArtifactRef] = []
        if task_id is not None:
            for record in self.store.list_task_artifacts(task_id=task_id):
                artifact = record.artifact
                if artifact.workspace_id != workspace_id:
                    continue
                if "source" in record.relations and artifact.artifact_type in {"dataset", "paper"}:
                    refs.append(artifact.ref)
        refs.extend(context_refs)
        unique_refs = list(dict.fromkeys(refs))
        if not unique_refs:
            return text
        lines = [
            "[Task-owned immutable sources available to this turn. These are routing identities; "
            "delegate scientific content inspection to the relevant Expert:]"
        ]
        for index, ref in enumerate(unique_refs, start=1):
            artifact = self.store.get_artifact(workspace_id=workspace_id, ref=ref)
            if artifact is None:
                raise RequestNotFound(ref.key)
            lines.append(
                f"- source_{index}: {ref.artifact_id}@v{ref.version} "
                f"({artifact.artifact_type}; {artifact.title})"
            )
        return f"{text}\n\n" + "\n".join(lines)

    def _task_in_workspace(self, task_id: str, request: RequestEnvelope):
        task = self.store.get_research_task(task_id, workspace_id=self._workspace_id(request))
        if task is None:
            raise TaskNotFound(task_id)
        return task
