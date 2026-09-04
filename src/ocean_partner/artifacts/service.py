"""Orchestrate immutable artifact filesystem commits through the durable store."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ocean_partner.artifacts.files import ArtifactFileStore, StagedArtifact
from ocean_partner.artifacts.models import ArtifactProjection, ArtifactVersion, ArtifactVersionDraft, validate_artifact_content
from ocean_partner.backend.store import ArtifactCommit, ArtifactCommitIntent, RequestStore
from ocean_partner.protocol.v2.models import (
    ArtifactCreatedEvent,
    ArtifactCreatedPayload,
    ArtifactSummaryPayload,
    ArtifactVersionCreatedEvent,
    EventEnvelope,
    RequestCompletedEvent,
    RequestCompletedPayload,
    new_event_id,
)


ArtifactEventFactory = Callable[
    [ArtifactVersion, ArtifactProjection, int, int, str],
    tuple[EventEnvelope, EventEnvelope | None],
]


class ArtifactService:
    """Create immutable artifacts without embedding domain-specific analysis recipes."""

    def __init__(self, *, store: RequestStore, files: ArtifactFileStore) -> None:
        self.store = store
        self.files = files

    @staticmethod
    def new_artifact_id(artifact_type: str) -> str:
        """Generate an opaque, type-prefixed stable artifact identifier."""

        return f"{artifact_type}_{uuid4().hex}"

    def commit(
        self,
        draft: ArtifactVersionDraft,
        *,
        request_id: str | None,
        origin_request_id: str | None = None,
        task_id: str | None = None,
        task_relation: str | None = None,
        expected_workspace_revision: int | None,
        files: Mapping[str, bytes | Path] | None,
        event_factory: ArtifactEventFactory,
        operation_id: str | None = None,
    ) -> ArtifactCommit:
        """Stage files, record intent, atomically rename, then commit authoritative metadata."""

        origin = origin_request_id or request_id
        if operation_id is not None:
            existing = self.store.get_artifact_intent(operation_id)
            if existing is not None:
                if existing.origin_request_id != origin:
                    raise RuntimeError("Artifact operation ID belongs to a different originating request")
                if existing.task_id != task_id or existing.task_relation != task_relation:
                    raise RuntimeError("Artifact operation belongs to different Task ownership")
                return self._resume_intent(
                    existing,
                    expected_workspace_revision=expected_workspace_revision,
                    event_factory=event_factory,
                )

        validated = draft.model_copy(
            update={"content": validate_artifact_content(draft.artifact_type, draft.content)}
        )
        version = self.store.next_artifact_version(
            workspace_id=validated.workspace_id,
            artifact_id=validated.artifact_id,
        )
        staged = self.files.stage(
            validated,
            version=version,
            files=files,
            operation_id=operation_id,
        )
        try:
            self._prepare(
                staged,
                request_id=request_id,
                origin_request_id=origin,
                task_id=task_id,
                task_relation=task_relation,
                expected_workspace_revision=expected_workspace_revision,
            )
            self.files.finalize(staged)
            return self.store.commit_artifact_intent(
                operation_id=staged.operation_id,
                expected_workspace_revision=expected_workspace_revision,
                event_factory=event_factory,
            )
        except Exception:
            intent = self.store.get_artifact_intent(staged.operation_id)
            if intent is not None and intent.status == "prepared":
                self._quarantine_intent(intent, reason="commit-failed")
            elif staged.staging_directory.exists():
                self.files.quarantine(staged, reason="prepare-failed")
            raise

    def _resume_intent(
        self,
        intent: ArtifactCommitIntent,
        *,
        expected_workspace_revision: int | None,
        event_factory: ArtifactEventFactory,
    ) -> ArtifactCommit:
        """Finish a model operation whose filesystem intent survived a retry boundary."""

        if intent.status == "quarantined":
            raise RuntimeError("Artifact operation was previously quarantined and cannot be replayed")
        if intent.status == "prepared":
            staging = self.files.paths.resolve_uri(intent.staging_uri)
            target_manifest = self.files.paths.resolve_uri(intent.target_uri)
            target = target_manifest.parent
            try:
                if target.exists():
                    self.files.verify_directory(target, expected_manifest=intent.manifest)
                elif staging.exists():
                    self.files.finalize(
                        StagedArtifact(
                            operation_id=intent.operation_id,
                            manifest=intent.manifest,
                            staging_directory=staging,
                            target_directory=target,
                        )
                    )
                else:
                    raise RuntimeError("Artifact operation has neither staged nor final files")
            except Exception:
                self._quarantine_intent(intent, reason="replay-failed")
                raise
        return self.store.commit_artifact_intent(
            operation_id=intent.operation_id,
            expected_workspace_revision=expected_workspace_revision,
            event_factory=event_factory,
        )

    def recover_pending(self) -> list[str]:
        """Deterministically finish valid intents or quarantine them before request recovery."""

        recovered: list[str] = []
        for intent in self.store.pending_artifact_intents():
            try:
                staging = self.files.paths.resolve_uri(intent.staging_uri)
                target_manifest = self.files.paths.resolve_uri(intent.target_uri)
                target = target_manifest.parent
                if target.exists():
                    self.files.verify_directory(target, expected_manifest=intent.manifest)
                elif staging.exists():
                    staged = StagedArtifact(
                        operation_id=intent.operation_id,
                        manifest=intent.manifest,
                        staging_directory=staging,
                        target_directory=target,
                    )
                    self.files.finalize(staged)
                else:
                    raise RuntimeError("Neither staged nor final artifact files are available")
                self.store.commit_artifact_intent(
                    operation_id=intent.operation_id,
                    expected_workspace_revision=intent.expected_workspace_revision,
                    event_factory=self._recovery_event_factory(intent),
                )
                recovered.append(intent.operation_id)
            except Exception:
                self._quarantine_intent(intent, reason="recovery-failed")
        return recovered

    def _quarantine_intent(self, intent: ArtifactCommitIntent, *, reason: str) -> None:
        sources = (
            self.files.paths.resolve_uri(intent.staging_uri),
            self.files.paths.resolve_uri(intent.target_uri).parent,
        )
        quarantine_uri: str | None = None
        for source in sources:
            if not source.exists():
                continue
            destination = self.files.quarantine_directory(
                source,
                operation_id=intent.operation_id,
                reason=reason,
            )
            quarantine_uri = self.files.paths.uri_for(destination)
            break
        self.store.quarantine_artifact_intent(
            operation_id=intent.operation_id,
            quarantine_uri=quarantine_uri,
        )

    @staticmethod
    def _recovery_event_factory(intent: ArtifactCommitIntent) -> ArtifactEventFactory:
        """Build durable no-session events for a commit that survived a backend crash."""

        def factory(
            artifact: ArtifactVersion,
            projection: ArtifactProjection,
            _previous_revision: int,
            workspace_revision: int,
            event_id: str,
        ) -> tuple[EventEnvelope, EventEnvelope | None]:
            summary = ArtifactSummaryPayload(
                ref=artifact.ref,
                artifact_type=artifact.artifact_type,
                title=artifact.title,
                summary=artifact.summary,
                projection=projection,
            )
            event_type = ArtifactCreatedEvent if artifact.ref.version == 1 else ArtifactVersionCreatedEvent
            domain = event_type(
                protocol_version=2,
                event_id=event_id,
                session_id=None,
                workspace_id=artifact.workspace_id,
                request_id=intent.origin_request_id,
                sequence=0,
                timestamp=datetime.now(timezone.utc),
                type="artifact.created" if artifact.ref.version == 1 else "artifact.version.created",
                payload=ArtifactCreatedPayload(
                    artifact=summary,
                    manifest_uri=artifact.manifest_uri,
                    workspace_revision=workspace_revision,
                ),
            )
            if intent.request_id is None:
                return domain, None
            terminal = RequestCompletedEvent(
                protocol_version=2,
                event_id=new_event_id(),
                session_id=None,
                workspace_id=artifact.workspace_id,
                request_id=intent.request_id,
                sequence=0,
                timestamp=datetime.now(timezone.utc),
                type="request.completed",
                payload=RequestCompletedPayload(
                    result={
                        "artifact": summary.model_dump(mode="json"),
                        "manifest_uri": artifact.manifest_uri,
                        "recovered_after_restart": True,
                    },
                    workspace_revision=workspace_revision,
                ),
            )
            return domain, terminal

        return factory

    def _prepare(
        self,
        staged: StagedArtifact,
        *,
        request_id: str | None,
        origin_request_id: str | None = None,
        task_id: str | None = None,
        task_relation: str | None = None,
        expected_workspace_revision: int | None,
    ) -> None:
        self.store.prepare_artifact_intent(
            operation_id=staged.operation_id,
            request_id=request_id,
            origin_request_id=origin_request_id or request_id,
            task_id=task_id,
            task_relation=task_relation,
            expected_workspace_revision=expected_workspace_revision,
            manifest=staged.manifest,
            staging_uri=self.files.paths.uri_for(staged.staging_directory),
            target_uri=self.files.paths.uri_for(staged.target_directory / "manifest.json"),
        )


__all__ = ["ArtifactEventFactory", "ArtifactService"]
