---
name: paper-grounded-idea-framing
description: Turn attributed paper findings and observed data gaps into distinct, feasible, falsifiable Ocean research ideas without overstating novelty.
metadata:
  origin: adapted
  sources:
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/methodology/scientify-idea-generation
  roles:
    - coordinator
    - scientific_discussion_partner
---

# Paper-grounded Idea Framing

Use this skill when the task asks for a new research idea, hypothesis direction, or next study grounded
in already reviewed paper evidence. Do not treat search snippets, unattributed model knowledge, or a
generic topic summary as sufficient grounding.

## Build from an explicit gap

Start from frozen Literature Expert results and any accepted data evidence. For each candidate idea,
identify:

- the paper-attributed findings or limitations it builds on;
- the unresolved contradiction, missing scale, untested assumption, method limitation, or data
  opportunity that creates the gap;
- what is OceanMind synthesis rather than an author's statement;
- the concrete observation that would support or contradict the idea;
- whether the attached data and available tools make a first test feasible.

Generate only enough meaningfully different candidates to expose real alternatives. Diversity can
come from combining mechanisms, relaxing an assumption, transferring a method across scales or
regions, testing a contradiction, or replacing an indirect proxy with a more discriminating measure.
Do not force a fixed number or fixed set of strategies.

## Compare ideas honestly

Compare candidates using task-relevant criteria such as scientific importance, evidentiary novelty,
falsifiability, data fit, execution cost, confounding risk, and expected information gain. A gap in the
reviewed set is not proof that no prior work exists; describe novelty as a positioning hypothesis until
targeted literature review verifies it.

For the selected direction, return a compact chain:

```text
reviewed evidence -> unresolved gap -> proposed explanation -> discriminating prediction
                  -> feasible test with current data -> possible outcomes and limits
```

If existing evidence cannot support that chain, request the specific missing literature or data
evidence rather than filling it with speculation. Detailed estimands and test design belong in a
subsequent Statistical Expert WorkOrder, which may independently load hypothesis-experiment-design.
