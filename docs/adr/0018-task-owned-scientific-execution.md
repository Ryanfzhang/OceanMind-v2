# ADR 0018: Task-Owned Scientific Execution

- Status: Superseded by ADR 0019; historical only
- Date: 2026-08-08
- Refines: ADR 0014 Team-Native Sparse Activation

## Context

Ocean previously exposed `analysis_run_create` and `run_id` to the model. In a
team request this created two lifecycle owners: the backend generated a child
WorkOrder, while the Lead or Executor also had to create, remember, and pass an
AnalysisRun identity. A timeout could therefore leave valid data and plans in
the Task but make the next child reference a nonexistent run.

The durable execution evidence is still necessary. Model-authored code must run
against frozen inputs in a sandbox, and attempts, checks, outputs, and
provenance must survive model/API failure. The mistake was making that evidence
container part of agent orchestration.

## Decision

1. The Task is the user-facing unit of work. WorkOrders are agent-execution
   nodes; scientific execution records are backend-owned evidence beneath the
   Task. They are different identities.
2. A Lead delegates an Executor with an `ExecutionSpec`: exact AnalysisPlan,
   dataset refs, materialization levels, expected outputs, assertions, and
   policy. Reuse is the default (`auto`; legacy `start` is a compatibility
   alias). Only an explicit `restart` requests a fresh execution. The Lead
   never supplies an execution-record ID.
3. Before a child starts, the backend validates the spec, freezes input
   fingerprints, creates the execution directory and durable record, and binds
   every Executor tool to that record.
4. In `auto`, compatibility `start`, and explicit `continue` modes, the backend
   resolves the newest non-abandoned execution in the same Task whose plan and
   ordered input contract exactly match the supplied spec. `continue` fails if
   no match exists; `auto` creates one. No cross-Task or approximate match is
   allowed.
5. `analysis_run_create` is absent from the team-native Lead and Executor
   registries. Bound Executor tools omit `run_id` from their schemas and inject
   it server-side.
6. A transient child/API failure retries the same logical WorkOrder and the same
   prepared execution record once. A code, contract, or validation failure may
   receive one Lead-directed recovery attempt on that same AgentJob. After two
   total attempts the durable partial result returns to the Lead; the backend
   rejects a third duplicate attempt.
7. Child hard ceilings are projected from the enclosing request budget instead
   of using a separate 180-second default. The foreground request remains the
   outer wall-clock and cancellation boundary.
8. The persisted Python/SQLite class and table may retain the `AnalysisRun`
   name during migration. This is an internal storage compatibility detail, not
   an agent-visible workflow requirement.
9. The legacy team-disabled foreground registry retains model-created
   AnalysisRun behavior only as a rollback path until its real-model evaluation
   fixtures are migrated.

## Consequences

- A missing or mistyped run ID can no longer prevent a team Executor from
  starting; neither Lead nor Executor can submit one.
- Preparing a scientific execution may advance workspace revision before the
  child runs. The whole Team DAG is persisted first so later WorkOrders keep
  stable identities across that mutation.
- A failed child can leave a draft execution record. This is intentional
  resumable evidence, not a half-published artifact.
- `AnalysisRun` remains useful for sandbox isolation, reproducibility, attempts,
  verification, and publication gates; it no longer coordinates agents.

## Required invariants

- An Executor starts only after its exact execution record exists.
- Agent-visible Executor tool schemas contain neither `analysis_run_create` nor
  `run_id`.
- Automatic retry and Lead-directed recovery preserve WorkOrder and
  execution-record identity.
- One WorkOrder has at most two attempts: the initial attempt and one recovery.
- Task continuation matches exact plan and inputs and never crosses Task scope.
- Advisors and Reviewers remain read-only; one lease controls the sole writer.
- Publication still requires a trusted, checks-passed attempt.

## Verification

- `tests/test_oceanx/test_team_orchestrator.py`
- `tests/test_oceanx/test_team_models.py`
- `tests/test_oceanx/test_analysis_runs.py`
