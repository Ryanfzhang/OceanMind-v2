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

Before adding children, identify at least one proposed child with a feasible test: name the available
data, the measurement or calculation, and outcomes that distinguish the claims. If no child qualifies,
record the missing data and the conditions under which testing becomes possible, rather than adding
another descriptive layer. In protocol v2, declare the infeasible test and continue other feasible work;
if none remains, pause without `completion_summary`. The existing two-decomposition `unverifiable`
fallback remains a safety net, not a target to reach by creating empty branches.

Distinguish competing explanations from contributions that may coexist. When using `alternatives`
for attribution, formulate mutually exclusive judgments (one contribution dominates, another
dominates, or their contributions are comparable), define a scientifically justified operational
criterion, and specify a test that separates their contributions. Detecting a mechanism's presence
does not establish its dominance. If the required budget terms or observations are unavailable,
report attribution as unresolved instead of forcing coupled mechanisms into an OR relationship.

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

When a numerical result conflicts with an interpretation, make the next WorkOrder about resolving
that specific conflict, not defending the intended conclusion. Cite the conflicting outputs and
ask which calculation, definition, or assumption explains the difference, with correction of any
affected result. A focused check can reuse existing code and saved evidence; it need not restart
the analysis. If the available evidence cannot discriminate competing mechanisms, record that gap
instead of requesting a more confident summary. An Expert's self-assessment is not independent
confirmation of its scientific claim.

A numerical or code audit can validate an estimate without distinguishing causal hypotheses. Save
pure audit findings as observations; do not manufacture a multi-target test merely to upgrade a
hypothesis to `established`. Multiple targets satisfy a structural requirement, not a scientific
one: explain how the observed outcome actually separates those claims. A reviewer approving the
calculations does not remove attribution limits stated in that same review.

Before finalizing consequential mechanism or inferential claims, consolidate their independent
review into one bounded evidence gap, rather than reviewing each partial return. Use existing
domain/statistical profiles in separate instances with `review=true` and the relevant `depends_on`
todos; the runtime forwards the full original results and read-only evidence locations, using the
Coordinator model configuration. Ask which claims survive concrete checks, not whether the author
sounds convincing. Keep normal intermediate continuation with the original Expert. After review,
request only a material correction and, if needed, a focused recheck; otherwise synthesize the
supported evidence and remaining limits. An interrupted reviewer has not approved the analysis.

After a correction, revisit only the affected evidence chain: derived quantities, figures, claims,
and research-branch judgments. A repaired sign or mask validates that repair, not the full budget or
mechanism. Check whether the corrected diagnostic still discriminates the hypothesis before keeping
a supported or contradicted verdict. If it no longer does, reopen or qualify that judgment rather
than inheriting it from the superseded result; preserve unaffected verified work.

## Stop deliberately

When the Coordinator uses research-tree protocol v2, use its three exits rather than
closing based only on diminishing returns. Hypotheses and Tests have separate IDs.
Declare each test's possible outcome/effects and associate it with all relevant targets;
dispatch its research_test_ids through ocean_assign. supported and contested are not
terminal. Record returned evidence before acknowledging terminal/periodic reflection.
All root hypotheses must be terminal and pending reflection acknowledged for an answered
or unable_to_answer report. Budget/user interruptions pause without completion_summary.
Parent OR/AND summaries preserve every unverifiable reason. An unexpected result or an
infeasible test does not refute its hypothesis. Never invent a second target merely to
meet the structural established guard.

Stop and synthesize when the user's evidence threshold is met, the next round is unlikely to change
the main conclusion, repeated work adds no material evidence, a blocking limitation cannot be resolved
with available sources or tools, or the remaining uncertainty should be reported rather than hidden.
Preserve conflicting evidence and unclosed gaps in the final boundary instead of manufacturing a
complete-looking trajectory.
