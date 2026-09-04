---
name: hypothesis-experiment-design
description: Turn Ocean observations or paper-grounded ideas into falsifiable hypotheses and proportionate experiments, comparisons, or sensitivity tests.
metadata:
  origin: adapted
  sources:
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/methodology/experimental-design-guide
  roles:
    - statistical_inference_expert
    - scientific_discussion_partner
---

# Hypothesis and Experiment Design

Use this skill when an observation, literature claim, or proposed mechanism must become a test that
could change the scientific conclusion. Do not load it for a purely descriptive inventory or when
the WorkOrder already fixes a valid analysis design.

## Define a discriminating claim

- State the target phenomenon, direction or pattern, relevant scale, and the evidence that would
  contradict it.
- Separate the proposed mechanism from its observable prediction.
- Name at least one plausible competing explanation and the result that would distinguish it.
- Mark hypotheses formed after seeing the data as exploratory; do not rewrite them as confirmatory.

## Match the design to the evidence

First decide whether the task permits intervention, natural comparison, observational inference, or
only a sensitivity analysis. Randomization, replication, and blocking are useful only when the
sampling or experiment actually permits them. For gridded or time-dependent Ocean data, define the
independent sampling unit and account for spatial or temporal dependence before treating cells or
timestamps as replicates.

Specify only the design elements that affect the claim:

- estimand, response, comparison or counterfactual;
- selection, baseline, aggregation, weighting, mask, and nuisance structure;
- effect size or practically meaningful detection threshold;
- uncertainty method and sensitivity choices;
- stopping condition and outcomes interpreted as support, contradiction, ambiguity, or insufficient
  evidence.

Use factorial or response-surface designs only when several controllable factors and interactions are
genuinely part of the question. For observational studies, prefer matched contrasts, stratification,
negative controls, lagged tests, or natural experiments when they better represent the available
evidence. A formal power calculation is appropriate only when its sampling assumptions are defensible;
otherwise report achievable precision or detectable effect under explicit dependence assumptions.

## Preserve the decision boundary

Keep exploration separate from confirmation. If the method changes after results are seen, record the
change and recompute affected evidence. Do not turn a visual resemblance, isolated p-value, or
post-hoc threshold into mechanism evidence. Stop when the bounded test answers the WorkOrder or when
the available data cannot discriminate the competing explanations at the required scale.

For physical mechanism checks, consult `references/review/physical-consistency.md`. For trend or
anomaly designs, consult `references/methods/trend.md` or `references/methods/anomaly.md` only when
those analyses are part of the WorkOrder.
