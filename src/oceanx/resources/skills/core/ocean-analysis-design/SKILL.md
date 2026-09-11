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
- Which dependence, uncertainty, or alternative choices could change the requested answer?

## evidence_requirements
Use the supplied question, source refs, answer_standard, and required_outputs. Keep suggested_path
and hints replaceable. Select assumptions and checks relevant to the conclusion; the possible methods
above are not default deliverables. sufficient_level describes the answer needed, while max_level
limits its strength. Neither is a requirement to obtain a positive finding.

## process_checkpoints
Choose, add, or replace methods and Tests within the authorized question, nodes, data, and budget
without waiting for Coordinator approval. Explain material path changes and recompute affected
results. New objectives or mechanism hypotheses need Coordinator approval; return those as leads
with their observed basis when useful. Empty leads and path_deviations are legitimate.

For a formal discrimination, describe beforehand which observations would distinguish the relevant
claims in the normal plan or code record. A separate preregistration call is not required. Record
chance findings as exploratory and retain their actual timing. Test summaries can accompany the
report, and small reads or code repairs need not each become a Test. Experts record their own Test
facts only; the Coordinator judges the evidence and updates hypothesis states.

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
Return the requested outputs through the existing result path, together with the evidence needed to
interpret them. Do not create additional reports or artifacts just because this skill lists them.
Explain any partial or blocked required output while preserving completed work.

## quality_gates
Explain whether a pattern describes an observation, supports an association, or actually distinguishes
the authorized claims. Keep consequential weighting, baseline, and selection choices inspectable.
Check feasible limitations that could change the answer, or retain them as unresolved with their
implications. A check being completed does not automatically exclude the concern.

## stop_or_escalation_conditions
If reasonable methods disagree, investigate or report the material difference within the assignment.
Ask the Coordinator to resolve a missing scientific objective or authorization, not to choose every
method. Finish when the bounded question is supported at its required level, including a supported
negative answer; do not continue solely to approach max_level. Unresolvable gaps and reasoned partial
results let the Coordinator choose a narrower answer, continuation, or insufficient evidence.

## relevant_references
`references/methods/anomaly.md`, `references/methods/trend.md`, `references/methods/transport.md`, `references/coding/large-array-practices.md`.
