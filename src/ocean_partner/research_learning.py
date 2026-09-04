"""Typed research memory and evidence-gated self-evolution domain models.

Research state is a projection of durable WorkOrders, WorkResults, ResultBundles,
and observations.  It is deliberately not a second orchestration mode or a
second source of truth.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ocean_partner.team.models import ExpertResult, FindingBasis, WorkOrder, WorkStatus


class ObservationKind(str, Enum):
    DATA = "data"
    LITERATURE = "literature"
    METHOD = "method"
    QUALITY = "quality"
    SEARCH = "search"
    EXECUTION = "execution"
    RESULT = "result"
    EVALUATION = "evaluation"
    LIMITATION = "limitation"
    EVIDENCE_GAP = "evidence_gap"


class ObservationRelation(str, Enum):
    """EvoScientist-style links between durable memory observations."""

    COMPLEMENTS = "complements"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class SavedExperienceStatus(str, Enum):
    """Lifecycle for an Agent-authored experience note."""

    PENDING = "pending"
    ABSORBED = "absorbed"
    DISMISSED = "dismissed"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResearchObservationDraft(FrozenModel):
    workspace_id: str
    task_id: str
    request_id: str
    work_order_id: str | None = None
    kind: ObservationKind
    statement: Annotated[str, Field(min_length=1, max_length=8_000)]
    evidence_refs: tuple[str, ...] = ()
    outcome: Literal["supported", "limited", "failed", "retrieved"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reusable: bool = False
    lesson_key: str | None = None


class ResearchObservation(ResearchObservationDraft):
    observation_id: str
    created_at: str


class ResearchObservationLink(FrozenModel):
    source_observation_id: str
    target_observation_id: str
    relation: ObservationRelation
    rationale: str
    created_at: str


class ResearchState(FrozenModel):
    """Task-local AutoResearch state projected from existing durable records."""

    task_id: str
    round_count: int = Field(ge=0)
    completed_rounds: int = Field(ge=0)
    active_rounds: int = Field(ge=0)
    evidence_gaps: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()
    status: Literal["idle", "investigating", "evidence_ready", "limited"]


class SavedExperience(FrozenModel):
    """One concise, optional note explicitly saved by an OceanMind Agent."""

    experience_id: str
    workspace_id: str
    task_id: str
    request_id: str
    work_order_id: str | None = None
    agent_id: str
    agent_role: str
    text: Annotated[str, Field(min_length=1, max_length=2_000)]
    status: SavedExperienceStatus
    absorbed_by_skill: str | None = None
    absorbed_by_version: int | None = Field(default=None, ge=1)
    reviewed_at: str | None = None
    created_at: str
    updated_at: str


class SkillEvaluation(FrozenModel):
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    sample_size: int = Field(ge=1)
    safety_regressions: int = Field(default=0, ge=0)
    evaluation_refs: tuple[str, ...] = Field(min_length=1)


class SkillReview(FrozenModel):
    """Independent stronger-model decision over one repeated experience."""

    decision: Literal["reject", "create", "update", "pending"]
    target_skill: str | None = None
    revised_rule: Annotated[str, Field(max_length=8_000)] = ""
    reason: Annotated[str, Field(min_length=1, max_length=4_000)]
    confidence: float = Field(ge=0.0, le=1.0)
    reviewer_model: Annotated[str, Field(min_length=1, max_length=256)]


class ExperienceCandidate(FrozenModel):
    candidate_id: str
    workspace_id: str
    candidate_key: str
    kind: ObservationKind
    title: str
    proposed_rule: str
    evidence_observation_ids: tuple[str, ...]
    distinct_task_count: int = Field(ge=1)
    status: CandidateStatus
    evaluation: SkillEvaluation | None = None
    review: SkillReview | None = None
    created_at: str
    updated_at: str


class SkillRevision(FrozenModel):
    workspace_id: str
    skill_name: str
    version: int = Field(ge=1)
    rule: str
    candidate_id: str
    status: Literal["active", "retired"]
    created_at: str


class EvolvedSkillRevision(FrozenModel):
    """A complete, role-scoped SKILL.md produced from saved experience."""

    workspace_id: str
    skill_name: str
    version: int = Field(ge=1)
    description: Annotated[str, Field(min_length=1, max_length=1_024)]
    roles: tuple[str, ...] = Field(min_length=1)
    content: Annotated[str, Field(min_length=1, max_length=32_000)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_experience_ids: tuple[str, ...] = Field(min_length=1)
    status: Literal["active", "retired"]
    reviewer_model: Annotated[str, Field(min_length=1, max_length=256)]
    review_reason: Annotated[str, Field(min_length=1, max_length=4_000)]
    created_at: str


_BASIS_KIND = {
    FindingBasis.OBSERVATION: ObservationKind.DATA,
    FindingBasis.METHOD: ObservationKind.METHOD,
    FindingBasis.LITERATURE: ObservationKind.LITERATURE,
    FindingBasis.ARTIFACT: ObservationKind.RESULT,
    FindingBasis.INFERENCE: ObservationKind.EVALUATION,
}


def lesson_key(kind: ObservationKind, statement: str) -> str:
    normalized = re.sub(r"\s+", " ", statement.strip().casefold())
    digest = hashlib.sha256(f"{kind.value}\0{normalized}".encode()).hexdigest()
    return f"{kind.value}:{digest[:32]}"


def observations_from_expert_result(
    *,
    workspace_id: str,
    task_id: str,
    order: WorkOrder,
    result: ExpertResult,
) -> tuple[ResearchObservationDraft, ...]:
    """Derive learning memory from the typed Expert handoff, never its private transcript."""

    common = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "request_id": order.parent_request_id,
        "work_order_id": order.work_order_id,
    }
    supported = result.status is WorkStatus.COMPLETED
    # Output-producing work becomes reusable learning only after Coordinator
    # publication. Candidate conclusions remain useful task memory, but cannot
    # silently evolve a workspace skill before review.
    evolution_eligible = supported and (
        not result.outputs or bool(result.result_refs)
    )
    evidence = tuple(
        dict.fromkeys(
            (
                *(item.ref for item in result.evidence_refs),
                *(ref.key for ref in result.result_refs),
            )
        )
    )
    drafts: list[ResearchObservationDraft] = []
    output_evidence = {
        output.item_id: output.result_ref.key
        for output in result.outputs
        if output.result_ref is not None
    }
    for conclusion in result.conclusions:
        conclusion_evidence = tuple(
            dict.fromkeys(
                (
                    *(item.ref for item in conclusion.evidence_refs),
                    *(
                        output_evidence[output_id]
                        for output_id in conclusion.output_ids
                        if output_id in output_evidence
                    ),
                )
            )
        )
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=_BASIS_KIND[conclusion.basis],
                statement=conclusion.statement,
                evidence_refs=conclusion_evidence,
                outcome="supported" if supported else "limited",
                confidence=conclusion.confidence,
                # Scientific conclusions remain task evidence. Only evaluated
                # methods and quality practices may become reusable rules.
                reusable=False,
            )
        )
    for finding in result.findings:
        kind = _BASIS_KIND[finding.basis]
        reusable = (
            evolution_eligible
            and kind is ObservationKind.METHOD
            and finding.confidence >= 0.7
        )
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=kind,
                statement=finding.claim,
                evidence_refs=tuple(item.ref for item in finding.evidence_refs),
                outcome="supported" if supported else "limited",
                confidence=finding.confidence,
                reusable=reusable,
                lesson_key=lesson_key(kind, finding.claim) if reusable else None,
            )
        )
    if result.method_summary.strip():
        reusable = evolution_eligible and result.confidence >= 0.7
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=ObservationKind.METHOD,
                statement=result.method_summary,
                evidence_refs=evidence,
                outcome="supported" if supported else "limited",
                confidence=result.confidence,
                reusable=reusable,
                lesson_key=(
                    lesson_key(ObservationKind.METHOD, result.method_summary)
                    if reusable
                    else None
                ),
            )
        )
    for check in result.checks_performed:
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=ObservationKind.QUALITY,
                statement=check,
                evidence_refs=evidence,
                outcome="supported" if supported else "limited",
                confidence=result.confidence,
                reusable=evolution_eligible,
                lesson_key=(
                    lesson_key(ObservationKind.QUALITY, check)
                    if evolution_eligible
                    else None
                ),
            )
        )
    for limitation in result.limitations:
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=ObservationKind.LIMITATION,
                statement=limitation,
                evidence_refs=evidence,
                outcome="limited",
                confidence=result.confidence,
            )
        )
    for question in result.unresolved_questions:
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=ObservationKind.EVIDENCE_GAP,
                statement=question,
                evidence_refs=evidence,
                outcome="limited",
                confidence=result.confidence,
            )
        )
    if result.failure_code is not None:
        statement = result.error or result.failure_code.value
        key = f"execution:{order.semantic_role}:{result.failure_code.value}"
        drafts.append(
            ResearchObservationDraft(
                **common,
                kind=ObservationKind.EXECUTION,
                statement=statement,
                evidence_refs=evidence,
                outcome="failed",
                reusable=True,
                lesson_key=key,
            )
        )
    return tuple(drafts)


def proposed_rule(kind: ObservationKind, statement: str) -> str:
    if kind is ObservationKind.QUALITY:
        return f"Under matching data and method preconditions, require this check: {statement}"
    if kind is ObservationKind.EXECUTION:
        return (
            "Under the same failure condition, preserve durable results and resume from the "
            f"last checkpoint instead of recomputing: {statement}"
        )
    return (
        "Under matching scientific scope and preconditions, reuse this method; otherwise keep "
        f"it task-local: {statement}"
    )


__all__ = [
    "CandidateStatus",
    "EvolvedSkillRevision",
    "ExperienceCandidate",
    "ObservationKind",
    "ObservationRelation",
    "ResearchObservation",
    "ResearchObservationDraft",
    "ResearchObservationLink",
    "ResearchState",
    "SavedExperience",
    "SavedExperienceStatus",
    "SkillEvaluation",
    "SkillReview",
    "SkillRevision",
    "lesson_key",
    "observations_from_expert_result",
    "proposed_rule",
]
