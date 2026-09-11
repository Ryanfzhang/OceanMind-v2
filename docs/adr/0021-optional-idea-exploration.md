# ADR 0021 — Coordinator judgments and Expert test facts

## Current decision (2026-09-11)

Scientific judgment stays with the Coordinator. The backend retains task and
WorkOrder ownership, basic references, existing mutation revisions, execution
budgets, cancellation and durable replay. It does not score evidence, compare
declared scientific levels, require a review receipt, or prescribe a research
sequence. The question, optional method suggestions and bounded Expert evidence
report are passed through the existing assignment/result chain.

Hypotheses still use untested, supported, contested, refuted, established and
unverifiable. A test result is a fact: recording it never applies an outcome-to-
status effect. New live test schemas contain optional expected outcomes and
interpretations; old v2 discriminator effects remain readable as historical
data. The Coordinator calls adjudicate to choose a node's scientific state.
A reason and evidence references can accompany the decision; they are not a
new review-form gate. Explicit changes retain prior judgments and evidence in
history. The Coordinator decides how contradictory evidence changes a claim.

The existing necessary structural guard for direct established remains:
the cited test must target this node and at least two distinct hypothesis
nodes. This does not demonstrate scientific validity or independent evidence.
It never determines whether the user's question has been answered.

Relations to children remain alternatives or prerequisites. Parent summaries
list descendants and their limitations without inferring OR/AND truth, closing
a parent, or overwriting its scientific judgment. New plans cannot reopen an
unverifiable hypothesis; deep decomposition cannot automatically make one
unverifiable. The Coordinator can record unavailable evidence directly at the
appropriate node, without manufacturing two levels of empty decomposition.

## Question completion

pause with decision=answered or unable_to_answer records the Coordinator's
question-level decision. For compatibility, completion_summary without a
decision is the Coordinator's answered declaration; ideas mode records
ideas_delivered. Supported or refuted evidence may answer a bounded question,
and untested optional directions do not block it. No established root child,
all-root-terminal state, pending reflection acknowledgement, review ID or
scientific revision lock is required.

Running reserved tests must still be settled before declaring final completion.
A pause without a scientific decision or completion summary retains the existing
budget-exhausted/interrupted outcome and unfinished execution state. Recording
late Expert facts does not reopen the question or infer a new answer. Request
delivery remains separate from scientific outcome. stop_request_id preserves
the originating request, so later requests do not inherit a previous answer
decision merely because they share a task.

Reflection remains optional Coordinator reasoning and may be recorded without a
pending trigger or new candidates. Old reflection fields remain readable but
do not block work. Test recommendations are advisory; available tests rank by
target depth, cost and identity. An assess recommendation asks the Coordinator
to consider the evidence; it does not mechanically require new hypotheses.

## Expert tests and accounting

The Coordinator retains the full tree tool. Experts receive a separate facts-
only interface bound by the backend to their task, WorkOrder and authorized
nodes. They may read authorized claims, register optional plans, or record their
own test results. They cannot propose hypotheses, change statuses, set effects,
read other Experts' tests, or reopen nodes. Local test names are namespaced by
WorkOrder, so parallel Experts can both use T1 without collisions. Returned
records also carry the durable identity for Coordinator references.

A result may include its test retrospectively; no registration API must be called
before executing an authorized analysis. Retrospective records have no fabricated
registered_at value. Formal discrimination should explain predicted observations
before execution in the normal plan, code or conversation. This is scientific
practice for the agents to follow and review, not an execution gate.

Existing task observation IDs can be attached to results. An optional execution
reference is checked against existing task/WorkOrder ownership and provides its
actual start time. No separate scientific evidence-qualification graph is added.
Standalone tasks store optional test notes in the existing method observation
journal without creating a hypothesis tree. Test notes are permitted in an
existing ideas task too; storing facts does not authorize new execution.

For assignments supplying research_test_ids, the router reserves the complete
wave after binding actual WorkOrder identities and before dispatch. This research
counter counts Expert WorkOrders: two Tests in one Expert round cost one attempt;
two Experts contributing to one shared Test cost two. Discussion-only participants
are excluded. Replayed or recovered WorkOrder identities retain their reservation;
a new follow-up WorkOrder costs another attempt. A wave that exceeds the remaining
research budget is rejected before any of its Experts start.

