"""Dependency impact state and reason-chain tests independent of SQLite plumbing."""

from __future__ import annotations

from oceanx.artifacts.impact import ImpactEdge, ImpactNode, rebuild_impact, would_create_propagating_cycle
from oceanx.artifacts.models import ArtifactRef


def _ref(name: str, version: int = 1) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, version=version)


def test_superseded_invalidated_and_source_unavailable_propagate_with_precedence():
    dataset = _ref("dataset_fixture")
    figure = _ref("figure_fixture")
    report = _ref("report_fixture")
    results = rebuild_impact(
        [
            ImpactNode(dataset, lifecycle_state="superseded"),
            ImpactNode(figure, lifecycle_state="available", decision_invalidated=True),
            ImpactNode(report, lifecycle_state="available"),
        ],
        [
            ImpactEdge(source=figure, target=dataset, relation="uses_dataset"),
            ImpactEdge(source=report, target=figure, relation="derived_from"),
        ],
    )

    assert results[dataset].state == "stale"
    assert results[figure].state == "invalidated"
    assert results[report].state == "invalidated"
    assert [hop.relation for hop in results[report].reasons] == ["self", "derived_from"]


def test_reference_only_source_unavailable_and_cycles_are_deterministic():
    dataset = _ref("dataset_fixture")
    view = _ref("interactive_view_fixture")
    edge = ImpactEdge(source=view, target=dataset, relation="uses_dataset")
    results = rebuild_impact(
        [
            ImpactNode(
                dataset,
                lifecycle_state="available",
                source_unavailable=True,
            ),
            ImpactNode(view, lifecycle_state="available"),
        ],
        [edge],
    )

    assert results[view].state == "source_unavailable"
    assert would_create_propagating_cycle(
        source=dataset,
        target=view,
        relation="derived_from",
        existing=[edge],
    )
    assert not would_create_propagating_cycle(
        source=dataset,
        target=view,
        relation="supersedes",
        existing=[edge],
    )
