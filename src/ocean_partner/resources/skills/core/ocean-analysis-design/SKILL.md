---
name: ocean-analysis-design
description: Design a transparent Ocean analysis with explicit selections, weighting, uncertainty, and pre-run validation assertions.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
    - ocean_process_expert
    - statistical_inference_expert
---

# Ocean Analysis Design

## when_to_use
Use once a task genuinely requires computation rather than literature reading, framing, or writing.

## research_objective
Translate a scientific question into inspectable selections, transformations, comparisons, and falsifiable checks that an agent may later implement in editable code.

## questions_to_resolve
- What spatial, temporal, vertical, and variable selection is scientifically intended?
- Which baseline, aggregation, area or volume weighting, mask, and seasonality treatment are defensible?
- How will autocorrelation, uncertainty, sensitivity, and alternative choices be evaluated?

## evidence_requirements
Declare exact input refs, requested outputs, method assumptions, and definition of done in the WorkOrder before computation.

## process_checkpoints
An Expert may repair its own implementation in the same workstream. A scientific-method change must be explained to the Coordinator and the affected result recomputed.

## expected_artifacts
ExperimentArtifact, InteractiveView, and a reproducible ReportArtifact when the task produces durable results.

## quality_gates
Do not treat a plotted pattern as a test. Do not hide weighting, baseline, or selection decisions in prose.

## stop_or_escalation_conditions
Pause for user input when competing scientifically reasonable methods would yield different conclusions.

## relevant_references
`references/methods/anomaly.md`, `references/methods/trend.md`, `references/methods/transport.md`, `references/coding/large-array-practices.md`.
