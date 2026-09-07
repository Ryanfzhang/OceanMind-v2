# ADR 0019: Coordinator, Expert Session, And Result Bundle

- Status: Current
- Date: 2026-08-18
- Supersedes: proposal-only teams, AnalysisRun orchestration, worker/executor attempt trees, task map state, and the Ocean terminal/Plot Studio clients

## Decision

OceanMind has one agent architecture and one Ocean client.

1. The Electron Desktop is the only Ocean client. Protocol v2 accepts only the
   `desktop` client kind.
2. A user request is owned by one Coordinator. The Coordinator either answers
   directly or issues a typed `WorkOrder` to the smallest useful set of Experts.
3. Each logical Expert owns one persistent Expert Session. Follow-up work reuses
   that session and its compact `WorkstreamCheckpoint`; it does not create a new
   worker, executor, attempt tree, or hidden analysis-run identity.
4. An Expert may inspect sources and execute code within its authority. It returns
   exactly one typed `WorkResult` per assignment round. The receiving Coordinator
   validates the envelope and decides whether the task is complete or which
   missing outcome should become the next `WorkOrder`.
5. User-facing scientific delivery consists only of ordered `interactive_view`
   artifacts and one `report`. Internal evidence artifacts remain immutable and
   are linked from the report rather than exposed as competing result types.
6. Code executions are evidence owned by the Expert Session. They are not an
   orchestration layer and their identifiers are never required in a WorkOrder.
7. Old SQLite schemas may be read only through one-time migrations. A fresh
   database never creates their tables, and no current runtime API exposes them.

## Terminal conditions

- Expert Session: one valid `WorkResult`, a material blocker, or cancellation.
- Coordinator: the user question is answered, required outcomes remain genuinely
  unavailable, user input is required, or the request is cancelled.
- Provider/tool interruption preserves the checkpoint; it does not create a new
  logical Expert.

## Verification

- `tests/test_oceanx/test_current_team_contract.py`
- `tests/test_oceanx/test_request_store.py`
- `tests/test_oceanx/test_protocol_v2.py`
- `tests/test_oceanx/test_stdio_host.py`
