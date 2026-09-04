---
name: paper-evidence-review
description: Read and critically evaluate selected scientific papers at the depth required by the WorkOrder, preserving claim-to-paper attribution, methods, limits, and contradictions.
metadata:
  origin: adapted
  sources:
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/paper-review/paper-reading-assistant
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/paper-review/paper-critique-framework
  roles:
    - literature_reproduction_expert
---

# Paper Evidence Review

Use this skill after relevant papers have been identified and the WorkOrder requires evidence beyond a
candidate abstract summary. Use paper-navigator for discovery and acquisition; use this skill to decide
how deeply to read and how to evaluate the resulting evidence.

## Choose reading depth from the question

Do not apply a fixed three-pass ritual to every paper. Progress only as far as the question requires:

1. Orientation: establish paper identity, contribution type, stated question, scope, and relevance.
2. Evidence reading: inspect the methods, figures, results, and limitations that bear on the assigned
   claim.
3. Verification: check critical assumptions, calculations, statistical claims, cited dependencies, or
   reproducibility details only when they could change the conclusion.

If only metadata, an abstract, or a public excerpt is available, label that evidence scope and do not
write as though the full paper was reviewed.

## Extract evidence, not a generic summary

For each material conclusion, retain the paper identity beside it and distinguish:

- what the authors explicitly report;
- the actual data, region, period, variables, design, comparison, and uncertainty supporting it;
- assumptions and limitations acknowledged or exposed by the review;
- OceanMind's task-specific interpretation or cross-paper synthesis;
- the exact diagnostic, threshold, mechanism, or comparison it can support, challenge, or help
  reproduce in the current project.

Evaluate a paper against its own stated goal and the current WorkOrder, not against an unrelated ideal
study. Avoid venue prestige or citation count as substitutes for evidence quality. When criticizing a
method, explain the consequence for the claim and what evidence would resolve it.

## Synthesize across papers

Organize the result by claims or questions rather than one isolated summary per paper. When papers
disagree, test whether the difference follows from definitions, sampling, resolution, region, period,
method, or a genuine contradiction. Preserve unresolved conflict instead of averaging it away.

Return a source-grounded answer with compact paper-level detail sufficient for the Coordinator to use
without re-reading the papers. Stop when the WorkOrder's evidence gap is answered or when unavailable
full text or missing methodological detail prevents a responsible conclusion.
