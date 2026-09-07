"""Typed immutable artifact and projection models for the Ocean workspace store."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ArtifactType = Literal[
    "project_context",
    "paper",
    "claim",
    "observation",
    "hypothesis",
    "dataset",
    "dataset_diagnosis",
    "interactive_view",
    "experiment",
    "decision",
    "report",
]
LinkRelation = Literal[
    "derived_from",
    "uses_dataset",
    "supports_claim",
    "contradicts_claim",
    "tests_hypothesis",
    "supersedes",
    "included_in_report",
    "motivates",
]
LifecycleState = Literal["draft", "available", "superseded", "archived", "tombstoned"]
ImpactState = Literal["current", "stale", "invalidated", "source_unavailable"]


class ArtifactModel(BaseModel):
    """Strict value object used by the artifact and projection store."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactRef(ArtifactModel):
    artifact_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    version: int = Field(ge=1)

    @property
    def key(self) -> str:
        return f"{self.artifact_id}@v{self.version:04d}"


class ArtifactLinkDraft(ArtifactModel):
    relation: LinkRelation
    target: ArtifactRef
    intrinsic: bool = True


class ArtifactFile(ArtifactModel):
    uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    mime_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactVersionDraft(ArtifactModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    artifact_type: ArtifactType
    schema_version: str = Field(default="ocean-artifact/v1", min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    summary: str = Field(default="", max_length=8_000)
    created_by: Literal["user", "agent", "tool", "import"]
    content: dict[str, Any] = Field(default_factory=dict)
    intrinsic_links: tuple[ArtifactLinkDraft, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    supersedes_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_intrinsic_links(self) -> "ArtifactVersionDraft":
        if self.supersedes_version is not None and self.supersedes_version < 1:
            raise ValueError("supersedes_version must be a positive version")
        if any(
            link.target.artifact_id == self.artifact_id and link.target.version == 0
            for link in self.intrinsic_links
        ):
            raise ValueError("Artifact links may not use version 0")
        payload_size = len(canonical_json(self.content).encode("utf-8"))
        if payload_size > 1_000_000:
            raise ValueError("Artifact content exceeds 1 MiB; store large data as an artifact file")
        return self


class ArtifactManifest(ArtifactModel):
    schema_version: Literal["ocean-artifact-manifest/v1"] = "ocean-artifact-manifest/v1"
    workspace_id: str
    artifact_id: str
    version: int = Field(ge=1)
    artifact_type: ArtifactType
    artifact_schema_version: str
    title: str
    summary: str
    created_at: datetime
    created_by: Literal["user", "agent", "tool", "import"]
    supersedes_version: int | None = None
    content: dict[str, Any]
    intrinsic_links: tuple[ArtifactLinkDraft, ...]
    provenance: dict[str, Any]
    files: tuple[ArtifactFile, ...]
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def from_draft(
        cls,
        draft: ArtifactVersionDraft,
        *,
        version: int,
        files: tuple[ArtifactFile, ...],
        created_at: datetime | None = None,
    ) -> "ArtifactManifest":
        timestamp = created_at or datetime.now(timezone.utc)
        unsigned = {
            "schema_version": "ocean-artifact-manifest/v1",
            "workspace_id": draft.workspace_id,
            "artifact_id": draft.artifact_id,
            "version": version,
            "artifact_type": draft.artifact_type,
            "artifact_schema_version": draft.schema_version,
            "title": draft.title,
            "summary": draft.summary,
            "created_at": timestamp.isoformat(),
            "created_by": draft.created_by,
            "supersedes_version": draft.supersedes_version,
            "content": draft.content,
            "intrinsic_links": [link.model_dump(mode="json") for link in draft.intrinsic_links],
            "provenance": draft.provenance,
            "files": [item.model_dump(mode="json") for item in files],
        }
        digest = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
        return cls(
            **{
                **unsigned,
                "created_at": timestamp,
                "intrinsic_links": draft.intrinsic_links,
                "files": files,
                "manifest_sha256": digest,
            }
        )


class ArtifactVersion(ArtifactModel):
    ref: ArtifactRef
    workspace_id: str
    artifact_type: ArtifactType
    schema_version: str
    title: str
    summary: str
    created_at: datetime
    created_by: Literal["user", "agent", "tool", "import"]
    supersedes_version: int | None
    content: dict[str, Any]
    intrinsic_links: tuple[ArtifactLinkDraft, ...]
    provenance: dict[str, Any]
    manifest_uri: str
    manifest_sha256: str
    files: tuple[ArtifactFile, ...]


class ImpactHop(ArtifactModel):
    source: ArtifactRef
    relation: LinkRelation | Literal["self"]
    trigger: Literal[
        "superseded",
        "tombstoned",
        "source_unavailable",
        "decision_invalidated",
    ]
    source_event_id: str | None = None


class ArtifactProjection(ArtifactModel):
    ref: ArtifactRef
    lifecycle_state: LifecycleState = "draft"
    impact_state: ImpactState = "current"
    impact_reasons: tuple[ImpactHop, ...] = ()
    updated_at: datetime
    source_event_id: str | None = None


class PaperCitation(ArtifactModel):
    """Bibliographic metadata only; document text is never embedded in this contract."""

    title: str = Field(min_length=1, max_length=512)
    authors: tuple[str, ...] = ()
    publication_year: int | None = Field(default=None, ge=1600, le=3000)
    venue: str | None = Field(default=None, max_length=512)
    doi: str | None = Field(default=None, min_length=1, max_length=512)
    source_url: str | None = Field(default=None, pattern=r"^https://", max_length=2_048)

    @model_validator(mode="after")
    def _unique_authors(self) -> "PaperCitation":
        normalized = [author.strip().casefold() for author in self.authors]
        if any(not author for author in normalized):
            raise ValueError("Paper citation authors must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Paper citation authors must be unique")
        return self


class PaperContent(ArtifactModel):
    """An immutable local PDF snapshot or user-entered bibliographic record."""

    schema_version: Literal["ocean-paper/v1"] = "ocean-paper/v1"
    citation: PaperCitation
    source_kind: Literal["local_pdf", "metadata_only"]
    materialization_level: Literal["materialized_snapshot", "metadata_only"]

    @model_validator(mode="after")
    def _source_matches_materialization(self) -> "PaperContent":
        expected = "materialized_snapshot" if self.source_kind == "local_pdf" else "metadata_only"
        if self.materialization_level != expected:
            raise ValueError("Paper source_kind and materialization_level must agree")
        return self


class ClaimEvidenceLocator(ArtifactModel):
    """A citation location, deliberately without copied paper text or hidden excerpts."""

    paper_ref: ArtifactRef
    locator_kind: Literal["page", "section", "figure", "table", "supplement", "other"]
    locator: str = Field(min_length=1, max_length=1_000)
    relationship: Literal["supports", "contradicts", "context"] = "supports"


class ClaimContent(ArtifactModel):
    """Keep reported source claims distinct from synthesis and model/user inference."""

    schema_version: Literal["ocean-claim/v1"] = "ocean-claim/v1"
    statement: str = Field(min_length=1, max_length=8_000)
    claim_kind: Literal["reported", "synthesis", "inference"]
    evidence: tuple[ClaimEvidenceLocator, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_evidence_and_limitations(self) -> "ClaimContent":
        evidence_keys = [
            (item.paper_ref, item.locator_kind, item.locator.strip(), item.relationship)
            for item in self.evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("Claim evidence locators must be unique")
        normalized_limitations = [item.strip() for item in self.limitations]
        if any(not item for item in normalized_limitations):
            raise ValueError("Claim limitations must not be blank")
        if len(normalized_limitations) != len(set(normalized_limitations)):
            raise ValueError("Claim limitations must be unique")
        return self


class ObservationContent(ArtifactModel):
    """A traceable observation, not an implicit causal interpretation."""

    schema_version: Literal["ocean-observation/v1"] = "ocean-observation/v1"
    statement: str = Field(min_length=1, max_length=8_000)
    observation_kind: Literal[
        "data_result", "figure_reading", "literature_reading", "user_report"
    ] = "user_report"
    source_refs: tuple[ArtifactRef, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_observation_sources(self) -> "ObservationContent":
        if self.observation_kind != "user_report" and not self.source_refs:
            raise ValueError("Data, figure, and literature observations require source_refs")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("Observation source_refs must be unique")
        return self


class HypothesisPrediction(ArtifactModel):
    prediction: str = Field(min_length=1, max_length=4_000)
    test_context: str = Field(min_length=1, max_length=2_000)


class HypothesisContent(ArtifactModel):
    """A falsifiable proposal; activation is a separate user-owned workspace action."""

    schema_version: Literal["ocean-hypothesis/v1"] = "ocean-hypothesis/v1"
    statement: str = Field(min_length=1, max_length=8_000)
    mechanism: str = Field(min_length=1, max_length=8_000)
    predictions: tuple[HypothesisPrediction, ...] = Field(min_length=1)
    falsification_criteria: tuple[str, ...] = Field(min_length=1)
    competing_explanations: tuple[str, ...] = ()
    evidence_refs: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def _falsifiable_and_unique(self) -> "HypothesisContent":
        prediction_keys = [
            (item.prediction.strip(), item.test_context.strip()) for item in self.predictions
        ]
        if len(prediction_keys) != len(set(prediction_keys)):
            raise ValueError("Hypothesis predictions must be unique")
        for field_name, values in (
            ("Hypothesis falsification criteria", self.falsification_criteria),
            ("Hypothesis competing explanations", self.competing_explanations),
        ):
            normalized = [item.strip() for item in values]
            if any(not item for item in normalized):
                raise ValueError(f"{field_name} must not be blank")
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{field_name} must be unique")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Hypothesis evidence_refs must be unique")
        return self


class ExperimentMetric(ArtifactModel):
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    description: str = Field(min_length=1, max_length=2_000)
    units: str | None = Field(default=None, max_length=128)
    comparison: Literal["increase", "decrease", "difference", "threshold", "descriptive"]


class ExperimentContent(ArtifactModel):
    """A pinned experimental proposal or outcome, never a hidden analysis recipe."""

    schema_version: Literal["ocean-experiment/v1"] = "ocean-experiment/v1"
    hypothesis_ref: ArtifactRef
    scientific_question: str = Field(min_length=1, max_length=4_000)
    input_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    baseline: str = Field(min_length=1, max_length=4_000)
    metrics: tuple[ExperimentMetric, ...] = Field(min_length=1)
    parameter_space: dict[str, Any] = Field(default_factory=dict)
    success_criteria: tuple[str, ...] = Field(min_length=1)
    failure_criteria: tuple[str, ...] = Field(min_length=1)
    stopping_rule: str = Field(min_length=1, max_length=4_000)
    result_refs: tuple[ArtifactRef, ...] = ()
    outcome: Literal["proposed", "ready", "completed", "inconclusive", "failed"] = "proposed"
    outcome_summary: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def _validate_experiment_contract(self) -> "ExperimentContent":
        for field_name, refs in (
            ("Experiment input_refs", self.input_refs),
            ("Experiment result_refs", self.result_refs),
        ):
            if len(set(refs)) != len(refs):
                raise ValueError(f"{field_name} must be unique")
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("Experiment metric IDs must be unique")
        for field_name, values in (
            ("Experiment success criteria", self.success_criteria),
            ("Experiment failure criteria", self.failure_criteria),
        ):
            normalized = [value.strip() for value in values]
            if any(not value for value in normalized):
                raise ValueError(f"{field_name} must not be blank")
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{field_name} must be unique")
        if self.outcome in {"completed", "inconclusive", "failed"} and not self.result_refs:
            raise ValueError("Completed, inconclusive, and failed experiments require result_refs")
        if self.outcome in {"proposed", "ready"} and self.result_refs:
            raise ValueError("Proposed and ready experiments must not declare result_refs")
        if self.outcome in {"completed", "inconclusive", "failed"} and not self.outcome_summary:
            raise ValueError("Experiment outcomes require an outcome_summary")
        return self


class DecisionContent(ArtifactModel):
    """A user-owned reason to retain bounded non-current evidence in a report."""

    schema_version: Literal["ocean-decision/v1"] = "ocean-decision/v1"
    decision_kind: Literal["retain_stale_evidence", "source_unavailable_limitation"]
    subject_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=8_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_subjects_and_limitations(self) -> "DecisionContent":
        if len(set(self.subject_refs)) != len(self.subject_refs):
            raise ValueError("Decision subject_refs must be unique")
        normalized = [limitation.strip() for limitation in self.limitations]
        if any(not limitation for limitation in normalized):
            raise ValueError("Decision limitations must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Decision limitations must be unique")
        return self


class ReportEvidenceDiagnostic(ArtifactModel):
    ref: ArtifactRef
    artifact_type: ArtifactType
    conclusion_eligible: bool
    limitation: str | None = Field(default=None, max_length=4_000)
    grounding: Literal["not_applicable", "grounded", "incomplete"] = "not_applicable"


class InteractiveViewContent(ArtifactModel):
    """One self-contained user-facing interactive result.

    The payload describes what the workbench can open; renderer implementation
    objects are deliberately not separate workspace artifacts.
    """

    schema_version: Literal["ocean-interactive-view/v1"] = "ocean-interactive-view/v1"
    view_kind: Literal[
        "spatial_map",
        "time_series",
        "profile",
        "section",
        "hovmoller",
        "scatter",
        "ts_diagram",
    ]
    data_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    preview_uri: str | None = Field(
        default=None, pattern=r"^ocean://[A-Za-z0-9_./-]+$"
    )
    dataset_refs: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=32)
    interaction: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_view_contract(self) -> "InteractiveViewContent":
        if len(set(self.dataset_refs)) != len(self.dataset_refs):
            raise ValueError("Interactive-view dataset_refs must be unique")
        if self.view_kind == "spatial_map":
            required = {
                "variable",
                "longitude_coordinate",
                "latitude_coordinate",
                "crs",
                "bounds",
                "width",
                "height",
                "units",
                "value_range",
                "colorbar",
            }
            missing = sorted(required.difference(self.metadata))
            if missing:
                raise ValueError(
                    "Spatial interactive view is missing metadata: " + ", ".join(missing)
                )
            bounds = self.metadata["bounds"]
            if not isinstance(bounds, list) or len(bounds) != 4:
                raise ValueError("Spatial interactive-view bounds must be [west,south,east,north]")
            west, south, east, north = bounds
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bounds):
                raise ValueError("Spatial interactive-view bounds must be finite")
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("Spatial interactive-view bounds are outside EPSG:4326")
            if self.preview_uri is None:
                raise ValueError("Spatial interactive views require a preview_uri")
        return self


class ReportContent(ArtifactModel):
    """A reproducible result package with narrative, notebook, code, and exact sources."""

    schema_version: Literal["ocean-report/v1"] = "ocean-report/v1"
    artifact_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    diagnostics: tuple[ReportEvidenceDiagnostic, ...] = Field(min_length=1)
    conclusion_export_allowed: bool
    fully_reproducible: bool
    notebook_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    code_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    environment_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    input_manifest_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    reproducibility_manifest_uri: str = Field(pattern=r"^ocean://[A-Za-z0-9_./-]+$")
    method_sections: tuple[
        Literal["data_sources", "calculation", "parameters", "checks", "limitations"],
        ...,
    ] = (
        "data_sources",
        "calculation",
        "parameters",
        "checks",
        "limitations",
    )

    @model_validator(mode="after")
    def _unique_refs(self) -> "ReportContent":
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("Report artifact_refs must pin each artifact version at most once")
        diagnostic_refs = tuple(item.ref for item in self.diagnostics)
        if diagnostic_refs != self.artifact_refs:
            raise ValueError("Report diagnostics must match artifact_refs in order")
        if self.conclusion_export_allowed and not all(
            item.conclusion_eligible for item in self.diagnostics
        ):
            raise ValueError("Conclusion-ready reports cannot contain ineligible evidence")
        if self.fully_reproducible and any(
            item.limitation and "unavailable" in item.limitation for item in self.diagnostics
        ):
            raise ValueError("Reports with unavailable sources cannot claim full reproducibility")
        return self


def validate_artifact_content(
    artifact_type: ArtifactType, content: dict[str, Any]
) -> dict[str, Any]:
    """Apply the Phase 2 type-specific contract without forcing analysis kernels."""

    validators: dict[str, type[ArtifactModel]] = {
        "paper": PaperContent,
        "claim": ClaimContent,
        "observation": ObservationContent,
        "hypothesis": HypothesisContent,
        "experiment": ExperimentContent,
        "decision": DecisionContent,
        "interactive_view": InteractiveViewContent,
        "report": ReportContent,
    }
    validator = validators.get(artifact_type)
    if validator is None:
        return content
    return validator.model_validate(content).model_dump(mode="json")


def canonical_json(value: Any) -> str:
    """Return stable compact JSON for checksums and content-size limits."""

    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ArtifactFile",
    "ArtifactLinkDraft",
    "ArtifactManifest",
    "ArtifactProjection",
    "ArtifactRef",
    "ArtifactType",
    "ArtifactVersion",
    "ArtifactVersionDraft",
    "ClaimContent",
    "ClaimEvidenceLocator",
    "DecisionContent",
    "ExperimentContent",
    "ExperimentMetric",
    "ImpactHop",
    "ImpactState",
    "HypothesisContent",
    "HypothesisPrediction",
    "LifecycleState",
    "LinkRelation",
    "ReportContent",
    "ReportEvidenceDiagnostic",
    "ObservationContent",
    "PaperCitation",
    "PaperContent",
    "canonical_json",
    "validate_artifact_content",
]
