# ADR 0014: ResearchTask And Connection Boundary

- Status: Accepted
- Date: 2026-07-14

## Context

A transport session is temporary and cannot be used as the identity of a
research conversation. Desktop task switching, reconnect, and backend restart
must not merge two researchers' lines of inquiry merely because they share a
workspace.

## Decision

`ResearchTask` is a durable, workspace-bound research thread. It owns a title,
status, revision, active foreground request pointer, display transcript,
stable conversation checkpoint, and task-local map projection. A Protocol v2
`RequestContext.task_id` selects that thread for `session.submit`; it is
optional only for legacy TUI compatibility.

`ClientConnection` remains an authenticated transport detail. It owns neither
conversation memory nor task history. One task may have at most one active
foreground request. Task mutations use optimistic `task_revision` checks.

## Consequences

- A desktop client can list, create, rename, archive, reopen, and open tasks
  without copying workspace artifacts.
- A task snapshot is renderer-safe: it carries display transcript and view
  state, never raw model messages or hidden prompts.
- Existing TUI `session.submit` requests with no task ID retain their current
  connection-scoped behavior until that client adopts task navigation.

## Verification

- `tests/test_ocean_partner/test_research_tasks.py`
- `tests/test_ocean_partner/test_task_router.py`
- `tests/test_ocean_partner/test_agent_router.py`
