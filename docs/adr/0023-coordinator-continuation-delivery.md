# Coordinator continuation and delivery

Coordinator cumulative input/output token usage is accounting, not a hard request
termination condition. The former router check could discard the turn that crossed
the threshold before final delivery. Expert token budgets remain independent;
existing model context limits, cancellation, and other runtime limits still apply.

Saved Expert candidates and published TaskResults are different states. A new query
in the same task can now review and publish retained candidates from earlier requests.
The publication tool no longer filters task-scoped candidates by current request id.
The original execution and WorkOrder remain provenance; publication is attributed to
the current Coordinator request. No scientific computation is repeated.

Coordinator resource inventory exposes task_outputs across request rounds, including
candidate/published state and exact immutable citation tokens for published results.
Continuation policy requires checking this inventory instead of trusting narrative
claims that a save was published. Existing frontend task-scoped link resolution is
retained. Old candidate files are not silently published by the backend; Coordinator
review still owns publication. A previously completed answer with unresolved links
is not rewritten by this code change.

Regression coverage includes final delivery above the old cumulative token thresholds
and publication of a previous request's retained candidate with a durable citation.
