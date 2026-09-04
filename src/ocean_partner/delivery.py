"""Project task outputs into the two OceanMind result capabilities."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ocean_partner.artifacts.models import ArtifactRef


DeliveryKind = Literal["interactive_view", "report"]


class DeliveryEntry(BaseModel):
    """One openable result entry in the Coordinator's answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(pattern=r"^delivery_[a-f0-9]{24}$")
    kind: DeliveryKind
    title: str = Field(min_length=1, max_length=512)
    open_ref: ArtifactRef
    artifact_type: Literal["interactive_view", "report"]
    placement: Literal["inline", "end"]
    capabilities: tuple[str, ...] = Field(default=(), max_length=16)
    openable: bool = True


class DeliveryManifest(BaseModel):
    """Request-scoped delivery truth independent of Markdown layout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=256)
    entries: tuple[DeliveryEntry, ...] = Field(default=(), max_length=65)


def _origins(output: dict[str, Any]) -> tuple[str, ...]:
    values = output.get("origin_request_ids")
    origins = [value for value in values if isinstance(value, str)] if isinstance(values, list) else []
    origin = output.get("origin_request_id")
    if isinstance(origin, str) and origin not in origins:
        origins.append(origin)
    return tuple(origins)


def _artifact(output: dict[str, Any]) -> dict[str, Any]:
    value = output.get("artifact")
    return value if isinstance(value, dict) else {}


def _entry(output: dict[str, Any], *, request_id: str) -> DeliveryEntry | None:
    artifact = _artifact(output)
    artifact_type = artifact.get("artifact_type")
    if artifact_type not in {"interactive_view", "report"}:
        return None
    try:
        ref = ArtifactRef.model_validate(artifact.get("ref"))
    except (TypeError, ValueError):
        return None
    kind: DeliveryKind = artifact_type
    title = artifact.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Interactive view" if kind == "interactive_view" else "Report"
    digest = hashlib.sha256(
        f"{request_id}\0{kind}\0{ref.key}".encode("utf-8")
    ).hexdigest()[:24]
    return DeliveryEntry(
        entry_id=f"delivery_{digest}",
        kind=kind,
        title=title,
        open_ref=ref,
        artifact_type=artifact_type,
        placement="inline" if kind == "interactive_view" else "end",
        capabilities=(
            ("pan_zoom", "hover_value", "coordinate_readout")
            if kind == "interactive_view"
            else ("evidence", "sources", "notebook")
        ),
    )


def build_delivery_manifest(
    outputs: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    request_id: str,
) -> DeliveryManifest:
    """List every accepted view in order, followed by the latest report."""

    accepted = [
        output
        for output in outputs
        if output.get("relation") in {"primary", "output"}
        and request_id in _origins(output)
    ]
    accepted.sort(key=lambda item: str(item.get("linked_at") or ""))
    entries = [
        entry
        for output in accepted
        if (entry := _entry(output, request_id=request_id)) is not None
    ]
    views = [entry for entry in entries if entry.kind == "interactive_view"]
    reports = [entry for entry in entries if entry.kind == "report"]
    return DeliveryManifest(
        request_id=request_id,
        entries=tuple([*views, *(reports[-1:] if reports else [])]),
    )


def build_delivery_manifests(
    outputs: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[DeliveryManifest, ...]:
    """Project every request with at least one accepted result."""

    recency: dict[str, str] = {}
    for output in outputs:
        if output.get("relation") not in {"primary", "output"}:
            continue
        for request_id in _origins(output):
            recency[request_id] = max(recency.get(request_id, ""), str(output.get("linked_at") or ""))
    manifests = (
        build_delivery_manifest(outputs, request_id=request_id)
        for request_id in sorted(recency, key=lambda item: (recency[item], item))
    )
    return tuple(manifest for manifest in manifests if manifest.entries)


__all__ = [
    "DeliveryEntry",
    "DeliveryKind",
    "DeliveryManifest",
    "build_delivery_manifest",
    "build_delivery_manifests",
]
