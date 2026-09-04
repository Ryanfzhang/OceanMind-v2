"""Workspace-level service boundary for snapshots and version-pinned active context."""

from __future__ import annotations

from ocean_partner.artifacts.models import ArtifactRef
from ocean_partner.backend.store import RequestStore, WorkspaceSnapshot


class WorkspaceService:
    """Keep workspace context changes explicit and revision-checked outside raw SQL callers."""

    def __init__(self, *, store: RequestStore) -> None:
        self.store = store

    def snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        return self.store.workspace_snapshot(workspace_id)

    def set_active_ref(
        self,
        *,
        workspace_id: str,
        slot: str,
        ref: ArtifactRef,
        expected_workspace_revision: int,
    ) -> WorkspaceSnapshot:
        return self.store.set_active_ref(
            workspace_id=workspace_id,
            slot=slot,
            ref=ref,
            expected_workspace_revision=expected_workspace_revision,
        )


__all__ = ["WorkspaceService"]
