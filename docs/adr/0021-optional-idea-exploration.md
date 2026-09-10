# ADR 0021 — Hypotheses, cross-hypothesis tests, and evidence (v2)

## Current decision (2026-09-10)

The current runtime uses a hypothesis-only tree. Tests are separately registered
operations targeting one or more hypothesis IDs, and Evidence references the
existing task observation journal. No data or execution artifacts are duplicated.

Hypotheses use untested, supported, contested, refuted, established, unverifiable.
Only the last three are terminal. Each declares alternatives (OR) or prerequisites
(AND) for its children. Tests declare outcome labels and their target effects before
execution. Every target must appear in the declared effects; direct established
requires at least two distinct targets. This is a structural guard, not a guarantee
that the proposed test actually discriminates or establishes scientific truth.
Opposite evidence becomes contested and retains the earlier evidence in history.

Every nonterminal leaf needs a feasible pending test or decomposition. Two successive
decompositions with no feasible test mark that line unverifiable, with its missing
test reasons retained. Root mechanism generation is not counted as decomposition.
Tests may be declared atomically with child hypotheses, so a testable second-level
child is not incorrectly closed. New feasible tests can reopen unverifiable lines.

After ALL children are terminal, synthesis runs mechanically, children first:

| Relation | Decisive case | Uniform opposite case | Otherwise |
| --- | --- | --- | --- |
| alternatives | Any established: established | All refuted: refuted | unverifiable |
| prerequisites | Any refuted: refuted | All established: established | unverifiable |

Supported or contested children prevent closure, even with a decisive sibling.
Parent summaries embed every child summary, propagating unverifiable reasons through
all ancestors, including established/refuted ancestors. No synthesis model call or
repeat experiment is required. Prior states and summaries remain in history.

Frontier tests are feasible and unexecuted. They rank by the shallowest hypothesis
they can affect, then relative cost, then stable test ID. There is no numeric belief,
weight, novelty, sampled reward, priority label, or historical node quota.

`ocean_assign.research_test_ids` connects the Coordinator's planned tests to a wave.
It is stripped before Expert dispatch. Dispatch atomically reserves one attempt per
test (default budget 20), before external work. Failed/uncertain attempts remain
charged. Record their results, or explicitly mark a pending/running test infeasible
with a reason; neither operation invents negative scientific evidence. Unexpected
outcomes are stored without applying an undeclared effect. Initial data inspection
before proposing hypotheses is uncharged. Other application/runtime limits still apply.

Reflection is queued when a direct root hypothesis becomes terminal, and whenever
executions cross the configured budget percentage (default 25%). Wait for running
test results before reflection. Coordinator answers whether current candidates cover
the question; incomplete coverage requires new root candidates. Acknowledged triggers
are durable, and unchanged terminal states do not repeatedly enqueue reflection.

Completion requires all root children terminal, no running tests and reflection done.
At least one established root child gives answered; otherwise unable_to_answer.
Final summary includes the merged root summary and unverifiable limitations. Budget
exhaustion pauses without a completion claim. User interruption is separately retained.
Ideation-only tasks may deliver an untested shortlist but cannot dispatch experiments.

### Compatibility and evaluation

The existing SQLite JSON row carries schema_version=2 for new trees. Historical v1
JSON remains untouched and readable, with legacy_read_only exposed to callers. New
protocol research must use a new task rather than fabricate test effects or logical
relations from old nodes. The old engine is retained as exploration_legacy.py for
historical regression fixtures; the registered model tool accepts only v2 inputs.
Frozen benchmark source and existing runtime instances are not modified or restarted.

Verification lives in tests/test_oceanx/test_exploration.py. V1 regression cases are
retained in test_exploration_legacy.py. These are protocol tests, not proof of improved
scientific quality or successful live-model behavior.

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
