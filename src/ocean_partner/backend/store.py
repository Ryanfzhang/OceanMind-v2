"""Durable request and minimal workspace journal for Protocol v2.

This is intentionally a compact Phase 1 store.  It establishes idempotent
request and terminal-event semantics now; Phase 2 migrates the tables into the
full project-local artifact metadata schema.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from uuid import uuid4

from ocean_partner.artifacts.impact import (
    ImpactEdge,
    ImpactNode,
    rebuild_impact,
    would_create_propagating_cycle,
)
from ocean_partner.artifacts.models import (
    ArtifactFile,
    ArtifactLinkDraft,
    ArtifactManifest,
    ArtifactProjection,
    ArtifactRef,
    ArtifactVersion,
    ClaimContent,
    DecisionContent,
    ExperimentContent,
    HypothesisContent,
    ImpactHop,
    InteractiveViewContent,
    ObservationContent,
    ReportContent,
)
from ocean_partner.delivery import build_delivery_manifests
from ocean_partner.protocol.v2.models import (
    EventEnvelope,
    RequestEnvelope,
    canonical_request_fields,
    new_event_id,
    parse_event,
)
from ocean_partner.research_learning import (
    CandidateStatus,
    EvolvedSkillRevision,
    ExperienceCandidate,
    ObservationKind,
    ObservationRelation,
    ResearchObservation,
    ResearchObservationDraft,
    ResearchObservationLink,
    ResearchState,
    SavedExperience,
    SavedExperienceStatus,
    SkillEvaluation,
    SkillReview,
    SkillRevision,
    observations_from_expert_result,
    proposed_rule,
)
from ocean_partner.storage import ensure_private_directory, ensure_private_file
from ocean_partner.task_results import TaskResultRef
from ocean_partner.team.models import (
    ChildAuthority,
    CoordinatorResult,
    EvidenceRef,
    ExpertOutput,
    ExpertResult,
    ExpertResultOrigin,
    ResultBundle,
    WorkFailureCode,
    WorkOrder,
    WorkStatus,
    WorkstreamCheckpoint,
    WorkstreamPhase,
    expert_output_item_id,
)


class RequestStoreError(RuntimeError):
    """Base error for durable request journal failures."""


class RequestIdConflict(RequestStoreError):
    """A reused request ID has a different semantic request hash."""


class RequestNotFound(RequestStoreError):
    """The requested durable record does not exist."""


class WorkspaceRevisionConflict(RequestStoreError):
    """A mutation was based on an obsolete workspace revision."""

    def __init__(self, current_revision: int) -> None:
        super().__init__(f"Expected workspace revision does not match {current_revision}")
        self.current_revision = current_revision


class ArtifactVersionConflict(RequestStoreError):
    """An immutable artifact ID/version already has different manifest content."""


class ArtifactCommitNotFound(RequestStoreError):
    """A filesystem staging operation has no durable metadata intent."""


class TaskNotFound(RequestStoreError):
    """The requested durable ResearchTask does not exist."""


class TaskRevisionConflict(RequestStoreError):
    """A task-local mutation was based on an obsolete task revision."""

    def __init__(self, current_revision: int) -> None:
        super().__init__(f"Expected task revision does not match {current_revision}")
        self.current_revision = current_revision


class TaskCheckpointIncompatible(RequestStoreError):
    """A task checkpoint cannot safely be restored by this backend."""


TERMINAL_EVENT_TYPES = frozenset({"request.completed", "request.failed", "request.cancelled"})
TERMINAL_REQUEST_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
ACTIVE_REQUEST_STATES = frozenset({"accepted", "in_progress"})


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    request_type: str
    canonical_hash: str
    canonical_request: dict[str, Any]
    principal: str
    session_id: str | None
    workspace_id: str | None
    task_id: str | None
    state: str
    terminal_event: EventEnvelope | None
    created_at: str
    updated_at: str

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_REQUEST_STATES


@dataclass(frozen=True)
class RequestReservation:
    record: RequestRecord
    created: bool


ResearchTaskState = Literal["active", "completed", "archived"]
TaskWorkflowState = Literal[
    "planning",
    "working",
    "completed",
    "incomplete",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class ResearchTaskRecord:
    task_id: str
    workspace_id: str
    title: str
    status: ResearchTaskState
    task_revision: int
    active_request_id: str | None
    stable_checkpoint_id: str | None
    conversation_generation: int
    created_at: str
    updated_at: str

    def as_summary(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "status": self.status,
            "task_revision": self.task_revision,
            "active_request_id": self.active_request_id,
            "stable_checkpoint_id": self.stable_checkpoint_id,
            "conversation_generation": self.conversation_generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskArtifactRecord:
    """One immutable artifact attached to a user-visible research task."""

    artifact: ArtifactVersion
    relations: tuple[str, ...]
    origin_request_ids: tuple[str, ...]
    linked_at: str


@dataclass(frozen=True)
class TaskWorkflowRecord:
    """Durable state for one Coordinator-owned task request."""

    request_id: str
    task_id: str
    workspace_id: str
    state: TaskWorkflowState
    activity: str
    checkpoint: dict[str, Any]
    heartbeat_at: str
    failure_fingerprint: str | None
    repeated_failures: int
    created_at: str
    updated_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "activity": self.activity,
            "checkpoint": self.checkpoint,
            "heartbeat_at": self.heartbeat_at,
            "failure_fingerprint": self.failure_fingerprint,
            "repeated_failures": self.repeated_failures,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskTranscriptItem:
    item_id: str
    task_id: str
    sequence: int
    role: Literal["user", "assistant", "tool", "system"]
    text: str
    request_id: str | None
    turn_id: str | None
    tool_call_id: str | None
    interrupted: bool
    created_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "sequence": self.sequence,
            "role": self.role,
            "text": self.text,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "interrupted": self.interrupted,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ConversationCheckpoint:
    checkpoint_id: str
    task_id: str
    conversation_generation: int
    terminal_request_id: str
    message_schema_version: int
    messages: tuple[dict[str, Any], ...]
    provider_id: str
    model_id: str
    runtime_profile_fingerprint: str
    system_prompt_fingerprint: str
    compaction_generation: int
    usage_summary: dict[str, Any]
    payload_sha256: str
    created_at: str

    def as_metadata(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "conversation_generation": self.conversation_generation,
            "terminal_request_id": self.terminal_request_id,
            "message_schema_version": self.message_schema_version,
            "message_count": len(self.messages),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "runtime_profile_fingerprint": self.runtime_profile_fingerprint,
            "system_prompt_fingerprint": self.system_prompt_fingerprint,
            "compaction_generation": self.compaction_generation,
            "usage_summary": self.usage_summary,
            "payload_sha256": self.payload_sha256,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExpertSessionCheckpoint:
    """Resumable model conversation for one logical task participant."""

    workspace_id: str
    task_scope: str
    participant_key: str
    job_key: str
    work_order_id: str
    messages: tuple[dict[str, Any], ...]
    compaction_generation: int
    payload_sha256: str
    updated_at: str


@dataclass(frozen=True)
class PendingInteractionRecord:
    interaction_id: str
    request_id: str
    task_id: str | None
    workspace_id: str
    session_id: str
    principal: str
    kind: Literal["question", "permission", "paper_selection"]
    question: str
    options: tuple[dict[str, Any], ...]
    state: Literal["pending", "answered", "interrupted"]
    created_at: str
    resolved_at: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "question": self.question,
            "options": list(self.options),
            "state": self.state,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ResearchTaskSnapshot:
    task: ResearchTaskRecord
    transcript: tuple[TaskTranscriptItem, ...]
    next_transcript_cursor: int | None
    sources: tuple[dict[str, Any], ...]
    outputs: tuple[dict[str, Any], ...]
    delivery_manifests: tuple[dict[str, Any], ...]
    interactions: tuple[PendingInteractionRecord, ...]
    checkpoint: ConversationCheckpoint | None
    workflow: TaskWorkflowRecord | None = None
    checkpoint_error: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "task": self.task.as_summary(),
            "transcript": [item.as_payload() for item in self.transcript],
            "next_transcript_cursor": self.next_transcript_cursor,
            "sources": list(self.sources),
            "outputs": list(self.outputs),
            "delivery_manifests": list(self.delivery_manifests),
            "interactions": [item.as_payload() for item in self.interactions],
            "checkpoint": (
                self.checkpoint.as_metadata()
                if self.checkpoint is not None
                else (
                    {"status": "incompatible", "reason": self.checkpoint_error}
                    if self.checkpoint_error is not None
                    else {}
                )
            ),
            "workflow": self.workflow.as_payload() if self.workflow is not None else None,
        }


@dataclass(frozen=True)
class WorkspaceSnapshot:
    workspace_id: str
    path: str | None
    revision: int
    artifacts: list[dict[str, Any]]
    active_refs: dict[str, ArtifactRef] = field(default_factory=dict)
    disclosure_policy: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "path": self.path,
            "revision": self.revision,
            "artifacts": self.artifacts,
            "active_refs": {
                slot: ref.model_dump(mode="json") for slot, ref in sorted(self.active_refs.items())
            },
            "disclosure_policy": self.disclosure_policy,
        }


@dataclass(frozen=True)
class WorkspaceOpenCommit:
    snapshot: WorkspaceSnapshot
    previous_revision: int
    change: str | None
    changed_event: EventEnvelope | None
    terminal_event: EventEnvelope


@dataclass(frozen=True)
class ActiveRefCommit:
    """One active-pointer mutation with its durable domain and terminal events."""

    snapshot: WorkspaceSnapshot
    previous_revision: int
    domain_event: EventEnvelope
    terminal_event: EventEnvelope


@dataclass(frozen=True)
class TeamWorkRecord:
    """Durable typed child work without retaining an unbounded transcript."""

    workspace_id: str
    work_order: WorkOrder
    state: WorkStatus
    result: ExpertResult | None
    checkpoint: WorkstreamCheckpoint
    resume_count: int
    created_at: str
    updated_at: str

    def as_summary(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "work_order": self.work_order.model_dump(mode="json"),
            "state": self.state.value,
            "result": self.result.model_dump(mode="json") if self.result is not None else None,
            "checkpoint": self.checkpoint.model_dump(mode="json"),
            "resume_count": self.resume_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CodeExecutionRecord:
    """One immutable audit record for code run by an Expert session."""

    execution_id: str
    workspace_id: str
    task_id: str
    work_order_id: str
    child_id: str
    state: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class DisclosurePolicyCommit:
    policy: dict[str, Any]
    previous_revision: int
    workspace_revision: int
    domain_event: EventEnvelope
    terminal_event: EventEnvelope


@dataclass(frozen=True)
class CancellationCommit:
    target: RequestRecord
    target_terminal_event: EventEnvelope | None
    terminal_event: EventEnvelope


@dataclass(frozen=True)
class ArtifactCommitIntent:
    operation_id: str
    request_id: str | None
    # ``request_id`` owns a standalone request terminal. ``origin_request_id``
    # only correlates a domain mutation performed inside a long-lived agent request.
    origin_request_id: str | None
    task_id: str | None
    task_relation: str | None
    workspace_id: str
    artifact_id: str
    version: int
    expected_workspace_revision: int | None
    staging_uri: str
    target_uri: str
    manifest: ArtifactManifest
    status: str
    quarantine_uri: str | None
    committed_event_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArtifactCommit:
    artifact: ArtifactVersion
    projection: ArtifactProjection
    previous_revision: int
    workspace_revision: int
    domain_event: EventEnvelope
    terminal_event: EventEnvelope | None


@dataclass(frozen=True)
class ToolCallRecord:
    """Durable model-tool correlation and final replay result, if available."""

    operation_id: str
    request_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    state: str
    result: dict[str, Any] | None
    created_at: str
    completed_at: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _state_for_terminal_event(event: EventEnvelope) -> str:
    if event.type == "request.completed":
        return "completed"
    if event.type == "request.cancelled":
        return "cancelled"
    if event.type == "request.failed":
        error = event.payload.error
        return "interrupted" if error.code == "request_interrupted" else "failed"
    raise RequestStoreError(f"Event is not request-terminal: {event.type}")


class RequestStore:
    """SQLite-backed idempotency journal with atomic event persistence."""

    def __init__(self, database_path: Path) -> None:
        self.path = database_path
        self._new_database = not database_path.exists()
        ensure_private_directory(self.path.parent)
        ensure_private_file(self.path)
        self._connection = sqlite3.connect(
            str(database_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._enforce_sqlite_private_files()
            self._connection.close()

    def _migrate(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS request_records (
                    request_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    canonical_hash TEXT NOT NULL,
                    canonical_request_json TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    session_id TEXT,
                    workspace_id TEXT,
                    state TEXT NOT NULL,
                    terminal_event_json TEXT,
                    terminal_event_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_records (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    workspace_id TEXT,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_records (
                    workspace_id TEXT PRIMARY KEY,
                    path TEXT,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_records (
                    session_id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    workspace_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operation_checkpoints (
                    operation_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_request_records_session_state
                    ON request_records(session_id, state);
                CREATE INDEX IF NOT EXISTS idx_event_records_workspace
                    ON event_records(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            baseline = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if baseline is None:
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                    (_utc_now(),),
                )
            if self._new_database:
                # These versions belonged to execution models removed by the
                # Coordinator -> persistent Expert Session architecture. A new
                # database must never create those transient tables. Existing
                # databases still run the bridge migrations below before the
                # old tables are retired.
                for retired_version in (
                    8,
                    12,
                    13,
                    14,
                    18,
                    19,
                    20,
                    21,
                    22,
                    23,
                    26,
                    27,
                    28,
                ):
                    self._connection.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (retired_version, _utc_now()),
                    )
            domain = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 2"
            ).fetchone()
            if domain is None:
                if not self._new_database:
                    self._backup_before_migration(2)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS artifact_versions (
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        artifact_type TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        supersedes_version INTEGER,
                        content_json TEXT NOT NULL,
                        intrinsic_links_json TEXT NOT NULL,
                        provenance_json TEXT NOT NULL,
                        manifest_uri TEXT NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, artifact_id, version),
                        UNIQUE (manifest_uri)
                    );

                    CREATE TABLE IF NOT EXISTS artifact_files (
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        uri TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, artifact_id, version, uri),
                        FOREIGN KEY (workspace_id, artifact_id, version)
                            REFERENCES artifact_versions(workspace_id, artifact_id, version)
                    );

                    CREATE TABLE IF NOT EXISTS artifact_links (
                        link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        source_artifact_id TEXT NOT NULL,
                        source_version INTEGER NOT NULL,
                        target_artifact_id TEXT NOT NULL,
                        target_version INTEGER NOT NULL,
                        relation TEXT NOT NULL,
                        intrinsic INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        source_event_id TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_artifact_links_source
                        ON artifact_links(workspace_id, source_artifact_id, source_version, relation);
                    CREATE INDEX IF NOT EXISTS idx_artifact_links_target
                        ON artifact_links(workspace_id, target_artifact_id, target_version, relation);

                    CREATE TABLE IF NOT EXISTS artifact_projections (
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        lifecycle_state TEXT NOT NULL,
                        review_state TEXT NOT NULL,
                        verification_state TEXT NOT NULL,
                        impact_state TEXT NOT NULL,
                        impact_reasons_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source_event_id TEXT,
                        PRIMARY KEY (workspace_id, artifact_id, version),
                        FOREIGN KEY (workspace_id, artifact_id, version)
                            REFERENCES artifact_versions(workspace_id, artifact_id, version)
                    );

                    CREATE TABLE IF NOT EXISTS workspace_active_refs (
                        workspace_id TEXT NOT NULL,
                        slot TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, slot)
                    );

                    CREATE TABLE IF NOT EXISTS artifact_commit_intents (
                    operation_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    origin_request_id TEXT,
                    task_id TEXT,
                    task_relation TEXT,
                    workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        expected_workspace_revision INTEGER,
                        staging_uri TEXT NOT NULL,
                        target_uri TEXT NOT NULL,
                        manifest_json TEXT NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        status TEXT NOT NULL,
                        quarantine_uri TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS artifact_state_events (
                        state_event_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        active INTEGER NOT NULL,
                        source_event_id TEXT,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (2, ?)",
                    (_utc_now(),),
                )
            state_events = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 3"
            ).fetchone()
            if state_events is None:
                if not self._new_database:
                    self._backup_before_migration(3)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(artifact_commit_intents)"
                    ).fetchall()
                }
                if "expected_workspace_revision" not in columns:
                    self._connection.execute(
                        "ALTER TABLE artifact_commit_intents ADD COLUMN expected_workspace_revision INTEGER"
                    )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifact_state_events (
                        state_event_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        active INTEGER NOT NULL,
                        source_event_id TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (3, ?)",
                    (_utc_now(),),
                )
            intent_constraint = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 4"
            ).fetchone()
            if intent_constraint is None:
                if not self._new_database:
                    self._backup_before_migration(4)
                try:
                    self._connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_commit_intents_version
                        ON artifact_commit_intents(workspace_id, artifact_id, version)
                        """
                    )
                except sqlite3.IntegrityError as exc:
                    raise RequestStoreError(
                        "Cannot enforce immutable artifact-version reservation on duplicate intents"
                    ) from exc
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (4, ?)",
                    (_utc_now(),),
                )
            committed_event = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 5"
            ).fetchone()
            if committed_event is None:
                if not self._new_database:
                    self._backup_before_migration(5)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(artifact_commit_intents)"
                    ).fetchall()
                }
                if "committed_event_id" not in columns:
                    self._connection.execute(
                        "ALTER TABLE artifact_commit_intents ADD COLUMN committed_event_id TEXT"
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (5, ?)",
                    (_utc_now(),),
                )
            disclosure = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 6"
            ).fetchone()
            if disclosure is None:
                if not self._new_database:
                    self._backup_before_migration(6)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_disclosure_policies (
                        workspace_id TEXT PRIMARY KEY,
                        provider_id TEXT NOT NULL,
                        policy_version INTEGER NOT NULL,
                        policy_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS disclosure_audit_records (
                        audit_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL,
                        policy_version INTEGER NOT NULL,
                        content_type TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        byte_count INTEGER NOT NULL,
                        item_count INTEGER NOT NULL,
                        source_ref_json TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_disclosure_audit_workspace
                        ON disclosure_audit_records(workspace_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (6, ?)",
                    (_utc_now(),),
                )
            resource_usage = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 7"
            ).fetchone()
            if resource_usage is None:
                if not self._new_database:
                    self._backup_before_migration(7)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS resource_usage_records (
                        usage_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT,
                        resource_kind TEXT NOT NULL,
                        resource_name TEXT NOT NULL,
                        resource_version TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_resource_usage_workspace
                        ON resource_usage_records(workspace_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_resource_usage_work_order
                        ON resource_usage_records(work_order_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (7, ?)",
                    (_utc_now(),),
                )
            analysis_runs = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 8"
            ).fetchone()
            if analysis_runs is None:
                if not self._new_database:
                    self._backup_before_migration(8)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS analysis_runs (
                        run_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        analysis_plan_artifact_id TEXT,
                        analysis_plan_version INTEGER,
                        analysis_plan_seeded INTEGER NOT NULL DEFAULT 0,
                        inputs_json TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        runtime_profile_json TEXT NOT NULL,
                        resource_policy_json TEXT NOT NULL,
                        work_uri TEXT NOT NULL,
                        selected_attempt_id TEXT,
                        last_terminal_state TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (
                            (analysis_plan_artifact_id IS NULL AND analysis_plan_version IS NULL)
                            OR (analysis_plan_artifact_id IS NOT NULL AND analysis_plan_version IS NOT NULL)
                        )
                    );
                    CREATE INDEX IF NOT EXISTS idx_analysis_runs_workspace
                        ON analysis_runs(workspace_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS analysis_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        operation_id TEXT,
                        run_id TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        directory_uri TEXT NOT NULL,
                        code_files_json TEXT NOT NULL,
                        input_fingerprints_before_json TEXT NOT NULL,
                        input_fingerprints_after_json TEXT NOT NULL,
                        environment_uri TEXT,
                        environment_sha256 TEXT,
                        stdout_uri TEXT,
                        stderr_uri TEXT,
                        output_manifest_uri TEXT,
                        outputs_json TEXT NOT NULL,
                        checks_json TEXT NOT NULL,
                        returncode INTEGER,
                        duration_seconds REAL,
                        execution_trust TEXT NOT NULL,
                        limit_trigger TEXT,
                        failure_reason TEXT,
                        started_at TEXT,
                        ended_at TEXT,
                        UNIQUE (run_id, attempt_number),
                        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_analysis_attempts_run
                        ON analysis_attempts(run_id, attempt_number);

                    CREATE TABLE IF NOT EXISTS analysis_run_events (
                        event_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        attempt_id TEXT,
                        kind TEXT NOT NULL,
                        from_state TEXT,
                        to_state TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id),
                        FOREIGN KEY (attempt_id) REFERENCES analysis_attempts(attempt_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_analysis_run_events_run
                        ON analysis_run_events(run_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (8, ?)",
                    (_utc_now(),),
                )
            disclosure_history = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 9"
            ).fetchone()
            if disclosure_history is None:
                if not self._new_database:
                    self._backup_before_migration(9)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_disclosure_policy_versions (
                        workspace_id TEXT NOT NULL,
                        policy_version INTEGER NOT NULL,
                        provider_id TEXT NOT NULL,
                        policy_json TEXT NOT NULL,
                        confirmed_request_id TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, policy_version)
                    );
                    CREATE INDEX IF NOT EXISTS idx_disclosure_policy_versions_workspace
                        ON workspace_disclosure_policy_versions(workspace_id, policy_version DESC);

                    INSERT OR IGNORE INTO workspace_disclosure_policy_versions (
                        workspace_id, policy_version, provider_id, policy_json, confirmed_request_id, created_at
                    )
                    SELECT workspace_id, policy_version, provider_id, policy_json, NULL, updated_at
                    FROM workspace_disclosure_policies;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (9, ?)",
                    (_utc_now(),),
                )
            intent_origin = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 10"
            ).fetchone()
            if intent_origin is None:
                if not self._new_database:
                    self._backup_before_migration(10)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(artifact_commit_intents)"
                    ).fetchall()
                }
                if "origin_request_id" not in columns:
                    self._connection.execute(
                        "ALTER TABLE artifact_commit_intents ADD COLUMN origin_request_id TEXT"
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (10, ?)",
                    (_utc_now(),),
                )

            tool_calls = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 11"
            ).fetchone()
            if tool_calls is None:
                if not self._new_database:
                    self._backup_before_migration(11)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tool_call_records (
                        operation_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        tool_call_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        state TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_tool_call_records_request
                        ON tool_call_records(request_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (11, ?)",
                    (_utc_now(),),
                )

            attempt_operation = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 12"
            ).fetchone()
            if attempt_operation is None:
                if not self._new_database:
                    self._backup_before_migration(12)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(analysis_attempts)"
                    ).fetchall()
                }
                if "operation_id" not in columns:
                    self._connection.execute(
                        "ALTER TABLE analysis_attempts ADD COLUMN operation_id TEXT"
                    )
                self._connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_attempts_operation
                    ON analysis_attempts(operation_id)
                    WHERE operation_id IS NOT NULL
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (12, ?)",
                    (_utc_now(),),
                )
            seeded_plan = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 13"
            ).fetchone()
            if seeded_plan is None:
                if not self._new_database:
                    self._backup_before_migration(13)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(analysis_runs)"
                    ).fetchall()
                }
                if "analysis_plan_seeded" not in columns:
                    self._connection.execute(
                        "ALTER TABLE analysis_runs ADD COLUMN analysis_plan_seeded INTEGER NOT NULL DEFAULT 0"
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (13, ?)",
                    (_utc_now(),),
                )
            multi_agent = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 14"
            ).fetchone()
            if multi_agent is None:
                if not self._new_database:
                    self._backup_before_migration(14)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS multi_agent_tasks (
                        task_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        state TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        base_workspace_revision INTEGER NOT NULL,
                        prompt_sha256 TEXT NOT NULL,
                        budget_json TEXT NOT NULL,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_multi_agent_tasks_workspace
                        ON multi_agent_tasks(workspace_id, created_at DESC);

                    CREATE TABLE IF NOT EXISTS multi_agent_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        state TEXT NOT NULL,
                        proposal_kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        source_refs_json TEXT NOT NULL,
                        base_workspace_revision INTEGER NOT NULL,
                        operation_id TEXT UNIQUE,
                        acceptance_request_id TEXT,
                        accepted_artifact_id TEXT,
                        accepted_artifact_version INTEGER,
                        rejection_reason TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES multi_agent_tasks(task_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_multi_agent_proposals_workspace
                        ON multi_agent_proposals(workspace_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_multi_agent_proposals_task
                        ON multi_agent_proposals(task_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (14, ?)",
                    (_utc_now(),),
                )
            research_tasks = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 15"
            ).fetchone()
            if research_tasks is None:
                if not self._new_database:
                    self._backup_before_migration(15)
                request_columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(request_records)"
                    ).fetchall()
                }
                if "task_id" not in request_columns:
                    self._connection.execute("ALTER TABLE request_records ADD COLUMN task_id TEXT")
                self._connection.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_request_records_task_state
                        ON request_records(task_id, state);

                    CREATE TABLE IF NOT EXISTS research_tasks (
                        task_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        task_revision INTEGER NOT NULL,
                        active_request_id TEXT,
                        stable_checkpoint_id TEXT,
                        conversation_generation INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (status IN ('active', 'completed', 'archived'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_research_tasks_workspace_updated
                        ON research_tasks(workspace_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS task_transcript_items (
                        item_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        text TEXT NOT NULL,
                        request_id TEXT,
                        turn_id TEXT,
                        tool_call_id TEXT,
                        interrupted INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        UNIQUE(task_id, sequence),
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        CHECK (role IN ('user', 'assistant', 'tool', 'system'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_transcript_items_task_sequence
                        ON task_transcript_items(task_id, sequence DESC);

                    CREATE TABLE IF NOT EXISTS task_conversation_checkpoints (
                        checkpoint_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        conversation_generation INTEGER NOT NULL,
                        terminal_request_id TEXT NOT NULL,
                        message_schema_version INTEGER NOT NULL,
                        messages_json TEXT NOT NULL,
                        message_count INTEGER NOT NULL,
                        provider_id TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        runtime_profile_fingerprint TEXT NOT NULL,
                        system_prompt_fingerprint TEXT NOT NULL,
                        compaction_generation INTEGER NOT NULL,
                        usage_summary_json TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(task_id, conversation_generation),
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_checkpoints_task_generation
                        ON task_conversation_checkpoints(task_id, conversation_generation DESC);

                    CREATE TABLE IF NOT EXISTS task_artifact_links (
                        task_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        relation TEXT NOT NULL,
                        origin_request_id TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (task_id, artifact_id, version, relation),
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_artifact_links_task
                        ON task_artifact_links(task_id, created_at DESC);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (15, ?)",
                    (_utc_now(),),
                )
            interactions = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 16"
            ).fetchone()
            if interactions is None:
                if not self._new_database:
                    self._backup_before_migration(16)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS task_interactions (
                        interaction_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        task_id TEXT,
                        workspace_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        principal TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        question TEXT NOT NULL,
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        CHECK (kind = 'question'),
                        CHECK (state IN ('pending', 'answered', 'interrupted'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_interactions_task_state
                        ON task_interactions(task_id, state, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_task_interactions_request_state
                        ON task_interactions(request_id, state);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (16, ?)",
                    (_utc_now(),),
                )
            interaction_kinds = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 17"
            ).fetchone()
            if interaction_kinds is None:
                if not self._new_database:
                    self._backup_before_migration(17)
                self._connection.executescript(
                    """
                    ALTER TABLE task_interactions RENAME TO task_interactions_v16;
                    DROP INDEX IF EXISTS idx_task_interactions_task_state;
                    DROP INDEX IF EXISTS idx_task_interactions_request_state;
                    CREATE TABLE task_interactions (
                        interaction_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        task_id TEXT,
                        workspace_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        principal TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        question TEXT NOT NULL,
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        CHECK (kind IN ('question', 'permission')),
                        CHECK (state IN ('pending', 'answered', 'interrupted'))
                    );
                    INSERT INTO task_interactions SELECT * FROM task_interactions_v16;
                    DROP TABLE task_interactions_v16;
                    CREATE INDEX idx_task_interactions_task_state
                        ON task_interactions(task_id, state, created_at DESC);
                    CREATE INDEX idx_task_interactions_request_state
                        ON task_interactions(request_id, state);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (17, ?)",
                    (_utc_now(),),
                )
            runtime_fingerprints = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 18"
            ).fetchone()
            if runtime_fingerprints is None:
                if not self._new_database:
                    self._backup_before_migration(18)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(analysis_attempts)"
                    ).fetchall()
                }
                for column in (
                    "runtime_manifest_uri TEXT",
                    "runtime_fingerprint_sha256 TEXT",
                    "runtime_baseline_sha256 TEXT",
                ):
                    if column.split(" ", 1)[0] not in columns:
                        self._connection.execute(
                            f"ALTER TABLE analysis_attempts ADD COLUMN {column}"
                        )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (18, ?)",
                    (_utc_now(),),
                )
            sparse_team = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 19"
            ).fetchone()
            if sparse_team is None:
                if not self._new_database:
                    self._backup_before_migration(19)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS team_work_records (
                        work_order_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        parent_request_id TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        state TEXT NOT NULL,
                        workspace_revision INTEGER NOT NULL,
                        work_order_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (authority IN ('advisor', 'executor', 'reviewer')),
                        CHECK (state IN ('queued', 'running', 'completed', 'failed', 'cancelled'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_team_work_workspace_updated
                        ON team_work_records(workspace_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_team_work_parent_state
                        ON team_work_records(parent_request_id, state, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (19, ?)",
                    (_utc_now(),),
                )
            executor_leases = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 20"
            ).fetchone()
            if executor_leases is None:
                if not self._new_database:
                    self._backup_before_migration(20)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS analysis_executor_leases (
                        run_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        release_reason TEXT,
                        acquired_at TEXT NOT NULL,
                        released_at TEXT,
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN ('active', 'released'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_executor_leases_workspace_state
                        ON analysis_executor_leases(workspace_id, state, acquired_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (20, ?)",
                    (_utc_now(),),
                )
            team_resource_usage = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 21"
            ).fetchone()
            if team_resource_usage is None:
                if not self._new_database:
                    self._backup_before_migration(21)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(resource_usage_records)"
                    ).fetchall()
                }
                if "work_order_id" not in columns:
                    self._connection.execute(
                        "ALTER TABLE resource_usage_records ADD COLUMN work_order_id TEXT"
                    )
                self._connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_resource_usage_work_order
                    ON resource_usage_records(work_order_id, created_at)
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (21, ?)",
                    (_utc_now(),),
                )
            durable_team_attempts = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 22"
            ).fetchone()
            if durable_team_attempts is None:
                if not self._new_database:
                    self._backup_before_migration(22)
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    ALTER TABLE analysis_executor_leases RENAME TO analysis_executor_leases_v21;
                    ALTER TABLE team_work_records RENAME TO team_work_records_v21;
                    DROP INDEX IF EXISTS idx_executor_leases_workspace_state;
                    DROP INDEX IF EXISTS idx_team_work_workspace_updated;
                    DROP INDEX IF EXISTS idx_team_work_parent_state;

                    CREATE TABLE team_work_records (
                        work_order_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        parent_request_id TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        state TEXT NOT NULL,
                        workspace_revision INTEGER NOT NULL,
                        work_order_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (authority IN ('advisor', 'executor', 'reviewer')),
                        CHECK (state IN (
                            'queued', 'running', 'waiting_retry', 'completed',
                            'failed', 'cancelled', 'skipped'
                        ))
                    );
                    INSERT INTO team_work_records SELECT * FROM team_work_records_v21;
                    CREATE INDEX idx_team_work_workspace_updated
                        ON team_work_records(workspace_id, updated_at DESC);
                    CREATE INDEX idx_team_work_parent_state
                        ON team_work_records(parent_request_id, state, created_at);

                    CREATE TABLE analysis_executor_leases (
                        run_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        release_reason TEXT,
                        acquired_at TEXT NOT NULL,
                        released_at TEXT,
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN ('active', 'released'))
                    );
                    INSERT INTO analysis_executor_leases
                        SELECT * FROM analysis_executor_leases_v21;
                    CREATE INDEX idx_executor_leases_workspace_state
                        ON analysis_executor_leases(workspace_id, state, acquired_at);

                    DROP TABLE analysis_executor_leases_v21;
                    DROP TABLE team_work_records_v21;

                    CREATE TABLE team_work_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        work_order_id TEXT NOT NULL,
                        number INTEGER NOT NULL,
                        child_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        usage_json TEXT NOT NULL,
                        failure_code TEXT,
                        retryability TEXT,
                        error TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        UNIQUE(work_order_id, number),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (number >= 1),
                        CHECK (state IN (
                            'starting', 'running', 'completed', 'failed',
                            'timed_out', 'cancelled', 'interrupted'
                        )),
                        CHECK (retryability IS NULL OR retryability IN ('automatic', 'manual', 'never'))
                    );
                    CREATE INDEX idx_team_attempts_work_number
                        ON team_work_attempts(work_order_id, number);
                    CREATE INDEX idx_team_attempts_state_started
                        ON team_work_attempts(state, started_at);
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (22, ?)",
                    (_utc_now(),),
                )
            adaptive_team_completion = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 23"
            ).fetchone()
            if adaptive_team_completion is None:
                if not self._new_database:
                    self._backup_before_migration(23)
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    ALTER TABLE analysis_executor_leases RENAME TO analysis_executor_leases_v22;
                    ALTER TABLE team_work_attempts RENAME TO team_work_attempts_v22;
                    ALTER TABLE team_work_records RENAME TO team_work_records_v22;
                    DROP INDEX IF EXISTS idx_executor_leases_workspace_state;
                    DROP INDEX IF EXISTS idx_team_attempts_work_number;
                    DROP INDEX IF EXISTS idx_team_attempts_state_started;
                    DROP INDEX IF EXISTS idx_team_work_workspace_updated;
                    DROP INDEX IF EXISTS idx_team_work_parent_state;

                    CREATE TABLE team_work_records (
                        work_order_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        parent_request_id TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        state TEXT NOT NULL,
                        workspace_revision INTEGER NOT NULL,
                        work_order_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (authority IN ('advisor', 'executor', 'reviewer')),
                        CHECK (state IN (
                            'queued', 'running', 'incomplete', 'retrying', 'waiting_retry',
                            'completed', 'failed', 'cancelled', 'skipped'
                        ))
                    );
                    INSERT INTO team_work_records SELECT * FROM team_work_records_v22;
                    CREATE INDEX idx_team_work_workspace_updated
                        ON team_work_records(workspace_id, updated_at DESC);
                    CREATE INDEX idx_team_work_parent_state
                        ON team_work_records(parent_request_id, state, created_at);

                    CREATE TABLE analysis_executor_leases (
                        run_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        release_reason TEXT,
                        acquired_at TEXT NOT NULL,
                        released_at TEXT,
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN ('active', 'released'))
                    );
                    INSERT INTO analysis_executor_leases SELECT * FROM analysis_executor_leases_v22;
                    CREATE INDEX idx_executor_leases_workspace_state
                        ON analysis_executor_leases(workspace_id, state, acquired_at);

                    CREATE TABLE team_work_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        work_order_id TEXT NOT NULL,
                        number INTEGER NOT NULL,
                        child_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        usage_json TEXT NOT NULL,
                        failure_code TEXT,
                        retryability TEXT,
                        error TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        UNIQUE(work_order_id, number),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (number >= 1),
                        CHECK (state IN (
                            'starting', 'running', 'completed', 'failed',
                            'timed_out', 'cancelled', 'interrupted'
                        )),
                        CHECK (retryability IS NULL OR retryability IN ('automatic', 'manual', 'never'))
                    );
                    INSERT INTO team_work_attempts SELECT * FROM team_work_attempts_v22;
                    CREATE INDEX idx_team_attempts_work_number
                        ON team_work_attempts(work_order_id, number);
                    CREATE INDEX idx_team_attempts_state_started
                        ON team_work_attempts(state, started_at);

                    DROP TABLE analysis_executor_leases_v22;
                    DROP TABLE team_work_attempts_v22;
                    DROP TABLE team_work_records_v22;
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (23, ?)",
                    (_utc_now(),),
                )
            task_output_deliveries = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 24"
            ).fetchone()
            if task_output_deliveries is None:
                if not self._new_database:
                    self._backup_before_migration(24)
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    ALTER TABLE task_artifact_links RENAME TO task_artifact_links_v23;
                    DROP INDEX IF EXISTS idx_task_artifact_links_task;
                    CREATE TABLE task_artifact_links (
                        task_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        relation TEXT NOT NULL,
                        origin_request_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (
                            task_id, artifact_id, version, relation, origin_request_id
                        ),
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id)
                    );
                    INSERT INTO task_artifact_links (
                        task_id, artifact_id, version, relation, origin_request_id, created_at
                    )
                    SELECT task_id, artifact_id, version, relation,
                           COALESCE(origin_request_id, ''), created_at
                    FROM task_artifact_links_v23;
                    DROP TABLE task_artifact_links_v23;
                    CREATE INDEX idx_task_artifact_links_task
                        ON task_artifact_links(task_id, created_at DESC);
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (24, ?)",
                    (_utc_now(),),
                )
            task_workflows = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 25"
            ).fetchone()
            if task_workflows is None:
                if not self._new_database:
                    self._backup_before_migration(25)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS task_workflows (
                        request_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        activity TEXT NOT NULL,
                        checkpoint_json TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL,
                        failure_fingerprint TEXT,
                        repeated_failures INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        CHECK (state IN (
                            'planning', 'awaiting_execution', 'verifying', 'repairing',
                            'publishing', 'awaiting_review', 'synthesizing', 'completed',
                            'recoverable_incomplete', 'failed', 'cancelled'
                        )),
                        CHECK (repeated_failures >= 0)
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_workflows_task_updated
                        ON task_workflows(task_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_task_workflows_state_heartbeat
                        ON task_workflows(state, heartbeat_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (25, ?)",
                    (_utc_now(),),
                )
            expert_code_executions = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 26"
            ).fetchone()
            if expert_code_executions is None:
                if not self._new_database:
                    self._backup_before_migration(26)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS code_executions (
                        execution_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL,
                        child_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        result_json TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN (
                            'running', 'succeeded', 'failed', 'timed_out',
                            'resource_limited', 'cancelled'
                        ))
                    );
                    CREATE INDEX IF NOT EXISTS idx_code_executions_work
                        ON code_executions(work_order_id, started_at);
                    CREATE INDEX IF NOT EXISTS idx_code_executions_task
                        ON code_executions(task_id, started_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (26, ?)",
                    (_utc_now(),),
                )
            expert_team_authority = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 27"
            ).fetchone()
            if expert_team_authority is None:
                if not self._new_database:
                    self._backup_before_migration(27)
                # Versions 19-23 constrained new rows to the retired
                # advisor/executor authorities.  Rebuild the three related
                # tables so current Expert work can be persisted while old
                # audit rows remain readable through the boundary projector.
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    ALTER TABLE code_executions RENAME TO code_executions_v26;
                    ALTER TABLE analysis_executor_leases RENAME TO analysis_executor_leases_v26;
                    ALTER TABLE team_work_attempts RENAME TO team_work_attempts_v26;
                    ALTER TABLE team_work_records RENAME TO team_work_records_v26;
                    DROP INDEX IF EXISTS idx_code_executions_work;
                    DROP INDEX IF EXISTS idx_code_executions_task;
                    DROP INDEX IF EXISTS idx_executor_leases_workspace_state;
                    DROP INDEX IF EXISTS idx_team_attempts_work_number;
                    DROP INDEX IF EXISTS idx_team_attempts_state_started;
                    DROP INDEX IF EXISTS idx_team_work_workspace_updated;
                    DROP INDEX IF EXISTS idx_team_work_parent_state;

                    CREATE TABLE team_work_records (
                        work_order_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        parent_request_id TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        state TEXT NOT NULL,
                        workspace_revision INTEGER NOT NULL,
                        work_order_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (authority IN ('expert', 'reviewer', 'advisor', 'executor')),
                        CHECK (state IN (
                            'queued', 'running', 'incomplete', 'retrying', 'waiting_retry',
                            'completed', 'failed', 'cancelled', 'skipped'
                        ))
                    );
                    INSERT INTO team_work_records SELECT * FROM team_work_records_v26;
                    CREATE INDEX idx_team_work_workspace_updated
                        ON team_work_records(workspace_id, updated_at DESC);
                    CREATE INDEX idx_team_work_parent_state
                        ON team_work_records(parent_request_id, state, created_at);

                    CREATE TABLE team_work_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        work_order_id TEXT NOT NULL,
                        number INTEGER NOT NULL,
                        child_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        usage_json TEXT NOT NULL,
                        failure_code TEXT,
                        retryability TEXT,
                        error TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        UNIQUE(work_order_id, number),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (number >= 1),
                        CHECK (state IN (
                            'starting', 'running', 'completed', 'failed',
                            'timed_out', 'cancelled', 'interrupted'
                        )),
                        CHECK (retryability IS NULL OR retryability IN ('automatic', 'manual', 'never'))
                    );
                    INSERT INTO team_work_attempts SELECT * FROM team_work_attempts_v26;
                    CREATE INDEX idx_team_attempts_work_number
                        ON team_work_attempts(work_order_id, number);
                    CREATE INDEX idx_team_attempts_state_started
                        ON team_work_attempts(state, started_at);

                    CREATE TABLE analysis_executor_leases (
                        run_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        release_reason TEXT,
                        acquired_at TEXT NOT NULL,
                        released_at TEXT,
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN ('active', 'released'))
                    );
                    INSERT INTO analysis_executor_leases
                        SELECT run_id, workspace_id, work_order_id, 'released',
                               COALESCE(release_reason, 'retired architecture'),
                               acquired_at, COALESCE(released_at, acquired_at)
                        FROM analysis_executor_leases_v26;

                    CREATE TABLE code_executions (
                        execution_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL,
                        child_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        result_json TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN (
                            'running', 'succeeded', 'failed', 'timed_out',
                            'resource_limited', 'cancelled'
                        ))
                    );
                    INSERT INTO code_executions SELECT * FROM code_executions_v26;
                    CREATE INDEX idx_code_executions_work
                        ON code_executions(work_order_id, started_at);
                    CREATE INDEX idx_code_executions_task
                        ON code_executions(task_id, started_at);

                    DROP TABLE code_executions_v26;
                    DROP TABLE analysis_executor_leases_v26;
                    DROP TABLE team_work_attempts_v26;
                    DROP TABLE team_work_records_v26;
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (27, ?)",
                    (_utc_now(),),
                )
            expert_execution_foreign_key = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 28"
            ).fetchone()
            if expert_execution_foreign_key is None:
                if not self._new_database:
                    self._backup_before_migration(28)
                foreign_targets = {
                    str(row["table"])
                    for row in self._connection.execute(
                        "PRAGMA foreign_key_list(code_executions)"
                    ).fetchall()
                }
                if "team_work_records" not in foreign_targets:
                    self._connection.executescript(
                        """
                        PRAGMA foreign_keys = OFF;
                        ALTER TABLE code_executions RENAME TO code_executions_v27;
                        DROP INDEX IF EXISTS idx_code_executions_work;
                        DROP INDEX IF EXISTS idx_code_executions_task;
                        CREATE TABLE code_executions (
                            execution_id TEXT PRIMARY KEY,
                            workspace_id TEXT NOT NULL,
                            task_id TEXT NOT NULL,
                            work_order_id TEXT NOT NULL,
                            child_id TEXT NOT NULL,
                            state TEXT NOT NULL,
                            request_json TEXT NOT NULL,
                            result_json TEXT,
                            started_at TEXT NOT NULL,
                            ended_at TEXT,
                            FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                            FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                            CHECK (state IN (
                                'running', 'succeeded', 'failed', 'timed_out',
                                'resource_limited', 'cancelled'
                            ))
                        );
                        INSERT INTO code_executions SELECT * FROM code_executions_v27;
                        DROP TABLE code_executions_v27;
                        CREATE INDEX idx_code_executions_work
                            ON code_executions(work_order_id, started_at);
                        CREATE INDEX idx_code_executions_task
                            ON code_executions(task_id, started_at);
                        PRAGMA foreign_keys = ON;
                        """
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (28, ?)",
                    (_utc_now(),),
                )
            hierarchy_reset = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 29"
            ).fetchone()
            if hierarchy_reset is None:
                if not self._new_database:
                    self._backup_before_migration(29)
                # This is an intentional breaking architecture migration.  Old
                # child execution records cannot be projected into the new
                # Coordinator -> Expert contract without preserving the very
                # attempt/executor semantics it replaces.  Keep conversations,
                # task files, and published artifacts; discard only orchestration
                # runtime state and rebuild its tables with the current contract.
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    DROP TABLE IF EXISTS team_work_attempts;
                    DROP TABLE IF EXISTS analysis_executor_leases;
                    DROP TABLE IF EXISTS code_executions;
                    DROP TABLE IF EXISTS team_work_records;
                    DROP INDEX IF EXISTS idx_team_attempts_work_number;
                    DROP INDEX IF EXISTS idx_team_attempts_state_started;
                    DROP INDEX IF EXISTS idx_executor_leases_workspace_state;
                    DROP INDEX IF EXISTS idx_code_executions_work;
                    DROP INDEX IF EXISTS idx_code_executions_task;
                    DROP INDEX IF EXISTS idx_team_work_workspace_updated;
                    DROP INDEX IF EXISTS idx_team_work_parent_state;

                    CREATE TABLE team_work_records (
                        work_order_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        parent_request_id TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        state TEXT NOT NULL,
                        workspace_revision INTEGER NOT NULL,
                        work_order_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (authority IN ('expert', 'discussion')),
                        CHECK (state IN (
                            'queued', 'running', 'incomplete', 'completed',
                            'failed', 'cancelled', 'skipped'
                        ))
                    );
                    CREATE INDEX idx_team_work_workspace_updated
                        ON team_work_records(workspace_id, updated_at DESC);
                    CREATE INDEX idx_team_work_parent_state
                        ON team_work_records(parent_request_id, state, created_at);

                    CREATE TABLE code_executions (
                        execution_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        work_order_id TEXT NOT NULL,
                        child_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        result_json TEXT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        FOREIGN KEY (work_order_id) REFERENCES team_work_records(work_order_id),
                        CHECK (state IN (
                            'running', 'succeeded', 'failed', 'timed_out',
                            'resource_limited', 'cancelled'
                        ))
                    );
                    CREATE INDEX idx_code_executions_work
                        ON code_executions(work_order_id, started_at);
                    CREATE INDEX idx_code_executions_task
                        ON code_executions(task_id, started_at);
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (29, ?)",
                    (_utc_now(),),
                )
            retired_run_model = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 30"
            ).fetchone()
            if retired_run_model is None:
                if not self._new_database:
                    self._backup_before_migration(30)
                # Published files live outside SQLite and are deliberately kept.
                # Only the retired orchestration state is removed.
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    DROP TABLE IF EXISTS analysis_run_events;
                    DROP TABLE IF EXISTS analysis_attempts;
                    DROP TABLE IF EXISTS analysis_runs;
                    DROP INDEX IF EXISTS idx_analysis_run_events_run;
                    DROP INDEX IF EXISTS idx_analysis_attempts_run;
                    DROP INDEX IF EXISTS idx_analysis_attempts_operation;
                    DROP INDEX IF EXISTS idx_analysis_runs_workspace;
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (30, ?)",
                    (_utc_now(),),
                )
            retired_acceptance_model = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 31"
            ).fetchone()
            if retired_acceptance_model is None:
                if not self._new_database:
                    self._backup_before_migration(31)
                self._connection.executescript(
                    """
                    DROP TABLE IF EXISTS review_records;
                    DROP TABLE IF EXISTS verification_records;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (31, ?)",
                    (_utc_now(),),
                )
            retired_legacy_coordination = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 32"
            ).fetchone()
            if retired_legacy_coordination is None:
                if not self._new_database:
                    self._backup_before_migration(32)
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    DROP TABLE IF EXISTS multi_agent_proposals;
                    DROP TABLE IF EXISTS multi_agent_tasks;
                    DROP TABLE IF EXISTS task_map_states;
                    DROP INDEX IF EXISTS idx_multi_agent_proposals_workspace;
                    DROP INDEX IF EXISTS idx_multi_agent_proposals_task;
                    DROP INDEX IF EXISTS idx_multi_agent_tasks_workspace;

                    ALTER TABLE artifact_projections RENAME TO artifact_projections_v31;
                    CREATE TABLE artifact_projections (
                        workspace_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        lifecycle_state TEXT NOT NULL,
                        impact_state TEXT NOT NULL,
                        impact_reasons_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source_event_id TEXT,
                        PRIMARY KEY (workspace_id, artifact_id, version),
                        FOREIGN KEY (workspace_id, artifact_id, version)
                            REFERENCES artifact_versions(workspace_id, artifact_id, version)
                    );
                    INSERT INTO artifact_projections (
                        workspace_id, artifact_id, version, lifecycle_state,
                        impact_state, impact_reasons_json, updated_at, source_event_id
                    )
                    SELECT workspace_id, artifact_id, version, lifecycle_state,
                           impact_state, impact_reasons_json, updated_at, source_event_id
                    FROM artifact_projections_v31;
                    DROP TABLE artifact_projections_v31;

                    ALTER TABLE resource_usage_records RENAME TO resource_usage_records_v31;
                    CREATE TABLE resource_usage_records (
                        usage_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        work_order_id TEXT,
                        resource_kind TEXT NOT NULL,
                        resource_name TEXT NOT NULL,
                        resource_version TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO resource_usage_records (
                        usage_id, workspace_id, work_order_id, resource_kind,
                        resource_name, resource_version, created_at
                    )
                    SELECT usage_id, workspace_id, work_order_id, resource_kind,
                           resource_name, resource_version, created_at
                    FROM resource_usage_records_v31;
                    DROP TABLE resource_usage_records_v31;
                    CREATE INDEX idx_resource_usage_workspace
                        ON resource_usage_records(workspace_id, created_at);
                    CREATE INDEX idx_resource_usage_work_order
                        ON resource_usage_records(work_order_id, created_at);
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (32, ?)",
                    (_utc_now(),),
                )
            simplified_workflow = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 33"
            ).fetchone()
            if simplified_workflow is None:
                if not self._new_database:
                    self._backup_before_migration(33)
                self._connection.executescript(
                    """
                    PRAGMA foreign_keys = OFF;
                    ALTER TABLE task_workflows RENAME TO task_workflows_v32;
                    DROP INDEX IF EXISTS idx_task_workflows_task_updated;
                    DROP INDEX IF EXISTS idx_task_workflows_state_heartbeat;
                    CREATE TABLE task_workflows (
                        request_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        activity TEXT NOT NULL,
                        checkpoint_json TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL,
                        failure_fingerprint TEXT,
                        repeated_failures INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        CHECK (state IN (
                            'planning', 'working', 'completed', 'incomplete',
                            'failed', 'cancelled'
                        )),
                        CHECK (repeated_failures >= 0)
                    );
                    INSERT INTO task_workflows (
                        request_id, task_id, workspace_id, state, activity,
                        checkpoint_json, heartbeat_at, failure_fingerprint,
                        repeated_failures, created_at, updated_at
                    )
                    SELECT request_id, task_id, workspace_id,
                           CASE
                               WHEN state IN ('planning', 'completed', 'failed', 'cancelled')
                                   THEN state
                               WHEN state = 'recoverable_incomplete' THEN 'incomplete'
                               ELSE 'working'
                           END,
                           activity, checkpoint_json, heartbeat_at, failure_fingerprint,
                           repeated_failures, created_at, updated_at
                    FROM task_workflows_v32;
                    DROP TABLE task_workflows_v32;
                    CREATE INDEX idx_task_workflows_task_updated
                        ON task_workflows(task_id, updated_at DESC);
                    CREATE INDEX idx_task_workflows_state_heartbeat
                        ON task_workflows(state, heartbeat_at);
                    PRAGMA foreign_keys = ON;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (33, ?)",
                    (_utc_now(),),
                )
            task_source_ownership = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 34"
            ).fetchone()
            if task_source_ownership is None:
                if not self._new_database:
                    self._backup_before_migration(34)
                # Older desktop imports were sometimes recorded as task outputs. Migrate
                # only import/register requests; derived datasets produced by Experts stay
                # outputs and never become implicit task inputs.
                self._connection.executescript(
                    """
                    INSERT OR IGNORE INTO task_artifact_links (
                        task_id, artifact_id, version, relation, origin_request_id, created_at
                    )
                    SELECT l.task_id, l.artifact_id, l.version, 'source',
                           l.origin_request_id, l.created_at
                    FROM task_artifact_links AS l
                    JOIN research_tasks AS t ON t.task_id = l.task_id
                    JOIN artifact_versions AS v
                      ON v.workspace_id = t.workspace_id
                     AND v.artifact_id = l.artifact_id AND v.version = l.version
                    LEFT JOIN request_records AS r
                      ON r.request_id = NULLIF(l.origin_request_id, '')
                    WHERE l.relation != 'source'
                      AND v.artifact_type IN ('dataset', 'paper')
                      AND (
                        v.created_by = 'import'
                        OR r.request_type IN (
                            'dataset.import', 'paper.import', 'paper.register', 'artifact.create'
                        )
                      );

                    DELETE FROM task_artifact_links
                    WHERE relation != 'source'
                      AND EXISTS (
                        SELECT 1 FROM task_artifact_links AS source_link
                        WHERE source_link.task_id = task_artifact_links.task_id
                          AND source_link.artifact_id = task_artifact_links.artifact_id
                          AND source_link.version = task_artifact_links.version
                          AND source_link.relation = 'source'
                      )
                      AND EXISTS (
                        SELECT 1 FROM research_tasks AS t
                        JOIN artifact_versions AS v ON v.workspace_id = t.workspace_id
                        LEFT JOIN request_records AS r
                          ON r.request_id = NULLIF(task_artifact_links.origin_request_id, '')
                        WHERE t.task_id = task_artifact_links.task_id
                          AND v.artifact_id = task_artifact_links.artifact_id
                          AND v.version = task_artifact_links.version
                          AND v.artifact_type IN ('dataset', 'paper')
                          AND (
                            v.created_by = 'import'
                            OR r.request_type IN (
                                'dataset.import', 'paper.import', 'paper.register', 'artifact.create'
                            )
                          )
                      );
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (34, ?)",
                    (_utc_now(),),
                )
            atomic_task_artifact_ownership = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 35"
            ).fetchone()
            if atomic_task_artifact_ownership is None:
                if not self._new_database:
                    self._backup_before_migration(35)
                intent_columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(artifact_commit_intents)"
                    ).fetchall()
                }
                if "task_id" not in intent_columns:
                    self._connection.execute(
                        "ALTER TABLE artifact_commit_intents ADD COLUMN task_id TEXT"
                    )
                if "task_relation" not in intent_columns:
                    self._connection.execute(
                        "ALTER TABLE artifact_commit_intents ADD COLUMN task_relation TEXT"
                    )
                # Recover only ownership that is provable from the original durable
                # request.  Workspace membership alone never implies Task ownership.
                self._connection.executescript(
                    """
                    UPDATE artifact_commit_intents AS i
                    SET task_id = (
                            SELECT r.task_id FROM request_records AS r
                            WHERE r.request_id = COALESCE(NULLIF(i.origin_request_id, ''), i.request_id)
                        ),
                        task_relation = 'source'
                    WHERE i.status IN ('prepared', 'committed')
                      AND i.task_id IS NULL
                      AND EXISTS (
                        SELECT 1 FROM request_records AS r
                        WHERE r.request_id = COALESCE(NULLIF(i.origin_request_id, ''), i.request_id)
                          AND r.task_id IS NOT NULL
                          AND r.request_type IN (
                              'dataset.import', 'paper.import', 'paper.register', 'artifact.create'
                          )
                      )
                      AND EXISTS (
                        SELECT 1 FROM artifact_versions AS v
                        WHERE v.workspace_id = i.workspace_id
                          AND v.artifact_id = i.artifact_id AND v.version = i.version
                          AND v.artifact_type IN ('dataset', 'paper')
                      );

                    UPDATE artifact_commit_intents AS i
                    SET task_id = (
                            SELECT r.task_id FROM request_records AS r
                            WHERE r.request_id = COALESCE(NULLIF(i.origin_request_id, ''), i.request_id)
                        ),
                        task_relation = 'candidate'
                    WHERE i.status IN ('prepared', 'committed')
                      AND i.task_id IS NULL
                      AND EXISTS (
                        SELECT 1 FROM request_records AS r
                        WHERE r.request_id = COALESCE(NULLIF(i.origin_request_id, ''), i.request_id)
                          AND r.task_id IS NOT NULL AND r.request_type = 'session.submit'
                      )
                      AND EXISTS (
                        SELECT 1 FROM artifact_versions AS v
                        WHERE v.workspace_id = i.workspace_id
                          AND v.artifact_id = i.artifact_id AND v.version = i.version
                          AND v.artifact_type IN ('interactive_view', 'report')
                      );

                    INSERT OR IGNORE INTO task_artifact_links (
                        task_id, artifact_id, version, relation, origin_request_id, created_at
                    )
                    SELECT task_id, artifact_id, version, task_relation,
                           COALESCE(origin_request_id, request_id, ''), updated_at
                    FROM artifact_commit_intents
                    WHERE status = 'committed'
                      AND task_id IS NOT NULL AND task_relation IS NOT NULL;
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (35, ?)",
                    (_utc_now(),),
                )
            persistent_expert_workstreams = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 36"
            ).fetchone()
            if persistent_expert_workstreams is None:
                if not self._new_database:
                    self._backup_before_migration(36)
                columns = {
                    row["name"]
                    for row in self._connection.execute(
                        "PRAGMA table_info(team_work_records)"
                    ).fetchall()
                }
                if "checkpoint_json" not in columns:
                    self._connection.execute(
                        "ALTER TABLE team_work_records ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '{}'"
                    )
                if "resume_count" not in columns:
                    self._connection.execute(
                        "ALTER TABLE team_work_records ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0"
                    )
                default_checkpoint = _canonical_json(WorkstreamCheckpoint().model_dump(mode="json"))
                self._connection.execute(
                    "UPDATE team_work_records SET checkpoint_json = ? WHERE checkpoint_json = '{}'",
                    (default_checkpoint,),
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (36, ?)",
                    (_utc_now(),),
                )
            current_workstream_phases = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 37"
            ).fetchone()
            if current_workstream_phases is None:
                if not self._new_database:
                    self._backup_before_migration(37)
                phase_aliases = {
                    "output_ready": WorkstreamPhase.RESULT_READY.value,
                    "submitted": WorkstreamPhase.DELIVERING.value,
                    "accepted": WorkstreamPhase.COMPLETED.value,
                }
                rows = self._connection.execute(
                    "SELECT work_order_id, checkpoint_json FROM team_work_records"
                ).fetchall()
                for row in rows:
                    checkpoint = json.loads(row["checkpoint_json"] or "{}")
                    phase = checkpoint.get("phase")
                    if phase not in phase_aliases:
                        continue
                    checkpoint["phase"] = phase_aliases[phase]
                    self._connection.execute(
                        "UPDATE team_work_records SET checkpoint_json = ? WHERE work_order_id = ?",
                        (_canonical_json(checkpoint), row["work_order_id"]),
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (37, ?)",
                    (_utc_now(),),
                )
            coordinator_result_receipts = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 38"
            ).fetchone()
            if coordinator_result_receipts is None:
                if not self._new_database:
                    self._backup_before_migration(38)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS coordinator_result_receipts (
                        request_id TEXT PRIMARY KEY,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (request_id) REFERENCES request_records(request_id)
                    );
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (38, ?)",
                    (_utc_now(),),
                )
            expert_session_checkpoints = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 39"
            ).fetchone()
            if expert_session_checkpoints is None:
                if not self._new_database:
                    self._backup_before_migration(39)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS expert_session_checkpoints (
                        workspace_id TEXT NOT NULL,
                        task_scope TEXT NOT NULL,
                        participant_key TEXT NOT NULL,
                        job_key TEXT NOT NULL,
                        work_order_id TEXT NOT NULL,
                        message_schema_version INTEGER NOT NULL,
                        messages_json TEXT NOT NULL,
                        message_count INTEGER NOT NULL,
                        compaction_generation INTEGER NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (
                            workspace_id, task_scope, participant_key, job_key
                        )
                    );
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (39, ?)",
                    (_utc_now(),),
                )
            research_learning = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 40"
            ).fetchone()
            if research_learning is None:
                if not self._new_database:
                    self._backup_before_migration(40)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS research_observations (
                        observation_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        work_order_id TEXT,
                        kind TEXT NOT NULL,
                        statement TEXT NOT NULL,
                        evidence_refs_json TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        reusable INTEGER NOT NULL,
                        lesson_key TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                        CHECK (reusable IN (0, 1)),
                        CHECK (confidence >= 0.0 AND confidence <= 1.0)
                    );
                    CREATE INDEX idx_research_observations_task
                        ON research_observations(task_id, created_at);
                    CREATE INDEX idx_research_observations_lesson
                        ON research_observations(workspace_id, lesson_key, task_id)
                        WHERE reusable = 1 AND lesson_key IS NOT NULL;

                    CREATE TABLE IF NOT EXISTS experience_candidates (
                        candidate_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        candidate_key TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        proposed_rule TEXT NOT NULL,
                        evidence_observation_ids_json TEXT NOT NULL,
                        distinct_task_count INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        evaluation_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (workspace_id, candidate_key),
                        CHECK (distinct_task_count >= 1),
                        CHECK (status IN ('proposed', 'validated', 'rejected', 'promoted'))
                    );
                    CREATE INDEX idx_experience_candidates_status
                        ON experience_candidates(workspace_id, status, updated_at);

                    CREATE TABLE IF NOT EXISTS evolved_skill_revisions (
                        workspace_id TEXT NOT NULL,
                        skill_name TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        rule TEXT NOT NULL,
                        candidate_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, skill_name, version),
                        FOREIGN KEY (candidate_id) REFERENCES experience_candidates(candidate_id),
                        CHECK (version >= 1),
                        CHECK (status IN ('active', 'retired'))
                    );
                    CREATE INDEX idx_evolved_skill_active
                        ON evolved_skill_revisions(workspace_id, skill_name, status);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (40, ?)",
                    (_utc_now(),),
                )
            expert_session_history = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 41"
            ).fetchone()
            if expert_session_history is None:
                if not self._new_database:
                    self._backup_before_migration(41)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS expert_session_message_history (
                        workspace_id TEXT NOT NULL,
                        task_scope TEXT NOT NULL,
                        participant_key TEXT NOT NULL,
                        job_key TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        message_json TEXT NOT NULL,
                        message_sha256 TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        PRIMARY KEY (
                            workspace_id, task_scope, participant_key, job_key, sequence
                        )
                    );
                    CREATE INDEX IF NOT EXISTS idx_expert_session_history_lookup
                        ON expert_session_message_history (
                            workspace_id, task_scope, participant_key, job_key, sequence
                        );
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (41, ?)",
                    (_utc_now(),),
                )
            research_observation_links = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 42"
            ).fetchone()
            if research_observation_links is None:
                if not self._new_database:
                    self._backup_before_migration(42)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS research_observation_links (
                        source_observation_id TEXT NOT NULL,
                        target_observation_id TEXT NOT NULL,
                        relation TEXT NOT NULL,
                        rationale TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (
                            source_observation_id, target_observation_id, relation
                        ),
                        FOREIGN KEY (source_observation_id)
                            REFERENCES research_observations(observation_id),
                        FOREIGN KEY (target_observation_id)
                            REFERENCES research_observations(observation_id),
                        CHECK (source_observation_id != target_observation_id),
                        CHECK (relation IN ('complements', 'contradicts', 'supersedes'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_research_observation_links_target
                        ON research_observation_links(target_observation_id, created_at);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (42, ?)",
                    (_utc_now(),),
                )
            skill_curator_reviews = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 43"
            ).fetchone()
            if skill_curator_reviews is None:
                if not self._new_database:
                    self._backup_before_migration(43)
                columns = {
                    str(row["name"])
                    for row in self._connection.execute(
                        "PRAGMA table_info(experience_candidates)"
                    ).fetchall()
                }
                if "review_json" not in columns:
                    self._connection.execute(
                        "ALTER TABLE experience_candidates ADD COLUMN review_json TEXT"
                    )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (43, ?)",
                    (_utc_now(),),
                )
            structured_interactions = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 44"
            ).fetchone()
            if structured_interactions is None:
                if not self._new_database:
                    self._backup_before_migration(44)
                self._connection.executescript(
                    """
                    ALTER TABLE task_interactions RENAME TO task_interactions_v43;
                    DROP INDEX IF EXISTS idx_task_interactions_task_state;
                    DROP INDEX IF EXISTS idx_task_interactions_request_state;
                    CREATE TABLE task_interactions (
                        interaction_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        task_id TEXT,
                        workspace_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        principal TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        question TEXT NOT NULL,
                        options_json TEXT NOT NULL DEFAULT '[]',
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        CHECK (kind IN ('question', 'permission', 'paper_selection')),
                        CHECK (state IN ('pending', 'answered', 'interrupted'))
                    );
                    INSERT INTO task_interactions (
                        interaction_id, request_id, task_id, workspace_id, session_id, principal,
                        kind, question, options_json, state, created_at, resolved_at
                    )
                    SELECT
                        interaction_id, request_id, task_id, workspace_id, session_id, principal,
                        kind, question, '[]', state, created_at, resolved_at
                    FROM task_interactions_v43;
                    DROP TABLE task_interactions_v43;
                    CREATE INDEX idx_task_interactions_task_state
                        ON task_interactions(task_id, state, created_at DESC);
                    CREATE INDEX idx_task_interactions_request_state
                        ON task_interactions(request_id, state);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (44, ?)",
                    (_utc_now(),),
                )
            saved_experience_inbox = self._connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 45"
            ).fetchone()
            if saved_experience_inbox is None:
                if not self._new_database:
                    self._backup_before_migration(45)
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS saved_experiences (
                        experience_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        work_order_id TEXT,
                        agent_id TEXT NOT NULL,
                        agent_role TEXT NOT NULL,
                        text TEXT NOT NULL,
                        status TEXT NOT NULL,
                        absorbed_by_skill TEXT,
                        absorbed_by_version INTEGER,
                        reviewed_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (status IN ('pending', 'absorbed', 'dismissed')),
                        CHECK (
                            (absorbed_by_skill IS NULL AND absorbed_by_version IS NULL)
                            OR
                            (absorbed_by_skill IS NOT NULL AND absorbed_by_version >= 1)
                        )
                    );
                    CREATE INDEX idx_saved_experiences_pending
                        ON saved_experiences(workspace_id, status, created_at, experience_id);

                    CREATE TABLE IF NOT EXISTS evolved_skill_documents (
                        workspace_id TEXT NOT NULL,
                        skill_name TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        description TEXT NOT NULL,
                        roles_json TEXT NOT NULL,
                        content TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        source_experience_ids_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        reviewer_model TEXT NOT NULL,
                        review_reason TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, skill_name, version),
                        CHECK (version >= 1),
                        CHECK (status IN ('active', 'retired'))
                    );
                    CREATE INDEX idx_evolved_skill_documents_active
                        ON evolved_skill_documents(workspace_id, skill_name, status);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (45, ?)",
                    (_utc_now(),),
                )
            self._enforce_sqlite_private_files()

    def _backup_before_migration(self, target_version: int) -> Path:
        """Create a private SQLite backup before changing an existing schema."""

        backup_directory = ensure_private_directory(self.path.parent / "backups")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = backup_directory / f"workspace-before-v{target_version}-{timestamp}.sqlite3"
        destination = sqlite3.connect(str(target))
        try:
            self._connection.backup(destination)
        finally:
            destination.close()
        ensure_private_file(target)
        backups = sorted(backup_directory.glob("workspace-before-v*.sqlite3"))
        for stale in backups[:-3]:
            stale.unlink(missing_ok=True)
        return target

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")
                self._enforce_sqlite_private_files()

    def _enforce_sqlite_private_files(self) -> None:
        """Keep SQLite's WAL sidecars under the same local-only policy as the DB."""

        ensure_private_file(self.path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                ensure_private_file(sidecar)

    @staticmethod
    def canonical_hash(request: RequestEnvelope) -> tuple[str, dict[str, Any]]:
        canonical = canonical_request_fields(request)
        digest = hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()
        return digest, canonical

    def reserve(self, request: RequestEnvelope, *, principal: str) -> RequestReservation:
        """Persist a request identity before emitting ``request.accepted``."""

        canonical_hash, canonical = self.canonical_hash(request)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM request_records WHERE request_id = ?", (request.request_id,)
            ).fetchone()
            if existing is not None:
                record = self._request_from_row(existing)
                if record.canonical_hash != canonical_hash:
                    raise RequestIdConflict(
                        f"Request ID already has a different payload: {request.request_id}"
                    )
                return RequestReservation(record=record, created=False)
            now = _utc_now()
            context = request.context
            connection.execute(
                """
                INSERT INTO request_records (
                    request_id, request_type, canonical_hash, canonical_request_json,
                    principal, session_id, workspace_id, task_id, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)
                """,
                (
                    request.request_id,
                    request.type,
                    canonical_hash,
                    _canonical_json(canonical),
                    principal,
                    context.session_id if context else None,
                    context.workspace_id if context else None,
                    context.task_id if context else None,
                    now,
                    now,
                ),
            )
            record = self._request_from_row(
                connection.execute(
                    "SELECT * FROM request_records WHERE request_id = ?", (request.request_id,)
                ).fetchone()
            )
            return RequestReservation(record=record, created=True)

    def mark_in_progress(self, request_id: str) -> RequestRecord:
        """Move a freshly accepted request into the durable active state."""

        with self._transaction() as connection:
            row = self._require_request_row(connection, request_id)
            record = self._request_from_row(row)
            if record.terminal:
                return record
            connection.execute(
                "UPDATE request_records SET state = 'in_progress', updated_at = ? WHERE request_id = ?",
                (_utc_now(), request_id),
            )
            return self._request_from_row(self._require_request_row(connection, request_id))

    def get_request(self, request_id: str) -> RequestRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM request_records WHERE request_id = ?", (request_id,)
            ).fetchone()
            return self._request_from_row(row) if row is not None else None

    def get_event(self, event_id: str) -> EventEnvelope | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT event_json FROM event_records WHERE event_id = ?", (event_id,)
            ).fetchone()
            return parse_event(json.loads(row["event_json"])) if row is not None else None

    def record_domain_event(self, event: EventEnvelope) -> None:
        """Persist an already-committed domain state notification exactly once before broadcast."""

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM event_records WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if existing is None:
                self._persist_event(connection, event)

    def commit_terminal(self, request_id: str, event: EventEnvelope) -> RequestRecord:
        """Persist a terminal result and its event before any broadcast occurs."""

        if event.type not in TERMINAL_EVENT_TYPES:
            raise RequestStoreError(f"Cannot commit non-terminal event {event.type}")
        if event.request_id != request_id:
            raise RequestStoreError("Terminal event request ID does not match the durable request")
        with self._transaction() as connection:
            record = self._request_from_row(self._require_request_row(connection, request_id))
            if record.terminal:
                if record.terminal_event is not None:
                    return record
                raise RequestStoreError(f"Terminal request has no terminal event: {request_id}")
            self._close_active_tool_calls_in_transaction(
                connection,
                request_id=request_id,
                terminal_event_type=event.type,
            )
            self._persist_event(connection, event)
            connection.execute(
                """
                UPDATE request_records
                SET state = ?, terminal_event_json = ?, terminal_event_id = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    _state_for_terminal_event(event),
                    event.model_dump_json(),
                    event.event_id,
                    _utc_now(),
                    request_id,
                ),
            )
            return self._request_from_row(self._require_request_row(connection, request_id))

    def commit_session_open(
        self,
        *,
        request_id: str,
        session_id: str,
        principal: str,
        workspace_id: str | None,
        terminal_event: EventEnvelope,
    ) -> RequestRecord:
        """Open/refresh a session and commit its request terminal in one transaction."""

        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, request_id)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO session_records (session_id, principal, workspace_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    principal = excluded.principal,
                    workspace_id = excluded.workspace_id,
                    updated_at = excluded.updated_at
                """,
                (session_id, principal, workspace_id, now, now),
            )
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            return self._request_from_row(self._require_request_row(connection, request_id))

    def commit_workspace_open(
        self,
        *,
        request_id: str,
        workspace_id: str,
        path: str,
        expected_revision: int | None,
        event_factory: Callable[
            [WorkspaceSnapshot, int, str | None], tuple[EventEnvelope | None, EventEnvelope]
        ],
    ) -> WorkspaceOpenCommit:
        """Atomically mutate workspace state, domain event, and request terminal."""

        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, request_id)
            existing = connection.execute(
                "SELECT * FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            previous_revision = int(existing["revision"]) if existing is not None else 0
            if expected_revision is not None and expected_revision != previous_revision:
                raise WorkspaceRevisionConflict(previous_revision)
            change: str | None
            if existing is None:
                revision = 1
                change = "opened"
                connection.execute(
                    """
                    INSERT INTO workspace_records (workspace_id, path, revision, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (workspace_id, path, revision, _utc_now()),
                )
            elif existing["path"] != path:
                revision = previous_revision + 1
                change = "updated"
                connection.execute(
                    """
                    UPDATE workspace_records SET path = ?, revision = ?, updated_at = ?
                    WHERE workspace_id = ?
                    """,
                    (path, revision, _utc_now(), workspace_id),
                )
            else:
                revision = previous_revision
                change = None
            snapshot = WorkspaceSnapshot(
                workspace_id=workspace_id,
                path=path,
                revision=revision,
                artifacts=self.list_artifact_summaries(workspace_id),
                active_refs=self.list_active_refs(workspace_id),
                disclosure_policy=self._disclosure_policy_summary_in_connection(
                    connection, workspace_id
                ),
            )
            changed_event, terminal_event = event_factory(snapshot, previous_revision, change)
            if changed_event is not None:
                self._persist_event(connection, changed_event)
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            return WorkspaceOpenCommit(
                snapshot=snapshot,
                previous_revision=previous_revision,
                change=change,
                changed_event=changed_event,
                terminal_event=terminal_event,
            )

    def workspace_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        """Read a complete replaceable workspace snapshot from authoritative projections."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            if row is None:
                return WorkspaceSnapshot(
                    workspace_id=workspace_id,
                    path=None,
                    revision=0,
                    artifacts=[],
                )
            return WorkspaceSnapshot(
                workspace_id=workspace_id,
                path=row["path"],
                revision=int(row["revision"]),
                artifacts=self.list_artifact_summaries(workspace_id),
                active_refs=self.list_active_refs(workspace_id),
                disclosure_policy=self._disclosure_policy_summary_in_connection(
                    self._connection, workspace_id
                ),
            )

    def create_research_task(
        self,
        *,
        workspace_id: str,
        title: str,
        task_id: str | None = None,
    ) -> ResearchTaskRecord:
        """Create one durable research thread without copying workspace artifacts."""

        normalized_title = title.strip()
        if not normalized_title:
            raise RequestStoreError("ResearchTask title must contain non-whitespace text")
        if len(normalized_title) > 512:
            raise RequestStoreError("ResearchTask title exceeds the 512-character limit")
        identifier = task_id or f"task_{uuid4().hex}"
        now = _utc_now()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO research_tasks (
                        task_id, workspace_id, title, status, task_revision, active_request_id,
                        stable_checkpoint_id, conversation_generation, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', 0, NULL, NULL, 0, ?, ?)
                    """,
                    (identifier, workspace_id, normalized_title, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise RequestStoreError(f"ResearchTask already exists: {identifier}") from exc
            return self._research_task_from_row(
                self._require_research_task_row(connection, identifier)
            )

    def list_research_tasks(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[ResearchTaskRecord]:
        """List task summaries without loading transcript or checkpoint payloads."""

        if not 1 <= limit <= 500:
            raise ValueError("ResearchTask list limit must be between 1 and 500")
        query = "SELECT * FROM research_tasks WHERE workspace_id = ?"
        values: list[Any] = [workspace_id]
        if not include_archived:
            query += " AND status != 'archived'"
        query += " ORDER BY updated_at DESC, task_id DESC LIMIT ?"
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
            return [self._research_task_from_row(row) for row in rows]

    def get_research_task(
        self,
        task_id: str,
        *,
        workspace_id: str | None = None,
    ) -> ResearchTaskRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM research_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            task = self._research_task_from_row(row)
            if workspace_id is not None and task.workspace_id != workspace_id:
                return None
            return task

    def rename_research_task(
        self,
        *,
        task_id: str,
        title: str,
        expected_task_revision: int | None,
    ) -> ResearchTaskRecord:
        normalized_title = title.strip()
        if not normalized_title:
            raise RequestStoreError("ResearchTask title must contain non-whitespace text")
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            self._require_task_revision(task, expected_task_revision)
            connection.execute(
                """
                UPDATE research_tasks
                SET title = ?, task_revision = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (normalized_title, task.task_revision + 1, _utc_now(), task_id),
            )
            return self._research_task_from_row(
                self._require_research_task_row(connection, task_id)
            )

    def set_research_task_status(
        self,
        *,
        task_id: str,
        status: ResearchTaskState,
        expected_task_revision: int | None,
    ) -> ResearchTaskRecord:
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            self._require_task_revision(task, expected_task_revision)
            if task.active_request_id is not None:
                raise RequestStoreError("Cannot archive or reopen a task with an active request")
            connection.execute(
                """
                UPDATE research_tasks
                SET status = ?, task_revision = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (status, task.task_revision + 1, _utc_now(), task_id),
            )
            return self._research_task_from_row(
                self._require_research_task_row(connection, task_id)
            )

    def delete_research_task(
        self,
        *,
        task_id: str,
        expected_task_revision: int | None,
        in_flight_request_id: str | None = None,
    ) -> None:
        """Remove one task and its task-scoped rows; workspace evidence stays."""

        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            self._require_task_revision(task, expected_task_revision)
            if task.active_request_id is not None:
                raise RequestStoreError("Cannot delete a task with an active request")
            # Code executions are owned by the Task even though they also point at a
            # WorkOrder.  Delete by the owning task first so lifecycle cleanup remains
            # correct if a future WorkOrder is not reachable through conversation
            # request records.
            connection.execute("DELETE FROM code_executions WHERE task_id = ?", (task_id,))
            request_ids = [
                row["request_id"]
                for row in connection.execute(
                    "SELECT request_id FROM request_records WHERE task_id = ?", (task_id,)
                ).fetchall()
                if row["request_id"] != in_flight_request_id
            ]
            if request_ids:
                request_marks = ", ".join("?" for _ in request_ids)
                work_order_ids = [
                    row["work_order_id"]
                    for row in connection.execute(
                        "SELECT work_order_id FROM team_work_records "
                        f"WHERE parent_request_id IN ({request_marks})",
                        request_ids,
                    ).fetchall()
                ]
                if work_order_ids:
                    order_marks = ", ".join("?" for _ in work_order_ids)
                    # Follow the WorkOrder edge so partially written code-execution
                    # rows cannot leave the parent conversation undeletable.
                    connection.execute(
                        f"DELETE FROM code_executions WHERE work_order_id IN ({order_marks})",
                        work_order_ids,
                    )
                connection.execute(
                    f"DELETE FROM team_work_records WHERE parent_request_id IN ({request_marks})",
                    request_ids,
                )
                for table in (
                    "operation_checkpoints",
                    "tool_call_records",
                    "event_records",
                    "task_interactions",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE request_id IN ({request_marks})",
                        request_ids,
                    )
            connection.execute("DELETE FROM task_interactions WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_workflows WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_transcript_items WHERE task_id = ?", (task_id,))
            connection.execute(
                "DELETE FROM task_conversation_checkpoints WHERE task_id = ?", (task_id,)
            )
            connection.execute("DELETE FROM task_artifact_links WHERE task_id = ?", (task_id,))
            # Expert memory is task-scoped but deliberately has no foreign-key edge:
            # sessions may be checkpointed before their WorkOrder row is durable.
            # Treat it as task-owned lifecycle data here so deleting a task cannot
            # leave hidden conversation history behind.
            connection.execute(
                "DELETE FROM expert_session_message_history "
                "WHERE workspace_id = ? AND task_scope = ?",
                (task.workspace_id, task_id),
            )
            connection.execute(
                "DELETE FROM expert_session_checkpoints "
                "WHERE workspace_id = ? AND task_scope = ?",
                (task.workspace_id, task_id),
            )
            # Unreviewed experience belongs to the deleted task. Absorbed or
            # dismissed rows stay as the immutable audit trail of a Skill review.
            connection.execute(
                "DELETE FROM saved_experiences WHERE task_id = ? AND status = 'pending'",
                (task_id,),
            )
            connection.execute(
                """
                DELETE FROM research_observation_links
                WHERE source_observation_id IN (
                    SELECT observation_id FROM research_observations WHERE task_id = ?
                ) OR target_observation_id IN (
                    SELECT observation_id FROM research_observations WHERE task_id = ?
                )
                """,
                (task_id, task_id),
            )
            connection.execute("DELETE FROM research_observations WHERE task_id = ?", (task_id,))
            # Coordinator receipts are the durable child of foreground requests.
            # Clear them before their request rows; SQLite correctly rejects the
            # opposite order while foreign-key enforcement is enabled.
            connection.execute(
                "DELETE FROM coordinator_result_receipts WHERE request_id IN "
                "(SELECT request_id FROM request_records WHERE task_id = ? "
                "AND request_id IS NOT ?)",
                (task_id, in_flight_request_id),
            )
            connection.execute(
                "DELETE FROM request_records WHERE task_id = ? AND request_id IS NOT ?",
                (task_id, in_flight_request_id),
            )
            connection.execute("DELETE FROM research_tasks WHERE task_id = ?", (task_id,))

    def begin_task_request(
        self,
        *,
        task_id: str,
        request_id: str,
        expected_task_revision: int | None,
    ) -> ResearchTaskRecord:
        """Claim the one foreground request slot before the model runtime starts."""

        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            self._require_task_revision(task, expected_task_revision)
            if task.status != "active":
                raise RequestStoreError("Only active ResearchTasks may receive an agent request")
            if task.active_request_id is not None:
                raise RequestStoreError("ResearchTask already has an active foreground request")
            connection.execute(
                """
                UPDATE research_tasks
                SET active_request_id = ?, task_revision = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (request_id, task.task_revision + 1, _utc_now(), task_id),
            )
            return self._research_task_from_row(
                self._require_research_task_row(connection, task_id)
            )

    def start_task_workflow(
        self,
        *,
        request_id: str,
        task_id: str,
        workspace_id: str,
    ) -> TaskWorkflowRecord:
        """Create the durable orchestration identity for one foreground task request."""

        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            if task.workspace_id != workspace_id or task.active_request_id != request_id:
                raise RequestStoreError("Task workflow must belong to the active task request")
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO task_workflows (
                    request_id, task_id, workspace_id, state, activity, checkpoint_json,
                    heartbeat_at, failure_fingerprint, repeated_failures, created_at, updated_at
                ) VALUES (?, ?, ?, 'planning', 'Planning the task', '{}', ?, NULL, 0, ?, ?)
                """,
                (request_id, task_id, workspace_id, now, now, now),
            )
            row = connection.execute(
                "SELECT * FROM task_workflows WHERE request_id = ?", (request_id,)
            ).fetchone()
            assert row is not None
            return self._task_workflow_from_row(row)

    def transition_task_workflow(
        self,
        *,
        request_id: str,
        state: TaskWorkflowState,
        activity: str,
        checkpoint: dict[str, Any] | None = None,
        failure_fingerprint: str | None = None,
    ) -> TaskWorkflowRecord | None:
        """Persist a workflow checkpoint and heartbeat without changing task revision."""

        if not activity.strip() or len(activity) > 512:
            raise RequestStoreError("Workflow activity must be a bounded non-empty string")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM task_workflows WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            prior = self._task_workflow_from_row(row)
            repeat_count = prior.repeated_failures
            if failure_fingerprint is not None:
                repeat_count = (
                    prior.repeated_failures + 1
                    if prior.failure_fingerprint == failure_fingerprint
                    else 1
                )
            now = _utc_now()
            payload = prior.checkpoint if checkpoint is None else checkpoint
            connection.execute(
                """
                UPDATE task_workflows
                SET state = ?, activity = ?, checkpoint_json = ?, heartbeat_at = ?,
                    failure_fingerprint = ?, repeated_failures = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    state,
                    activity.strip(),
                    _canonical_json(payload),
                    now,
                    failure_fingerprint,
                    repeat_count,
                    now,
                    request_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM task_workflows WHERE request_id = ?", (request_id,)
            ).fetchone()
            assert updated is not None
            return self._task_workflow_from_row(updated)

    def update_task_workflow_progress(
        self,
        *,
        request_id: str,
        activity: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> TaskWorkflowRecord | None:
        """Persist non-terminal progress without inventing a lifecycle transition.

        This is the single state-preserving progress API for foreground task
        workflows.  Callers that intentionally change lifecycle state must use
        :meth:`transition_task_workflow` instead.
        """

        current = self.get_task_workflow(request_id)
        if current is None:
            return None
        return self.transition_task_workflow(
            request_id=request_id,
            state=current.state,
            activity=activity,
            checkpoint=checkpoint,
        )

    def get_task_workflow(self, request_id: str) -> TaskWorkflowRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM task_workflows WHERE request_id = ?", (request_id,)
            ).fetchone()
            return self._task_workflow_from_row(row) if row is not None else None

    def record_research_observation(self, draft: ResearchObservationDraft) -> ResearchObservation:
        """Commit one typed task observation and refresh only evidence-backed candidates."""

        with self._transaction() as connection:
            task = self._require_research_task(connection, draft.task_id)
            if task.workspace_id != draft.workspace_id:
                raise RequestStoreError("Research observation belongs to another workspace")
            return self._insert_research_observation(connection, draft)

    def _insert_research_observation(
        self,
        connection: sqlite3.Connection,
        draft: ResearchObservationDraft,
    ) -> ResearchObservation:
        identity = _canonical_json(draft.model_dump(mode="json"))
        observation_id = "obs_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        now = _utc_now()
        connection.execute(
            """
            INSERT OR IGNORE INTO research_observations (
                observation_id, workspace_id, task_id, request_id, work_order_id,
                kind, statement, evidence_refs_json, outcome, confidence, reusable,
                lesson_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                draft.workspace_id,
                draft.task_id,
                draft.request_id,
                draft.work_order_id,
                draft.kind.value,
                draft.statement,
                _canonical_json(list(draft.evidence_refs)),
                draft.outcome,
                draft.confidence,
                int(draft.reusable),
                draft.lesson_key,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_observations WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        assert row is not None
        observation = self._research_observation_from_row(row)
        self._link_research_observation(connection, observation)
        return observation

    @staticmethod
    def _link_research_observation(
        connection: sqlite3.Connection,
        observation: ResearchObservation,
    ) -> None:
        """Link new memory to matching prior evidence before skill extraction.

        This is the deterministic Observation Linker stage: evidence-enriched
        observations supersede weaker precursors, conflicting outcomes are
        retained as contradictions, and repeated compatible observations
        complement one another. Nothing is deleted or silently overwritten.
        """

        prior_rows = connection.execute(
            """
            SELECT * FROM research_observations
            WHERE workspace_id = ? AND kind = ? AND statement = ?
              AND observation_id != ?
            ORDER BY created_at DESC, observation_id DESC
            LIMIT 16
            """,
            (
                observation.workspace_id,
                observation.kind.value,
                observation.statement,
                observation.observation_id,
            ),
        ).fetchall()
        new_evidence = set(observation.evidence_refs)
        for prior in prior_rows:
            prior_evidence = set(json.loads(prior["evidence_refs_json"]))
            prior_outcome = str(prior["outcome"])
            if new_evidence > prior_evidence or (
                observation.outcome == "supported" and prior_outcome == "limited"
            ):
                relation = ObservationRelation.SUPERSEDES
                rationale = "The newer observation carries stronger immutable evidence."
            elif {observation.outcome, prior_outcome} == {"supported", "failed"}:
                relation = ObservationRelation.CONTRADICTS
                rationale = "The observations record incompatible evidence outcomes."
            else:
                relation = ObservationRelation.COMPLEMENTS
                rationale = "The observations independently support the same scoped statement."
            connection.execute(
                """
                INSERT OR IGNORE INTO research_observation_links (
                    source_observation_id, target_observation_id, relation,
                    rationale, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    str(prior["observation_id"]),
                    relation.value,
                    rationale,
                    observation.created_at,
                ),
            )

    def _refresh_experience_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        candidate_key: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM research_observations
            WHERE workspace_id = ? AND lesson_key = ? AND reusable = 1
            ORDER BY created_at, observation_id
            """,
            (workspace_id, candidate_key),
        ).fetchall()
        distinct_tasks = {str(row["task_id"]) for row in rows}
        # Repetition inside one task improves task memory but is not cross-task evolution.
        if len(distinct_tasks) < 3:
            return
        existing = connection.execute(
            """
            SELECT * FROM experience_candidates
            WHERE workspace_id = ? AND candidate_key = ?
            """,
            (workspace_id, candidate_key),
        ).fetchone()
        if existing is not None and existing["status"] != CandidateStatus.PROPOSED.value:
            return
        representative = rows[0]
        kind = ObservationKind(str(representative["kind"]))
        evidence_ids = tuple(str(row["observation_id"]) for row in rows[-64:])
        candidate_id = (
            "candidate_"
            + hashlib.sha256(f"{workspace_id}\0{candidate_key}".encode("utf-8")).hexdigest()[:32]
        )
        now = _utc_now()
        title = f"Reusable {kind.value} observed across {len(distinct_tasks)} tasks"
        rule = proposed_rule(kind, str(representative["statement"]))
        if existing is None:
            connection.execute(
                """
                INSERT INTO experience_candidates (
                    candidate_id, workspace_id, candidate_key, kind, title,
                    proposed_rule, evidence_observation_ids_json, distinct_task_count,
                    status, evaluation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', NULL, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    candidate_key,
                    kind.value,
                    title,
                    rule,
                    _canonical_json(list(evidence_ids)),
                    len(distinct_tasks),
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE experience_candidates
                SET title = ?, proposed_rule = ?, evidence_observation_ids_json = ?,
                    distinct_task_count = ?, updated_at = ?
                WHERE candidate_id = ? AND status = 'proposed'
                """,
                (
                    title,
                    rule,
                    _canonical_json(list(evidence_ids)),
                    len(distinct_tasks),
                    now,
                    existing["candidate_id"],
                ),
            )

    def list_research_observations(self, *, task_id: str) -> tuple[ResearchObservation, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM research_observations
                WHERE task_id = ? ORDER BY created_at, observation_id
                """,
                (task_id,),
            ).fetchall()
            return tuple(self._research_observation_from_row(row) for row in rows)

    def list_research_observation_links(
        self,
        *,
        task_id: str,
    ) -> tuple[ResearchObservationLink, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT link.*
                FROM research_observation_links AS link
                JOIN research_observations AS source
                  ON source.observation_id = link.source_observation_id
                WHERE source.task_id = ?
                ORDER BY link.created_at, link.source_observation_id,
                         link.target_observation_id
                """,
                (task_id,),
            ).fetchall()
            return tuple(
                ResearchObservationLink(
                    source_observation_id=str(row["source_observation_id"]),
                    target_observation_id=str(row["target_observation_id"]),
                    relation=ObservationRelation(str(row["relation"])),
                    rationale=str(row["rationale"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )

    def project_research_state(self, *, workspace_id: str, task_id: str) -> ResearchState:
        """Project the AutoResearch loop from existing work; no parallel mode state exists."""

        work = self.list_task_team_work(workspace_id=workspace_id, task_id=task_id)
        observations = self.list_research_observations(task_id=task_id)
        active = sum(item.state in {WorkStatus.QUEUED, WorkStatus.RUNNING} for item in work)
        completed = sum(item.state is WorkStatus.COMPLETED for item in work)
        gaps = tuple(
            dict.fromkeys(
                item.statement for item in observations if item.kind is ObservationKind.EVIDENCE_GAP
            )
        )
        result_refs = tuple(
            dict.fromkeys(
                ref.key
                for item in work
                if item.result is not None
                for ref in item.result.result_refs
            )
        )
        if active:
            status = "investigating"
        elif not work:
            status = "idle"
        elif gaps:
            status = "limited"
        elif completed or result_refs:
            status = "evidence_ready"
        else:
            status = "limited"
        return ResearchState(
            task_id=task_id,
            round_count=len(work),
            completed_rounds=completed,
            active_rounds=active,
            evidence_gaps=gaps,
            result_refs=result_refs,
            observation_ids=tuple(item.observation_id for item in observations),
            status=status,
        )

    def save_experience(
        self,
        *,
        workspace_id: str,
        task_id: str,
        request_id: str,
        agent_id: str,
        agent_role: str,
        text: str,
        work_order_id: str | None = None,
    ) -> SavedExperience:
        """Save one Agent-authored note without classifying or promoting it."""

        normalized_text = re.sub(r"\s+", " ", text).strip()
        if not normalized_text or len(normalized_text) > 2_000:
            raise RequestStoreError("Saved experience must contain 1 to 2,000 characters")
        normalized_agent_id = agent_id.strip()
        normalized_role = agent_role.strip()
        normalized_request_id = request_id.strip()
        if not normalized_agent_id or not normalized_role or not normalized_request_id:
            raise RequestStoreError("Saved experience identity is incomplete")
        identity = _canonical_json(
            {
                "workspace_id": workspace_id,
                "task_id": task_id,
                "request_id": normalized_request_id,
                "work_order_id": work_order_id,
                "agent_id": normalized_agent_id,
                "agent_role": normalized_role,
                "text": normalized_text,
            }
        )
        experience_id = "experience_" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:32]
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            if task.workspace_id != workspace_id:
                raise RequestStoreError("Saved experience belongs to another workspace")
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO saved_experiences (
                    experience_id, workspace_id, task_id, request_id, work_order_id,
                    agent_id, agent_role, text, status, absorbed_by_skill,
                    absorbed_by_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)
                """,
                (
                    experience_id,
                    workspace_id,
                    task_id,
                    normalized_request_id,
                    work_order_id,
                    normalized_agent_id,
                    normalized_role,
                    normalized_text,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM saved_experiences WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
            assert row is not None
            return self._saved_experience_from_row(row)

    def list_saved_experiences(
        self,
        *,
        workspace_id: str,
        status: SavedExperienceStatus | None = None,
        limit: int | None = None,
    ) -> tuple[SavedExperience, ...]:
        """Return the explicit experience inbox; no implicit observations are included."""

        if limit is not None and limit < 1:
            raise RequestStoreError("Saved experience limit must be positive")
        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        sql = (
            "SELECT * FROM saved_experiences WHERE "
            + " AND ".join(clauses)
            + " ORDER BY (reviewed_at IS NOT NULL), reviewed_at, created_at, experience_id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, values).fetchall()
        return tuple(self._saved_experience_from_row(row) for row in rows)

    def list_workspaces_with_pending_experiences(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT workspace_id FROM saved_experiences
                WHERE status = 'pending' AND reviewed_at IS NULL ORDER BY workspace_id
                """
            ).fetchall()
        return tuple(str(row["workspace_id"]) for row in rows)

    def dismiss_saved_experiences(
        self, *, workspace_id: str, experience_ids: tuple[str, ...]
    ) -> int:
        """Mark reviewed non-reusable notes so the Curator will not reread them."""

        identifiers = tuple(dict.fromkeys(experience_ids))
        if not identifiers:
            return 0
        marks = ",".join("?" for _ in identifiers)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE saved_experiences
                SET status = 'dismissed', reviewed_at = ?, updated_at = ?
                WHERE workspace_id = ? AND status = 'pending'
                  AND experience_id IN ({marks})
                """,
                (_utc_now(), _utc_now(), workspace_id, *identifiers),
            )
            return int(cursor.rowcount)

    def defer_saved_experiences(
        self, *, workspace_id: str, experience_ids: tuple[str, ...]
    ) -> int:
        """Mark still-pending notes reviewed until new experience reopens the workspace."""

        identifiers = tuple(dict.fromkeys(experience_ids))
        if not identifiers:
            return 0
        marks = ",".join("?" for _ in identifiers)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE saved_experiences SET reviewed_at = ?, updated_at = ?
                WHERE workspace_id = ? AND status = 'pending'
                  AND experience_id IN ({marks})
                """,
                (_utc_now(), _utc_now(), workspace_id, *identifiers),
            )
            return int(cursor.rowcount)

    def install_evolved_skill_revision(
        self,
        *,
        workspace_id: str,
        skill_name: str,
        description: str,
        roles: tuple[str, ...],
        content: str,
        source_experience_ids: tuple[str, ...],
        reviewer_model: str,
        review_reason: str,
    ) -> EvolvedSkillRevision:
        """Atomically install a full Skill and absorb exactly its cited notes."""

        if re.fullmatch(r"[a-z][a-z0-9-]{2,127}", skill_name) is None:
            raise RequestStoreError("Invalid evolved skill name")
        normalized_description = description.strip()
        normalized_roles = tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))
        normalized_content = content.strip()
        identifiers = tuple(dict.fromkeys(source_experience_ids))
        if not normalized_description or not normalized_roles or not normalized_content:
            raise RequestStoreError("Evolved skill content, description, and roles are required")
        if not identifiers:
            raise RequestStoreError("Evolved skill needs at least one source experience")
        marks = ",".join("?" for _ in identifiers)
        digest = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT experience_id FROM saved_experiences
                WHERE workspace_id = ? AND status = 'pending'
                  AND experience_id IN ({marks})
                """,
                (workspace_id, *identifiers),
            ).fetchall()
            found = {str(row["experience_id"]) for row in rows}
            if found != set(identifiers):
                raise RequestStoreError(
                    "Evolved skill cites missing, already reviewed, or foreign experience"
                )
            version_row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM evolved_skill_documents
                WHERE workspace_id = ? AND skill_name = ?
                """,
                (workspace_id, skill_name),
            ).fetchone()
            version = int(version_row["version"]) + 1
            now = _utc_now()
            connection.execute(
                """
                UPDATE evolved_skill_documents SET status = 'retired'
                WHERE workspace_id = ? AND skill_name = ? AND status = 'active'
                """,
                (workspace_id, skill_name),
            )
            connection.execute(
                """
                INSERT INTO evolved_skill_documents (
                    workspace_id, skill_name, version, description, roles_json,
                    content, content_sha256, source_experience_ids_json, status,
                    reviewer_model, review_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    workspace_id,
                    skill_name,
                    version,
                    normalized_description,
                    _canonical_json(list(normalized_roles)),
                    normalized_content,
                    digest,
                    _canonical_json(list(identifiers)),
                    reviewer_model.strip(),
                    review_reason.strip(),
                    now,
                ),
            )
            cursor = connection.execute(
                f"""
                UPDATE saved_experiences
                SET status = 'absorbed', absorbed_by_skill = ?,
                    absorbed_by_version = ?, reviewed_at = ?, updated_at = ?
                WHERE workspace_id = ? AND status = 'pending'
                  AND experience_id IN ({marks})
                """,
                (skill_name, version, now, now, workspace_id, *identifiers),
            )
            if int(cursor.rowcount) != len(identifiers):
                raise RequestStoreError("Experience absorption was not atomic")
            row = connection.execute(
                """
                SELECT * FROM evolved_skill_documents
                WHERE workspace_id = ? AND skill_name = ? AND version = ?
                """,
                (workspace_id, skill_name, version),
            ).fetchone()
            assert row is not None
            return self._evolved_skill_revision_from_row(row)

    def list_evolved_skill_revisions(
        self,
        *,
        workspace_id: str,
        skill_name: str | None = None,
        active_only: bool = False,
    ) -> tuple[EvolvedSkillRevision, ...]:
        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if skill_name is not None:
            clauses.append("skill_name = ?")
            values.append(skill_name)
        if active_only:
            clauses.append("status = 'active'")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM evolved_skill_documents WHERE "
                + " AND ".join(clauses)
                + " ORDER BY skill_name, version",
                values,
            ).fetchall()
        return tuple(self._evolved_skill_revision_from_row(row) for row in rows)

    def list_experience_candidates(
        self,
        *,
        workspace_id: str,
        status: CandidateStatus | None = None,
    ) -> tuple[ExperienceCandidate, ...]:
        with self._lock:
            if status is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM experience_candidates
                    WHERE workspace_id = ? ORDER BY updated_at, candidate_id
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM experience_candidates
                    WHERE workspace_id = ? AND status = ?
                    ORDER BY updated_at, candidate_id
                    """,
                    (workspace_id, status.value),
                ).fetchall()
            return tuple(self._experience_candidate_from_row(row) for row in rows)

    def list_candidate_evidence(
        self, *, candidate_id: str
    ) -> tuple[ResearchObservation, ...]:
        """Return the immutable observations cited by one experience candidate."""

        with self._lock:
            row = self._connection.execute(
                "SELECT evidence_observation_ids_json FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise RequestStoreError(f"Unknown experience candidate: {candidate_id}")
            identifiers = tuple(json.loads(row["evidence_observation_ids_json"]))
            if not identifiers:
                return ()
            placeholders = ",".join("?" for _ in identifiers)
            evidence_rows = self._connection.execute(
                f"SELECT * FROM research_observations WHERE observation_id IN ({placeholders}) "
                "ORDER BY created_at, observation_id",
                identifiers,
            ).fetchall()
            return tuple(self._research_observation_from_row(item) for item in evidence_rows)

    def review_experience_candidate(
        self, *, candidate_id: str, review: SkillReview
    ) -> ExperienceCandidate:
        """Apply a semantic decision made by the independent Skill Curator LLM.

        The store checks identity and transition safety only. It never tries to
        infer whether the scientific experience is correct or reusable.
        """

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise RequestStoreError(f"Unknown experience candidate: {candidate_id}")
            if row["status"] != CandidateStatus.PROPOSED.value:
                raise RequestStoreError("Only proposed candidates may be reviewed")
            if review.decision in {"create", "update"}:
                target = (review.target_skill or "").strip()
                if re.fullmatch(r"[a-z][a-z0-9-]{2,127}", target) is None:
                    raise RequestStoreError("An approved skill review needs a valid target skill")
                if not review.revised_rule.strip():
                    raise RequestStoreError("An approved skill review needs a reusable rule")
                next_status = CandidateStatus.VALIDATED.value
                next_rule = review.revised_rule.strip()
            elif review.decision == "reject":
                next_status = CandidateStatus.REJECTED.value
                next_rule = str(row["proposed_rule"])
            else:
                next_status = CandidateStatus.PROPOSED.value
                next_rule = str(row["proposed_rule"])
            connection.execute(
                """
                UPDATE experience_candidates
                SET status = ?, proposed_rule = ?, review_json = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    next_status,
                    next_rule,
                    _canonical_json(review.model_dump(mode="json")),
                    _utc_now(),
                    candidate_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            assert updated is not None
            return self._experience_candidate_from_row(updated)

    def create_curator_candidate(
        self,
        *,
        workspace_id: str,
        task_id: str,
        request_id: str,
        kind: ObservationKind,
        proposed_rule: str,
        evidence_refs: tuple[str, ...],
        confidence: float,
    ) -> ExperienceCandidate:
        """Persist one candidate already selected semantically by Skill Curator.

        This method deliberately performs no reuse or correctness classification.
        It only binds the model's proposed rule to existing task evidence and
        creates an idempotent candidate that can pass through the normal review
        and revision transaction.
        """

        if not proposed_rule.strip():
            raise RequestStoreError("Curator candidate needs a bounded rule")
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            if task.workspace_id != workspace_id:
                raise RequestStoreError("Curator candidate belongs to another workspace")
            observation = self._insert_research_observation(
                connection,
                ResearchObservationDraft(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    request_id=request_id,
                    kind=kind,
                    statement=proposed_rule.strip(),
                    evidence_refs=evidence_refs,
                    outcome="supported",
                    confidence=confidence,
                    reusable=False,
                ),
            )
            identity = f"{workspace_id}\0{task_id}\0{proposed_rule.strip()}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            candidate_id = "candidate_" + digest[:32]
            candidate_key = "curator:" + digest[:32]
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO experience_candidates (
                    candidate_id, workspace_id, candidate_key, kind, title,
                    proposed_rule, evidence_observation_ids_json, distinct_task_count,
                    status, evaluation_json, review_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'proposed', NULL, NULL, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    candidate_key,
                    kind.value,
                    "Skill Curator task insight",
                    proposed_rule.strip(),
                    _canonical_json([observation.observation_id]),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            assert row is not None
            return self._experience_candidate_from_row(row)

    def evaluate_experience_candidate(
        self,
        *,
        candidate_id: str,
        evaluation: SkillEvaluation,
    ) -> ExperienceCandidate:
        """Validate a candidate only after replay evidence beats the baseline safely."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise RequestStoreError(f"Unknown experience candidate: {candidate_id}")
            if row["status"] != CandidateStatus.PROPOSED.value:
                raise RequestStoreError("Only proposed candidates may be evaluated")
            passed = (
                evaluation.sample_size >= 3
                and evaluation.safety_regressions == 0
                and evaluation.candidate_score >= evaluation.baseline_score + 0.05
            )
            connection.execute(
                """
                UPDATE experience_candidates
                SET status = ?, evaluation_json = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    (CandidateStatus.VALIDATED.value if passed else CandidateStatus.REJECTED.value),
                    _canonical_json(evaluation.model_dump(mode="json")),
                    _utc_now(),
                    candidate_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            assert updated is not None
            return self._experience_candidate_from_row(updated)

    def promote_experience_candidate(self, *, candidate_id: str, skill_name: str) -> SkillRevision:
        """Create a versioned active rule; older revisions remain available for rollback."""

        if re.fullmatch(r"[a-z][a-z0-9-]{2,127}", skill_name) is None:
            raise RequestStoreError("Invalid evolved skill name")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM experience_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise RequestStoreError(f"Unknown experience candidate: {candidate_id}")
            if row["status"] != CandidateStatus.VALIDATED.value:
                raise RequestStoreError("Only validated candidates may be promoted")
            version_row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM evolved_skill_revisions
                WHERE workspace_id = ? AND skill_name = ?
                """,
                (row["workspace_id"], skill_name),
            ).fetchone()
            version = int(version_row["version"]) + 1
            now = _utc_now()
            connection.execute(
                """
                UPDATE evolved_skill_revisions SET status = 'retired'
                WHERE workspace_id = ? AND skill_name = ? AND status = 'active'
                """,
                (row["workspace_id"], skill_name),
            )
            connection.execute(
                """
                INSERT INTO evolved_skill_revisions (
                    workspace_id, skill_name, version, rule, candidate_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    row["workspace_id"],
                    skill_name,
                    version,
                    row["proposed_rule"],
                    candidate_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE experience_candidates SET status = 'promoted', updated_at = ?
                WHERE candidate_id = ?
                """,
                (now, candidate_id),
            )
            return SkillRevision(
                workspace_id=str(row["workspace_id"]),
                skill_name=skill_name,
                version=version,
                rule=str(row["proposed_rule"]),
                candidate_id=candidate_id,
                status="active",
                created_at=now,
            )

    def list_skill_revisions(
        self, *, workspace_id: str, skill_name: str | None = None
    ) -> tuple[SkillRevision, ...]:
        with self._lock:
            if skill_name is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM evolved_skill_revisions
                    WHERE workspace_id = ? ORDER BY skill_name, version
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM evolved_skill_revisions
                    WHERE workspace_id = ? AND skill_name = ? ORDER BY version
                    """,
                    (workspace_id, skill_name),
                ).fetchall()
            return tuple(self._skill_revision_from_row(row) for row in rows)

    @staticmethod
    def _research_observation_from_row(row: sqlite3.Row) -> ResearchObservation:
        return ResearchObservation(
            observation_id=str(row["observation_id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            request_id=str(row["request_id"]),
            work_order_id=(str(row["work_order_id"]) if row["work_order_id"] is not None else None),
            kind=ObservationKind(str(row["kind"])),
            statement=str(row["statement"]),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            outcome=str(row["outcome"]),
            confidence=float(row["confidence"]),
            reusable=bool(row["reusable"]),
            lesson_key=(str(row["lesson_key"]) if row["lesson_key"] else None),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _experience_candidate_from_row(row: sqlite3.Row) -> ExperienceCandidate:
        return ExperienceCandidate(
            candidate_id=str(row["candidate_id"]),
            workspace_id=str(row["workspace_id"]),
            candidate_key=str(row["candidate_key"]),
            kind=ObservationKind(str(row["kind"])),
            title=str(row["title"]),
            proposed_rule=str(row["proposed_rule"]),
            evidence_observation_ids=tuple(json.loads(row["evidence_observation_ids_json"])),
            distinct_task_count=int(row["distinct_task_count"]),
            status=CandidateStatus(str(row["status"])),
            evaluation=(
                SkillEvaluation.model_validate_json(row["evaluation_json"])
                if row["evaluation_json"] is not None
                else None
            ),
            review=(
                SkillReview.model_validate_json(row["review_json"])
                if "review_json" in row.keys() and row["review_json"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _skill_revision_from_row(row: sqlite3.Row) -> SkillRevision:
        return SkillRevision(
            workspace_id=str(row["workspace_id"]),
            skill_name=str(row["skill_name"]),
            version=int(row["version"]),
            rule=str(row["rule"]),
            candidate_id=str(row["candidate_id"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _saved_experience_from_row(row: sqlite3.Row) -> SavedExperience:
        return SavedExperience(
            experience_id=str(row["experience_id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            request_id=str(row["request_id"]),
            work_order_id=(
                str(row["work_order_id"]) if row["work_order_id"] is not None else None
            ),
            agent_id=str(row["agent_id"]),
            agent_role=str(row["agent_role"]),
            text=str(row["text"]),
            status=SavedExperienceStatus(str(row["status"])),
            absorbed_by_skill=(
                str(row["absorbed_by_skill"])
                if row["absorbed_by_skill"] is not None
                else None
            ),
            absorbed_by_version=(
                int(row["absorbed_by_version"])
                if row["absorbed_by_version"] is not None
                else None
            ),
            reviewed_at=(str(row["reviewed_at"]) if row["reviewed_at"] is not None else None),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _evolved_skill_revision_from_row(row: sqlite3.Row) -> EvolvedSkillRevision:
        return EvolvedSkillRevision(
            workspace_id=str(row["workspace_id"]),
            skill_name=str(row["skill_name"]),
            version=int(row["version"]),
            description=str(row["description"]),
            roles=tuple(json.loads(row["roles_json"])),
            content=str(row["content"]),
            content_sha256=str(row["content_sha256"]),
            source_experience_ids=tuple(json.loads(row["source_experience_ids_json"])),
            status=str(row["status"]),
            reviewer_model=str(row["reviewer_model"]),
            review_reason=str(row["review_reason"]),
            created_at=str(row["created_at"]),
        )

    def save_expert_session_checkpoint(
        self,
        *,
        workspace_id: str,
        task_scope: str,
        participant_key: str,
        job_key: str,
        work_order_id: str,
        messages: list[dict[str, Any]],
        compaction_generation: int,
    ) -> ExpertSessionCheckpoint:
        """Replace one logical Expert's conversation at a resumable boundary."""

        if not all((workspace_id, task_scope, participant_key, job_key, work_order_id)):
            raise TaskCheckpointIncompatible("Expert session checkpoint identity is incomplete")
        if compaction_generation < 0:
            raise TaskCheckpointIncompatible(
                "Expert session compaction generation cannot be negative"
            )
        if len(messages) > 10_000 or any(not isinstance(message, dict) for message in messages):
            raise TaskCheckpointIncompatible("Expert session checkpoint messages are invalid")
        messages_json = _canonical_json(messages)
        if len(messages_json.encode("utf-8")) > 2 * 1024 * 1024:
            raise TaskCheckpointIncompatible(
                "Expert session checkpoint exceeds the hard size limit"
            )
        payload_sha256 = hashlib.sha256(messages_json.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._transaction() as connection:
            prior_row = connection.execute(
                """
                SELECT messages_json FROM expert_session_checkpoints
                WHERE workspace_id = ? AND task_scope = ?
                  AND participant_key = ? AND job_key = ?
                """,
                (workspace_id, task_scope, participant_key, job_key),
            ).fetchone()
            prior_messages: list[dict[str, Any]] = []
            if prior_row is not None:
                try:
                    decoded_prior = json.loads(str(prior_row["messages_json"]))
                    if isinstance(decoded_prior, list) and all(
                        isinstance(message, dict) for message in decoded_prior
                    ):
                        prior_messages = decoded_prior
                except json.JSONDecodeError:
                    prior_messages = []
            history_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM expert_session_message_history
                    WHERE workspace_id = ? AND task_scope = ?
                      AND participant_key = ? AND job_key = ?
                    """,
                    (workspace_id, task_scope, participant_key, job_key),
                ).fetchone()["count"]
            )
            history_append: list[dict[str, Any]] = []
            if history_count == 0 and prior_messages:
                history_append.extend(prior_messages)
            if not prior_messages:
                history_append.extend(messages)
            else:
                common_prefix = 0
                for old_message, new_message in zip(prior_messages, messages, strict=False):
                    if old_message != new_message:
                        break
                    common_prefix += 1
                if common_prefix:
                    history_append.extend(messages[common_prefix:])
                elif messages != prior_messages:
                    # Context compaction may replace the old prefix with a
                    # summary. Preserve the raw old history and append the new
                    # compacted boundary rather than silently losing either.
                    history_append.extend(messages)
            next_sequence = history_count + 1
            for message in history_append:
                serialized_message = _canonical_json(message)
                connection.execute(
                    """
                    INSERT INTO expert_session_message_history (
                        workspace_id, task_scope, participant_key, job_key,
                        sequence, message_json, message_sha256, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        task_scope,
                        participant_key,
                        job_key,
                        next_sequence,
                        serialized_message,
                        hashlib.sha256(serialized_message.encode("utf-8")).hexdigest(),
                        now,
                    ),
                )
                next_sequence += 1
            connection.execute(
                """
                INSERT INTO expert_session_checkpoints (
                    workspace_id, task_scope, participant_key, job_key, work_order_id,
                    message_schema_version, messages_json, message_count,
                    compaction_generation, payload_sha256, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    workspace_id, task_scope, participant_key, job_key
                ) DO UPDATE SET
                    message_schema_version = excluded.message_schema_version,
                    work_order_id = excluded.work_order_id,
                    messages_json = excluded.messages_json,
                    message_count = excluded.message_count,
                    compaction_generation = excluded.compaction_generation,
                    payload_sha256 = excluded.payload_sha256,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    task_scope,
                    participant_key,
                    job_key,
                    work_order_id,
                    messages_json,
                    len(messages),
                    compaction_generation,
                    payload_sha256,
                    now,
                ),
            )
        return ExpertSessionCheckpoint(
            workspace_id=workspace_id,
            task_scope=task_scope,
            participant_key=participant_key,
            job_key=job_key,
            work_order_id=work_order_id,
            messages=tuple(messages),
            compaction_generation=compaction_generation,
            payload_sha256=payload_sha256,
            updated_at=now,
        )

    def get_expert_session_checkpoint(
        self,
        *,
        workspace_id: str,
        task_scope: str,
        participant_key: str,
        job_key: str,
    ) -> ExpertSessionCheckpoint | None:
        """Load and verify one logical Expert's latest resumable boundary."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM expert_session_checkpoints
                WHERE workspace_id = ? AND task_scope = ?
                  AND participant_key = ? AND job_key = ?
                """,
                (workspace_id, task_scope, participant_key, job_key),
            ).fetchone()
        return self._expert_session_checkpoint_from_row(row) if row is not None else None

    def get_expert_session_message_history(
        self,
        *,
        workspace_id: str,
        task_scope: str,
        participant_key: str,
        job_key: str,
    ) -> tuple[dict[str, Any], ...]:
        """Read the append-only display history for one logical Expert."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT message_json, message_sha256
                FROM expert_session_message_history
                WHERE workspace_id = ? AND task_scope = ?
                  AND participant_key = ? AND job_key = ?
                ORDER BY sequence
                """,
                (workspace_id, task_scope, participant_key, job_key),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            raw = str(row["message_json"])
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["message_sha256"]:
                raise TaskCheckpointIncompatible("Expert session history checksum does not match")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TaskCheckpointIncompatible("Expert session history JSON is invalid") from exc
            if not isinstance(message, dict):
                raise TaskCheckpointIncompatible(
                    "Expert session history entry is not a message object"
                )
            messages.append(message)
        return tuple(messages)

    def delete_expert_session_checkpoint(
        self,
        *,
        workspace_id: str,
        task_scope: str,
        participant_key: str,
        job_key: str,
    ) -> None:
        """Remove request-scoped participant memory after its owner closes."""

        with self._transaction() as connection:
            connection.execute(
                """
                DELETE FROM expert_session_message_history
                WHERE workspace_id = ? AND task_scope = ?
                  AND participant_key = ? AND job_key = ?
                """,
                (workspace_id, task_scope, participant_key, job_key),
            )
            connection.execute(
                """
                DELETE FROM expert_session_checkpoints
                WHERE workspace_id = ? AND task_scope = ?
                  AND participant_key = ? AND job_key = ?
                """,
                (workspace_id, task_scope, participant_key, job_key),
            )

    def append_task_transcript_item(
        self,
        *,
        task_id: str,
        item_id: str,
        role: Literal["user", "assistant", "tool", "system"],
        text: str,
        request_id: str | None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        interrupted: bool = False,
    ) -> TaskTranscriptItem:
        """Persist one bounded display record; streaming deltas stay transport-local."""

        if len(text) > 64_000:
            raise RequestStoreError("Task transcript item exceeds the 64 KiB display limit")
        with self._transaction() as connection:
            self._require_research_task(connection, task_id)
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                    "FROM task_transcript_items WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["next_sequence"]
            )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO task_transcript_items (
                    item_id, task_id, sequence, role, text, request_id, turn_id, tool_call_id,
                    interrupted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    task_id,
                    sequence,
                    role,
                    text,
                    request_id,
                    turn_id,
                    tool_call_id,
                    int(interrupted),
                    now,
                ),
            )
            connection.execute(
                "UPDATE research_tasks SET updated_at = ? WHERE task_id = ?", (now, task_id)
            )
            return TaskTranscriptItem(
                item_id=item_id,
                task_id=task_id,
                sequence=sequence,
                role=role,
                text=text,
                request_id=request_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                interrupted=interrupted,
                created_at=now,
            )

    def task_snapshot(
        self,
        *,
        task_id: str,
        transcript_limit: int = 100,
        before_sequence: int | None = None,
    ) -> ResearchTaskSnapshot:
        """Read renderer-safe task state without exposing checkpoint messages."""

        if not 1 <= transcript_limit <= 500:
            raise ValueError("Task transcript limit must be between 1 and 500")
        with self._transaction() as connection:
            task = self._research_task_from_row(
                self._require_research_task_row(connection, task_id)
            )
            query = "SELECT * FROM task_transcript_items WHERE task_id = ?"
            values: list[Any] = [task_id]
            if before_sequence is not None:
                query += " AND sequence < ?"
                values.append(before_sequence)
            query += " ORDER BY sequence DESC LIMIT ?"
            values.append(transcript_limit)
            rows = connection.execute(query, values).fetchall()
            transcript = tuple(reversed(tuple(self._task_transcript_from_row(row) for row in rows)))
            next_cursor: int | None = None
            if transcript:
                older = connection.execute(
                    "SELECT 1 FROM task_transcript_items WHERE task_id = ? AND sequence < ? LIMIT 1",
                    (task_id, transcript[0].sequence),
                ).fetchone()
                if older is not None:
                    next_cursor = transcript[0].sequence
            sources = tuple(self._task_source_summaries_in_connection(connection, task_id))
            outputs = tuple(self._task_output_summaries_in_connection(connection, task_id))
            interactions = tuple(self._pending_interactions_in_connection(connection, task_id))
            try:
                checkpoint = self._checkpoint_for_task_in_connection(connection, task)
                checkpoint_error = None
            except TaskCheckpointIncompatible as exc:
                checkpoint = None
                checkpoint_error = str(exc)
            workflow_row = connection.execute(
                "SELECT * FROM task_workflows WHERE task_id = ? ORDER BY updated_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return ResearchTaskSnapshot(
                task=task,
                transcript=transcript,
                next_transcript_cursor=next_cursor,
                sources=sources,
                outputs=outputs,
                delivery_manifests=tuple(
                    manifest.model_dump(mode="json")
                    for manifest in build_delivery_manifests(outputs)
                ),
                interactions=interactions,
                checkpoint=checkpoint,
                workflow=(
                    self._task_workflow_from_row(workflow_row) if workflow_row is not None else None
                ),
                checkpoint_error=checkpoint_error,
            )

    def list_task_transcript_for_request(
        self,
        *,
        task_id: str,
        request_id: str,
    ) -> tuple[TaskTranscriptItem, ...]:
        """Return the complete visible Coordinator transcript for one request."""

        with self._lock:
            self._require_research_task_row(self._connection, task_id)
            rows = self._connection.execute(
                """
                SELECT * FROM task_transcript_items
                WHERE task_id = ? AND request_id = ?
                ORDER BY sequence
                """,
                (task_id, request_id),
            ).fetchall()
            return tuple(self._task_transcript_from_row(row) for row in rows)

    def create_interaction(
        self,
        *,
        interaction_id: str,
        request_id: str,
        task_id: str | None,
        workspace_id: str,
        session_id: str,
        principal: str,
        question: str,
        kind: Literal["question", "permission", "paper_selection"],
        options: tuple[dict[str, Any], ...] = (),
    ) -> PendingInteractionRecord:
        if not question.strip() or len(question) > 4_000:
            raise RequestStoreError("Interaction must contain at most 4,000 characters")
        if kind == "paper_selection" and not options:
            raise RequestStoreError("Paper selection must contain at least one option")
        if kind != "paper_selection" and options:
            raise RequestStoreError("Only paper selection may contain options")
        if len(options) > 30:
            raise RequestStoreError("Paper selection may contain at most 30 options")
        serialized_options = _canonical_json(list(options))
        if len(serialized_options) > 64_000:
            raise RequestStoreError("Interaction options exceed the 64,000 character limit")
        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, request_id)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO task_interactions (
                    interaction_id, request_id, task_id, workspace_id, session_id, principal,
                    kind, question, options_json, state, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
                """,
                (
                    interaction_id,
                    request_id,
                    task_id,
                    workspace_id,
                    session_id,
                    principal,
                    kind,
                    question.strip(),
                    serialized_options,
                    now,
                ),
            )
            return self._interaction_from_row(
                self._require_interaction_row(connection, interaction_id)
            )

    def commit_interaction_response(
        self,
        *,
        interaction_id: str,
        session_id: str,
        principal: str,
        terminal_event: EventEnvelope,
    ) -> PendingInteractionRecord:
        with self._transaction() as connection:
            request_id = terminal_event.request_id
            if request_id is None:
                raise RequestStoreError("Interaction response terminal requires a request ID")
            self._ensure_nonterminal_request(connection, request_id)
            interaction = self._interaction_from_row(
                self._require_interaction_row(connection, interaction_id)
            )
            if interaction.session_id != session_id or interaction.principal != principal:
                raise RequestStoreError("Interaction belongs to a different session")
            if interaction.state != "pending":
                raise RequestStoreError("Interaction is no longer pending")
            connection.execute(
                "UPDATE task_interactions SET state = 'answered', resolved_at = ? WHERE interaction_id = ?",
                (_utc_now(), interaction_id),
            )
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            return self._interaction_from_row(
                self._require_interaction_row(connection, interaction_id)
            )

    def get_task_checkpoint(self, task_id: str) -> ConversationCheckpoint | None:
        """Read backend-only checkpoint messages after hash/schema validation."""

        with self._lock:
            task = self._research_task_from_row(
                self._require_research_task_row(self._connection, task_id)
            )
            return self._checkpoint_for_task_in_connection(self._connection, task)

    def commit_task_terminal_with_checkpoint(
        self,
        *,
        request_id: str,
        task_id: str,
        terminal_event: EventEnvelope,
        messages: list[dict[str, Any]],
        provider_id: str,
        model_id: str,
        runtime_profile_fingerprint: str,
        system_prompt_fingerprint: str,
        compaction_generation: int,
        usage_summary: dict[str, Any],
    ) -> ConversationCheckpoint:
        """Atomically commit a successful task terminal and its restorable model memory."""

        if terminal_event.type != "request.completed":
            raise RequestStoreError("Stable task checkpoints require request.completed")
        if len(messages) > 10_000:
            raise TaskCheckpointIncompatible("Task checkpoint message count exceeds the hard limit")
        if any(not isinstance(message, dict) for message in messages):
            raise TaskCheckpointIncompatible("Task checkpoint messages must be JSON objects")
        messages_json = _canonical_json(messages)
        if len(messages_json.encode("utf-8")) > 2 * 1024 * 1024:
            raise TaskCheckpointIncompatible("Task checkpoint payload exceeds the hard limit")
        payload_sha256 = hashlib.sha256(messages_json.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            request = self._request_from_row(self._require_request_row(connection, request_id))
            if request.task_id != task_id:
                raise RequestStoreError(
                    "Task checkpoint request does not belong to the supplied task"
                )
            task = self._require_research_task(connection, task_id)
            if task.active_request_id != request_id:
                raise RequestStoreError("Task checkpoint request is not the active task request")
            generation = task.conversation_generation + 1
            checkpoint_id = f"chk_{uuid4().hex}"
            now = _utc_now()
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            connection.execute(
                """
                INSERT INTO task_conversation_checkpoints (
                    checkpoint_id, task_id, conversation_generation, terminal_request_id,
                    message_schema_version, messages_json, message_count, provider_id, model_id,
                    runtime_profile_fingerprint, system_prompt_fingerprint, compaction_generation,
                    usage_summary_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    task_id,
                    generation,
                    request_id,
                    messages_json,
                    len(messages),
                    provider_id,
                    model_id,
                    runtime_profile_fingerprint,
                    system_prompt_fingerprint,
                    compaction_generation,
                    _canonical_json(usage_summary),
                    payload_sha256,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE research_tasks
                SET stable_checkpoint_id = ?, conversation_generation = ?, active_request_id = NULL,
                    task_revision = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (checkpoint_id, generation, task.task_revision + 1, now, task_id),
            )
            connection.execute(
                """
                UPDATE task_workflows
                SET state = 'completed', activity = 'Task completed', heartbeat_at = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (now, now, request_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO task_artifact_links (
                    task_id, artifact_id, version, relation, origin_request_id, created_at
                )
                SELECT ?, artifact_id, version, 'supporting', ?, ?
                FROM artifact_commit_intents
                WHERE workspace_id = ? AND origin_request_id = ? AND status = 'committed'
                """,
                (task_id, request_id, now, request.workspace_id, request_id),
            )
            row = connection.execute(
                "SELECT * FROM task_conversation_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            return self._conversation_checkpoint_from_row(row)

    def commit_task_terminal_without_checkpoint(
        self,
        *,
        request_id: str,
        terminal_event: EventEnvelope,
    ) -> RequestRecord:
        """Commit a failed/cancelled task request while retaining the previous stable memory."""

        with self._transaction() as connection:
            request = self._request_from_row(self._require_request_row(connection, request_id))
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            if request.task_id is not None:
                mark_interrupted = terminal_event.type == "request.cancelled"
                workflow_state: TaskWorkflowState = "cancelled"
                workflow_activity = "Cancelled by user"
                failure_fingerprint: str | None = None
                if terminal_event.type == "request.failed":
                    error = terminal_event.payload.error
                    mark_interrupted = error.code == "request_interrupted"
                    workflow_state = "incomplete" if error.recoverable else "failed"
                    workflow_activity = (
                        "Recoverable incomplete — resume from the saved checkpoint"
                        if error.recoverable
                        else "Task failed"
                    )
                    failure_fingerprint = hashlib.sha256(
                        f"{error.code}\0{error.message}".encode("utf-8")
                    ).hexdigest()
                if mark_interrupted:
                    self._mark_task_transcript_interrupted_in_connection(
                        connection, task_id=request.task_id, request_id=request_id
                    )
                row = connection.execute(
                    "SELECT failure_fingerprint, repeated_failures FROM task_workflows "
                    "WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                repeated = 0
                if failure_fingerprint is not None:
                    repeated = (
                        int(row["repeated_failures"]) + 1
                        if row is not None and row["failure_fingerprint"] == failure_fingerprint
                        else 1
                    )
                now = _utc_now()
                connection.execute(
                    """
                    UPDATE task_workflows
                    SET state = ?, activity = ?, heartbeat_at = ?, failure_fingerprint = ?,
                        repeated_failures = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (
                        workflow_state,
                        workflow_activity,
                        now,
                        failure_fingerprint,
                        repeated,
                        now,
                        request_id,
                    ),
                )
                self._clear_task_active_request_in_connection(
                    connection, task_id=request.task_id, request_id=request_id
                )
            return self._request_from_row(self._require_request_row(connection, request_id))

    def commit_disclosure_policy(
        self,
        *,
        request_id: str,
        workspace_id: str,
        provider_id: str,
        policy: dict[str, Any],
        expected_workspace_revision: int | None,
        event_factory: Callable[
            [dict[str, Any], int, int, str], tuple[EventEnvelope, EventEnvelope]
        ],
    ) -> DisclosurePolicyCommit:
        """Atomically version a confirmed disclosure policy with its request terminal."""

        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, request_id)
            current_revision = self._workspace_revision(connection, workspace_id)
            if (
                expected_workspace_revision is not None
                and expected_workspace_revision != current_revision
            ):
                raise WorkspaceRevisionConflict(current_revision)
            existing = self._disclosure_policy_in_connection(connection, workspace_id)
            policy_version = 1 if existing is None else int(existing["policy_version"]) + 1
            updated_at = _utc_now()
            connection.execute(
                """
                INSERT INTO workspace_disclosure_policies (
                    workspace_id, provider_id, policy_version, policy_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    policy_version = excluded.policy_version,
                    policy_json = excluded.policy_json,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, provider_id, policy_version, _canonical_json(policy), updated_at),
            )
            connection.execute(
                """
                INSERT INTO workspace_disclosure_policy_versions (
                    workspace_id, policy_version, provider_id, policy_json, confirmed_request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    policy_version,
                    provider_id,
                    _canonical_json(policy),
                    request_id,
                    updated_at,
                ),
            )
            summary = self._disclosure_policy_summary_from_record(
                {
                    "provider_id": provider_id,
                    "policy_version": policy_version,
                    "policy": policy,
                    "confirmed_request_id": request_id,
                    "updated_at": updated_at,
                }
            )
            previous_revision, workspace_revision = self._bump_workspace_revision(
                connection, workspace_id
            )
            event_id = new_event_id()
            domain_event, terminal_event = event_factory(
                summary,
                previous_revision,
                workspace_revision,
                event_id,
            )
            if domain_event.event_id != event_id:
                raise RequestStoreError(
                    "Disclosure policy event factory must preserve the supplied durable event ID"
                )
            self._persist_event(connection, domain_event)
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            return DisclosurePolicyCommit(
                policy=summary,
                previous_revision=previous_revision,
                workspace_revision=workspace_revision,
                domain_event=domain_event,
                terminal_event=terminal_event,
            )

    def next_artifact_version(self, *, workspace_id: str, artifact_id: str) -> int:
        """Return a version beyond both committed and durable in-flight intents."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS latest FROM (
                    SELECT version FROM artifact_versions
                    WHERE workspace_id = ? AND artifact_id = ?
                    UNION ALL
                    SELECT version FROM artifact_commit_intents
                    WHERE workspace_id = ? AND artifact_id = ?
                )
                """,
                (workspace_id, artifact_id, workspace_id, artifact_id),
            ).fetchone()
            return int(row["latest"]) + 1

    def prepare_artifact_intent(
        self,
        *,
        operation_id: str,
        request_id: str | None,
        origin_request_id: str | None,
        task_id: str | None,
        task_relation: str | None,
        expected_workspace_revision: int | None,
        manifest: ArtifactManifest,
        staging_uri: str,
        target_uri: str,
    ) -> ArtifactCommitIntent:
        """Durably record a staged filesystem operation before its atomic rename."""

        if (task_id is None) != (task_relation is None):
            raise RequestStoreError("Artifact Task ownership requires both task_id and relation")
        if task_relation is not None and (not task_relation or len(task_relation) > 128):
            raise RequestStoreError("Artifact Task relation must be bounded and non-empty")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM artifact_commit_intents WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is not None:
                intent = self._intent_from_row(existing)
                if intent.manifest.manifest_sha256 != manifest.manifest_sha256:
                    raise ArtifactVersionConflict(
                        f"Operation ID already belongs to a different manifest: {operation_id}"
                    )
                if intent.origin_request_id != origin_request_id:
                    raise ArtifactVersionConflict(
                        f"Operation ID already belongs to a different originating request: {operation_id}"
                    )
                if intent.task_id != task_id or intent.task_relation != task_relation:
                    raise ArtifactVersionConflict(
                        "Operation ID already belongs to different Task ownership"
                    )
                return intent
            now = _utc_now()
            try:
                connection.execute(
                    """
                    INSERT INTO artifact_commit_intents (
                        operation_id, request_id, origin_request_id, task_id, task_relation,
                        workspace_id, artifact_id, version,
                        expected_workspace_revision, staging_uri, target_uri, manifest_json, manifest_sha256, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                    """,
                    (
                        operation_id,
                        request_id,
                        origin_request_id,
                        task_id,
                        task_relation,
                        manifest.workspace_id,
                        manifest.artifact_id,
                        manifest.version,
                        expected_workspace_revision,
                        staging_uri,
                        target_uri,
                        manifest.model_dump_json(),
                        manifest.manifest_sha256,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ArtifactVersionConflict(
                    f"Artifact version is already reserved: {manifest.artifact_id}@v{manifest.version:04d}"
                ) from exc
            return self._intent_from_row(
                connection.execute(
                    "SELECT * FROM artifact_commit_intents WHERE operation_id = ?", (operation_id,)
                ).fetchone()
            )

    def get_artifact_intent(self, operation_id: str) -> ArtifactCommitIntent | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM artifact_commit_intents WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return self._intent_from_row(row) if row is not None else None

    def pending_artifact_intents(self) -> list[ArtifactCommitIntent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM artifact_commit_intents WHERE status = 'prepared' ORDER BY created_at"
            ).fetchall()
            return [self._intent_from_row(row) for row in rows]

    def quarantine_artifact_intent(
        self,
        *,
        operation_id: str,
        quarantine_uri: str | None,
    ) -> ArtifactCommitIntent:
        """Mark an unrecoverable filesystem operation as audit-only, never publishable."""

        with self._transaction() as connection:
            intent = self._intent_from_row(self._require_intent_row(connection, operation_id))
            if intent.status == "committed":
                raise RequestStoreError("A committed artifact intent cannot be quarantined")
            connection.execute(
                """
                UPDATE artifact_commit_intents
                SET status = 'quarantined', quarantine_uri = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (quarantine_uri, _utc_now(), operation_id),
            )
            row = self._require_intent_row(connection, operation_id)
            return self._intent_from_row(row)

    def commit_artifact_intent(
        self,
        *,
        operation_id: str,
        expected_workspace_revision: int | None,
        event_factory: Callable[
            [ArtifactVersion, ArtifactProjection, int, int, str],
            tuple[EventEnvelope, EventEnvelope | None],
        ],
    ) -> ArtifactCommit:
        """Finalize immutable metadata, domain event, and request terminal atomically."""

        with self._transaction() as connection:
            intent = self._intent_from_row(self._require_intent_row(connection, operation_id))
            if intent.status == "committed":
                artifact = self._require_artifact(
                    connection, intent.workspace_id, intent.artifact_id, intent.version
                )
                projection = self._require_projection(
                    connection,
                    intent.workspace_id,
                    intent.artifact_id,
                    intent.version,
                )
                if intent.committed_event_id is None:
                    raise RequestStoreError("Committed artifact has no durable source event")
                event_row = connection.execute(
                    "SELECT event_json FROM event_records WHERE event_id = ?",
                    (intent.committed_event_id,),
                ).fetchone()
                if event_row is None:
                    raise RequestStoreError(
                        "Committed artifact event is missing from the durable journal"
                    )
                event = parse_event(json.loads(event_row["event_json"]))
                request_row = (
                    self._require_request_row(connection, intent.request_id)
                    if intent.request_id is not None
                    else None
                )
                terminal = (
                    self._request_from_row(request_row).terminal_event
                    if request_row is not None
                    else None
                )
                return ArtifactCommit(
                    artifact=artifact,
                    projection=projection,
                    previous_revision=self._workspace_revision(connection, intent.workspace_id),
                    workspace_revision=self._workspace_revision(connection, intent.workspace_id),
                    domain_event=event,
                    terminal_event=terminal,
                )
            if intent.status != "prepared":
                raise RequestStoreError(f"Artifact intent is not finalizable: {intent.status}")
            workspace = connection.execute(
                "SELECT * FROM workspace_records WHERE workspace_id = ?", (intent.workspace_id,)
            ).fetchone()
            if workspace is None:
                raise RequestStoreError(f"Workspace is not open: {intent.workspace_id}")
            previous_revision = int(workspace["revision"])
            expected = intent.expected_workspace_revision
            if expected_workspace_revision is not None and expected_workspace_revision != expected:
                raise RequestStoreError("Artifact intent expected revision changed after staging")
            if expected is not None and expected != previous_revision:
                raise WorkspaceRevisionConflict(previous_revision)
            existing = connection.execute(
                """
                SELECT manifest_sha256 FROM artifact_versions
                WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                """,
                (intent.workspace_id, intent.artifact_id, intent.version),
            ).fetchone()
            if existing is not None:
                if existing["manifest_sha256"] != intent.manifest.manifest_sha256:
                    raise ArtifactVersionConflict(
                        f"Artifact version already has different content: {intent.artifact_id}@{intent.version}"
                    )
                raise RequestStoreError(
                    "Prepared intent unexpectedly already has an artifact version"
                )

            manifest = intent.manifest
            artifact = self._artifact_from_manifest(manifest, intent.target_uri)
            self._validate_artifact_links(connection, artifact)
            self._validate_domain_content_refs(connection, artifact)
            domain_event_id = new_event_id()
            projection = ArtifactProjection(
                ref=artifact.ref,
                lifecycle_state="available",
                impact_state="current",
                impact_reasons=(),
                updated_at=datetime.now(timezone.utc),
                source_event_id=domain_event_id,
            )
            workspace_revision = previous_revision + 1
            self._insert_artifact_version(connection, artifact)
            self._insert_artifact_links(connection, artifact, source_event_id=domain_event_id)
            self._upsert_projection(connection, projection, workspace_id=intent.workspace_id)
            if artifact.supersedes_version is not None:
                self._mark_superseded(
                    connection,
                    workspace_id=intent.workspace_id,
                    artifact_id=artifact.ref.artifact_id,
                    version=artifact.supersedes_version,
                    source_event_id=domain_event_id,
                )
            rebuilt = self._rebuild_impact_in_transaction(
                connection, workspace_id=intent.workspace_id
            )
            projection = rebuilt[artifact.ref]
            domain_event, terminal_event = event_factory(
                artifact,
                projection,
                previous_revision,
                workspace_revision,
                domain_event_id,
            )
            if domain_event.event_id != domain_event_id:
                raise RequestStoreError(
                    "Artifact event factory must preserve the supplied durable event ID"
                )
            connection.execute(
                "UPDATE workspace_records SET revision = ?, updated_at = ? WHERE workspace_id = ?",
                (workspace_revision, _utc_now(), intent.workspace_id),
            )
            self._persist_event(connection, domain_event)
            if intent.task_id is not None and intent.task_relation is not None:
                task = self._require_research_task(connection, intent.task_id)
                if task.workspace_id != intent.workspace_id:
                    raise RequestStoreError("Artifact Task ownership crosses workspaces")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO task_artifact_links (
                        task_id, artifact_id, version, relation, origin_request_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.task_id,
                        artifact.ref.artifact_id,
                        artifact.ref.version,
                        intent.task_relation,
                        intent.origin_request_id or intent.request_id or "",
                        _utc_now(),
                    ),
                )
            if terminal_event is not None:
                if intent.request_id is None:
                    raise RequestStoreError(
                        "Artifact terminal event requires a request-backed commit intent"
                    )
                self._commit_terminal_in_transaction(connection, intent.request_id, terminal_event)
            connection.execute(
                """
                UPDATE artifact_commit_intents
                SET status = 'committed', committed_event_id = ?, updated_at = ? WHERE operation_id = ?
                """,
                (domain_event.event_id, _utc_now(), operation_id),
            )
            return ArtifactCommit(
                artifact=artifact,
                projection=projection,
                previous_revision=previous_revision,
                workspace_revision=workspace_revision,
                domain_event=domain_event,
                terminal_event=terminal_event,
            )

    def list_artifact_summaries(
        self,
        workspace_id: str,
        *,
        artifact_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return compact, projection-bearing summaries without loading large artifact files."""

        query = """
            SELECT v.*, p.lifecycle_state,
                   p.impact_state, p.impact_reasons_json, p.updated_at, p.source_event_id
            FROM artifact_versions AS v
            JOIN artifact_projections AS p
              ON p.workspace_id = v.workspace_id
             AND p.artifact_id = v.artifact_id
             AND p.version = v.version
            WHERE v.workspace_id = ?
        """
        values: list[Any] = [workspace_id]
        if artifact_type is not None:
            query += " AND v.artifact_type = ?"
            values.append(artifact_type)
        query += " ORDER BY v.created_at DESC LIMIT ?"
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
            return [self._artifact_summary_from_row(row) for row in rows]

    def find_local_dataset_reference(
        self,
        *,
        workspace_id: str,
        source_scope: str,
        source_locator: str,
        registered_fingerprint: dict[str, int],
    ) -> ArtifactVersion | None:
        """Return an existing immutable reference to the same unchanged local source.

        This is metadata identity, not content inspection.  It prevents repeated UI
        attachment of one path from creating another artifact or copying bytes while
        still creating a new version when the source fingerprint changes.
        """

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM artifact_versions
                WHERE workspace_id = ? AND artifact_type = 'dataset'
                ORDER BY created_at DESC, artifact_id, version DESC
                """,
                (workspace_id,),
            ).fetchall()
            for row in rows:
                artifact = self._artifact_from_row(row, connection=self._connection)
                content = artifact.content
                if (
                    content.get("materialization_level") != "local_reference"
                    or content.get("source_scope") != source_scope
                    or content.get("registered_fingerprint") != registered_fingerprint
                ):
                    continue
                locator = content.get(
                    "source_relative_path" if source_scope == "workspace" else "source_path"
                )
                if locator == source_locator:
                    return artifact
            return None

    def list_task_output_summaries(self, *, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return exact artifact versions recorded as outputs of one research task."""

        if not 1 <= limit <= 500:
            raise ValueError("Task output limit must be between 1 and 500")
        with self._lock:
            self._require_research_task_row(self._connection, task_id)
            return self._task_output_summaries_in_connection(self._connection, task_id, limit=limit)

    def list_task_artifacts(self, *, task_id: str) -> list[TaskArtifactRecord]:
        """Return complete immutable artifacts owned or referenced by one task.

        This is an internal filesystem-projection API. Renderer payloads continue to use
        ``list_task_output_summaries`` so local URIs and file inventories never leak into
        Protocol v2 snapshots.
        """

        with self._lock:
            self._require_research_task_row(self._connection, task_id)
            rows = self._connection.execute(
                """
                SELECT l.relation, l.origin_request_id, l.created_at AS task_linked_at, v.*
                FROM task_artifact_links AS l
                JOIN research_tasks AS t ON t.task_id = l.task_id
                JOIN artifact_versions AS v
                  ON v.workspace_id = t.workspace_id
                 AND v.artifact_id = l.artifact_id
                 AND v.version = l.version
                WHERE l.task_id = ?
                ORDER BY l.created_at, v.artifact_id, v.version
                """,
                (task_id,),
            ).fetchall()
            grouped: dict[tuple[str, int], dict[str, Any]] = {}
            for row in rows:
                key = (str(row["artifact_id"]), int(row["version"]))
                item = grouped.setdefault(
                    key,
                    {
                        "row": row,
                        "relations": [],
                        "origin_request_ids": [],
                        "linked_at": str(row["task_linked_at"]),
                    },
                )
                relation = str(row["relation"])
                if relation not in item["relations"]:
                    item["relations"].append(relation)
                raw_origin = row["origin_request_id"]
                if raw_origin:
                    origin = str(raw_origin)
                    if origin not in item["origin_request_ids"]:
                        item["origin_request_ids"].append(origin)
                item["linked_at"] = max(item["linked_at"], str(row["task_linked_at"]))
            return [
                TaskArtifactRecord(
                    artifact=self._artifact_from_row(item["row"], connection=self._connection),
                    relations=tuple(item["relations"]),
                    origin_request_ids=tuple(item["origin_request_ids"]),
                    linked_at=item["linked_at"],
                )
                for item in grouped.values()
            ]

    def link_task_artifact(
        self,
        *,
        task_id: str,
        ref: ArtifactRef,
        relation: str,
        origin_request_id: str | None,
    ) -> None:
        """Associate one immutable artifact version with its owning task."""

        if not relation or len(relation) > 128:
            raise RequestStoreError("Task artifact relation must be a bounded non-empty string")
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            artifact = connection.execute(
                """
                SELECT 1 FROM artifact_versions
                WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                """,
                (task.workspace_id, ref.artifact_id, ref.version),
            ).fetchone()
            if artifact is None:
                raise RequestStoreError("Task artifact must exist in the task workspace")
            connection.execute(
                """
                INSERT OR IGNORE INTO task_artifact_links (
                    task_id, artifact_id, version, relation, origin_request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    ref.artifact_id,
                    ref.version,
                    relation,
                    origin_request_id or "",
                    _utc_now(),
                ),
            )

    def replace_task_request_deliveries(
        self,
        *,
        task_id: str,
        origin_request_id: str,
        primary_refs: tuple[ArtifactRef, ...],
        supporting_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        """Atomically replace one response's presentation set without deleting evidence."""

        if not origin_request_id:
            raise RequestStoreError("Task delivery replacement requires a request ID")
        if not primary_refs:
            raise RequestStoreError("A task response requires at least one primary delivery")
        if len(set(primary_refs)) != len(primary_refs):
            raise RequestStoreError("Primary task deliveries must be unique")
        if len(set(supporting_refs)) != len(supporting_refs):
            raise RequestStoreError("Supporting task deliveries must be unique")
        if set(primary_refs).intersection(supporting_refs):
            raise RequestStoreError("Primary and supporting task deliveries must be disjoint")

        deliveries = tuple((ref, "primary") for ref in primary_refs) + tuple(
            (ref, "supporting") for ref in supporting_refs
        )
        with self._transaction() as connection:
            task = self._require_research_task(connection, task_id)
            for ref, _relation in deliveries:
                artifact = connection.execute(
                    """
                    SELECT 1 FROM artifact_versions
                    WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                    """,
                    (task.workspace_id, ref.artifact_id, ref.version),
                ).fetchone()
                if artifact is None:
                    raise RequestStoreError("Task artifact must exist in the task workspace")

            # Direct publication and earlier result-submission paths may already have projected
            # several representations as outputs. The final presentation contract is
            # authoritative for this request only; supporting/evidence links remain durable.
            connection.execute(
                """
                DELETE FROM task_artifact_links
                WHERE task_id = ? AND origin_request_id = ?
                  AND relation IN ('primary', 'output')
                """,
                (task_id, origin_request_id),
            )
            now = _utc_now()
            for ref, relation in deliveries:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO task_artifact_links (
                        task_id, artifact_id, version, relation, origin_request_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        ref.artifact_id,
                        ref.version,
                        relation,
                        origin_request_id,
                        now,
                    ),
                )

    def _task_output_summaries_in_connection(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT l.relation, l.origin_request_id, l.created_at AS task_linked_at,
                   v.*, p.lifecycle_state,
                   p.impact_state, p.impact_reasons_json, p.updated_at, p.source_event_id
            FROM task_artifact_links AS l
            JOIN research_tasks AS t ON t.task_id = l.task_id
            JOIN artifact_versions AS v
              ON v.workspace_id = t.workspace_id
             AND v.artifact_id = l.artifact_id
             AND v.version = l.version
            JOIN artifact_projections AS p
              ON p.workspace_id = v.workspace_id
             AND p.artifact_id = v.artifact_id
             AND p.version = v.version
            WHERE l.task_id = ? AND l.relation != 'source'
            ORDER BY l.created_at DESC
            """,
            (task_id,),
        ).fetchall()
        grouped: dict[tuple[str, int], dict[str, Any]] = {}
        relation_priority = {"primary": 3, "output": 3, "supporting": 2, "internal": 1}
        for row in rows:
            key = (str(row["artifact_id"]), int(row["version"]))
            origin = str(row["origin_request_id"]) or None
            priority = relation_priority.get(str(row["relation"]), 0)
            summary = grouped.get(key)
            if summary is None:
                presentation = self._task_output_presentation_metadata_in_connection(
                    connection,
                    row,
                )
                summary = {
                    "artifact": self._artifact_summary_from_row(row),
                    "relation": row["relation"],
                    "origin_request_id": origin,
                    "origin_request_ids": [],
                    "linked_at": row["task_linked_at"],
                    **presentation,
                    "_relation_priority": priority,
                }
                grouped[key] = summary
            elif priority > int(summary["_relation_priority"]):
                summary["relation"] = row["relation"]
                summary["origin_request_id"] = origin
                summary["origin_request_ids"] = []
                summary["linked_at"] = row["task_linked_at"]
                summary["_relation_priority"] = priority
            elif priority < int(summary["_relation_priority"]):
                continue
            if origin is not None and origin not in summary["origin_request_ids"]:
                summary["origin_request_ids"].append(origin)
        for summary in grouped.values():
            summary.pop("_relation_priority", None)
        return list(grouped.values())[:limit]

    def _task_source_summaries_in_connection(
        self,
        connection: sqlite3.Connection,
        task_id: str,
    ) -> list[dict[str, Any]]:
        """Return task-owned inputs separately from user-facing result outputs."""

        rows = connection.execute(
            """
            SELECT l.origin_request_id, l.created_at AS task_linked_at,
                   v.*, p.lifecycle_state,
                   p.impact_state, p.impact_reasons_json, p.updated_at, p.source_event_id
            FROM task_artifact_links AS l
            JOIN research_tasks AS t ON t.task_id = l.task_id
            JOIN artifact_versions AS v
              ON v.workspace_id = t.workspace_id
             AND v.artifact_id = l.artifact_id
             AND v.version = l.version
            JOIN artifact_projections AS p
              ON p.workspace_id = v.workspace_id
             AND p.artifact_id = v.artifact_id
             AND p.version = v.version
            WHERE l.task_id = ? AND l.relation = 'source'
            ORDER BY l.created_at, v.artifact_id, v.version
            """,
            (task_id,),
        ).fetchall()
        grouped: dict[tuple[str, int], dict[str, Any]] = {}
        for row in rows:
            key = (str(row["artifact_id"]), int(row["version"]))
            origin = str(row["origin_request_id"]) or None
            summary = grouped.get(key)
            if summary is None:
                summary = {
                    "artifact": self._artifact_summary_from_row(row),
                    "relation": "source",
                    "origin_request_id": origin,
                    "origin_request_ids": [],
                    "linked_at": row["task_linked_at"],
                }
                grouped[key] = summary
            if origin is not None and origin not in summary["origin_request_ids"]:
                summary["origin_request_ids"].append(origin)
        return list(grouped.values())

    @staticmethod
    def _task_output_presentation_metadata_in_connection(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        """Project the artifact's direct user-facing result capability."""

        artifact_type = str(row["artifact_type"])
        artifact_key = f"{row['artifact_id']}@v{int(row['version'])}"
        return {
            "result_entry_kind": (
                artifact_type if artifact_type in {"interactive_view", "report"} else None
            ),
            "result_ref": artifact_key,
        }

    def list_artifact_versions(
        self, *, workspace_id: str, artifact_id: str
    ) -> list[dict[str, Any]]:
        """Return every immutable version of one artifact in newest-first order."""

        query = """
            SELECT v.*, p.lifecycle_state,
                   p.impact_state, p.impact_reasons_json, p.updated_at, p.source_event_id
            FROM artifact_versions AS v
            JOIN artifact_projections AS p
              ON p.workspace_id = v.workspace_id
             AND p.artifact_id = v.artifact_id
             AND p.version = v.version
            WHERE v.workspace_id = ? AND v.artifact_id = ?
            ORDER BY v.version DESC
        """
        with self._lock:
            rows = self._connection.execute(query, (workspace_id, artifact_id)).fetchall()
        return [self._artifact_summary_from_row(row) for row in rows]

    def get_artifact_file(self, *, workspace_id: str, uri: str) -> ArtifactFile | None:
        """Resolve a URI only when it belongs to an immutable artifact in this workspace."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT uri, mime_type, size_bytes, sha256 FROM artifact_files
                WHERE workspace_id = ? AND uri = ?
                """,
                (workspace_id, uri),
            ).fetchone()
        if row is None:
            return None
        return ArtifactFile(
            uri=row["uri"],
            mime_type=row["mime_type"],
            size_bytes=int(row["size_bytes"]),
            sha256=row["sha256"],
        )

    def list_artifact_links(
        self,
        *,
        workspace_id: str,
        ref: ArtifactRef,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Return typed graph edges in either direction without loading artifact payloads."""

        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError(f"Unsupported artifact-link direction: {direction}")
        clauses: list[str] = []
        values: list[Any] = [workspace_id]
        if direction in {"outgoing", "both"}:
            clauses.append("(source_artifact_id = ? AND source_version = ?)")
            values.extend((ref.artifact_id, ref.version))
        if direction in {"incoming", "both"}:
            clauses.append("(target_artifact_id = ? AND target_version = ?)")
            values.extend((ref.artifact_id, ref.version))
        query = (
            """
            SELECT * FROM artifact_links
            WHERE workspace_id = ? AND ("""
            + " OR ".join(clauses)
            + ") ORDER BY link_id"
        )
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [
            {
                "source": {
                    "artifact_id": row["source_artifact_id"],
                    "version": int(row["source_version"]),
                },
                "target": {
                    "artifact_id": row["target_artifact_id"],
                    "version": int(row["target_version"]),
                },
                "relation": row["relation"],
                "intrinsic": bool(row["intrinsic"]),
                "created_at": row["created_at"],
                "source_event_id": row["source_event_id"],
            }
            for row in rows
        ]

    def list_active_refs(self, workspace_id: str) -> dict[str, ArtifactRef]:
        """Read named workspace pointers that always pin an exact artifact version."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT slot, artifact_id, version FROM workspace_active_refs
                WHERE workspace_id = ? ORDER BY slot
                """,
                (workspace_id,),
            ).fetchall()
        return {
            row["slot"]: ArtifactRef(artifact_id=row["artifact_id"], version=int(row["version"]))
            for row in rows
        }

    def set_disclosure_policy(
        self,
        *,
        workspace_id: str,
        provider_id: str,
        policy_version: int,
        policy: dict[str, Any],
    ) -> None:
        """Persist a provider-bound disclosure policy without storing disclosed content."""

        with self._transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
                ).fetchone()
                is None
            ):
                raise RequestStoreError(f"Workspace is not open: {workspace_id}")
            updated_at = _utc_now()
            serialized_policy = _canonical_json(policy)
            connection.execute(
                """
                INSERT INTO workspace_disclosure_policies (
                    workspace_id, provider_id, policy_version, policy_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    policy_version = excluded.policy_version,
                    policy_json = excluded.policy_json,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, provider_id, policy_version, serialized_policy, updated_at),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO workspace_disclosure_policy_versions (
                    workspace_id, policy_version, provider_id, policy_json, confirmed_request_id, created_at
                ) VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (workspace_id, policy_version, provider_id, serialized_policy, updated_at),
            )

    def get_disclosure_policy(self, workspace_id: str) -> dict[str, Any] | None:
        """Read the active provider-bound policy as structured data."""

        with self._lock:
            return self._disclosure_policy_in_connection(self._connection, workspace_id)

    @staticmethod
    def _disclosure_policy_in_connection(
        connection: sqlite3.Connection,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM workspace_disclosure_policies WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "provider_id": row["provider_id"],
            "policy_version": int(row["policy_version"]),
            "policy": json.loads(row["policy_json"]),
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _disclosure_policy_summary_in_connection(
        cls,
        connection: sqlite3.Connection,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        policy = cls._disclosure_policy_in_connection(connection, workspace_id)
        if policy is None:
            return None
        version = connection.execute(
            """
            SELECT confirmed_request_id FROM workspace_disclosure_policy_versions
            WHERE workspace_id = ? AND policy_version = ?
            """,
            (workspace_id, policy["policy_version"]),
        ).fetchone()
        return cls._disclosure_policy_summary_from_record(
            {
                **policy,
                "confirmed_request_id": version["confirmed_request_id"]
                if version is not None
                else None,
            }
        )

    @staticmethod
    def _disclosure_policy_summary_from_record(record: dict[str, Any]) -> dict[str, Any]:
        policy = record["policy"]
        return {
            "provider_id": record["provider_id"],
            "policy_version": int(record["policy_version"]),
            "metadata": policy["metadata"],
            "aggregate_statistics": policy["aggregate_statistics"],
            "raw_bounded_sample": policy["raw_bounded_sample"],
            "document_text": policy["document_text"],
            "diagnostic_excerpt": policy["diagnostic_excerpt"],
            "confirmed": bool(record.get("confirmed_request_id")),
            "updated_at": datetime.fromisoformat(record["updated_at"])
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    def list_disclosure_policy_versions(
        self,
        *,
        workspace_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return immutable policy snapshots newest first without returning disclosed data."""

        if limit < 1 or limit > 500:
            raise ValueError("Disclosure policy version limit must be between 1 and 500")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM workspace_disclosure_policy_versions
                WHERE workspace_id = ?
                ORDER BY policy_version DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [
            {
                **self._disclosure_policy_summary_from_record(
                    {
                        "provider_id": row["provider_id"],
                        "policy_version": int(row["policy_version"]),
                        "policy": json.loads(row["policy_json"]),
                        "confirmed_request_id": row["confirmed_request_id"],
                        "updated_at": row["created_at"],
                    }
                ),
                "confirmed_request_id": row["confirmed_request_id"],
            }
            for row in rows
        ]

    def get_disclosure_policy_summary(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the active policy with its request-bound confirmation state."""

        with self._lock:
            return self._disclosure_policy_summary_in_connection(self._connection, workspace_id)

    def record_disclosure_audit(
        self,
        *,
        audit_id: str,
        workspace_id: str,
        provider_id: str,
        policy_version: int,
        content_type: str,
        disposition: str,
        byte_count: int,
        item_count: int,
        source_ref: ArtifactRef | None = None,
    ) -> None:
        """Record only disclosure metadata, never the potentially sensitive disclosed value."""

        if byte_count < 0 or item_count < 0:
            raise ValueError("Disclosure audit sizes must be non-negative")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO disclosure_audit_records (
                    audit_id, workspace_id, provider_id, policy_version, content_type,
                    disposition, byte_count, item_count, source_ref_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    workspace_id,
                    provider_id,
                    policy_version,
                    content_type,
                    disposition,
                    byte_count,
                    item_count,
                    _canonical_json(source_ref.model_dump(mode="json")) if source_ref else None,
                    _utc_now(),
                ),
            )

    def list_disclosure_audits(
        self, *, workspace_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return audit metadata suitable for an inspector without returning disclosed payloads."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM disclosure_audit_records
                WHERE workspace_id = ? ORDER BY created_at DESC, audit_id DESC LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [
            {
                "audit_id": row["audit_id"],
                "provider_id": row["provider_id"],
                "policy_version": int(row["policy_version"]),
                "content_type": row["content_type"],
                "disposition": row["disposition"],
                "byte_count": int(row["byte_count"]),
                "item_count": int(row["item_count"]),
                "source_ref": json.loads(row["source_ref_json"])
                if row["source_ref_json"]
                else None,
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def record_resource_usage(
        self,
        *,
        usage_id: str,
        workspace_id: str,
        resource_kind: str,
        resource_name: str,
        resource_version: str,
        work_order_id: str | None = None,
    ) -> None:
        """Audit a loaded skill/reference by identity and version, never by copied content."""

        with self._transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
                ).fetchone()
                is None
            ):
                raise RequestStoreError(f"Workspace is not open: {workspace_id}")
            connection.execute(
                """
                INSERT INTO resource_usage_records (
                    usage_id, workspace_id, work_order_id, resource_kind, resource_name,
                    resource_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage_id,
                    workspace_id,
                    work_order_id,
                    resource_kind,
                    resource_name,
                    resource_version,
                    _utc_now(),
                ),
            )

    def list_resource_usage(
        self,
        *,
        workspace_id: str,
        work_order_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read resource identity records for an inspector or reproducibility appendix."""

        with self._lock:
            if work_order_id is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM resource_usage_records
                    WHERE workspace_id = ? ORDER BY created_at DESC, usage_id DESC LIMIT ?
                    """,
                    (workspace_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM resource_usage_records
                    WHERE workspace_id = ? AND work_order_id = ?
                    ORDER BY created_at DESC, usage_id DESC LIMIT ?
                    """,
                    (workspace_id, work_order_id, limit),
                ).fetchall()
        return [
            {
                "usage_id": row["usage_id"],
                "work_order_id": row["work_order_id"],
                "resource_kind": row["resource_kind"],
                "resource_name": row["resource_name"],
                "resource_version": row["resource_version"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def set_active_ref(
        self,
        *,
        workspace_id: str,
        slot: str,
        ref: ArtifactRef,
        expected_workspace_revision: int | None,
    ) -> WorkspaceSnapshot:
        """Move one named active pointer while preserving exact immutable refs."""

        with self._transaction() as connection:
            workspace = connection.execute(
                "SELECT * FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise RequestStoreError(f"Workspace is not open: {workspace_id}")
            previous_revision = int(workspace["revision"])
            if (
                expected_workspace_revision is not None
                and expected_workspace_revision != previous_revision
            ):
                raise WorkspaceRevisionConflict(previous_revision)
            self._require_artifact(connection, workspace_id, ref.artifact_id, ref.version)
            connection.execute(
                """
                INSERT INTO workspace_active_refs (workspace_id, slot, artifact_id, version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, slot) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, slot, ref.artifact_id, ref.version, _utc_now()),
            )
            revision = previous_revision + 1
            connection.execute(
                "UPDATE workspace_records SET revision = ?, updated_at = ? WHERE workspace_id = ?",
                (revision, _utc_now(), workspace_id),
            )
            return WorkspaceSnapshot(
                workspace_id=workspace_id,
                path=workspace["path"],
                revision=revision,
                artifacts=self.list_artifact_summaries(workspace_id),
                active_refs=self.list_active_refs(workspace_id),
            )

    def commit_active_ref(
        self,
        *,
        request_id: str,
        workspace_id: str,
        slot: str,
        ref: ArtifactRef,
        expected_workspace_revision: int | None,
        event_factory: Callable[[WorkspaceSnapshot, int, int], tuple[EventEnvelope, EventEnvelope]],
    ) -> ActiveRefCommit:
        """Atomically update one active pointer with its request terminal and domain event."""

        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, request_id)
            workspace = connection.execute(
                "SELECT * FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise RequestStoreError(f"Workspace is not open: {workspace_id}")
            previous_revision = int(workspace["revision"])
            if (
                expected_workspace_revision is not None
                and expected_workspace_revision != previous_revision
            ):
                raise WorkspaceRevisionConflict(previous_revision)
            self._require_artifact(connection, workspace_id, ref.artifact_id, ref.version)
            connection.execute(
                """
                INSERT INTO workspace_active_refs (workspace_id, slot, artifact_id, version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, slot) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, slot, ref.artifact_id, ref.version, _utc_now()),
            )
            workspace_revision = previous_revision + 1
            connection.execute(
                "UPDATE workspace_records SET revision = ?, updated_at = ? WHERE workspace_id = ?",
                (workspace_revision, _utc_now(), workspace_id),
            )
            snapshot = WorkspaceSnapshot(
                workspace_id=workspace_id,
                path=workspace["path"],
                revision=workspace_revision,
                artifacts=self.list_artifact_summaries(workspace_id),
                active_refs=self.list_active_refs(workspace_id),
                disclosure_policy=self._disclosure_policy_summary_in_connection(
                    connection, workspace_id
                ),
            )
            domain_event, terminal_event = event_factory(
                snapshot,
                previous_revision,
                workspace_revision,
            )
            self._persist_event(connection, domain_event)
            self._commit_terminal_in_transaction(connection, request_id, terminal_event)
            return ActiveRefCommit(
                snapshot=snapshot,
                previous_revision=previous_revision,
                domain_event=domain_event,
                terminal_event=terminal_event,
            )

    def create_team_work_order(
        self,
        *,
        workspace_id: str,
        work_order: WorkOrder,
    ) -> TeamWorkRecord:
        """Persist one validated child delegation without changing workspace revision."""

        payload = _canonical_json(work_order.model_dump(mode="json"))
        now = _utc_now()
        with self._transaction() as connection:
            workspace = connection.execute(
                "SELECT revision FROM workspace_records WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                raise RequestStoreError(f"Workspace is not open: {workspace_id}")
            current_revision = int(workspace["revision"])
            existing = connection.execute(
                "SELECT * FROM team_work_records WHERE work_order_id = ?",
                (work_order.work_order_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workspace_id"] != workspace_id
                    or existing["work_order_json"] != payload
                ):
                    raise RequestStoreError(
                        f"work_order_id already has different content: {work_order.work_order_id}"
                    )
                return self._team_work_from_row(connection, existing)
            if work_order.workspace_revision != current_revision:
                raise WorkspaceRevisionConflict(current_revision)
            initial_checkpoint = WorkstreamCheckpoint()
            if (
                work_order.authority is ChildAuthority.EXPERT
                and work_order.task_id is not None
                and work_order.job_key is not None
            ):
                prior_rows = connection.execute(
                    """
                    SELECT * FROM team_work_records
                    WHERE workspace_id = ? AND authority = ?
                    ORDER BY updated_at, work_order_id
                    """,
                    (workspace_id, ChildAuthority.EXPERT.value),
                ).fetchall()
                prior_records = [
                    self._team_work_from_row(connection, candidate) for candidate in prior_rows
                ]
                matching_prior = [
                    candidate
                    for candidate in prior_records
                    if candidate.work_order.task_id == work_order.task_id
                    and candidate.work_order.job_key == work_order.job_key
                    and candidate.work_order.profile_id == work_order.profile_id
                    and candidate.work_order.session_round < work_order.session_round
                ]
                if matching_prior:
                    prior = max(
                        matching_prior,
                        key=lambda candidate: (
                            candidate.work_order.session_round,
                            candidate.updated_at,
                        ),
                    )
                    initial_checkpoint = WorkstreamCheckpoint(
                        phase=WorkstreamPhase.ASSIGNED,
                        latest_execution_id=prior.checkpoint.latest_execution_id,
                        successful_execution_ids=(prior.checkpoint.successful_execution_ids),
                        output_ready_execution_ids=(prior.checkpoint.output_ready_execution_ids),
                        result_bundle=ResultBundle(
                            items=tuple(
                                item
                                for item in prior.checkpoint.result_bundle.items
                                if item.result_ref is not None
                            )
                        ),
                        result_refs=prior.checkpoint.result_refs,
                    )
            connection.execute(
                """
                INSERT INTO team_work_records (
                    work_order_id, workspace_id, parent_request_id, authority, state,
                    workspace_revision, work_order_json, result_json, checkpoint_json,
                    resume_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, NULL, ?, 0, ?, ?)
                """,
                (
                    work_order.work_order_id,
                    workspace_id,
                    work_order.parent_request_id,
                    work_order.authority.value,
                    work_order.workspace_revision,
                    payload,
                    _canonical_json(initial_checkpoint.model_dump(mode="json")),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM team_work_records WHERE work_order_id = ?",
                (work_order.work_order_id,),
            ).fetchone()
            assert row is not None
            return self._team_work_from_row(connection, row)

    def resume_team_work(
        self,
        *,
        workspace_id: str,
        work_order: WorkOrder,
        max_resumes: int | None,
    ) -> TeamWorkRecord:
        """Resume one durable workstream without discarding its checkpoint.

        A continuation may refine the assignment envelope, but it must preserve
        the backend-owned participant identity, parent request, authority,
        profile, and frozen inputs.  Code executions and output refs remain
        attached to the same ``work_order_id``.
        """

        payload = _canonical_json(work_order.model_dump(mode="json"))
        with self._transaction() as connection:
            row = self._require_team_work_row(connection, work_order.work_order_id)
            previous = self._team_work_from_row(connection, row)
            if row["workspace_id"] != workspace_id:
                raise RequestStoreError("Expert workstream belongs to another workspace")
            legacy_unsubmitted_completion = (
                previous.state is WorkStatus.COMPLETED
                and previous.result is not None
                and previous.result.result_origin is ExpertResultOrigin.BACKEND_RECOVERED
            )
            if (
                previous.state
                not in {
                    WorkStatus.INCOMPLETE,
                    WorkStatus.FAILED,
                    WorkStatus.CANCELLED,
                }
                and not legacy_unsubmitted_completion
            ):
                raise RequestStoreError(
                    f"Cannot resume team work from state: {previous.state.value}"
                )
            if max_resumes is not None and previous.resume_count >= max_resumes:
                raise RequestStoreError("Expert workstream continuation limit reached")
            old = previous.work_order
            if (
                old.task_id != work_order.task_id
                or old.parent_request_id != work_order.parent_request_id
                or old.job_key != work_order.job_key
                or old.authority is not work_order.authority
                or old.profile_id != work_order.profile_id
                or old.input_refs != work_order.input_refs
            ):
                raise RequestStoreError(
                    "A continuation cannot change workstream identity or frozen inputs"
                )
            connection.execute(
                """
                UPDATE team_work_records
                SET state = 'queued', workspace_revision = ?, work_order_json = ?,
                    resume_count = resume_count + 1, updated_at = ?
                WHERE work_order_id = ?
                """,
                (
                    work_order.workspace_revision,
                    payload,
                    _utc_now(),
                    work_order.work_order_id,
                ),
            )
            return self._team_work_from_row(
                connection, self._require_team_work_row(connection, work_order.work_order_id)
            )

    def mark_team_work_running(self, work_order_id: str) -> TeamWorkRecord:
        """Transition queued work to running before the child factory is invoked."""

        with self._transaction() as connection:
            row = self._require_team_work_row(connection, work_order_id)
            if row["state"] == WorkStatus.RUNNING.value:
                return self._team_work_from_row(connection, row)
            if row["state"] != WorkStatus.QUEUED.value:
                raise RequestStoreError(
                    f"Cannot start team work from terminal state: {row['state']}"
                )
            row_checkpoint = self._workstream_checkpoint_from_row(row)
            connection.execute(
                """
                UPDATE team_work_records
                SET state = 'running', result_json = NULL, checkpoint_json = ?, updated_at = ?
                WHERE work_order_id = ?
                """,
                (
                    _canonical_json(
                        row_checkpoint.model_copy(
                            update={
                                "phase": (
                                    WorkstreamPhase.DELIVERING
                                    if row_checkpoint.draft_result is not None
                                    or row_checkpoint.result_refs
                                    or row_checkpoint.result_bundle.items
                                    else WorkstreamPhase.RUNNING
                                )
                            }
                        ).model_dump(mode="json")
                    ),
                    _utc_now(),
                    work_order_id,
                ),
            )
            return self._team_work_from_row(
                connection, self._require_team_work_row(connection, work_order_id)
            )

    def start_code_execution(
        self,
        *,
        execution_id: str,
        workspace_id: str,
        task_id: str,
        work_order_id: str,
        child_id: str,
        request: dict[str, Any],
        started_at: str,
    ) -> CodeExecutionRecord:
        """Start one Expert-owned code activity under an active workstream."""

        with self._transaction() as connection:
            work = self._require_team_work_row(connection, work_order_id)
            if work["workspace_id"] != workspace_id:
                raise RequestStoreError("Code execution workstream belongs to another workspace")
            if work["authority"] != ChildAuthority.EXPERT.value:
                raise RequestStoreError(
                    "Code execution requires a current domain Expert workstream"
                )
            if work["state"] != WorkStatus.RUNNING.value:
                raise RequestStoreError("Code execution requires an active Expert workstream")
            task = connection.execute(
                "SELECT workspace_id FROM research_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None or task["workspace_id"] != workspace_id:
                raise RequestStoreError("Code execution task is unavailable")
            connection.execute(
                """
                INSERT INTO code_executions (
                    execution_id, workspace_id, task_id, work_order_id, child_id,
                    state, request_json, result_json, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, ?, NULL)
                """,
                (
                    execution_id,
                    workspace_id,
                    task_id,
                    work_order_id,
                    child_id,
                    _canonical_json(request),
                    started_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM code_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            assert row is not None
            return self._code_execution_from_row(row)

    def finish_code_execution(
        self,
        *,
        execution_id: str,
        state: str,
        result: dict[str, Any],
        ended_at: str,
    ) -> CodeExecutionRecord:
        """Finish one Expert code activity without changing workstream semantics."""

        if state not in {
            "succeeded",
            "failed",
            "timed_out",
            "resource_limited",
            "cancelled",
        }:
            raise ValueError(f"Unsupported code execution terminal state: {state}")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM code_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise RequestStoreError(f"Code execution is unavailable: {execution_id}")
            if row["state"] != "running":
                if row["state"] == state and row["result_json"] == _canonical_json(result):
                    return self._code_execution_from_row(row)
                raise RequestStoreError("Code execution already has a terminal result")
            connection.execute(
                """
                UPDATE code_executions
                SET state = ?, result_json = ?, ended_at = ?
                WHERE execution_id = ?
                """,
                (state, _canonical_json(result), ended_at, execution_id),
            )
            if state == "succeeded":
                work = self._require_team_work_row(connection, row["work_order_id"])
                checkpoint = self._workstream_checkpoint_from_row(work)
                successful = tuple(
                    dict.fromkeys((*checkpoint.successful_execution_ids, execution_id))
                )
                declared_outputs = result.get("output_files", ())
                has_outputs = isinstance(declared_outputs, (list, tuple)) and bool(declared_outputs)
                output_ready = tuple(
                    dict.fromkeys(
                        (
                            *checkpoint.output_ready_execution_ids,
                            *((execution_id,) if has_outputs else ()),
                        )
                    )
                )
                checkpoint = checkpoint.model_copy(
                    update={
                        "phase": (WorkstreamPhase.RUNNING),
                        "latest_execution_id": execution_id,
                        "successful_execution_ids": successful,
                        "output_ready_execution_ids": output_ready,
                    }
                )
                connection.execute(
                    """
                    UPDATE team_work_records
                    SET checkpoint_json = ?, updated_at = ?
                    WHERE work_order_id = ?
                    """,
                    (
                        _canonical_json(checkpoint.model_dump(mode="json")),
                        ended_at,
                        row["work_order_id"],
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM code_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            assert updated is not None
            return self._code_execution_from_row(updated)

    def list_code_executions(self, work_order_id: str) -> tuple[CodeExecutionRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM code_executions
                WHERE work_order_id = ? ORDER BY started_at, execution_id
                """,
                (work_order_id,),
            ).fetchall()
            return tuple(self._code_execution_from_row(row) for row in rows)

    def list_agent_job_code_executions(
        self,
        *,
        workspace_id: str,
        task_id: str | None,
        parent_request_id: str | None = None,
        job_key: str,
    ) -> tuple[CodeExecutionRecord, ...]:
        """List durable executions owned by one logical Expert session.

        A Coordinator follow-up creates a new WorkOrder, but it does not create a
        new Expert session when ``job_key`` is unchanged.  Evidence continuity is
        therefore scoped by workspace + research task + job key rather than by a
        foreground request or the latest WorkOrder. Request scope is retained
        only as a compatibility fallback for taskless sessions.
        """

        if task_id is not None:
            work_records = self.list_task_team_work(
                workspace_id=workspace_id,
                task_id=task_id,
            )
        elif parent_request_id is not None:
            work_records = self.list_team_work(
                workspace_id=workspace_id,
                parent_request_id=parent_request_id,
            )
        else:
            raise RequestStoreError(
                "AgentJob execution lookup requires a task or parent request scope"
            )
        work_order_ids = tuple(
            record.work_order.work_order_id
            for record in work_records
            if record.work_order.job_key == job_key
        )
        if not work_order_ids:
            return ()
        placeholders = ", ".join("?" for _ in work_order_ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM code_executions
                WHERE workspace_id = ?
                  AND work_order_id IN ({placeholders})
                ORDER BY started_at, execution_id
                """,
                (workspace_id, *work_order_ids),
            ).fetchall()
            return tuple(self._code_execution_from_row(row) for row in rows)

    def list_request_code_executions(
        self,
        *,
        workspace_id: str,
        task_id: str,
        parent_request_id: str,
    ) -> tuple[CodeExecutionRecord, ...]:
        """List durable computation results produced anywhere in one team request.

        Expert conversation memory remains isolated by ``job_key``.  This separate
        result boundary exposes only immutable code-execution records belonging to
        sibling WorkOrders in the same foreground request and task.  It lets a later
        packaging or visualization Expert consume an earlier scientific result
        without inheriting another Expert's transcript or recomputing the result.
        """

        work_order_ids = tuple(
            record.work_order.work_order_id
            for record in self.list_team_work(
                workspace_id=workspace_id,
                parent_request_id=parent_request_id,
            )
        )
        if not work_order_ids:
            return ()
        placeholders = ", ".join("?" for _ in work_order_ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM code_executions
                WHERE workspace_id = ?
                  AND task_id = ?
                  AND work_order_id IN ({placeholders})
                ORDER BY started_at, execution_id
                """,
                (workspace_id, task_id, *work_order_ids),
            ).fetchall()
            return tuple(self._code_execution_from_row(row) for row in rows)

    def get_code_execution(self, execution_id: str) -> CodeExecutionRecord | None:
        """Read one Expert-owned code audit record by its immutable identity."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM code_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            return self._code_execution_from_row(row) if row is not None else None

    def list_running_code_executions(self) -> tuple[CodeExecutionRecord, ...]:
        """Return executions that require startup settlement or interruption."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM code_executions WHERE state = 'running' "
                "ORDER BY started_at, execution_id"
            ).fetchall()
            return tuple(self._code_execution_from_row(row) for row in rows)

    def fail_running_code_executions(self) -> int:
        """Mark only executions left unprovable after manifest reconciliation."""

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT execution_id FROM code_executions WHERE state = 'running'"
            ).fetchall()
            if not rows:
                return 0
            now = _utc_now()
            connection.execute(
                """
                UPDATE code_executions
                SET state = 'failed', result_json = ?, ended_at = ?
                WHERE state = 'running'
                """,
                (
                    _canonical_json(
                        {
                            "error": "Code execution was interrupted before a complete "
                            "result manifest could be verified.",
                            "failure_code": WorkFailureCode.BACKEND_INTERRUPTED.value,
                        }
                    ),
                    now,
                ),
            )
            return len(rows)

    def complete_team_work(self, result: ExpertResult) -> TeamWorkRecord:
        """Atomically persist one terminal typed result for its work order."""

        with self._transaction() as connection:
            row = self._require_team_work_row(connection, result.work_order_id)
            checkpoint = self._workstream_checkpoint_from_row(row)
            terminal_outputs = {item.item_id: item for item in result.outputs}
            # Previously accepted checkpoint items are authoritative over the
            # otherwise identical candidate projection because they carry the
            # Coordinator-owned task result reference.
            for item in checkpoint.result_bundle.items:
                terminal_outputs[item.item_id] = item
            result = ExpertResult.model_validate(
                {
                    **result.model_dump(mode="python"),
                    "outputs": tuple(terminal_outputs.values()),
                }
            )
            result_json = _canonical_json(result.model_dump(mode="json"))
            if row["state"] in {
                WorkStatus.INCOMPLETE.value,
                WorkStatus.COMPLETED.value,
                WorkStatus.FAILED.value,
                WorkStatus.CANCELLED.value,
                WorkStatus.SKIPPED.value,
            }:
                if row["state"] == result.status.value and row["result_json"] == result_json:
                    self._record_expert_result_observations(connection, row, result)
                    return self._team_work_from_row(connection, row)
                raise RequestStoreError(
                    f"Team work already has a different terminal result: {result.work_order_id}"
                )
            if row["state"] not in {
                WorkStatus.QUEUED.value,
                WorkStatus.RUNNING.value,
            }:
                raise RequestStoreError(f"Invalid team work state: {row['state']}")
            if result.status is WorkStatus.COMPLETED:
                terminal_phase = WorkstreamPhase.COMPLETED
            elif (
                result.status is WorkStatus.INCOMPLETE
                and result.expert_decision is not None
                and result.expert_decision.value == "blocked"
            ):
                terminal_phase = WorkstreamPhase.BLOCKED
            elif result.status is WorkStatus.INCOMPLETE:
                terminal_phase = WorkstreamPhase.INCOMPLETE
            else:
                terminal_phase = WorkstreamPhase.FAILED
            checkpoint = checkpoint.model_copy(
                update={
                    "phase": terminal_phase,
                    "result_refs": tuple(
                        dict.fromkeys((*checkpoint.result_refs, *result.result_refs))
                    ),
                }
            )
            connection.execute(
                """
                UPDATE team_work_records
                SET state = ?, result_json = ?, checkpoint_json = ?, updated_at = ?
                WHERE work_order_id = ?
                """,
                (
                    result.status.value,
                    result_json,
                    _canonical_json(checkpoint.model_dump(mode="json")),
                    _utc_now(),
                    result.work_order_id,
                ),
            )
            self._record_expert_result_observations(connection, row, result)
            return self._team_work_from_row(
                connection, self._require_team_work_row(connection, result.work_order_id)
            )

    def _record_expert_result_observations(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        result: ExpertResult,
    ) -> None:
        record = self._team_work_from_row(connection, row)
        order = record.work_order
        task_id = order.task_id
        if task_id is None:
            request_row = connection.execute(
                "SELECT task_id FROM request_records WHERE request_id = ?",
                (order.parent_request_id,),
            ).fetchone()
            task_id = (
                str(request_row["task_id"])
                if request_row is not None and request_row["task_id"] is not None
                else None
            )
        if task_id is None:
            return
        task_row = connection.execute(
            "SELECT workspace_id FROM research_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task_row is None or task_row["workspace_id"] != record.workspace_id:
            return
        for draft in observations_from_expert_result(
            workspace_id=record.workspace_id,
            task_id=task_id,
            order=order,
            result=result,
        ):
            self._insert_research_observation(connection, draft)

    def record_workstream_results(
        self,
        work_order_id: str,
        refs: tuple[TaskResultRef, ...],
        *,
        output_bindings: dict[str, tuple[str, TaskResultRef]] | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> TeamWorkRecord:
        """Checkpoint task-local results after Coordinator acceptance.

        ``output_bindings`` joins each materialized view/report back to the
        immutable execution output already present in the Expert handoff. The
        refs and links are committed together so an API interruption cannot
        leave an accepted result detached from its execution evidence.
        """

        if not refs and not output_bindings:
            record = self.get_team_work(work_order_id)
            if record is None:
                raise RequestStoreError(f"Unknown team work order: {work_order_id}")
            return record
        with self._transaction() as connection:
            row = self._require_team_work_row(connection, work_order_id)
            checkpoint = self._workstream_checkpoint_from_row(row)
            bindings = output_bindings or {}
            available_refs = set((*checkpoint.result_refs, *refs))
            unsupported_refs = [
                ref.key for _execution_id, ref in bindings.values() if ref not in available_refs
            ]
            if unsupported_refs:
                raise RequestStoreError(
                    "ResultBundle output binding uses an unattached task result: "
                    + ", ".join(sorted(set(unsupported_refs)))
                )
            bundle = checkpoint.result_bundle
            if bindings:
                # Candidate outputs live in ExpertResult, not this accepted
                # ResultBundle. Retain every previously published result.
                bundle_items = {
                    item.output_name: item
                    for item in checkpoint.result_bundle.items
                    if item.result_ref is not None
                }
                execution_ids = {execution_id for execution_id, _result_ref in bindings.values()}
                bound_result_refs = {result_ref for _execution_id, result_ref in bindings.values()}
                if len(execution_ids) != 1 or len(bound_result_refs) != 1:
                    raise RequestStoreError(
                        "One ResultBundle item must bind one execution and one task result"
                    )
                execution_id = next(iter(execution_ids))
                result_ref = next(iter(bound_result_refs))
                output_records: dict[str, dict[str, Any]] = {}
                for output_name in bindings:
                    execution = connection.execute(
                        """
                        SELECT workspace_id, task_id, work_order_id, state, result_json
                        FROM code_executions WHERE execution_id = ?
                        """,
                        (execution_id,),
                    ).fetchone()
                    if execution is None:
                        raise RequestStoreError(
                            f"ResultBundle execution is unavailable: {execution_id}"
                        )
                    current_work = self._team_work_from_row(connection, row)
                    origin_row = self._require_team_work_row(connection, execution["work_order_id"])
                    origin_work = self._team_work_from_row(connection, origin_row)
                    same_task = (
                        current_work.work_order.task_id is not None
                        and current_work.work_order.task_id
                        == origin_work.work_order.task_id
                        == execution["task_id"]
                    )
                    same_legacy_agent_job = (
                        current_work.work_order.task_id is None
                        and current_work.work_order.job_key is not None
                        and current_work.work_order.job_key == origin_work.work_order.job_key
                    )
                    same_legacy_request = (
                        (
                            current_work.work_order.task_id is None
                            or origin_work.work_order.task_id is None
                        )
                        and current_work.work_order.parent_request_id
                        == origin_work.work_order.parent_request_id
                    )
                    if (
                        execution["workspace_id"] != row["workspace_id"]
                        or execution["state"] == "running"
                        or execution["result_json"] is None
                        or (
                            execution["work_order_id"] != work_order_id
                            and not (
                                same_task
                                or same_legacy_agent_job
                                or same_legacy_request
                            )
                        )
                    ):
                        raise RequestStoreError(
                            f"ResultBundle execution is unavailable: {execution_id}"
                        )
                    execution_result = json.loads(execution["result_json"])
                    declared_outputs = execution_result.get("outputs", ())
                    output = next(
                        (
                            candidate
                            for candidate in declared_outputs
                            if isinstance(candidate, dict) and candidate.get("name") == output_name
                        ),
                        None,
                    )
                    if output is None:
                        raise RequestStoreError(
                            f"ResultBundle output is unavailable: {output_name}"
                        )
                    size_bytes = output.get("bytes")
                    sha256 = output.get("sha256")
                    if (
                        not isinstance(size_bytes, int)
                        or size_bytes < 0
                        or not isinstance(sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
                    ):
                        raise RequestStoreError(
                            f"ResultBundle output metadata is invalid: {output_name}"
                        )
                    output_records[output_name] = output
                primary_output_name = next(iter(bindings))
                primary_output = output_records[primary_output_name]
                item_id = expert_output_item_id(
                    execution_id=execution_id,
                    output_name=primary_output_name,
                )
                metadata = result_metadata or {}
                result_kind = metadata.get("kind")
                if result_kind not in {None, "interactive_view", "report"}:
                    raise RequestStoreError(f"Unsupported ResultBundle result kind: {result_kind}")
                bundle_items[primary_output_name] = ExpertOutput(
                    item_id=item_id,
                    execution_id=execution_id,
                    output_name=primary_output_name,
                    supporting_output_names=tuple(bindings)[1:],
                    size_bytes=int(primary_output["bytes"]),
                    sha256=str(primary_output["sha256"]),
                    result_ref=result_ref,
                    result_kind=result_kind,
                    source_handle=metadata.get("source_handle"),
                    title=metadata.get("title", ""),
                    summary=metadata.get("summary", ""),
                    view_type=metadata.get("view_type"),
                    view_spec=metadata.get("view_spec"),
                    data_schema=metadata.get("data_schema"),
                    claims=tuple(
                        str(claim).strip()
                        for claim in metadata.get("claims", ())
                        if str(claim).strip()
                    ),
                )
                bundle = ResultBundle(items=tuple(bundle_items.values()))
            terminal_result_json: str | None = None
            terminal_result = (
                ExpertResult.model_validate(json.loads(row["result_json"]))
                if row["result_json"] is not None
                else None
            )
            checkpoint = checkpoint.model_copy(
                update={
                    "phase": (
                        checkpoint.phase
                        if terminal_result is not None
                        else WorkstreamPhase.DELIVERING
                    ),
                    "result_refs": tuple(dict.fromkeys((*checkpoint.result_refs, *refs))),
                    "result_bundle": bundle,
                }
            )
            if terminal_result is not None:
                terminal_outputs = {
                    item.item_id: item for item in terminal_result.outputs
                }
                for item in bundle.items:
                    terminal_outputs[item.item_id] = item
                available_output_ids = set(terminal_outputs)
                retained_conclusions = tuple(
                    conclusion
                    for conclusion in terminal_result.conclusions
                    if (
                        conclusion.output_ids
                        and set(conclusion.output_ids).issubset(available_output_ids)
                    )
                    or (not conclusion.output_ids and conclusion.evidence_refs)
                )
                terminal_result = ExpertResult.model_validate(
                    {
                        **terminal_result.model_dump(mode="python"),
                        "outputs": tuple(terminal_outputs.values()),
                        "conclusions": retained_conclusions,
                    }
                )
                terminal_result_json = _canonical_json(
                    terminal_result.model_dump(mode="json")
                )
            connection.execute(
                """
                UPDATE team_work_records
                SET checkpoint_json = ?,
                    result_json = COALESCE(?, result_json),
                    updated_at = ?
                WHERE work_order_id = ?
                """,
                (
                    _canonical_json(checkpoint.model_dump(mode="json")),
                    terminal_result_json,
                    _utc_now(),
                    work_order_id,
                ),
            )
            if terminal_result is not None:
                # Publication strengthens the original handoff with immutable
                # result refs. Record the evidence-bound observation so the
                # memory linker can supersede its unreviewed precursor.
                self._record_expert_result_observations(connection, row, terminal_result)
            return self._team_work_from_row(
                connection, self._require_team_work_row(connection, work_order_id)
            )

    def record_workstream_draft(
        self,
        work_order_id: str,
        result: ExpertResult | None = None,
        *,
        phase: WorkstreamPhase = WorkstreamPhase.DELIVERING,
    ) -> TeamWorkRecord:
        """Persist the receiver-validated result capsule before delivery.

        This is deliberately method-agnostic. It makes formatting, provider,
        or artifact-publication failures resumable without asking an Expert to
        repeat completed scientific work.
        """

        if phase not in {
            WorkstreamPhase.RESULT_READY,
            WorkstreamPhase.DELIVERING,
        }:
            raise ValueError("A nonterminal workstream draft needs a delivery phase")
        with self._transaction() as connection:
            row = self._require_team_work_row(connection, work_order_id)
            if row["state"] not in {
                WorkStatus.QUEUED.value,
                WorkStatus.RUNNING.value,
            }:
                raise RequestStoreError("Cannot checkpoint a terminal Expert workstream")
            checkpoint = self._workstream_checkpoint_from_row(row)
            if result is not None:
                result = ExpertResult.model_validate(
                    {
                        **result.model_dump(mode="python"),
                        "outputs": checkpoint.result_bundle.items,
                    }
                )
            checkpoint = checkpoint.model_copy(
                update={
                    "phase": phase,
                    "draft_result": result if result is not None else checkpoint.draft_result,
                }
            )
            connection.execute(
                """
                UPDATE team_work_records
                SET checkpoint_json = ?, updated_at = ? WHERE work_order_id = ?
                """,
                (
                    _canonical_json(checkpoint.model_dump(mode="json")),
                    _utc_now(),
                    work_order_id,
                ),
            )
            return self._team_work_from_row(
                connection, self._require_team_work_row(connection, work_order_id)
            )

    def get_team_work(self, work_order_id: str) -> TeamWorkRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM team_work_records WHERE work_order_id = ?",
                (work_order_id,),
            ).fetchone()
            return self._team_work_from_row(self._connection, row) if row is not None else None

    def list_team_work(
        self,
        *,
        workspace_id: str,
        parent_request_id: str | None = None,
    ) -> list[TeamWorkRecord]:
        with self._lock:
            if parent_request_id is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM team_work_records
                    WHERE workspace_id = ? ORDER BY created_at, work_order_id
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM team_work_records
                    WHERE workspace_id = ? AND parent_request_id = ?
                    ORDER BY created_at, work_order_id
                    """,
                    (workspace_id, parent_request_id),
                ).fetchall()
            return [self._team_work_from_row(self._connection, row) for row in rows]

    def list_task_team_work(
        self,
        *,
        workspace_id: str,
        task_id: str,
    ) -> list[TeamWorkRecord]:
        """List every assignment round for persistent Experts in one task.

        New records carry ``task_id`` directly in the WorkOrder. The parent
        request lookup keeps pre-migration records readable without inventing a
        second participant identity or copying their evidence.
        """

        selected: list[TeamWorkRecord] = []
        for record in self.list_team_work(workspace_id=workspace_id):
            order_task_id = record.work_order.task_id
            if order_task_id is None:
                parent = self.get_request(record.work_order.parent_request_id)
                order_task_id = parent.task_id if parent is not None else None
            if order_task_id == task_id:
                selected.append(record)
        return selected

    def list_active_team_work(self) -> tuple[TeamWorkRecord, ...]:
        """Return workstreams that need startup result reconciliation."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM team_work_records
                WHERE state IN ('queued', 'running')
                ORDER BY created_at, work_order_id
                """
            ).fetchall()
            return tuple(self._team_work_from_row(self._connection, row) for row in rows)

    def interrupt_active_team_work(self) -> int:
        """Close workstreams after durable executions/results have been reconciled."""

        with self._transaction() as connection:
            now = _utc_now()
            rows = connection.execute(
                """
                SELECT * FROM team_work_records
                WHERE state IN ('queued', 'running')
                """
            ).fetchall()
            for row in rows:
                work = self._team_work_from_row(connection, row)
                order = work.work_order
                checkpoint = self._workstream_checkpoint_from_row(row)
                task_scope = order.task_id or f"request:{order.parent_request_id}"
                participant_key = order.profile_id or (
                    f"{order.authority.value}:{order.semantic_role}"
                )
                job_key = order.job_key or order.work_order_id
                session_row = connection.execute(
                    """
                    SELECT * FROM expert_session_checkpoints
                    WHERE workspace_id = ? AND task_scope = ?
                      AND participant_key = ? AND job_key = ?
                    """,
                    (row["workspace_id"], task_scope, participant_key, job_key),
                ).fetchone()
                saved_text = ""
                if session_row is not None:
                    try:
                        session_checkpoint = self._expert_session_checkpoint_from_row(session_row)
                        if session_checkpoint.work_order_id == order.work_order_id:
                            saved_text = self._last_assistant_text(session_checkpoint.messages)
                    except TaskCheckpointIncompatible:
                        saved_text = ""
                has_saved_result = bool(
                    saved_text
                    or checkpoint.draft_result is not None
                    or checkpoint.result_refs
                    or checkpoint.result_bundle.items
                    or checkpoint.successful_execution_ids
                )
                evidence_refs = tuple(
                    EvidenceRef(kind="code_execution", ref=execution_id)
                    for execution_id in checkpoint.successful_execution_ids
                )
                if checkpoint.draft_result is not None:
                    result = ExpertResult.model_validate(
                        {
                            **checkpoint.draft_result.model_dump(mode="python"),
                            "status": WorkStatus.INCOMPLETE,
                            "result_origin": ExpertResultOrigin.BACKEND_RECOVERED,
                            "evidence_refs": tuple(
                                dict.fromkeys(
                                    (
                                        *checkpoint.draft_result.evidence_refs,
                                        *evidence_refs,
                                    )
                                )
                            ),
                            "outputs": checkpoint.result_bundle.items,
                            "failure_code": WorkFailureCode.BACKEND_INTERRUPTED,
                            "error": "Expert result delivery was interrupted by a backend restart.",
                        }
                    )
                else:
                    result = ExpertResult(
                        work_order_id=row["work_order_id"],
                        status=(WorkStatus.INCOMPLETE if has_saved_result else WorkStatus.FAILED),
                        result_origin=ExpertResultOrigin.BACKEND_RECOVERED,
                        text=(
                            saved_text
                            or (
                                "Expert work was interrupted after durable partial results "
                                "were saved."
                                if has_saved_result
                                else "Expert work was interrupted by a backend restart."
                            )
                        ),
                        evidence_refs=evidence_refs,
                        outputs=checkpoint.result_bundle.items,
                        failure_code=WorkFailureCode.BACKEND_INTERRUPTED,
                        error="Expert work was interrupted by a backend restart.",
                    )
                checkpoint = checkpoint.model_copy(
                    update={
                        "phase": (
                            WorkstreamPhase.INCOMPLETE
                            if result.status is WorkStatus.INCOMPLETE
                            else WorkstreamPhase.FAILED
                        ),
                        "draft_result": (
                            result
                            if result.status is WorkStatus.INCOMPLETE
                            else checkpoint.draft_result
                        ),
                    }
                )
                connection.execute(
                    """
                    UPDATE team_work_records
                    SET state = ?, result_json = ?, checkpoint_json = ?, updated_at = ?
                    WHERE work_order_id = ?
                    """,
                    (
                        result.status.value,
                        _canonical_json(result.model_dump(mode="json")),
                        _canonical_json(checkpoint.model_dump(mode="json")),
                        now,
                        row["work_order_id"],
                    ),
                )
            return len(rows)

    def get_artifact(self, *, workspace_id: str, ref: ArtifactRef) -> ArtifactVersion | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM artifact_versions
                WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                """,
                (workspace_id, ref.artifact_id, ref.version),
            ).fetchone()
            return self._artifact_from_row(row) if row is not None else None

    def get_projection(self, *, workspace_id: str, ref: ArtifactRef) -> ArtifactProjection | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM artifact_projections
                WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                """,
                (workspace_id, ref.artifact_id, ref.version),
            ).fetchone()
            return self._projection_from_row(row) if row is not None else None

    def rebuild_impact_projections(
        self, *, workspace_id: str
    ) -> dict[ArtifactRef, ArtifactProjection]:
        """Deterministically rebuild impact projections from authoritative DB records."""

        with self._transaction() as connection:
            return self._rebuild_impact_in_transaction(connection, workspace_id=workspace_id)

    def set_artifact_lifecycle(
        self,
        *,
        workspace_id: str,
        ref: ArtifactRef,
        lifecycle_state: str,
        source_event_id: str | None = None,
    ) -> ArtifactProjection:
        """Change a lifecycle projection without mutating the immutable artifact manifest."""

        with self._transaction() as connection:
            projection = self._require_projection(
                connection,
                workspace_id,
                ref.artifact_id,
                ref.version,
            ).model_copy(
                update={
                    "lifecycle_state": lifecycle_state,
                    "updated_at": datetime.now(timezone.utc),
                    "source_event_id": source_event_id,
                }
            )
            self._upsert_projection(connection, projection, workspace_id=workspace_id)
            rebuilt = self._rebuild_impact_in_transaction(connection, workspace_id=workspace_id)
            return rebuilt[ref]

    def set_artifact_impact_input(
        self,
        *,
        state_event_id: str,
        workspace_id: str,
        ref: ArtifactRef,
        kind: str,
        active: bool,
        source_event_id: str | None = None,
    ) -> ArtifactProjection:
        """Record source availability/decision facts and rebuild dependent projections."""

        if kind not in {"source_unavailable", "decision_invalidated"}:
            raise RequestStoreError(f"Unsupported artifact impact input: {kind}")
        with self._transaction() as connection:
            self._require_artifact(connection, workspace_id, ref.artifact_id, ref.version)
            connection.execute(
                """
                INSERT INTO artifact_state_events (
                    state_event_id, workspace_id, artifact_id, version, kind, active,
                    source_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state_event_id) DO UPDATE SET
                    active = excluded.active,
                    source_event_id = excluded.source_event_id
                """,
                (
                    state_event_id,
                    workspace_id,
                    ref.artifact_id,
                    ref.version,
                    kind,
                    int(active),
                    source_event_id,
                    _utc_now(),
                ),
            )
            rebuilt = self._rebuild_impact_in_transaction(connection, workspace_id=workspace_id)
            return rebuilt[ref]

    def commit_cancellation(
        self,
        *,
        cancel_request_id: str,
        target_request_id: str,
        target_event: EventEnvelope | None,
        terminal_event: EventEnvelope,
    ) -> CancellationCommit:
        """Persist target cancellation and the control-request terminal together."""

        with self._transaction() as connection:
            self._ensure_nonterminal_request(connection, cancel_request_id)
            target = self._request_from_row(
                self._require_request_row(connection, target_request_id)
            )
            if target.terminal:
                target_event = None
            elif target_event is None:
                raise RequestStoreError("Active cancellation requires a target terminal event")
            elif target_event.request_id != target_request_id:
                raise RequestStoreError("Cancellation target event has the wrong request ID")
            if target_event is not None:
                self._commit_terminal_in_transaction(connection, target_request_id, target_event)
                self._interrupt_interactions_for_request_in_connection(
                    connection, target_request_id
                )
                if target.task_id is not None:
                    self._mark_task_transcript_interrupted_in_connection(
                        connection, task_id=target.task_id, request_id=target_request_id
                    )
                    self._clear_task_active_request_in_connection(
                        connection,
                        task_id=target.task_id,
                        request_id=target_request_id,
                    )
            self._commit_terminal_in_transaction(connection, cancel_request_id, terminal_event)
            return CancellationCommit(
                target=self._request_from_row(
                    self._require_request_row(connection, target_request_id)
                ),
                target_terminal_event=target_event,
                terminal_event=terminal_event,
            )

    def record_coordinator_result(
        self,
        *,
        request_id: str,
        result: CoordinatorResult,
    ) -> CoordinatorResult:
        """Persist the Coordinator boundary before acknowledging its tool call."""

        serialized = _canonical_json(result.model_dump(mode="json"))
        with self._transaction() as connection:
            request = self._request_from_row(self._require_request_row(connection, request_id))
            if request.request_type != "session.submit":
                raise RequestStoreError(
                    "Coordinator result parent is not a foreground Ocean request"
                )
            existing = connection.execute(
                "SELECT result_json FROM coordinator_result_receipts WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if existing["result_json"] != serialized:
                    raise RequestStoreError(
                        "Coordinator result request already has a different durable receipt"
                    )
                return CoordinatorResult.model_validate_json(existing["result_json"])
            if request.terminal:
                raise RequestStoreError("Cannot attach a Coordinator result to a terminal request")
            connection.execute(
                """
                INSERT INTO coordinator_result_receipts (
                    request_id, result_json, created_at
                ) VALUES (?, ?, ?)
                """,
                (request_id, serialized, _utc_now()),
            )
            return result

    def get_coordinator_result(self, request_id: str) -> CoordinatorResult | None:
        """Read the durable Coordinator boundary for one foreground request."""

        with self._lock:
            row = self._connection.execute(
                "SELECT result_json FROM coordinator_result_receipts WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            return CoordinatorResult.model_validate_json(row["result_json"])

    def recover_incomplete(
        self,
        event_factory: Callable[[RequestRecord], EventEnvelope],
        completed_event_factory: Callable[[RequestRecord, CoordinatorResult], EventEnvelope]
        | None = None,
    ) -> list[EventEnvelope]:
        """Settle active requests from durable receipts or mark them interrupted."""

        recovered: list[EventEnvelope] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM request_records WHERE state IN ('accepted', 'in_progress')"
            ).fetchall()
            for row in rows:
                record = self._request_from_row(row)
                receipt_row = connection.execute(
                    "SELECT result_json FROM coordinator_result_receipts WHERE request_id = ?",
                    (record.request_id,),
                ).fetchone()
                coordinator_result = (
                    CoordinatorResult.model_validate_json(receipt_row["result_json"])
                    if receipt_row is not None
                    else None
                )
                recovered_completion = (
                    coordinator_result is not None
                    and completed_event_factory is not None
                    and record.request_type == "session.submit"
                )
                event = (
                    completed_event_factory(record, coordinator_result)
                    if recovered_completion and coordinator_result is not None
                    else event_factory(record)
                )
                self._commit_terminal_in_transaction(connection, record.request_id, event)
                self._interrupt_interactions_for_request_in_connection(
                    connection, record.request_id
                )
                if record.task_id is not None:
                    workflow = connection.execute(
                        "SELECT 1 FROM task_workflows WHERE request_id = ?",
                        (record.request_id,),
                    ).fetchone()
                    if recovered_completion:
                        now = _utc_now()
                        item_id = f"recovered-coordinator-{record.request_id}"
                        existing_item = connection.execute(
                            "SELECT 1 FROM task_transcript_items WHERE item_id = ?",
                            (item_id,),
                        ).fetchone()
                        if existing_item is None and coordinator_result is not None:
                            sequence = int(
                                connection.execute(
                                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                                    "FROM task_transcript_items WHERE task_id = ?",
                                    (record.task_id,),
                                ).fetchone()["next_sequence"]
                            )
                            connection.execute(
                                """
                                INSERT INTO task_transcript_items (
                                    item_id, task_id, sequence, role, text, request_id,
                                    turn_id, tool_call_id, interrupted, created_at
                                ) VALUES (?, ?, ?, 'assistant', ?, ?, NULL, NULL, 0, ?)
                                """,
                                (
                                    item_id,
                                    record.task_id,
                                    sequence,
                                    coordinator_result.answer_markdown,
                                    record.request_id,
                                    now,
                                ),
                            )
                        connection.execute(
                            """
                            UPDATE task_workflows
                            SET state = 'completed', activity = 'Task completed',
                                heartbeat_at = ?, updated_at = ?
                            WHERE request_id = ?
                            """,
                            (now, now, record.request_id),
                        )
                    elif workflow is None:
                        # Preserve legacy behavior for requests created before
                        # durable workflow checkpoints existed.
                        self._mark_task_transcript_interrupted_in_connection(
                            connection,
                            task_id=record.task_id,
                            request_id=record.request_id,
                        )
                    else:
                        now = _utc_now()
                        connection.execute(
                            """
                            UPDATE task_workflows
                            SET state = 'incomplete',
                                activity = 'Backend restarted — ready to resume from checkpoint',
                                heartbeat_at = ?, updated_at = ?
                            WHERE request_id = ?
                            """,
                            (now, now, record.request_id),
                        )
                    self._clear_task_active_request_in_connection(
                        connection,
                        task_id=record.task_id,
                        request_id=record.request_id,
                    )
                recovered.append(event)
        return recovered

    def operation_result(self, operation_id: str) -> dict[str, Any] | None:
        """Return an existing model-tool operation result without re-executing it."""

        with self._lock:
            row = self._connection.execute(
                "SELECT result_json FROM operation_checkpoints WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return json.loads(row["result_json"]) if row is not None else None

    def begin_tool_call(
        self,
        *,
        operation_id: str,
        request_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
    ) -> ToolCallRecord:
        """Record a model mutation before its domain action begins.

        The operation ID is backend-generated, so disagreement on any correlated
        field signals a programming/integration error rather than a retry.
        """

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tool_call_records WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is not None:
                record = self._tool_call_record_from_row(row)
                identity = (
                    record.request_id,
                    record.turn_id,
                    record.tool_call_id,
                    record.tool_name,
                )
                proposed = (request_id, turn_id, tool_call_id, tool_name)
                if identity != proposed:
                    raise RequestStoreError(
                        f"Operation ID already belongs to a different tool call: {operation_id}"
                    )
                return record
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO tool_call_records (
                    operation_id, request_id, turn_id, tool_call_id, tool_name, state, created_at
                ) VALUES (?, ?, ?, ?, ?, 'in_progress', ?)
                """,
                (operation_id, request_id, turn_id, tool_call_id, tool_name, now),
            )
            return self._tool_call_record_from_row(
                connection.execute(
                    "SELECT * FROM tool_call_records WHERE operation_id = ?", (operation_id,)
                ).fetchone()
            )

    def get_tool_call_record(self, operation_id: str) -> ToolCallRecord | None:
        """Read one durable model-tool operation record for UI/audit tooling."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tool_call_records WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return self._tool_call_record_from_row(row) if row is not None else None

    def record_operation_result(
        self,
        *,
        operation_id: str,
        request_id: str,
        turn_id: str,
        tool_call_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a model-originated operation checkpoint exactly once."""

        serialized = _canonical_json(result)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT result_json FROM operation_checkpoints WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                replayed = json.loads(existing["result_json"])
                self._complete_tool_call_in_transaction(connection, operation_id, replayed)
                return replayed
            connection.execute(
                """
                INSERT INTO operation_checkpoints (
                    operation_id, request_id, turn_id, tool_call_id, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (operation_id, request_id, turn_id, tool_call_id, serialized, _utc_now()),
            )
            self._complete_tool_call_in_transaction(connection, operation_id, result)
            return result

    def _artifact_from_manifest(
        self, manifest: ArtifactManifest, manifest_uri: str
    ) -> ArtifactVersion:
        return ArtifactVersion(
            ref=ArtifactRef(artifact_id=manifest.artifact_id, version=manifest.version),
            workspace_id=manifest.workspace_id,
            artifact_type=manifest.artifact_type,
            schema_version=manifest.artifact_schema_version,
            title=manifest.title,
            summary=manifest.summary,
            created_at=manifest.created_at,
            created_by=manifest.created_by,
            supersedes_version=manifest.supersedes_version,
            content=manifest.content,
            intrinsic_links=manifest.intrinsic_links,
            provenance=manifest.provenance,
            manifest_uri=manifest_uri,
            manifest_sha256=manifest.manifest_sha256,
            files=manifest.files,
        )

    def _insert_artifact_version(
        self, connection: sqlite3.Connection, artifact: ArtifactVersion
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifact_versions (
                workspace_id, artifact_id, version, artifact_type, schema_version,
                title, summary, created_at, created_by, supersedes_version,
                content_json, intrinsic_links_json, provenance_json, manifest_uri, manifest_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.workspace_id,
                artifact.ref.artifact_id,
                artifact.ref.version,
                artifact.artifact_type,
                artifact.schema_version,
                artifact.title,
                artifact.summary,
                artifact.created_at.isoformat(),
                artifact.created_by,
                artifact.supersedes_version,
                _canonical_json(artifact.content),
                _canonical_json(
                    [link.model_dump(mode="json") for link in artifact.intrinsic_links]
                ),
                _canonical_json(artifact.provenance),
                artifact.manifest_uri,
                artifact.manifest_sha256,
            ),
        )
        for file in artifact.files:
            connection.execute(
                """
                INSERT INTO artifact_files (
                    workspace_id, artifact_id, version, uri, mime_type, size_bytes, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.workspace_id,
                    artifact.ref.artifact_id,
                    artifact.ref.version,
                    file.uri,
                    file.mime_type,
                    file.size_bytes,
                    file.sha256,
                ),
            )

    @staticmethod
    def _links_for_artifact(artifact: ArtifactVersion) -> list[ArtifactLinkDraft]:
        links = list(artifact.intrinsic_links)
        if artifact.supersedes_version is not None:
            links.append(
                ArtifactLinkDraft(
                    relation="supersedes",
                    target=ArtifactRef(
                        artifact_id=artifact.ref.artifact_id,
                        version=artifact.supersedes_version,
                    ),
                    intrinsic=True,
                )
            )
        return links

    def _validate_artifact_links(
        self,
        connection: sqlite3.Connection,
        artifact: ArtifactVersion,
    ) -> None:
        """Reject dangling, self-referential, or cyclic typed dependencies before insertion."""

        existing_edges = self._impact_edges(connection, workspace_id=artifact.workspace_id)
        for link in self._links_for_artifact(artifact):
            if link.target == artifact.ref:
                raise ArtifactVersionConflict("Artifact cannot link to itself")
            target = connection.execute(
                """
                SELECT artifact_type FROM artifact_versions
                WHERE workspace_id = ? AND artifact_id = ? AND version = ?
                """,
                (artifact.workspace_id, link.target.artifact_id, link.target.version),
            ).fetchone()
            if target is None:
                raise ArtifactVersionConflict(
                    f"Artifact link target does not exist: {link.target.key}"
                )
            if link.relation == "supersedes" and target["artifact_type"] != artifact.artifact_type:
                raise ArtifactVersionConflict(
                    "An artifact version may only supersede the same artifact type"
                )
            if would_create_propagating_cycle(
                source=artifact.ref,
                target=link.target,
                relation=link.relation,
                existing=existing_edges,
            ):
                raise ArtifactVersionConflict("Intrinsic dependency links may not create a cycle")
            existing_edges.append(
                ImpactEdge(source=artifact.ref, target=link.target, relation=link.relation)
            )

    def _validate_domain_content_refs(
        self,
        connection: sqlite3.Connection,
        artifact: ArtifactVersion,
    ) -> None:
        """Validate exact current-model references without planning or renderer objects."""

        required: list[tuple[ArtifactRef, set[str], str]] = []
        required_links: list[tuple[str, ArtifactRef, str]] = []
        if artifact.artifact_type == "claim":
            content = ClaimContent.model_validate(artifact.content)
            required.extend(
                (item.paper_ref, {"paper"}, "Claim evidence paper_ref") for item in content.evidence
            )
            required_links.extend(
                ("derived_from", item.paper_ref, "Claim evidence paper_ref")
                for item in content.evidence
            )
        elif artifact.artifact_type == "observation":
            content = ObservationContent.model_validate(artifact.content)
            required.extend((ref, set(), "Observation source_ref") for ref in content.source_refs)
            required_links.extend(
                ("derived_from", ref, "Observation source_ref") for ref in content.source_refs
            )
        elif artifact.artifact_type == "hypothesis":
            content = HypothesisContent.model_validate(artifact.content)
            required.extend(
                (ref, set(), "Hypothesis evidence_ref") for ref in content.evidence_refs
            )
            required_links.extend(
                ("motivates", ref, "Hypothesis evidence_ref") for ref in content.evidence_refs
            )
        elif artifact.artifact_type == "experiment":
            content = ExperimentContent.model_validate(artifact.content)
            required.append((content.hypothesis_ref, {"hypothesis"}, "Experiment hypothesis_ref"))
            required.extend(
                (ref, {"dataset"}, "Experiment input_ref") for ref in content.input_refs
            )
            required.extend(
                (
                    ref,
                    {"interactive_view", "observation", "report"},
                    "Experiment result_ref",
                )
                for ref in content.result_refs
            )
            required_links.append(
                ("tests_hypothesis", content.hypothesis_ref, "Experiment hypothesis_ref")
            )
            required_links.extend(
                ("uses_dataset", ref, "Experiment input_ref") for ref in content.input_refs
            )
            required_links.extend(
                ("derived_from", ref, "Experiment result_ref") for ref in content.result_refs
            )
        elif artifact.artifact_type == "decision":
            content = DecisionContent.model_validate(artifact.content)
            required.extend((ref, set(), "Decision subject_ref") for ref in content.subject_refs)
        elif artifact.artifact_type == "interactive_view":
            content = InteractiveViewContent.model_validate(artifact.content)
            required.extend(
                (ref, {"dataset"}, "Interactive view dataset_ref") for ref in content.dataset_refs
            )
            required_links.extend(
                ("uses_dataset", ref, "Interactive view dataset_ref")
                for ref in content.dataset_refs
            )
        elif artifact.artifact_type == "report":
            content = ReportContent.model_validate(artifact.content)
            required.extend((ref, set(), "Report artifact_ref") for ref in content.artifact_refs)
            required_links.extend(
                ("included_in_report", ref, "Report artifact_ref") for ref in content.artifact_refs
            )

        for ref, allowed_types, label in required:
            target = self._require_artifact(
                connection, artifact.workspace_id, ref.artifact_id, ref.version
            )
            if allowed_types and target.artifact_type not in allowed_types:
                expected = ", ".join(sorted(allowed_types))
                raise ArtifactVersionConflict(
                    f"{label} must reference {expected}, found {target.artifact_type}: {ref.key}"
                )
        intrinsic_links = {
            (link.relation, link.target) for link in artifact.intrinsic_links if link.intrinsic
        }
        for relation, ref, label in required_links:
            if (relation, ref) not in intrinsic_links:
                raise ArtifactVersionConflict(
                    f"{label} requires an intrinsic {relation} link to {ref.key}"
                )

    def _insert_artifact_links(
        self,
        connection: sqlite3.Connection,
        artifact: ArtifactVersion,
        *,
        source_event_id: str,
    ) -> None:
        for link in self._links_for_artifact(artifact):
            connection.execute(
                """
                INSERT INTO artifact_links (
                    workspace_id, source_artifact_id, source_version,
                    target_artifact_id, target_version, relation, intrinsic,
                    created_at, source_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.workspace_id,
                    artifact.ref.artifact_id,
                    artifact.ref.version,
                    link.target.artifact_id,
                    link.target.version,
                    link.relation,
                    int(link.intrinsic),
                    _utc_now(),
                    source_event_id,
                ),
            )

    def _mark_superseded(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        artifact_id: str,
        version: int,
        source_event_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT * FROM artifact_projections
            WHERE workspace_id = ? AND artifact_id = ? AND version = ?
            """,
            (workspace_id, artifact_id, version),
        ).fetchone()
        if row is None:
            raise ArtifactVersionConflict(
                f"Superseded version does not exist: {artifact_id}@v{version:04d}"
            )
        projection = self._projection_from_row(row).model_copy(
            update={
                "lifecycle_state": "superseded",
                "updated_at": datetime.now(timezone.utc),
                "source_event_id": source_event_id,
            }
        )
        self._upsert_projection(connection, projection, workspace_id=workspace_id)

    def _rebuild_impact_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
    ) -> dict[ArtifactRef, ArtifactProjection]:
        rows = connection.execute(
            """
            SELECT p.*, v.artifact_id AS version_artifact_id
            FROM artifact_projections AS p
            JOIN artifact_versions AS v
              ON v.workspace_id = p.workspace_id
             AND v.artifact_id = p.artifact_id
             AND v.version = p.version
            WHERE p.workspace_id = ?
            """,
            (workspace_id,),
        ).fetchall()
        state_rows = connection.execute(
            """
            SELECT * FROM artifact_state_events
            WHERE workspace_id = ? AND active = 1 ORDER BY created_at, state_event_id
            """,
            (workspace_id,),
        ).fetchall()
        state_inputs: dict[ArtifactRef, dict[str, sqlite3.Row]] = {}
        for row in state_rows:
            ref = ArtifactRef(artifact_id=row["artifact_id"], version=int(row["version"]))
            state_inputs.setdefault(ref, {})[row["kind"]] = row
        projections: dict[ArtifactRef, ArtifactProjection] = {}
        for row in rows:
            projection = self._projection_from_row(row)
            projections[projection.ref] = projection
        nodes: list[ImpactNode] = []
        for ref, projection in projections.items():
            inputs = state_inputs.get(ref, {})
            event_row = inputs.get("decision_invalidated") or inputs.get("source_unavailable")
            nodes.append(
                ImpactNode(
                    ref=ref,
                    lifecycle_state=projection.lifecycle_state,
                    source_unavailable="source_unavailable" in inputs,
                    decision_invalidated="decision_invalidated" in inputs,
                    source_event_id=(
                        event_row["source_event_id"]
                        if event_row is not None
                        else projection.source_event_id
                    ),
                )
            )
        impacts = rebuild_impact(nodes, self._impact_edges(connection, workspace_id=workspace_id))
        rebuilt: dict[ArtifactRef, ArtifactProjection] = {}
        for ref, existing in projections.items():
            impact = impacts[ref]
            projection = existing.model_copy(
                update={
                    "impact_state": impact.state,
                    "impact_reasons": impact.reasons,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._upsert_projection(connection, projection, workspace_id=workspace_id)
            rebuilt[ref] = projection
        return rebuilt

    @staticmethod
    def _impact_edges(connection: sqlite3.Connection, *, workspace_id: str) -> list[ImpactEdge]:
        rows = connection.execute(
            """
            SELECT source_artifact_id, source_version, target_artifact_id, target_version, relation
            FROM artifact_links WHERE workspace_id = ? ORDER BY link_id
            """,
            (workspace_id,),
        ).fetchall()
        return [
            ImpactEdge(
                source=ArtifactRef(
                    artifact_id=row["source_artifact_id"], version=int(row["source_version"])
                ),
                target=ArtifactRef(
                    artifact_id=row["target_artifact_id"], version=int(row["target_version"])
                ),
                relation=row["relation"],
            )
            for row in rows
        ]

    @staticmethod
    def _upsert_projection(
        connection: sqlite3.Connection,
        projection: ArtifactProjection,
        *,
        workspace_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifact_projections (
                workspace_id, artifact_id, version, lifecycle_state,
                impact_state, impact_reasons_json, updated_at, source_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, artifact_id, version) DO UPDATE SET
                lifecycle_state = excluded.lifecycle_state,
                impact_state = excluded.impact_state,
                impact_reasons_json = excluded.impact_reasons_json,
                updated_at = excluded.updated_at,
                source_event_id = excluded.source_event_id
            """,
            (
                workspace_id,
                projection.ref.artifact_id,
                projection.ref.version,
                projection.lifecycle_state,
                projection.impact_state,
                _canonical_json(
                    [reason.model_dump(mode="json") for reason in projection.impact_reasons]
                ),
                projection.updated_at.isoformat(),
                projection.source_event_id,
            ),
        )

    def _require_artifact(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        artifact_id: str,
        version: int,
    ) -> ArtifactVersion:
        row = connection.execute(
            """
            SELECT * FROM artifact_versions
            WHERE workspace_id = ? AND artifact_id = ? AND version = ?
            """,
            (workspace_id, artifact_id, version),
        ).fetchone()
        if row is None:
            raise RequestNotFound(f"Artifact version is not found: {artifact_id}@v{version:04d}")
        return self._artifact_from_row(row, connection=connection)

    def _require_projection(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        artifact_id: str,
        version: int,
    ) -> ArtifactProjection:
        row = connection.execute(
            """
            SELECT * FROM artifact_projections
            WHERE workspace_id = ? AND artifact_id = ? AND version = ?
            """,
            (workspace_id, artifact_id, version),
        ).fetchone()
        if row is None:
            raise RequestNotFound(f"Artifact projection is not found: {artifact_id}@v{version:04d}")
        return self._projection_from_row(row)

    @staticmethod
    def _workspace_revision(connection: sqlite3.Connection, workspace_id: str) -> int:
        row = connection.execute(
            "SELECT revision FROM workspace_records WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise RequestStoreError(f"Workspace is not open: {workspace_id}")
        return int(row["revision"])

    @staticmethod
    def _bump_workspace_revision(
        connection: sqlite3.Connection,
        workspace_id: str,
    ) -> tuple[int, int]:
        """Advance a workspace revision inside the enclosing domain mutation transaction."""

        previous_revision = RequestStore._workspace_revision(connection, workspace_id)
        workspace_revision = previous_revision + 1
        connection.execute(
            "UPDATE workspace_records SET revision = ?, updated_at = ? WHERE workspace_id = ?",
            (workspace_revision, _utc_now(), workspace_id),
        )
        return previous_revision, workspace_revision

    @staticmethod
    def _require_research_task_row(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM research_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"Unknown research task: {task_id}")
        return row

    @classmethod
    def _require_research_task(
        cls, connection: sqlite3.Connection, task_id: str
    ) -> ResearchTaskRecord:
        return cls._research_task_from_row(cls._require_research_task_row(connection, task_id))

    @staticmethod
    def _require_task_revision(
        task: ResearchTaskRecord, expected_task_revision: int | None
    ) -> None:
        if expected_task_revision is not None and expected_task_revision != task.task_revision:
            raise TaskRevisionConflict(task.task_revision)

    @staticmethod
    def _research_task_from_row(row: sqlite3.Row) -> ResearchTaskRecord:
        return ResearchTaskRecord(
            task_id=row["task_id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            status=row["status"],
            task_revision=int(row["task_revision"]),
            active_request_id=row["active_request_id"],
            stable_checkpoint_id=row["stable_checkpoint_id"],
            conversation_generation=int(row["conversation_generation"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _task_workflow_from_row(row: sqlite3.Row) -> TaskWorkflowRecord:
        return TaskWorkflowRecord(
            request_id=row["request_id"],
            task_id=row["task_id"],
            workspace_id=row["workspace_id"],
            state=row["state"],
            activity=row["activity"],
            checkpoint=json.loads(row["checkpoint_json"]),
            heartbeat_at=row["heartbeat_at"],
            failure_fingerprint=row["failure_fingerprint"],
            repeated_failures=int(row["repeated_failures"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _task_transcript_from_row(row: sqlite3.Row) -> TaskTranscriptItem:
        return TaskTranscriptItem(
            item_id=row["item_id"],
            task_id=row["task_id"],
            sequence=int(row["sequence"]),
            role=row["role"],
            text=row["text"],
            request_id=row["request_id"],
            turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
            interrupted=bool(row["interrupted"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _conversation_checkpoint_from_row(row: sqlite3.Row) -> ConversationCheckpoint:
        if int(row["message_schema_version"]) != 1:
            raise TaskCheckpointIncompatible(
                f"Unsupported task checkpoint schema: {row['message_schema_version']}"
            )
        try:
            messages = json.loads(row["messages_json"])
            usage_summary = json.loads(row["usage_summary_json"])
        except json.JSONDecodeError as exc:
            raise TaskCheckpointIncompatible("Task checkpoint JSON is unreadable") from exc
        if not isinstance(messages, list) or any(
            not isinstance(message, dict) for message in messages
        ):
            raise TaskCheckpointIncompatible(
                "Task checkpoint messages are not a list of JSON objects"
            )
        canonical_messages = _canonical_json(messages)
        payload_sha256 = hashlib.sha256(canonical_messages.encode("utf-8")).hexdigest()
        if payload_sha256 != row["payload_sha256"]:
            raise TaskCheckpointIncompatible("Task checkpoint payload hash does not match")
        if not isinstance(usage_summary, dict):
            raise TaskCheckpointIncompatible("Task checkpoint usage summary is not a JSON object")
        return ConversationCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            conversation_generation=int(row["conversation_generation"]),
            terminal_request_id=row["terminal_request_id"],
            message_schema_version=int(row["message_schema_version"]),
            messages=tuple(messages),
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            runtime_profile_fingerprint=row["runtime_profile_fingerprint"],
            system_prompt_fingerprint=row["system_prompt_fingerprint"],
            compaction_generation=int(row["compaction_generation"]),
            usage_summary=usage_summary,
            payload_sha256=row["payload_sha256"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _expert_session_checkpoint_from_row(
        row: sqlite3.Row,
    ) -> ExpertSessionCheckpoint:
        if int(row["message_schema_version"]) != 1:
            raise TaskCheckpointIncompatible("Unsupported Expert session checkpoint schema")
        raw = str(row["messages_json"])
        try:
            messages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TaskCheckpointIncompatible("Expert session checkpoint JSON is invalid") from exc
        canonical = _canonical_json(messages)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["payload_sha256"]:
            raise TaskCheckpointIncompatible("Expert session checkpoint checksum does not match")
        if (
            not isinstance(messages, list)
            or len(messages) != int(row["message_count"])
            or any(not isinstance(message, dict) for message in messages)
        ):
            raise TaskCheckpointIncompatible("Expert session checkpoint message count is invalid")
        compaction_generation = int(row["compaction_generation"])
        if compaction_generation < 0:
            raise TaskCheckpointIncompatible("Expert session compaction generation is invalid")
        return ExpertSessionCheckpoint(
            workspace_id=row["workspace_id"],
            task_scope=row["task_scope"],
            participant_key=row["participant_key"],
            job_key=row["job_key"],
            work_order_id=row["work_order_id"],
            messages=tuple(messages),
            compaction_generation=compaction_generation,
            payload_sha256=row["payload_sha256"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _last_assistant_text(messages: tuple[dict[str, Any], ...]) -> str:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            text = "".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if text:
                if len(text) <= 8_000:
                    return text
                return text[:4_000] + "\n... [middle omitted] ...\n" + text[-4_000:]
        return ""

    @classmethod
    def _checkpoint_for_task_in_connection(
        cls, connection: sqlite3.Connection, task: ResearchTaskRecord
    ) -> ConversationCheckpoint | None:
        if task.stable_checkpoint_id is None:
            return None
        row = connection.execute(
            "SELECT * FROM task_conversation_checkpoints WHERE checkpoint_id = ?",
            (task.stable_checkpoint_id,),
        ).fetchone()
        if row is None:
            raise TaskCheckpointIncompatible("Task references a missing stable checkpoint")
        checkpoint = cls._conversation_checkpoint_from_row(row)
        if checkpoint.task_id != task.task_id:
            raise TaskCheckpointIncompatible("Task stable checkpoint belongs to another task")
        return checkpoint

    @staticmethod
    def _clear_task_active_request_in_connection(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        request_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE research_tasks
            SET active_request_id = NULL, task_revision = task_revision + 1, updated_at = ?
            WHERE task_id = ? AND active_request_id = ?
            """,
            (_utc_now(), task_id, request_id),
        )

    @staticmethod
    def _mark_task_transcript_interrupted_in_connection(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        request_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE task_transcript_items
            SET interrupted = 1
            WHERE task_id = ? AND request_id = ?
            """,
            (task_id, request_id),
        )

    @staticmethod
    def _require_intent_row(connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM artifact_commit_intents WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise ArtifactCommitNotFound(f"Artifact commit intent is not found: {operation_id}")
        return row

    @staticmethod
    def _intent_from_row(row: sqlite3.Row) -> ArtifactCommitIntent:
        return ArtifactCommitIntent(
            operation_id=row["operation_id"],
            request_id=row["request_id"],
            origin_request_id=row["origin_request_id"],
            task_id=row["task_id"],
            task_relation=row["task_relation"],
            workspace_id=row["workspace_id"],
            artifact_id=row["artifact_id"],
            version=int(row["version"]),
            expected_workspace_revision=row["expected_workspace_revision"],
            staging_uri=row["staging_uri"],
            target_uri=row["target_uri"],
            manifest=ArtifactManifest.model_validate_json(row["manifest_json"]),
            status=row["status"],
            quarantine_uri=row["quarantine_uri"],
            committed_event_id=row["committed_event_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _tool_call_record_from_row(row: sqlite3.Row) -> ToolCallRecord:
        return ToolCallRecord(
            operation_id=row["operation_id"],
            request_id=row["request_id"],
            turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            state=row["state"],
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _complete_tool_call_in_transaction(
        connection: sqlite3.Connection,
        operation_id: str,
        result: dict[str, Any],
    ) -> None:
        """Complete a record when this operation was started through a model tool."""

        connection.execute(
            """
            UPDATE tool_call_records
            SET state = 'completed', result_json = ?, completed_at = ?
            WHERE operation_id = ?
            """,
            (_canonical_json(result), _utc_now(), operation_id),
        )

    @staticmethod
    def _close_active_tool_calls_in_transaction(
        connection: sqlite3.Connection,
        *,
        request_id: str,
        terminal_event_type: str,
    ) -> None:
        """Converge audit/UI state when a request reaches any terminal event.

        A process interruption between the domain action and the model-tool
        receipt must not leave a permanent ``in_progress`` operation.  This is
        lifecycle cleanup only; it never replays or invents a domain mutation.
        """

        result = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "The parent request reached a terminal state before this tool "
                        "receipt completed."
                    ),
                }
            ],
            "is_error": True,
            "metadata": {
                "request_terminal": True,
                "terminal_event_type": terminal_event_type,
            },
        }
        connection.execute(
            """
            UPDATE tool_call_records
            SET state = 'completed', result_json = ?, completed_at = ?
            WHERE request_id = ? AND state = 'in_progress'
            """,
            (_canonical_json(result), _utc_now(), request_id),
        )

    def _artifact_from_row(
        self,
        row: sqlite3.Row,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ArtifactVersion:
        active_connection = connection or self._connection
        file_rows = active_connection.execute(
            """
            SELECT uri, mime_type, size_bytes, sha256 FROM artifact_files
            WHERE workspace_id = ? AND artifact_id = ? AND version = ? ORDER BY uri
            """,
            (row["workspace_id"], row["artifact_id"], row["version"]),
        ).fetchall()
        return ArtifactVersion(
            ref=ArtifactRef(artifact_id=row["artifact_id"], version=int(row["version"])),
            workspace_id=row["workspace_id"],
            artifact_type=row["artifact_type"],
            schema_version=row["schema_version"],
            title=row["title"],
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row["created_by"],
            supersedes_version=row["supersedes_version"],
            content=json.loads(row["content_json"]),
            intrinsic_links=tuple(
                ArtifactLinkDraft.model_validate(item)
                for item in json.loads(row["intrinsic_links_json"])
            ),
            provenance=json.loads(row["provenance_json"]),
            manifest_uri=row["manifest_uri"],
            manifest_sha256=row["manifest_sha256"],
            files=tuple(
                ArtifactFile(
                    uri=file_row["uri"],
                    mime_type=file_row["mime_type"],
                    size_bytes=int(file_row["size_bytes"]),
                    sha256=file_row["sha256"],
                )
                for file_row in file_rows
            ),
        )

    @staticmethod
    def _projection_from_row(row: sqlite3.Row) -> ArtifactProjection:
        return ArtifactProjection(
            ref=ArtifactRef(artifact_id=row["artifact_id"], version=int(row["version"])),
            lifecycle_state=row["lifecycle_state"],
            impact_state=row["impact_state"],
            impact_reasons=tuple(
                ImpactHop.model_validate(item) for item in json.loads(row["impact_reasons_json"])
            ),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            source_event_id=row["source_event_id"],
        )

    def _artifact_summary_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        projection = self._projection_from_row(row)
        return {
            "ref": {"artifact_id": row["artifact_id"], "version": int(row["version"])},
            "artifact_type": row["artifact_type"],
            "title": row["title"],
            "summary": row["summary"],
            "projection": projection.model_dump(mode="json"),
        }

    def _persist_event(self, connection: sqlite3.Connection, event: EventEnvelope) -> None:
        connection.execute(
            """
            INSERT INTO event_records (event_id, request_id, workspace_id, event_type, event_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.request_id,
                event.workspace_id,
                event.type,
                event.model_dump_json(),
                _utc_now(),
            ),
        )

    def _commit_terminal_in_transaction(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        event: EventEnvelope,
    ) -> None:
        if event.type not in TERMINAL_EVENT_TYPES:
            raise RequestStoreError(f"Cannot commit non-terminal event {event.type}")
        if event.request_id != request_id:
            raise RequestStoreError("Terminal event request ID does not match the durable request")
        record = self._request_from_row(self._require_request_row(connection, request_id))
        if record.terminal:
            raise RequestStoreError(f"Request is already terminal: {request_id}")
        self._close_active_tool_calls_in_transaction(
            connection,
            request_id=request_id,
            terminal_event_type=event.type,
        )
        self._persist_event(connection, event)
        connection.execute(
            """
            UPDATE request_records
            SET state = ?, terminal_event_json = ?, terminal_event_id = ?, updated_at = ?
            WHERE request_id = ?
            """,
            (
                _state_for_terminal_event(event),
                event.model_dump_json(),
                event.event_id,
                _utc_now(),
                request_id,
            ),
        )

    def _ensure_nonterminal_request(
        self, connection: sqlite3.Connection, request_id: str
    ) -> RequestRecord:
        record = self._request_from_row(self._require_request_row(connection, request_id))
        if record.terminal:
            raise RequestStoreError(f"Request is already terminal: {request_id}")
        return record

    @staticmethod
    def _require_request_row(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM request_records WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise RequestNotFound(f"Unknown request: {request_id}")
        return row

    @staticmethod
    def _require_interaction_row(
        connection: sqlite3.Connection, interaction_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM task_interactions WHERE interaction_id = ?", (interaction_id,)
        ).fetchone()
        if row is None:
            raise RequestNotFound("Interaction is not found")
        return row

    @staticmethod
    def _interaction_from_row(row: sqlite3.Row) -> PendingInteractionRecord:
        return PendingInteractionRecord(
            interaction_id=row["interaction_id"],
            request_id=row["request_id"],
            task_id=row["task_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            principal=row["principal"],
            kind=row["kind"],
            question=row["question"],
            options=tuple(json.loads(row["options_json"])),
            state=row["state"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    @classmethod
    def _pending_interactions_in_connection(
        cls, connection: sqlite3.Connection, task_id: str
    ) -> list[PendingInteractionRecord]:
        rows = connection.execute(
            "SELECT * FROM task_interactions WHERE task_id = ? AND state = 'pending' ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [cls._interaction_from_row(row) for row in rows]

    @staticmethod
    def _interrupt_interactions_for_request_in_connection(
        connection: sqlite3.Connection, request_id: str
    ) -> None:
        connection.execute(
            "UPDATE task_interactions SET state = 'interrupted', resolved_at = ? WHERE request_id = ? AND state = 'pending'",
            (_utc_now(), request_id),
        )

    @staticmethod
    def _require_team_work_row(connection: sqlite3.Connection, work_order_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM team_work_records WHERE work_order_id = ?",
            (work_order_id,),
        ).fetchone()
        if row is None:
            raise RequestNotFound(f"Unknown team work order: {work_order_id}")
        return row

    @classmethod
    def _team_work_from_row(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> TeamWorkRecord:
        work_order_payload = cls._readable_work_order_payload(json.loads(row["work_order_json"]))
        result_payload = (
            cls._readable_expert_result_payload(json.loads(row["result_json"]))
            if row["result_json"]
            else None
        )
        return TeamWorkRecord(
            workspace_id=row["workspace_id"],
            work_order=WorkOrder.model_validate(work_order_payload),
            state=WorkStatus(row["state"]),
            result=(
                ExpertResult.model_validate(result_payload) if result_payload is not None else None
            ),
            checkpoint=cls._workstream_checkpoint_from_row(row),
            resume_count=int(row["resume_count"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _workstream_checkpoint_from_row(row: sqlite3.Row) -> WorkstreamCheckpoint:
        raw = row["checkpoint_json"] if "checkpoint_json" in row.keys() else None
        if not raw:
            return WorkstreamCheckpoint()
        payload = json.loads(raw)
        if not payload:
            return WorkstreamCheckpoint()
        return WorkstreamCheckpoint.model_validate(payload)

    @staticmethod
    def _readable_work_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a current hierarchy record without legacy projection."""

        return dict(payload)

    @staticmethod
    def _readable_expert_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a current hierarchy result without legacy projection."""

        return dict(payload)

    @staticmethod
    def _code_execution_from_row(row: sqlite3.Row) -> CodeExecutionRecord:
        return CodeExecutionRecord(
            execution_id=row["execution_id"],
            workspace_id=row["workspace_id"],
            task_id=row["task_id"],
            work_order_id=row["work_order_id"],
            child_id=row["child_id"],
            state=row["state"],
            request=json.loads(row["request_json"]),
            result=(json.loads(row["result_json"]) if row["result_json"] is not None else None),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    @staticmethod
    def _request_from_row(row: sqlite3.Row) -> RequestRecord:
        terminal_json = row["terminal_event_json"]
        return RequestRecord(
            request_id=row["request_id"],
            request_type=row["request_type"],
            canonical_hash=row["canonical_hash"],
            canonical_request=json.loads(row["canonical_request_json"]),
            principal=row["principal"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            task_id=row["task_id"],
            state=row["state"],
            terminal_event=parse_event(json.loads(terminal_json)) if terminal_json else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = [
    "ACTIVE_REQUEST_STATES",
    "ActiveRefCommit",
    "ArtifactCommit",
    "ArtifactCommitIntent",
    "ArtifactCommitNotFound",
    "ArtifactVersionConflict",
    "CancellationCommit",
    "DisclosurePolicyCommit",
    "PendingInteractionRecord",
    "RequestIdConflict",
    "RequestNotFound",
    "RequestRecord",
    "RequestReservation",
    "RequestStore",
    "RequestStoreError",
    "ToolCallRecord",
    "TERMINAL_REQUEST_STATES",
    "WorkspaceOpenCommit",
    "WorkspaceRevisionConflict",
    "WorkspaceSnapshot",
]
