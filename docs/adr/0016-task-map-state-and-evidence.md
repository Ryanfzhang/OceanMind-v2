# ADR 0016: Task Map State And Scientific Evidence

- Status: Superseded by ADR 0019; historical only
- Date: 2026-07-14

## Context

The map must remain present while a researcher changes tasks, but viewport,
layer visibility, and active inspection are UI projection state. Storing those
changes in immutable scientific artifacts would create evidence versions for
ordinary navigation.

## Decision

`TaskMapState` is a mutable, task-local projection containing an exact
`MapScene` reference, viewport, layer controls, active `Selection` reference,
and active `LinkedPlot` reference. The backend validates every supplied
artifact reference belongs to the task workspace and has the expected type.
The state is protected by task revision, not workspace revision.

`MapScene`, `SpatialLayer`, `Selection`, and `LinkedPlot` remain immutable
artifacts. Publishing new evidence creates a new artifact version; changing
zoom or opacity changes only `TaskMapState`.

## Verification

- `tests/test_ocean_partner/test_research_tasks.py`
- `tests/test_ocean_partner/test_task_router.py`
