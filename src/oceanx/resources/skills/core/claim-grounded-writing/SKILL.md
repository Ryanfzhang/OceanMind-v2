---
name: claim-grounded-writing
description: Write Ocean research claims with explicit evidence links and clear separation of observation, inference, hypothesis, and conclusion.
metadata:
  origin: oceanmind
  roles:
    - coordinator
    - data_reproducibility_expert
    - ocean_process_expert
    - statistical_inference_expert
    - literature_reproduction_expert
    - visualization_communication_expert
    - scientific_discussion_partner
---

# Claim Grounded Writing

## when_to_use
Use when drafting a result summary, report, manuscript section, caption, or research decision.

## research_objective
Make each consequential statement traceable to precise evidence while preserving uncertainty and limitations.

## questions_to_resolve
- Is the statement an observation, inference, hypothesis, speculation, or conclusion?
- Which exact paper, dataset, experiment, interactive view, or report ref supports it?
- What scope, uncertainty, source-unavailability, or stale-version limitation belongs beside the claim?

## evidence_requirements
Use exact artifact refs rather than latest-version labels. Keep unsupported prose as a question or proposal.

## process_checkpoints
Experts provide evidence-grounded interpretations; the Coordinator decides whether the evidence
supports the final wording and updates affected hypothesis states before synthesis. Preserve these
judgments with evidence in the normal response or state-update reason, not a mandatory review form.

Keep direction, inference level, and sufficiency separate. Supports, opposes, mixed, and undetermined
are directions, not ranks. Description, association, mechanism_consistent, and causal_attribution
describe the kind of inference, not confidence. Match the prose to the actual evidence and the
WorkOrder's answer_standard. sufficient_level is the requested answer; max_level is a ceiling and
does not justify stronger wording. A sufficiently supported negative result can answer the question.

For each consequential attribution, compare the wording with the actual diagnostic and its scope.
"Consistent with" is not "demonstrates"; a budget residual is not an independently observed driver;
absence of a required diagnostic does not rule out a mechanism. State what was measured, what was
inferred under assumptions, and what remains untested beside the claim, not only in a final caveat.
Numerically consistent recalculations can support a diagnostic while leaving the mechanism
unresolved. Name material alternatives the evidence has not distinguished rather than relying on
a compliant level label or a generic caution.

If prose, logs, figures, or Experts disagree on a consequential quantity, name the conflict and
request a focused verification. Retain competing estimates and their definitions until the evidence
resolves them. Do not rename an inconvenient estimate "noise", select the preferred estimate, or
upgrade certainty during a text-only closing round without supporting checks. After a correction,
use the corrected evidence and identify which earlier claim or output it supersedes; otherwise
retain the unresolved limitation in the main conclusion.
If the saved hypothesis status conflicts with unresolved material evidence, the Coordinator should
revise that judgment and state before finalizing, not merely soften the prose and leave the conflict
in the tree. Important research claims should connect to the relevant question, evidence, and
limitations. Descriptive numbers can cite reports or files directly without creating hypothesis nodes.

Match the temporal and spatial scope of each claim to its evidence. A period-mean sign does not
establish the same sign throughout formation, persistence, and decay, nor does a regional mean
exclude a local contribution. When a consequential term reverses sign, distinguish the phases or
subregions and retain the counterexample in the synthesis. Define the aggregation window before
comparing mechanisms; do not silently select a window that removes contradictory evidence.

## expected_artifacts
Use the requested answer or existing report/output path. Do not create extra artifacts, a second
equivalent report narrative, or a fixed set of sections. Include limitations, conflicts, and optional
leads only when they materially help the reader assess the answer; empty sections need not be filled.

## quality_gates
Rejected, failed, or invalidated evidence cannot be written as a current conclusion. Stale evidence needs an explicit decision and reason.

## stop_or_escalation_conditions
When the evidence cannot support the desired certainty or attribution, give the supported level and
state what evidence is missing. The Coordinator may narrow the claim, continue, or acknowledge
insufficient evidence. An answered question need not have an established hypothesis or all terminal
nodes; untested optional leads can remain as future work. Do not present budget or delivery failures
as scientific refutation, and do not delay a sufficient answer merely to approach max_level.

## relevant_references
`references/review/reproducibility.md`, `references/review/physical-consistency.md`.