This remains opt-in registered-research accounting, not a new rule that charges
every inspection or code call. Waves without research_test_ids still use the
existing WorkBudget and shared team token limits. The begin_tests helper retains
singular execution_id and historical per-test defaults for older callers; the
production router supplies the wave's actual Expert WorkOrder IDs. Recording,
splitting or replaying test facts never adds another execution charge.

## Compatibility and verification

The existing SQLite JSON row and schema_version=2 carry the revised tree
contract; runtime protocol identity is versioned at the application boundary.
Historical v1 JSON remains untouched and readable with legacy_read_only.
Old test outcome-effect data is not replayed into scientific states. No history,
scientific code, output files, publication path or benchmark attempts are rewritten.

Tests in test_exploration.py cover explicit positive/negative/insufficient
answers, unresolved optional nodes, no automatic state changes, the retained
established guard, running work and budgets, task/Expert scope, retrospective
and no-tree facts, parallel local identities, replay, and request provenance.
The v1 regression fixtures remain in test_exploration_legacy.py. These checks
verify protocol behavior; they do not prove scientific accuracy or improved
live-model performance.

## Historical v1 decision (superseded)

Status: Updated after the 2026-09-10 AutoDiscovery-style benchmark.

## Decision

Use deterministic evidence-gap scheduling instead of PW/UCT and sampled belief rewards.
The benchmark spent about 39 minutes in failed/successful belief sampling, produced two
zero rewards, and stopped despite an expansion recommendation. This policy is an OceanX
adaptation, not a claim to reproduce AutoDiscovery.

Coordinator owns the tree and assigns bounded experiments to Experts. Ordinary tasks
still need no tree. Candidate and feedback priority is contradiction, key_gap, or supporting;
cost is low, medium, or high. Feedback can inherit candidate priority/cost. Selection scans
all eligible nodes, ranks priority then cost, and prefers an already proposed test to
expansion on ties. There are no sampling API calls. Old reward/history records remain
readable for audit but have no influence on scheduling. The former sampler module and
service injection field are retained for compatibility only and are not called by the tool.

After an experiment, Coordinator records the remaining testable question in follow_up.
Supported does not mean solved. An explicit follow-up cannot coexist with solved. Expand
creates a discriminating child question; synthesize updates a parent from completed child
evidence without another experiment. Closed parents do not hide pending children. Solved,
contradicted, blocked and exhausted nodes are not automatically expanded. New goal-relevant
questions can still be proposed under root; no fixed depth or mechanism count is required.

Before leaving any finished branch, Coordinator checks for distinct alternatives revealed by
its evidence, including evidence that refuted the hypothesis. New mechanisms go under root
with their source node/observation IDs in the rationale and a discriminating test; refinements
stay under their parent, and existing alternatives are reused. Missing-data directions are
blocked. No useful alternative means a brief note in the closing summary, not forced branching.
This is part of the existing Coordinator reasoning and introduces no extra model calls or
mandatory schema fields; semantic compliance still depends on the Coordinator.

Completion with completion_summary is rejected while any scheduled contradiction/key_gap
remains, even when the node budget prevents expansion. A no-summary pause always allows
budget/user interruptions without falsely claiming resolution. Missing-data work is blocked,
not refuted. Optional supporting nodes do not prevent completion. Parents with finished
children need a synthesis update. The Coordinator still judges scientific sufficiency and
whether the root question has been answered; priority labels are not independent truth checks.
An undisclosed gap cannot be detected by this deterministic scheduler.

## Scope and compatibility

Task isolation, durable mutation receipts, observation provenance, revision guards, existing
node budgets, Expert execution authority and final tree display are retained. Existing trees
use key_gap/medium when priority/cost is absent. Paused historical tasks remain paused.
This change does not repair or modify benchmark delivery, production publication, or scientific
analysis code. The frozen completed benchmark and its evaluation remain historical evidence.

## Verification

Tests cover no sampling even with an unavailable injected sampler, receipt replay, priority
and cost ordering, critical-gap completion guard, optional/blocked branches, child synthesis,
legacy reward independence, budget interruption, task isolation and ordinary-task behavior.
