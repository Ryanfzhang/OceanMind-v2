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

Before scaling up a consequential transformation, test its non-obvious assumptions on a small,
hand-checkable example. Select checks for the operations actually used, not an exhaustive audit:

- For column-wise interpolation or indexing, verify that each output uses only its own spatial
  column. With `T(z, y, x)` and `k(y, x)`, `T[k]` introduces extra spatial axes; a pointwise gather
  such as `np.take_along_axis(T, k[None, ...], axis=0)[0]` preserves `(y, x)`. Check index bounds,
  interpolation brackets, missing values, and a few columns with different known values.
- For vertical or spatial integrals, verify weights against the actual wet integration interval or
  area, including partial boundary cells. Integrating a constant should recover that constant times
  the intended thickness or area; averaging it should recover the constant.
- Put compared budget terms in common units and use the same selections, masks, and conventions.
  Derive figures and reported summaries from those checked series, not separately converted copies.
- In weighted means, restrict both numerator and denominator to the same finite values and valid
  weights; an all-missing selection is missing, not zero. Derivatives and vertical integration can
  remove cells that were valid in the source, so derive validity from the resulting diagnostic.
  Compare terms on common support or quantify the coverage difference. For area fractions, state
  whether the denominator is wet ocean, valid observations, or the full geographic box; do not
  silently include land or unsampled cells as negative observations.

Preserve the relevant check outputs with the calculation. If a check fails, repair the affected
computation and its dependent figures or claims; formatting an old result does not repair it.

## expected_artifacts
ExperimentArtifact, InteractiveView, and a reproducible ReportArtifact when the task produces durable results.

## quality_gates
Do not treat a plotted pattern as a test. Do not hide weighting, baseline, or selection decisions in prose.

## stop_or_escalation_conditions
Pause for user input when competing scientifically reasonable methods would yield different conclusions.

## relevant_references
`references/methods/anomaly.md`, `references/methods/trend.md`, `references/methods/transport.md`, `references/coding/large-array-practices.md`.
