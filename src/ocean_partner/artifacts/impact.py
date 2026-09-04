"""Deterministic, cycle-safe artifact dependency-impact projection logic."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Literal

from ocean_partner.artifacts.models import ArtifactRef, ImpactHop, ImpactState, LinkRelation


PROPAGATING_RELATIONS = frozenset(
    {
        "derived_from",
        "uses_dataset",
        "supports_claim",
        "contradicts_claim",
        "tests_hypothesis",
        "motivates",
        "included_in_report",
    }
)
_PRIORITY: dict[ImpactState, int] = {
    "current": 0,
    "stale": 1,
    "source_unavailable": 2,
    "invalidated": 3,
}
Trigger = Literal[
    "superseded",
    "tombstoned",
    "source_unavailable",
    "decision_invalidated",
]


@dataclass(frozen=True)
class ImpactNode:
    ref: ArtifactRef
    lifecycle_state: str
    source_unavailable: bool = False
    decision_invalidated: bool = False
    source_event_id: str | None = None


@dataclass(frozen=True)
class ImpactEdge:
    source: ArtifactRef
    target: ArtifactRef
    relation: LinkRelation


@dataclass(frozen=True)
class ImpactResult:
    state: ImpactState
    reasons: tuple[ImpactHop, ...]


def rebuild_impact(
    nodes: Iterable[ImpactNode],
    edges: Iterable[ImpactEdge],
) -> dict[ArtifactRef, ImpactResult]:
    """Compute impact states from immutable refs and current projection/state records."""

    node_by_ref = {node.ref: node for node in nodes}
    incoming: dict[ArtifactRef, list[ImpactEdge]] = {ref: [] for ref in node_by_ref}
    for edge in edges:
        if edge.source not in node_by_ref or edge.target not in node_by_ref:
            continue
        if edge.relation in PROPAGATING_RELATIONS:
            incoming.setdefault(edge.target, []).append(edge)
    for edge_list in incoming.values():
        edge_list.sort(key=lambda edge: (edge.source.key, edge.relation))

    results: dict[ArtifactRef, ImpactResult] = {
        ref: ImpactResult(state="current", reasons=()) for ref in node_by_ref
    }
    queue: deque[tuple[ArtifactRef, ImpactState, tuple[ImpactHop, ...]]] = deque()
    for node in sorted(node_by_ref.values(), key=lambda item: item.ref.key):
        direct = _direct_impact(node)
        if direct is None:
            continue
        state, trigger = direct
        hop = ImpactHop(
            source=node.ref,
            relation="self",
            trigger=trigger,
            source_event_id=node.source_event_id,
        )
        _offer(results, node.ref, state, (hop,), queue)

    while queue:
        target, state, chain = queue.popleft()
        for edge in incoming.get(target, []):
            hop = ImpactHop(
                source=target,
                relation=edge.relation,
                trigger=chain[0].trigger,
                source_event_id=chain[0].source_event_id,
            )
            _offer(results, edge.source, state, chain + (hop,), queue)
    return results


def would_create_propagating_cycle(
    *,
    source: ArtifactRef,
    target: ArtifactRef,
    relation: LinkRelation,
    existing: Iterable[ImpactEdge],
) -> bool:
    """Return true when adding source -> target would create an intrinsic dependency cycle."""

    if relation not in PROPAGATING_RELATIONS:
        return False
    if source == target:
        return True
    adjacency: dict[ArtifactRef, list[ArtifactRef]] = {}
    for edge in existing:
        if edge.relation in PROPAGATING_RELATIONS:
            adjacency.setdefault(edge.source, []).append(edge.target)
    pending = [target]
    visited: set[ArtifactRef] = set()
    while pending:
        current = pending.pop()
        if current == source:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, []))
    return False


def _direct_impact(node: ImpactNode) -> tuple[ImpactState, Trigger] | None:
    if node.lifecycle_state == "tombstoned":
        return "invalidated", "tombstoned"
    if node.decision_invalidated:
        return "invalidated", "decision_invalidated"
    if node.source_unavailable:
        return "source_unavailable", "source_unavailable"
    if node.lifecycle_state == "superseded":
        return "stale", "superseded"
    return None


def _offer(
    results: dict[ArtifactRef, ImpactResult],
    ref: ArtifactRef,
    state: ImpactState,
    reasons: tuple[ImpactHop, ...],
    queue: deque[tuple[ArtifactRef, ImpactState, tuple[ImpactHop, ...]]],
) -> None:
    previous = results[ref]
    if _PRIORITY[state] < _PRIORITY[previous.state]:
        return
    if _PRIORITY[state] == _PRIORITY[previous.state] and previous.reasons and len(previous.reasons) <= len(reasons):
        return
    results[ref] = ImpactResult(state=state, reasons=reasons)
    queue.append((ref, state, reasons))


__all__ = [
    "ImpactEdge",
    "ImpactNode",
    "ImpactResult",
    "PROPAGATING_RELATIONS",
    "rebuild_impact",
    "would_create_propagating_cycle",
]
