"""Budgeted, policy-audited research context assembly for future Ocean model turns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from oceanx.artifacts.models import ArtifactRef
from oceanx.backend.store import RequestStore


DisclosureDecision = Literal["allow", "prompt", "deny"]
DisclosureContentType = Literal[
    "metadata",
    "aggregate_statistics",
    "raw_bounded_sample",
    "document_text",
    "diagnostic_excerpt",
]


class ContextPolicyModel(BaseModel):
    """Immutable policy shape persisted as JSON in the project-local state store."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelDataDisclosurePolicy(ContextPolicyModel):
    """Provider-bound model context policy with every supported category enabled."""

    provider_id: str = Field(min_length=1, max_length=256)
    policy_version: int = Field(ge=1)
    metadata: DisclosureDecision = "allow"
    aggregate_statistics: DisclosureDecision = "allow"
    raw_bounded_sample: DisclosureDecision = "allow"
    document_text: DisclosureDecision = "allow"
    diagnostic_excerpt: DisclosureDecision = "allow"

    def decision_for(self, content_type: DisclosureContentType) -> DisclosureDecision:
        return getattr(self, content_type)

    def as_storage_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"provider_id", "policy_version"})

    @classmethod
    def from_storage(cls, payload: dict[str, Any]) -> "ModelDataDisclosurePolicy":
        return cls(
            provider_id=payload["provider_id"],
            policy_version=payload["policy_version"],
            **payload["policy"],
        )


class ContextEntry(ContextPolicyModel):
    ref: ArtifactRef
    artifact_type: str
    title: str
    summary: str
    projection: dict[str, Any]
    active_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class OceanContextSnapshot:
    """The bounded model-visible state plus non-sensitive audit identifiers."""

    payload: dict[str, Any]
    estimated_tokens: int
    audit_ids: tuple[str, ...]
    excluded_refs: tuple[ArtifactRef, ...]


class ContextPolicyError(RuntimeError):
    """A requested provider or context category violates the workspace policy."""


