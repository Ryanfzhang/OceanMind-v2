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

Describe the decisions a material gap affects and what evidence would resolve or narrow it. Select a
professional role and dependencies when delegation helps. Keep user-requested outputs and evidence
standards distinct from optional methods and future questions; explain why any added output is essential.

Before adding children, identify at least one proposed child with a feasible test: name the available
data, the measurement or calculation, and outcomes that distinguish the claims. If no child qualifies,
record the missing data and the conditions under which testing becomes possible, rather than adding
another descriptive layer. A key untestable gap can justify `unable_to_answer` directly. Do not wait
for repeated decompositions to diagnose it or treat unavailable evidence as a refuted hypothesis.

Distinguish competing explanations from contributions that may coexist. When using `alternatives`
for attribution, formulate mutually exclusive judgments (one contribution dominates, another
dominates, or their contributions are comparable), define a scientifically justified operational
criterion, and specify a test that separates their contributions. Detecting a mechanism's presence
does not establish its dominance. If the required budget terms or observations are unavailable,
report attribution as unresolved instead of forcing coupled mechanisms into an OR relationship.

The Coordinator owns the WorkOrder graph. Literature discovery and source review remain with the
Literature Expert; scientific data interpretation remains with the relevant domain Expert. This skill
may suggest concrete methods with a reason, while leaving the Expert free to choose an alternative.
User-specified methods and necessary corrections remain requirements when their reason is explicit.
Do not choose skills for another Agent or make suggested methods an acceptance checklist.

## Revise after evidence, not on a fixed script

After each returned result, consider what it adds or changes, which observations or conflicts are
worth explaining, and whether the original question has been answered. Assess what the evidence
distinguishes and what limitations could change the answer. Decide what further analysis could add
and whether that knowledge is worth its cost. Record consequential new judgments with their evidence
in normal responses, follow-ups, or state-update reasons; omit inapplicable points and reference
unchanged judgments. This does not require five paragraphs, a review form, or another model call.

Dispatch independent gaps together when useful and preserve the same Expert identity for focused
continuations. Do not follow a fixed number of phases, papers, queries, or iterations. A broad request
may require multiple source types, but breadth is evidence-driven rather than a checklist. Experts
can add or replace Tests within their authorized question and nodes. New hypotheses or substantive
goal changes require the Coordinator's approval; out-of-scope work still needs the user's authority.
Empty leads are valid. A lead can be pursued, deferred, or declined without declaring the current
answer incomplete; explain an approved extension's value and incremental budget.

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
one: explain how the observed outcome actually separates those claims. Assess independence through
shared signals, assumptions, and possible errors, not counts of sources, methods, or Experts. A
reviewer approving the calculations does not remove attribution limits stated in that same review.

When independent review can resolve a material question about consequential evidence, define that
bounded gap and use existing
domain/statistical profiles in separate instances with `review=true` and the relevant `depends_on`
todos; the runtime forwards the full original results and read-only evidence locations, using the
Coordinator model configuration. Ask which claims survive concrete checks, not whether the author
sounds convincing. Keep normal intermediate continuation with the original Expert. After review,
request a material correction or focused recheck when warranted; otherwise synthesize the supported
evidence and remaining limits. Review is not an automatic stage. An interrupted reviewer has not
approved the analysis, and a passed review does not update a hypothesis by itself.

After a correction, revisit only the affected evidence chain: derived quantities, figures, claims,
and research-branch judgments. A repaired sign or mask validates that repair, not the full budget or
mechanism. Check whether the corrected diagnostic still discriminates the hypothesis before keeping
a supported or contradicted verdict. If it no longer does, reopen or qualify that judgment rather
than inheriting it from the superseded result; preserve unaffected verified work.

## Stop deliberately

Judge the evidence before adjudicating a hypothesis state. Test records preserve observations and
do not apply scientific verdicts. Keep hypothesis state separate from question completion: supported
or contested nodes and untested optional leads can remain when the required question is answered.
Sufficient negative evidence can also answer a question. No established hypothesis, all-root terminal
state, or mandatory reflection is needed to finish; the existing multi-target established guard is
not a reason to invent another target.

Stop when the evidence resolves the question at its sufficient_level. max_level is a ceiling, not
an invitation to keep increasing claim strength. Continuing after sufficiency needs an explicit
scientific benefit, an approved lead within scope, and an incremental budget. If a consequential gap
cannot be resolved by reasonable further work, give the supported partial answer and missing evidence
with an insufficient-evidence decision (`unable_to_answer` in the tree). Budget, cancellation, or
service interruptions use the existing pause path without changing a scientific state to refuted or
unverifiable. Preserve running-work status and saved outputs. Delivery repair follows the existing
file and publication path; it does not automatically reopen scientific analysis.
