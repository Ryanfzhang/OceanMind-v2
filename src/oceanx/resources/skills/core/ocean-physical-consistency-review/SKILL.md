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

For an assigned independent review, use the forwarded original ExpertResult and read-only evidence
locations. Select the consequential claims first, then inspect their scripts and saved arrays on
demand; do not ingest whole conversations/logs or rerun the entire study. Test a small known case
or independent numerical spot-check when code correctness matters. Save checks only in your own
workspace. Report the exact evidence location, observed discrepancy or passed check, its impact on
the claim, and any unexamined scope. Partial results may be reviewed only within their available
coverage; no review of absent data or unfinished calculations is implied. Return findings to the
Coordinator, not instructions to another Expert, and do not treat a plausible mechanism as a verdict.

Use only checks relevant to the claim under review:

- Check the governing equation before attributing a mechanism. For a scalar `C`, horizontal flux
  divergence obeys `div_h(u_h C) = u_h dot grad_h(C) + C div_h(u_h)`; it is not automatically the
  horizontal advective tendency. Identify the continuity assumptions, boundary transports, and
  vertical terms needed for a consistent budget. An isolated flux term can legitimately depend on
  the reference zero of `C`; if that changes the attribution, examine the compensating terms rather
  than choosing the convenient reference or declaring the mechanism excluded.
- Trace a consequential number from the calculation to its plotted series and prose. Units in a
  label are not proof of conversion: never subtract a temperature tendency in K/day from a heat
  flux in W/m2. For a layer-mean tendency over thickness `H` with constant density and heat capacity,
  the equivalent flux is `rho * cp * H * tendency_K_per_day / 86400`; verify the applicable layer,
  sign convention, and assumptions rather than applying this to arbitrary pointwise tendencies.
- Inspect shapes and coordinate alignment before reductions. Verify column interpolation and
  layer weights on a small known case; a plausible regional mean can hide accidental broadcasting
  or integration outside the stated depth range.
- Keep an unclosed budget residual distinct from a measured forcing. Unresolved transport,
  entrainment, sampling, and numerical errors may contribute; missing flux data cannot establish
  surface-forcing dominance. A mixed-layer-depth tendency alone is not a complete entrainment budget.
- Do not discard a mean because daily variability exceeds it or monthly values change sign.
  Mean divided by daily standard deviation is not a significance test; uncertainty of the mean
  requires the sampling dependence and effective sample size to be considered. Seek statistical
  review when needed, or leave significance unresolved.
- An unexpected sign or magnitude is a reason to check definitions and computations, not permission
  to replace the value with a physically preferred story. Assess net flux using its relevant
  components, not shortwave radiation alone; distinguish variability from estimator failure.
- A statistical decomposition is not a mechanism detector. Fluctuations relative to a temporal
  mean need not isolate coherent eddies or any other named process; persistent structures can
  contribute to the mean. A small covariance or domain-mean contribution cannot exclude that
  process without a discriminating diagnostic at the relevant spatial and temporal scales.

## expected_artifacts
ObservationArtifact, DecisionArtifact, or reproducibility and limitation sections in a ReportArtifact.

## quality_gates
Do not label a result physically plausible merely because a color map looks familiar.

## stop_or_escalation_conditions
Escalate sign, unit, conservation, or boundary inconsistencies before publication-oriented figures or conclusions are approved.

## relevant_references
`references/review/physical-consistency.md`, `references/data/common-variables-and-units.md`, `references/methods/transport.md`.