class OceanContextBuilder:
    """Select the smallest useful workspace summary without leaking raw local evidence."""

    def __init__(self, *, store: RequestStore) -> None:
        self.store = store

    def set_policy(self, *, workspace_id: str, policy: ModelDataDisclosurePolicy) -> None:
        self.store.set_disclosure_policy(
            workspace_id=workspace_id,
            provider_id=policy.provider_id,
            policy_version=policy.policy_version,
            policy=policy.as_storage_payload(),
        )

    def policy_for(self, *, workspace_id: str, provider_id: str) -> ModelDataDisclosurePolicy:
        stored = self.store.get_disclosure_policy(workspace_id)
        if stored is None:
            policy = ModelDataDisclosurePolicy(provider_id=provider_id, policy_version=1)
            self.set_policy(workspace_id=workspace_id, policy=policy)
            return policy
        policy = ModelDataDisclosurePolicy.from_storage(stored)
        if policy.provider_id != provider_id:
            policy = ModelDataDisclosurePolicy(
                provider_id=provider_id,
                policy_version=policy.policy_version + 1,
            )
            self.set_policy(workspace_id=workspace_id, policy=policy)
        return policy

    def build(
        self,
        *,
        workspace_id: str,
        provider_id: str,
        task_id: str | None = None,
        token_budget: int = 3_000,
        routing_only: bool = False,
    ) -> OceanContextSnapshot:
        """Build a bounded summary for one authorization scope.

        A research task is the model-visible security boundary.  Workspace-wide
        context remains available only to callers that explicitly omit ``task_id``
        (for example, workspace administration).  Agent turns always pass a task
        identifier and therefore cannot discover artifacts linked only to another
        task in the same workspace.
        """

        if token_budget < 64:
            raise ValueError("Context token budget must be at least 64")
        policy = self.policy_for(workspace_id=workspace_id, provider_id=provider_id)
        snapshot = self.store.workspace_snapshot(workspace_id)
        task_records = None
        if task_id is not None:
            task = self.store.get_research_task(task_id, workspace_id=workspace_id)
            if task is None:
                raise ContextPolicyError("Task is unavailable in the requested workspace")
            task_records = self.store.list_task_artifacts(task_id=task_id)
            workspace_summary = {
                "workspace_id": snapshot.workspace_id,
                "revision": snapshot.revision,
                "access_scope": "task",
                "task_id": task_id,
                "artifact_count": len(task_records),
            }
        else:
            workspace_summary = {
                "workspace_id": snapshot.workspace_id,
                "revision": snapshot.revision,
                "access_scope": "workspace",
                "artifact_count": len(snapshot.artifacts),
                "active_refs": {
                    slot: ref.model_dump(mode="json")
                    for slot, ref in sorted(snapshot.active_refs.items())
                },
            }
        base = {
            "workspace": workspace_summary,
            "disclosure_policy": {
                "provider_id": policy.provider_id,
                "policy_version": policy.policy_version,
                "metadata": policy.metadata,
                "raw_bounded_sample": policy.raw_bounded_sample,
                "document_text": policy.document_text,
                "diagnostic_excerpt": policy.diagnostic_excerpt,
            },
            "active_artifacts": [],
            "recent_artifacts": [],
        }
        if _estimate_tokens(base) > token_budget:
            minimal_scope = {
                "workspace_id": snapshot.workspace_id,
                "revision": snapshot.revision,
            }
            if task_id is not None:
                minimal_scope.update(
                    {
                        "access_scope": "task",
                        "task_id": task_id,
                    }
                )
            base = {
                "workspace": minimal_scope,
                "disclosure_policy": {
                    "provider_id": policy.provider_id,
                    "policy_version": policy.policy_version,
                },
                "active_artifacts": [],
                "recent_artifacts": [],
            }
        if _estimate_tokens(base) > token_budget:
            raise ContextPolicyError("Context budget cannot fit the minimal workspace header")
        if policy.decision_for("metadata") != "allow":
            audit_id = self._audit(
                workspace_id=workspace_id,
                policy=policy,
                content_type="metadata",
                disposition=policy.decision_for("metadata"),
                byte_count=0,
                item_count=0,
            )
            return OceanContextSnapshot(
                payload=base,
                estimated_tokens=_estimate_tokens(base),
                audit_ids=(audit_id,),
                excluded_refs=(
                    tuple(record.artifact.ref for record in task_records)
                    if task_records is not None
                    else tuple(
                        ArtifactRef.model_validate(item["ref"])
                        for item in snapshot.artifacts
                    )
                ),
            )

        slots_by_ref: dict[ArtifactRef, list[str]] = {}
        if task_records is not None:
            summaries_by_ref = {}
            for record in task_records:
                ref = record.artifact.ref
                slots_by_ref[ref] = [f"task:{relation}" for relation in record.relations]
                projection = self.store.get_projection(workspace_id=workspace_id, ref=ref)
                summaries_by_ref[ref] = {
                    "ref": ref.model_dump(mode="json"),
                    "artifact_type": record.artifact.artifact_type,
                    "title": record.artifact.title,
                    "summary": record.artifact.summary,
                    "projection": (
                        projection.model_dump(mode="json") if projection is not None else {}
                    ),
                }
            ordered_refs = [record.artifact.ref for record in task_records]
        else:
            for slot, ref in snapshot.active_refs.items():
                slots_by_ref.setdefault(ref, []).append(slot)
            summaries_by_ref = {
                ArtifactRef.model_validate(item["ref"]): item
                for item in snapshot.artifacts
            }
            ordered_refs = list(slots_by_ref)
            ordered_refs.extend(ref for ref in summaries_by_ref if ref not in slots_by_ref)
        excluded: list[ArtifactRef] = []
        for ref in ordered_refs:
            summary = summaries_by_ref.get(ref)
            if summary is None:
                excluded.append(ref)
                continue
            if routing_only:
                entry = {
                    "ref": ref.model_dump(mode="json"),
                    "artifact_type": str(summary["artifact_type"]),
                    "title": str(summary["title"]),
                    "active_slots": tuple(sorted(slots_by_ref.get(ref, []))),
                }
            else:
                entry = ContextEntry(
                    ref=ref,
                    artifact_type=str(summary["artifact_type"]),
                    title=str(summary["title"]),
                    summary=str(summary["summary"]),
                    projection=dict(summary["projection"]),
                    active_slots=tuple(sorted(slots_by_ref.get(ref, []))),
                ).model_dump(mode="json")
            target_key = "active_artifacts" if ref in slots_by_ref else "recent_artifacts"
            candidate = {**base, target_key: [*base[target_key], entry]}
            if _estimate_tokens(candidate) > token_budget:
                excluded.append(ref)
                continue
            base = candidate
        payload_bytes = len(_canonical_json(base).encode("utf-8"))
        audit_id = self._audit(
            workspace_id=workspace_id,
            policy=policy,
            content_type="metadata",
            disposition="allow",
            byte_count=payload_bytes,
            item_count=len(base["active_artifacts"]) + len(base["recent_artifacts"]),
        )
        return OceanContextSnapshot(
            payload=base,
            estimated_tokens=_estimate_tokens(base),
            audit_ids=(audit_id,),
            excluded_refs=tuple(excluded),
        )

    def _audit(
        self,
        *,
        workspace_id: str,
        policy: ModelDataDisclosurePolicy,
        content_type: DisclosureContentType,
        disposition: DisclosureDecision,
        byte_count: int,
        item_count: int,
    ) -> str:
        audit_id = f"disclosure_{uuid4().hex}"
        self.store.record_disclosure_audit(
            audit_id=audit_id,
            workspace_id=workspace_id,
            provider_id=policy.provider_id,
            policy_version=policy.policy_version,
            content_type=content_type,
            disposition=disposition,
            byte_count=byte_count,
            item_count=item_count,
        )
        return audit_id


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _estimate_tokens(value: Any) -> int:
    return max(1, (len(_canonical_json(value)) + 3) // 4)


__all__ = [
    "ContextEntry",
    "ContextPolicyError",
    "DisclosureContentType",
    "DisclosureDecision",
    "ModelDataDisclosurePolicy",
    "OceanContextBuilder",
    "OceanContextSnapshot",
]
