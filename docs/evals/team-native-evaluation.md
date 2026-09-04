# Team-native evaluation baseline

This matrix evaluates the unified adaptive runtime using the same prompt,
workspace, provider disclosure policy, and immutable input refs. It evaluates
scientific benefit rather than rewarding a larger number of agents.

## Modes

| Mode | Child policy | Intended use |
| --- | --- | --- |
| Legacy foreground | Team runtime flag disabled | Compatibility baseline |
| Adaptive direct | No child AgentJob | Explain existing evidence or answer directly |
| Adaptive delegated | Successive 1--4 active jobs from the role pool; 12 total by default | Inspection, computation, cross-evidence, and review chains |

## Fixed task set

1. Explain an existing artifact: expect zero children.
2. Inspect one dataset's coordinates, units, masks, and missingness: expect one
   Advisor and no workspace revision change.
3. Produce a regional mean time series from a pinned AnalysisPlan and dataset:
   expect one Executor, a single run lease, deterministic checks, and human
   approval still pending.
4. Compare two datasets and separate sampling, processing, and physical causes:
   expect two or three independent Advisors and a structure-preserving Lead
   synthesis.
5. Evaluate competing mechanism hypotheses: expect one WorkPlan and at most one
   two-party Challenge/Response round.
6. Review a publication-grade figure: expect an isolated Reviewer with frozen
   refs and typed ReviewFindings.
7. Repeat task 3 with Executor interruption, cancellation, and backend restart:
   expect failed durable work and a released lease.
8. Run a literature synthesis with and without `literature.read`: expect the
   literature Manual and reference access only when capability-enabled.

## Recorded metrics

- task completion and required-output coverage;
- evidence-ref validity and unsupported-claim count;
- deterministic check pass/fail state;
- useful Reviewer issues and subsequent fix rate;
- retained disagreements, counterevidence, failed branches, and confidence;
- input/output tokens, tool calls, summed child wall time, and end-to-end latency;
- canonical mutations, lease conflicts, cancellation residue, and disclosure
  violations;
- researcher rating of usefulness, calibration, and auditability.

Adaptive routing passes only when tasks 4--6 show repeatable improvement in
evidence coverage, error detection, or calibration after cost and latency are
included. Repeating one failed logical assignment as several visually distinct
Executor jobs is a hard failure.
