---
name: research-trajectory-planning
description: Plan and revise a long-horizon Ocean research trajectory around explicit evidence gaps, independent workstreams, contradictions, and stopping conditions.
metadata:
  origin: adapted
  sources:
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/deep-research/in-depth-research-guide
    - https://github.com/wentorai/research-plugins/tree/main/skills/research/deep-research/open-researcher-guide
  roles:
    - coordinator
    - scientific_discussion_partner
---

# Research Trajectory Planning

Use this skill for genuinely multi-stage research that must connect literature, data, competing
explanations, and iterative verification. Do not load it for one bounded question that can be answered
directly or by one Expert round.

## Frame the trajectory around evidence gaps

Define the central question, the answer-level evidence threshold, the current known evidence, and the
material gaps that prevent a responsible conclusion. Decompose only gaps that can yield independently
reviewable evidence. A search query, plot, file conversion, or formatting action is not itself a
research question.

For each gap, state:

- what decision or claim it affects;
- what evidence would close or materially narrow it;
- which professional role owns that evidence;
- dependencies on earlier results;
- what would make further work unproductive or impossible.

The Coordinator owns the WorkOrder graph. Literature discovery and source review remain with the
Literature Expert; scientific data interpretation remains with the relevant domain Expert. This skill
must not prescribe another Agent's internal method or choose skills for it.

## Revise after evidence, not on a fixed script

After each returned result, update the evidence picture:

- accepted findings and their source or result references;
- contradictions and whether they arise from definitions, region, period, scale, method, or genuine
  scientific disagreement;
- unresolved gaps that could still change the answer;
- new questions exposed by actual evidence rather than generic comprehensiveness.

Dispatch independent gaps together when useful and preserve the same Expert identity for focused
continuations. Do not follow a fixed number of phases, papers, queries, or iterations. A broad request
may require multiple source types, but breadth is evidence-driven rather than a checklist.

## Stop deliberately

Stop and synthesize when the user's evidence threshold is met, the next round is unlikely to change
the main conclusion, repeated work adds no material evidence, a blocking limitation cannot be resolved
with available sources or tools, or the remaining uncertainty should be reported rather than hidden.
Preserve conflicting evidence and unclosed gaps in the final boundary instead of manufacturing a
complete-looking trajectory.
