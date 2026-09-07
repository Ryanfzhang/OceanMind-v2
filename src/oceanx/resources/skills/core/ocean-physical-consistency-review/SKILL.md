---
name: ocean-physical-consistency-review
description: Review Ocean results for units, dimensions, signs, conservation, grid metrics, and physically plausible structure.
metadata:
  origin: oceanmind
  roles:
    - ocean_process_expert
    - scientific_discussion_partner
---

# Ocean Physical Consistency Review

## when_to_use
Use after a calculation, figure, or inferred mechanism is available for review.

## research_objective
Assess whether a result is physically and numerically coherent without replacing the analysis with a hidden alternative algorithm.

## questions_to_resolve
- Are units, dimensions, magnitudes, signs, and coordinate directions consistent?
- Could grid-cell area, layer thickness, boundaries, masks, or land contamination explain the signal?
- Does the structure agree with known seasonality, stratification, circulation, or conservation constraints at the stated scale?

## evidence_requirements
Return checks with exact artifact refs and state whether each concern is a warning, failed check, or unresolved interpretation. The Coordinator uses these findings in its decision.

## process_checkpoints
Review code assumptions and data conventions separately from the physical conclusion.

## expected_artifacts
ObservationArtifact, DecisionArtifact, or reproducibility and limitation sections in a ReportArtifact.

## quality_gates
Do not label a result physically plausible merely because a color map looks familiar.

## stop_or_escalation_conditions
Escalate sign, unit, conservation, or boundary inconsistencies before publication-oriented figures or conclusions are approved.

## relevant_references
`references/review/physical-consistency.md`, `references/data/common-variables-and-units.md`, `references/methods/transport.md`.
