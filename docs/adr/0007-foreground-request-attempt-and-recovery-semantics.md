# ADR 0007: Foreground Request, Attempt, And Recovery Semantics

- Status: Superseded by ADR 0019; historical only
- Date: 2026-07-11
- Decision IDs: OD-08

## Context

Ocean Research Partner must be able to cancel an agent request, safely stop an
analysis attempt, and survive a backend restart without turning a client retry
into a second mutation.  These behaviors cannot be inferred from a stream of
assistant text.  The first implementation deliberately has one foreground
agent request per session, so it needs an explicit lifecycle before the
runtime, transport, and persistent store are generalized.

## Decision

1. A mutating Protocol v2 request has the durable lifecycle `accepted`,
   `in_progress`, then exactly one of `completed`, `failed`, `cancelled`, or
   `interrupted`.  The durable `RequestRecord` is written before
   `request.accepted`; its terminal result and terminal event reference are
   committed before either is broadcast.
2. Only one agent-originated foreground request may be active in a session.
   Read-only requests, including workspace snapshots and map reads, may run
   alongside it.  Control requests (`request.cancel`, `interaction.respond`,
   and `system.shutdown`) bypass the foreground queue.
3. Request identity is a canonical SHA-256 hash of protocol version, request
   type, payload, workspace ID, and expected workspace revision.  A retry with
   the same ID and hash replays durable state or the terminal result; the same
   ID with a different hash returns `request_id_conflict`.  Replay is still
   checked against the server-bound principal and capability set, and is never
   an authority to execute the mutation again.
4. An `AnalysisRun` is a plan-bound container; each execution is an immutable
   attempt.  Attempt states are `queued`, `running`, `succeeded`, `rejected`,
   `failed`, `timed_out`, `resource_limited`, `cancelled`, `interrupted`, and
   `source_changed`.  Cancellation preserves the attempt record and returns
   its run to `ready`.  A cancelled or restarted request never silently
   continues its model or Python process.
5. `request.cancel` is best effort but is terminally observable.  It cancels
   the model stream and every foreground subprocess it owns, writes
   `request.cancelled`, and leaves finished evidence intact.  A hard budget
   failure follows the same containment rule: write `budget_exhausted`, stop
   the active attempt, and return the run to `ready`.
6. On controlled shutdown or startup recovery, unfinished requests become
   `interrupted`; running attempts become `interrupted` and their parent run
   becomes `ready`.  Completed records, artifacts, and event references remain
   queryable.  Version 1 does not implement transparent model/process resume.
7. Each foreground request receives a versioned budget containing maximum
   model turns, input/output tokens, tool calls, wall time, and optional cost
   ceiling.  Analysis subprocesses additionally obey the sandbox resource
   policy from ADR 0002.  A permission prompt cannot extend a hard limit.
8. Every model-originated mutating tool call receives a backend-generated
   `operation_id`, persisted with the request/turn/tool-call checkpoint in the
   same transaction as its domain mutation.  Replaying an operation returns
   the old tool result instead of repeating the mutation.

## Consequences

- Frontends set busy state only from `request.accepted` and terminal request
  events, never from `assistant.turn.completed`.
- The first RequestStore is intentionally a small durable journal rather than
  a full event-sourced database.  Phase 2 migrates it into the project-local
  metadata store with the artifact and workspace tables.
- Test hooks may inject a crash after terminal commit and before broadcast.
  This is a required contract test, not a production recovery mechanism.
- A user must explicitly create a new request or attempt after interruption;
  the UI should make preserved work and the reason visible instead of implying
  hidden continuation.

## Verification

- `tests/test_oceanx/test_request_store.py`
- `tests/test_oceanx/test_router.py`
- `tests/test_oceanx/test_stdio_host.py`
